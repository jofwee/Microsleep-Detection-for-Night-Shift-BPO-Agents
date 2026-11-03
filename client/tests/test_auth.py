"""Client auth: the HTTP client, sign-in, workstation pairing, and the face-MFA orchestration.
A fake transport records every request, so these tests check exactly what is sent to the server."""
import base64
import json

import httpx
import keyring.errors
import numpy as np
import pytest

from auth import face_mfa, login, workstation
from auth.supabase_client import Api, ApiError, jwt_claims
from config import ConfigError, load_config
from identity.liveness import Action
from identity.profile import EnrolledProfile
from test_enroll import FRAME, FakeEmbedder, make_face
from vision.calibration import Calibration

URL = "https://abc.supabase.co"
KEY = "sb_publishable_test"


def make_jwt(**claims) -> str:
    def enc(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{enc({'alg': 'HS256'})}.{enc(claims)}.sig"


class Server:
    """Routes (method, path) to canned answers and records requests."""

    def __init__(self):
        self.routes: dict[tuple[str, str], list] = {}
        self.requests: list[httpx.Request] = []

    def on(self, method, path, status=200, body=None):
        self.routes.setdefault((method, path), []).append((status, body))
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes.get((request.method, request.url.path))
        if not queue:
            return httpx.Response(404, json={"error": "no_route", "message": f"{request.method} {request.url.path}"})
        status, body = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

    def last(self, path=None) -> httpx.Request:
        return [r for r in self.requests if path is None or r.url.path == path][-1]

    def paths(self):
        return [(r.method, r.url.path) for r in self.requests]


def api_for(server: Server, **kw) -> Api:
    return Api(URL, KEY, client=httpx.Client(transport=httpx.MockTransport(server)), **kw)


TOKEN = make_jwt(sub="user-1", session_id="sess-1")
SIGN_IN_OK = {"access_token": TOKEN, "refresh_token": "r1", "expires_in": 3600, "user": {"id": "user-1"}}


# ---------------------------------------------------------------------------- Api
def test_jwt_claims_reads_the_payload_and_survives_garbage():
    assert jwt_claims(TOKEN) == {"sub": "user-1", "session_id": "sess-1"}
    assert jwt_claims("nonsense") == {}


def test_sign_in_sends_only_the_publishable_key_and_stores_the_session_in_memory():
    s = Server().on("POST", "/auth/v1/token", 200, SIGN_IN_OK)
    api = api_for(s, workstation_token="W" * 30)
    api.sign_in("a@example.com", "pw")
    req = s.last()
    assert req.headers["apikey"] == KEY
    assert "authorization" not in req.headers  # nothing to authenticate with yet
    assert "x-workstation-token" not in req.headers  # the PC token is not sent to the auth server
    assert req.url.params["grant_type"] == "password"
    assert json.loads(req.content) == {"email": "a@example.com", "password": "pw"}
    assert api.signed_in and api.user_id == "user-1" and api.session_id == "sess-1"


def test_authenticated_calls_carry_the_token_and_the_workstation_token():
    s = Server().on("POST", "/auth/v1/token", 200, SIGN_IN_OK).on("POST", "/functions/v1/face-challenge", 200, {"ok": 1})
    api = api_for(s, workstation_token="W" * 30)
    api.sign_in("a@example.com", "pw")
    api.function("face-challenge", {})
    req = s.last()
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert req.headers["x-workstation-token"] == "W" * 30
    assert req.headers["apikey"] == KEY


def test_a_call_with_auth_false_never_sends_credentials():
    s = Server().on("POST", "/auth/v1/token", 200, SIGN_IN_OK).on("POST", "/functions/v1/pair-workstation", 200, {})
    api = api_for(s, workstation_token="W" * 30)
    api.sign_in("a@example.com", "pw")
    api.function("pair-workstation", {"code": "X"}, auth=False)
    req = s.last()
    assert "authorization" not in req.headers and "x-workstation-token" not in req.headers


@pytest.mark.parametrize(
    "status, body, code, extra",
    [
        (403, {"error": "verification_failed", "message": "no", "reason": "no_match"}, "verification_failed", {"reason": "no_match"}),
        (403, {"code": "42501", "message": "denied", "details": None}, "42501", {"details": None}),
        (400, {"code": 400, "error_code": "invalid_credentials", "msg": "Invalid login credentials"}, "invalid_credentials", {}),
        (429, {"error": "face_locked", "message": "wait", "retry_after_seconds": 600}, "face_locked", {"retry_after_seconds": 600}),
    ],
)
def test_server_errors_become_apierror_with_a_stable_code(status, body, code, extra):
    s = Server().on("POST", "/functions/v1/x", status, body)
    api = api_for(s)
    with pytest.raises(ApiError) as e:
        api.function("x", {})
    assert e.value.status == status and e.value.code == code and e.value.extra == extra


def test_a_non_json_error_and_a_network_failure_are_reported_cleanly():
    s = Server().on("POST", "/functions/v1/x", 502, None)
    with pytest.raises(ApiError) as e:
        api_for(s).function("x", {})
    assert e.value.code == "http_502"

    def boom(request):
        raise httpx.ConnectError("no route to host")

    api = Api(URL, KEY, client=httpx.Client(transport=httpx.MockTransport(boom)))
    with pytest.raises(ApiError) as e2:
        api.function("x", {})
    assert e2.value.code == "network_error"


def test_the_token_is_refreshed_before_it_expires(monkeypatch):
    new_token = make_jwt(sub="user-1", session_id="sess-1", n=2)
    s = (Server()
         .on("POST", "/auth/v1/token", 200, SIGN_IN_OK)
         .on("POST", "/functions/v1/f", 200, {"ok": 1}))
    api = api_for(s)
    api.sign_in("a@example.com", "pw")
    s.routes[("POST", "/auth/v1/token")] = [(200, {"access_token": new_token, "refresh_token": "r2", "expires_in": 3600})]
    monkeypatch.setattr("auth.supabase_client.time.time", lambda: api.expires_at - 30)  # inside the last minute
    api.function("f", {})
    assert s.last("/auth/v1/token").url.params["grant_type"] == "refresh_token"
    assert s.last("/functions/v1/f").headers["authorization"] == f"Bearer {new_token}"


def test_sign_out_forgets_the_session_even_if_the_server_refuses():
    s = Server().on("POST", "/auth/v1/token", 200, SIGN_IN_OK).on("POST", "/auth/v1/logout", 401, {"error": "bad"})
    api = api_for(s)
    api.sign_in("a@example.com", "pw")
    api.sign_out()
    assert not api.signed_in and api.refresh_token is None


def test_data_api_calls_build_the_right_requests():
    s = (Server().on("POST", "/auth/v1/token", 200, SIGN_IN_OK)
         .on("GET", "/rest/v1/profiles", 200, [{"id": "x"}])
         .on("POST", "/rest/v1/events", 201, [{"id": 1}])
         .on("PATCH", "/rest/v1/sessions", 204, None)
         .on("POST", "/rest/v1/rpc/ack_alert", 200, None))
    api = api_for(s)
    api.sign_in("a@example.com", "pw")
    assert api.select("profiles", {"id": "eq.x"}) == [{"id": "x"}]
    assert s.last().url.params["id"] == "eq.x"
    api.insert("events", {"type": "YAWN"}, ignore_duplicates=True)
    assert s.last().headers["prefer"] == "return=representation,resolution=ignore-duplicates"
    api.patch("sessions", {"id": "s1"}, {"ended_at": "now"})
    assert s.last().url.params["id"] == "eq.s1"
    assert api.rpc("ack_alert", {"p_alert": "a"}) is None


# ---------------------------------------------------------------------------- login
PROFILE = {"role": "agent", "team_id": "t1", "full_name": "Agent One", "must_change_password": True, "active": True}


def signed_in_server(profile=PROFILE):
    return (Server().on("POST", "/auth/v1/token", 200, SIGN_IN_OK)
            .on("GET", "/rest/v1/profiles", 200, [profile] if profile else [])
            .on("POST", "/auth/v1/logout", 204, None))


def test_agent_sign_in_returns_the_account_and_normalises_the_email():
    s = signed_in_server()
    api = api_for(s)
    acct = login.sign_in(api, "  Agent@Example.COM ", "pw")
    assert acct == login.Account("user-1", "Agent One", "t1", True)
    assert json.loads(s.requests[0].content)["email"] == "agent@example.com"


def test_a_team_leader_cannot_use_the_desktop_app_and_is_signed_out():
    s = signed_in_server({**PROFILE, "role": "team_leader"})
    api = api_for(s)
    with pytest.raises(login.NotAnAgent):
        login.sign_in(api, "l@example.com", "pw")
    assert not api.signed_in and ("POST", "/auth/v1/logout") in s.paths()


def test_an_inactive_or_missing_profile_is_refused():
    for profile in ({**PROFILE, "active": False}, None):
        api = api_for(signed_in_server(profile))
        with pytest.raises(ApiError) as e:
            login.sign_in(api, "a@example.com", "pw")
        assert e.value.code == "inactive" and not api.signed_in


@pytest.mark.parametrize(
    "pw, problems",
    [("Short1a", 1), ("alllowercase123", 1), ("ALLUPPERCASE123", 1), ("NoDigitsHereAtAll", 1), ("short", 3), ("GoodPassw0rdLong", 0)],
)
def test_password_policy(pw, problems):
    assert len(login.password_problems(pw)) == problems


def test_change_password_rejects_a_weak_one_without_calling_the_server():
    s = signed_in_server()
    api = api_for(s)
    api.sign_in("a@example.com", "pw")
    n = len(s.requests)
    with pytest.raises(ValueError, match="too weak"):
        login.change_password(api, "weak")
    assert len(s.requests) == n


def test_change_password_updates_auth_then_opens_the_gate():
    s = signed_in_server().on("PUT", "/auth/v1/user", 200, {}).on("POST", "/rest/v1/rpc/ack_password_changed", 204, None)
    api = api_for(s)
    api.sign_in("a@example.com", "pw")
    login.change_password(api, "A-Brand-New-Passw0rd")
    assert s.paths()[-2:] == [("PUT", "/auth/v1/user"), ("POST", "/rest/v1/rpc/ack_password_changed")]
    assert json.loads(s.last("/auth/v1/user").content) == {"password": "A-Brand-New-Passw0rd"}


# ---------------------------------------------------------------------------- workstation
class FakeStore:
    def __init__(self):
        self.data = {}

    def get_password(self, service, username):
        return self.data.get((service, username))

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.data:
            raise keyring.errors.PasswordDeleteError("missing")
        del self.data[(service, username)]


def test_pairing_stores_the_token_and_the_api_starts_using_it():
    s = Server().on("POST", "/functions/v1/pair-workstation", 200, {"workstation_id": "ws1", "team_id": "t1", "token": "T" * 43})
    api, store = api_for(s), FakeStore()
    ws = workstation.pair(api, "ABCDE-FGHJK", "Desk 4", store)
    assert ws == workstation.Workstation("ws1", "t1", "T" * 43)
    assert workstation.load(store) == ws
    assert api.workstation_token == "T" * 43
    assert json.loads(s.last().content) == {"code": "ABCDE-FGHJK", "name": "Desk 4"}
    assert "authorization" not in s.last().headers and "x-workstation-token" not in s.last().headers


def test_workstation_load_tolerates_missing_or_damaged_entries_and_forget_is_idempotent():
    store = FakeStore()
    assert workstation.load(store) is None
    store.set_password(workstation.SERVICE, workstation.ACCOUNT, "not json")
    assert workstation.load(store) is None
    workstation.save(workstation.Workstation("a", "b", "c"), store)
    assert workstation.load(store).token == "c"
    workstation.forget(store)
    workstation.forget(store)
    assert workstation.load(store) is None


# ---------------------------------------------------------------------------- config
def test_config_reads_the_env_file_and_refuses_a_secret_key(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    f = tmp_path / ".env"
    f.write_text('# comment\nSUPABASE_URL="https://abc.supabase.co/"\nSUPABASE_PUBLISHABLE_KEY=sb_publishable_x\n')
    cfg = load_config(f)
    assert (cfg.supabase_url, cfg.publishable_key) == ("https://abc.supabase.co", "sb_publishable_x")
    f.write_text("SUPABASE_URL=https://abc.supabase.co\nSUPABASE_PUBLISHABLE_KEY=sb_secret_oops\n")
    with pytest.raises(ConfigError, match="SECRET"):
        load_config(f)
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.env")


# ---------------------------------------------------------------------------- face MFA
def unit(seed, n=512):
    v = np.random.default_rng(seed).normal(size=n)
    return v / np.linalg.norm(v)


def good_evidence(n=5, passed=True):
    return face_mfa.Evidence([unit(i) for i in range(n)], [0.99] * n, passed)


CHALLENGE = {"challenge_id": "c" * 36, "nonce": "N" * 43, "actions": ["BLINK_TWICE", "TURN_LEFT"], "expires_at": "x"}


def mfa_server(verify_status=200, verify_body=None):
    return (Server().on("POST", "/auth/v1/token", 200, SIGN_IN_OK)
            .on("POST", "/functions/v1/face-challenge", 200, CHALLENGE)
            .on("POST", "/functions/v1/verify-face", verify_status, verify_body or {"verified": True, "expires_at": "later", "score": 0.8}))


def test_face_mfa_success_sends_embeddings_and_liveness_and_nothing_else():
    s = mfa_server()
    api = api_for(s)
    api.sign_in("a@example.com", "pw")
    seen = {}
    result = face_mfa.run_face_mfa(api, lambda actions: seen.update(actions=actions) or good_evidence())
    assert result == face_mfa.FaceMfaResult(True, "ok", "later")
    assert seen["actions"] == [Action.BLINK_TWICE, Action.TURN_LEFT]  # the server's random actions
    body = json.loads(s.last("/functions/v1/verify-face").content)
    assert set(body) == {"challenge_id", "nonce", "embeddings", "liveness"}
    assert body["challenge_id"] == "c" * 36 and body["nonce"] == "N" * 43
    assert len(body["embeddings"]) == 5 and len(body["embeddings"][0]) == 512
    assert body["liveness"] == {"passive": [0.99] * 5, "challenge_passed": True}


def test_face_mfa_reports_why_the_server_refused():
    s = mfa_server(403, {"error": "verification_failed", "message": "no", "reason": "no_match"})
    api = api_for(s)
    api.sign_in("a@example.com", "pw")
    assert face_mfa.run_face_mfa(api, lambda a: good_evidence()) == face_mfa.FaceMfaResult(False, "no_match")


def test_not_enrolled_and_locked_are_raised_to_the_caller():
    for status, code in ((409, "not_enrolled"), (429, "face_locked")):
        s = (Server().on("POST", "/auth/v1/token", 200, SIGN_IN_OK)
             .on("POST", "/functions/v1/face-challenge", status, {"error": code, "message": "x"}))
        api = api_for(s)
        api.sign_in("a@example.com", "pw")
        with pytest.raises(ApiError) as e:
            face_mfa.run_face_mfa(api, lambda a: good_evidence())
        assert e.value.code == code


def test_too_few_frames_is_not_sent_to_the_server():
    s = mfa_server()
    api = api_for(s)
    api.sign_in("a@example.com", "pw")
    res = face_mfa.run_face_mfa(api, lambda a: good_evidence(2, passed=False))
    assert not res.verified
    assert ("POST", "/functions/v1/verify-face") not in s.paths()


def test_a_failed_challenge_is_still_reported_so_the_server_counts_it():
    s = mfa_server(403, {"error": "verification_failed", "message": "no", "reason": "challenge_failed"})
    api = api_for(s)
    api.sign_in("a@example.com", "pw")
    res = face_mfa.run_face_mfa(api, lambda a: good_evidence(5, passed=False))
    assert res == face_mfa.FaceMfaResult(False, "challenge_failed")
    assert json.loads(s.last("/functions/v1/verify-face").content)["liveness"]["challenge_passed"] is False


def test_upload_enrollment_sends_numbers_only():
    s = Server().on("POST", "/functions/v1/enroll-face", 200, {"enrolled": True})
    api = api_for(s)
    embs = np.stack([unit(i) for i in range(5)])
    profile = EnrolledProfile(embs, embs.mean(axis=0), "arcface_w600k_r50", "buffalo_l", Calibration(ear_open=0.3, ear_closed=0.1))
    face_mfa.upload_enrollment(api, profile, "ABCDE-FGHJK", True)
    body = json.loads(s.last().content)
    assert set(body) == {"code", "consent", "model_name", "model_version", "embeddings", "calibration"}
    assert body["consent"] is True and len(body["embeddings"]) == 5
    assert body["calibration"]["ear_open"] == 0.3
    with pytest.raises(ValueError):
        face_mfa.upload_enrollment(api, EnrolledProfile(embs, embs.mean(axis=0), "m", "v"), "C", True)


# ---------------------------------------------------------------------------- collect_evidence
class Landmarker:
    def __init__(self, script):
        self.script = script

    def detect(self, frame, ts):
        return self.script(ts)


class Liveness:
    def __init__(self, score=0.99):
        self.value = score

    def score(self, frame, face):
        return self.value


def frame_stream(ms, step=33):
    return ((t, FRAME) for t in range(0, ms, step))


def test_evidence_is_collected_while_the_challenge_runs():
    def script(t):  # face straight for the first 1.2 s, then turned toward their left
        return [make_face(turn=0.0 if t < 1200 else 0.25)]

    ev = face_mfa.collect_evidence(frame_stream(8000), Landmarker(script), FakeEmbedder(), Liveness(), [Action.TURN_LEFT])
    assert ev.challenge_passed and ev.failure is None
    assert len(ev.embeddings) >= 3 and len(ev.passive) == len(ev.embeddings)
    assert all(p == 0.99 for p in ev.passive)


def test_a_face_that_does_nothing_fails_the_challenge():
    ev = face_mfa.collect_evidence(frame_stream(15_000), Landmarker(lambda t: [make_face()]), FakeEmbedder(), Liveness(), [Action.TURN_LEFT])
    assert not ev.challenge_passed and "challenge failed" in ev.failure


def test_a_second_person_in_view_collects_nothing():
    ev = face_mfa.collect_evidence(frame_stream(3000), Landmarker(lambda t: [make_face(), make_face(cx=100)]), FakeEmbedder(), Liveness(), [Action.BLINK_TWICE])
    assert ev.embeddings == [] and not ev.challenge_passed


def test_on_frame_can_cancel_and_sees_the_liveness_score():
    seen = []

    def on_frame(frame, challenge, status, n_faces, live):
        seen.append((n_faces, live))
        return len(seen) < 20

    ev = face_mfa.collect_evidence(frame_stream(5000), Landmarker(lambda t: [make_face()]), FakeEmbedder(), Liveness(0.42), [Action.TURN_LEFT], on_frame=on_frame)
    assert ev.failure == "cancelled" and len(seen) == 20
    assert seen[-1] == (1, 0.42)
