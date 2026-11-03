# Agent Microsleep & Fatigue Monitor — Tech Stack & Plan

## Context
This is a thesis project built from scratch. The goal is a webcam system that detects **microsleep (EAR)**, **blink frequency**, **yawning** and **head tilt/nod**. It keeps a **saved biometric profile per agent** and has an **identity-lock fallback**: it only analyzes the enrolled agent's face. Another person in the frame, someone sitting in for the agent, a photo or a face-swap feed can't be measured as the agent or pass as them.

Agents work in a **simulated BPO**: AI callers (Groq API) phone in with different inquiries in English, Tagalog or Taglish, and the agent answers by voice. The face monitor runs **only while the agent is Available, on a call or in wrap-up**.

Access runs on **Supabase** with two roles: **agent** and **team leader (supervisor)**. Agents sign in with email + password + face (the face is the second factor). Team leaders sign in with email + password and only see their own team.

**Assumption:** "agents" means call-center or office agents on Windows PCs with ordinary webcams, at prototype scale (tens of agents). If they are drivers instead, the client hardware changes (a Jetson or Raspberry Pi with an IR camera) and the BPO simulation is dropped. The rest of the stack stays the same.

## Recommended stack

| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.11 or 3.12** (desktop client), **TypeScript** (Edge Functions, dashboard) | Every CV/ML library below has wheels for Python. TypeScript is what Supabase functions and the React dashboard use |
| Camera | **OpenCV** (`opencv-python`) | Capture, overlays, `solvePnP`, and video-file replay for testing |
| Landmarks / blendshapes / head pose | **MediaPipe Face Landmarker** (Tasks API, `face_landmarker.task`) | 478 3D landmarks, 52 blendshapes (`eyeBlink*`, `jawOpen`, `eyeLookDown*`) and a head transform matrix. Runs in real time on CPU. Set `num_faces=3` so it also sees bystanders |
| Math | **NumPy** | EAR, MAR, PERCLOS and Euler angles |
| Face recognition (biometric) | **InsightFace ArcFace** `w600k_r50` from the `buffalo_l` pack (`w600k_mbf` from `buffalo_s` on weak PCs), run directly on **ONNX Runtime** | 512-d embeddings with state-of-the-art 1:1 verification. A CPU is enough because it runs every few seconds, not every frame. The `insightface` package is skipped (it needs a C++ compiler on Windows); faces are aligned from MediaPipe's landmarks instead |
| Liveness / anti-spoofing | **MiniFASNet** (Silent-Face-Anti-Spoofing, exported to ONNX) plus an active challenge ("blink twice" / "turn left") | The passive check catches photos and screen replays. The challenge reuses your own EAR and head-pose code |
| Desktop UI | **PySide6** (Qt, LGPL) | Tray app, camera preview for enrollment, the agent desktop (status, call panel, transcript) and a full-screen "are you awake?" alert with sound |
| Voice I/O | **sounddevice** + **Silero VAD** (ONNX) | Mic capture, playback and end-of-speech detection for the call loop. Silero runs on the ONNX Runtime already in the stack |
| Local storage | **SQLite** | Offline event queue that syncs when the PC is back online |
| Packaging | **PyInstaller** (one-folder) | Ships as a Windows .exe, with the `.task`/`.onnx` models bundled as data |
| Backend & auth | **Supabase** Auth + Edge Functions (Deno / TypeScript) | Email + password sign-in with no public sign-up. Edge Functions hold everything that needs a secret: face verification, template encryption and the Groq calls |
| Database | **Supabase Postgres** with Row Level Security (RLS) and `pg_cron` | Teams, profiles, templates, calls, sessions, events and per-minute metrics. RLS limits an agent to their own rows and a team leader to their team's rows |
| Real-time | **Supabase Realtime** (Postgres Changes) | Call offers to agents. Live status, queue and alerts to team leaders |
| Security | RLS, the **face-MFA gate** (`face_mfa_ok()`), **AES-256-GCM** (Web Crypto, in Edge Functions) for embeddings at rest, TLS in transit | Biometrics are sensitive data. Keys live in Edge Function secrets, never in the client |
| Client SDKs | `httpx` (a ~100-line client for Supabase Auth, the data API and functions), `supabase-js` (dashboard), `keyring` | The desktop client uses a small `httpx` wrapper instead of `supabase-py`: it does everything the client needs, is easy to test with a fake transport, and has no surprises. `keyring` keeps the workstation token in Windows Credential Manager |
| AI callers | **Groq API**: LLM `qwen/qwen3.8-27b` (fallback `openai/gpt-oss-120b`) and Whisper `whisper-large-v3` with a Taglish prompt (chosen in Spike S1, see `docs/spike-s1-results.md`) | Fast enough for a live conversation. Called only from Edge Functions, so the key never reaches a PC |
| Caller voice | A `Synthesizer` interface: Groq Orpheus for English, another provider for Tagalog/Taglish | Groq's TTS has no Tagalog. The provider is picked in Spike S1 |
| Fictional data | YAML scenarios + own Filipino name lists (Faker's PH locales return US names) | Customers and accounts for the simulated calls, with no real data |
| Team-leader dashboard | **React + TypeScript + Vite**, Tailwind, Recharts, TanStack Query, `supabase-js` | Call queue, live agent grid, transcripts, shift reports and simulation control. Styled loosely with the `bryl-minimal-design` skill (monochrome); the one exception is red for MICROSLEEP and IDENTITY_MISMATCH |
| Deploy | **Supabase Cloud** (the project "Thesis" runs in Tokyo, `ap-northeast-1`; Singapore was planned, but the region cannot be changed after creation and the few extra milliseconds do not matter here) or `supabase start` locally. Supabase CLI for migrations and functions. The dashboard is a static build | `supabase start` needs Docker Desktop. Any static host or `vite preview` serves the dashboard |
| Research / evaluation | Jupyter, pandas, scikit-learn (ROC/EER, F1), matplotlib, **pytest** | Threshold tuning and the thesis results chapter |

**Why a desktop client and not a browser app:** browsers throttle background tabs, and agents spend the day in other apps. A native app keeps 30 FPS and can read the OS input-idle time, which helps suppress false alarms. All video processing happens on the agent's PC: **no video or face image leaves it**. Only events, metrics and face embeddings do. Call audio goes to Groq through the Edge Functions.

## Architecture
```
AGENT PC (Python client)
  Sign-in: email + password -> face MFA (camera on once, then released)
  Agent desktop status: OFFLINE | NOT_READY | AVAILABLE | RINGING | ON_CALL | WRAP_UP | SYSTEM_HOLD
        |  camera ON only in AVAILABLE / RINGING / ON_CALL / WRAP_UP / SYSTEM_HOLD
        v
  Webcam -> capture thread -> MediaPipe Face Landmarker (up to 3 faces)
     -> Identity Lock: keep only the verified agent's face, ignore others
          (ArcFace re-verify every ~3 s, liveness, track continuity)
     -> EAR / MAR / head pose -> event state machines -> fatigue state
     -> local alert (PySide6) + SQLite event queue + heartbeat every ~5 s
  Mic -> Silero VAD -> call voice loop -> speaker
                 |  HTTPS + Realtime (events, metrics, status, embeddings, call audio; no video)
SUPABASE
  Auth (email + password) -- Postgres + RLS -- Realtime --> team-leader dashboard (React)
  Edge Functions: face-challenge, verify-face, enroll-face, pair-workstation, manage-team,
                  caller-turn / transcribe / speak --> Groq API (LLM, Whisper) + TTS provider,
                  SQL: start_run / stop_run, simulation tick (pg_cron), dispatch
  SQL dispatch: queued AI calls -> longest-idle AVAILABLE, verified agent
```

## Detection logic
Every threshold is per-agent (taken from calibration), and timing is measured in ms, not frame counts.

| Signal | How it's computed | Event rule (configurable defaults) |
|---|---|---|
| EAR | Right eye `[33,160,158,133,153,144]`, left eye `[362,385,387,263,373,380]` (p1…p6). `EAR = (‖p2−p6‖+‖p3−p5‖) / (2‖p1−p4‖)`, smoothed with a 3-frame median | Eye counts as closed when EAR < `closed + 0.5·(open−closed)`, with hysteresis on reopening |
| Blink frequency | A closure of 50–500 ms is a blink | Blinks/min over a sliding 60 s window, compared with the agent's baseline. Blinks of 300–500 ms count as "long blinks", a drowsiness cue |
| Microsleep | Continuous eye closure | ≥ 500 ms triggers a **critical alert** (tune between 0.5 and 1 s) |
| PERCLOS (P80) | % of time EAR ≤ `closed + 0.2·(open−closed)` | Over 60 s; above ~15 % moves the agent to DROWSY |
| Yawn | `MAR = ‖lm13−lm14‖ / ‖lm78−lm308‖`, cross-checked with the `jawOpen` blendshape | Above the agent's threshold for ≥ 2 s. Mouth openings during speech are shorter, which matters for call agents |
| Head tilt / nod | Euler angles from the MediaPipe transform matrix (fallback: `cv2.solvePnP` + `RQDecomp3x3`), relative to the calibrated neutral pose | Tilt: \|roll\| > ~15° for ≥ 2 s. Nod: pitch drops > ~15° and snaps back |

**False-alarm guards:**
- Looking down at the keyboard: high `eyeLookDown*` with pitch down, or keyboard/mouse activity in the last ~2 s from Windows `GetLastInputInfo`. That reads idle time only; it doesn't log keys.
- Head turned away (\|yaw\| > ~30°): use only the visible eye, or pause.

**Fatigue states:** **ALERT → DROWSY** (PERCLOS, long blinks, yawns and nods over a 5–10 min window) **→ MICROSLEEP**.
- The agent gets an alert they must acknowledge.
- The team leader is notified when it repeats (for example, ≥ 2 microsleeps in 10 min).
- Start rule-based. An optional ML classifier on windowed features is a stretch goal.

## Per-agent biometric profile and identity-lock fallback

**Enrollment** (once per agent, with consent, using a single-use code from their team leader):
1. The agent signs in, gives consent and enters the enrollment code. Then the client captures ~20 frames across 5 head poses (with and without glasses). It runs a quality check (face size, blur, brightness), computes ArcFace embeddings, and keeps 5 of them plus a mean template.
2. Calibrate:
   - 10 s with eyes open, looking at the screen: `ear_open`, `mar_closed` and neutral pitch/yaw/roll.
   - 3 s with eyes closed: `ear_closed`.
   - Optional: looking at the keyboard, and an open mouth.
3. `enroll-face` saves the encrypted template and the calibration, tagged with the model name and version (embeddings from different models can't be compared).

**Sign-in:** email + password, then face MFA (see "Authentication and roles"). `verify-face` matches the face on the server (cosine ≥ τ; start at ~0.4–0.5 and tune with an ROC on your enrollment data), and the client passes MiniFASNet plus a random challenge. The calibration profile loads into memory only. Everything is tied to the account, not the PC, so hot-desking still works.

**Going Available:** the camera opens and a short local re-check runs (liveness + match against the in-memory sign-in reference). Only then does the agent become `AVAILABLE`. If the re-check fails, the full face MFA runs again.

**During the session (the fallback).** This runs only while the camera is on (`AVAILABLE`, `RINGING`, `ON_CALL`, `WRAP_UP`, `SYSTEM_HOLD`):
- Only the face track bound to the verified agent is measured. Any other face is ignored, and no metrics are computed for it.
- Re-verify every ~3 s, and immediately when the track is lost, the face count changes or the face box jumps.
- No verified face for > 10 s → `ABSENT`. Metrics pause, the time isn't counted as sleep, and the agent goes to `SYSTEM_HOLD`.
- A non-matching face in the agent's seat for > 5 s → `IDENTITY_MISMATCH`, a team-leader alert, a revoked face verification and a forced sign-out.
- Anti-spoofing and anti-face-swap:
  - Run passive liveness periodically.
  - No spontaneous blink for a long time (for example, 90 s) → `SPOOF_SUSPECTED`.
  - Block virtual cameras by device name (OBS Virtual Camera, ManyCam, DroidCam and similar; real-time face-swap tools like DeepFaceLive inject video this way), and lock the client to the physical webcam chosen at setup. Names come from `cv2-enumerate-cameras`, read from the same Media Foundation backend the client opens, because camera indexes differ between backends.

## Authentication and roles (Supabase)

Supabase Auth handles email + password, and public sign-up is off. The Groq key, the template encryption key and the service key live only in Edge Function secrets. The .exe and the dashboard ship only the publishable key.

| | Agent | Team leader (supervisor) |
|---|---|---|
| Signs in on | Desktop client | Web dashboard |
| Factors | Email + password + face | Email + password |
| Sees | Own calls, transcripts, events and status | Their team: queue, live status, calls and transcripts, events, alerts, reports |
| Can also | Take calls, acknowledge their own alerts | Create, deactivate and reset agent accounts; issue enrollment and pairing codes; start and stop simulation runs; delete an agent's biometric data |

- Each agent belongs to one team (`profiles.team_id`) and each team has one leader (`teams.leader_id`). That link is the only path a team leader has to an agent's data.
- The project owner creates teams and leader accounts once with `scripts/create_team.py` (secret key from a local `.env`, never shipped). That is a setup step, not an app role.

**Agent sign-in (every shift)**
1. Email + password through Supabase Auth. On the first sign-in the agent replaces the temporary password and, if no face is enrolled, enrollment runs.
2. The client calls `face-challenge` and gets a one-time nonce plus random actions ("turn left, blink twice"), valid for 60 s. It only answers PCs with a valid workstation token.
3. On the PC the client checks the webcam is the paired physical one, runs MiniFASNet and the challenge, and computes ArcFace embeddings from ~5 good frames. Only embeddings leave the PC.
4. `verify-face` checks the nonce, session and workstation, decrypts the template (AES-256-GCM) and takes the best cosine score. If it's ≥ τ and liveness passed, it writes a `face_verifications` row bound to the JWT `session_id`, valid for 12 h.
5. RLS (`private.face_mfa_ok()`) lets the agent write sessions, events, metrics, call turns and status only while that row is valid. The embeddings that just passed become the identity lock's in-memory reference, so the stored template never leaves Supabase.
6. 5 failed checks in 15 min lock face sign-in for 15 min and alert the team leader. An `IDENTITY_MISMATCH` or `SPOOF_SUSPECTED` event revokes the verification and signs the client out, so a modified client can't keep writing. The app notices (a latched lock status, or a request the server refuses with 401/403), closes the camera and returns to sign-in with a message; events not yet uploaded stay in the offline queue and go out after the next sign-in.

**One-time codes** (single-use, valid 15 min, stored hashed, issued by the team leader):
- **Enrollment code:** the agent gives consent, enters it and captures; `enroll-face` saves the encrypted template and calibration. A stolen agent password alone can't enroll someone else's face.
- **Pairing code:** the client's first-run wizard trades it for a workstation token (kept in Windows Credential Manager via `keyring`) and locks to the chosen physical webcam.

**Accounts:** team leaders create agent accounts with a temporary password through `manage-team`. Emails are only used as usernames, so no mail server is needed. Every account action goes to `audit_log`.

**Integrity:**
- A trigger fills `agent_id` and `team_id` on inserted rows, so the client never supplies them.
- No client can read `face_templates` or change `profiles.role` or `team_id`.

## Simulated BPO (AI callers via Groq)

**Idea:** a team leader starts a *simulation run*. AI callers arrive in a queue, get routed to available agents, and talk to them by voice. The monitor watches the agents while they work. This gives the fatigue data a realistic workload, and lets you analyse fatigue by call type, language and time on shift.

**Inquiry catalog** (one YAML per scenario in `simulation/scenarios/`, all customers fictional): billing dispute, internet/tech support, account access and ID verification, order tracking and refund, plan upgrade/downgrade, complaint/escalation, card or e-wallet fraud report. Each has a goal, facts the caller knows, resolution steps and a difficulty. Moods: calm, confused, in a hurry, frustrated (kept at realistic difficulty, no slurs).

**Languages:** English, Tagalog, Taglish. Default mix 40 % English / 40 % Taglish / 20 % Tagalog, configurable per run.

**Call lifecycle:** `QUEUED → OFFERED → ACTIVE → WRAP_UP → COMPLETED`, or `ABANDONED` (waited too long), `CALLER_HANGUP`, `MISSED` (the agent didn't accept in 20 s, so it's re-offered).

**Agent statuses and the camera**

| Status | Camera / face detection | Meaning |
|---|---|---|
| `OFFLINE` | off | Signed out |
| `NOT_READY` (reason: signed-in, break, lunch, back-office work) | **off** | Chosen by the agent. Not taking calls |
| `AVAILABLE` | **ON** | Ready for calls |
| `RINGING` | **ON** | A call is being offered |
| `ON_CALL` | **ON** | In a call |
| `WRAP_UP` | **ON** | Disposition and notes after a call |
| `SYSTEM_HOLD` | **ON** | Set by the system after ABSENT > 10 s, `IDENTITY_MISMATCH`, `MICROSLEEP` or 2 missed offers. The agent must acknowledge to return |

- Face MFA at sign-in uses the camera once, then releases it.
- **Going Available** opens the camera. The pipeline loads the calibration profile and does the short local re-check described above. Only after that does the status become `AVAILABLE`.
- **Break / lunch / sign-out** closes the camera. Those minutes are not monitored and not counted as sleep.
- The tray shows a visible "Camera monitoring: ON/OFF" indicator.
- A microsleep or absence during `ON_CALL` doesn't drop the caller. The call continues, and the agent goes to `SYSTEM_HOLD` when it ends.

**Dispatch (in SQL, not the client):** `private.dispatch_calls()` picks the longest-idle agent in the team that is `AVAILABLE`, has `monitor_state = LIVE`, `identity_ok`, a heartbeat newer than 15 s and a valid face verification, using `FOR UPDATE SKIP LOCKED`. It runs on call insert, status change and agent heartbeat. `pg_cron` sweeps expired offers and abandoned calls. The client calls RPCs only: `set_status`, `heartbeat`, `accept_call`, `decline_call`, `end_call`. The state machine is enforced in SQL.

**Voice call loop (client, half-duplex):**
1. The call is accepted. `caller-turn` returns the caller's opening line, and the client speaks it with the local `Synthesizer` (`edge-tts` voices, no key needed).
2. The mic is gated while the caller speaks (headset recommended). A simple energy-based voice detector (adaptive noise floor; Silero VAD stays the upgrade if background noise breaks it) cuts the agent's utterance after ~500 ms of silence.
3. `transcribe` (Groq Whisper) returns text. `caller-turn` (Groq LLM) returns `{text, state}` with state ∈ continuing / satisfied / escalating / hangup, and the client voices it sentence by sentence.
4. The loop repeats until the caller ends the call or the agent ends it. After ~8 s of silence the caller says "hello? andyan pa po ba kayo?", which tests dead air.
5. Target turn latency (end of agent speech → start of caller audio): ≤ 3 s. Each turn logs `stt_ms / llm_ms / tts_ms` and the time spent inside each function. **Measured 2026-11-04: 3.1–3.3 s per turn** (plus the 0.5 s end-of-speech pause), down from 4.8–8.9 s. Still a little short of the target; the voice's first sentence (0.7–1.2 s) and the function lookups (0.3–0.6 s) are what is left.

**Groq and voice usage (Spike S1 done 2026-11-03; details in `docs/spike-s1-results.md`)**
- **Callers (LLM):** `qwen/qwen3.8-27b`, fallback `openai/gpt-oss-120b`. The Llama models in Groq's documentation are not available to this account: the live `GET /models` list is the source of truth, and the call code checks it.
- **Agent speech (STT):** `whisper-large-v3` with automatic language detection and a prompt written in natural Taglish agent speech (`STT_PROMPT`). Measured on 24 recordings: English 3 % word error, Taglish 10 % (13 % with the first prompt), Tagalog 24 % (character error only 6 to 8 %, mostly spelling), about 0.35 s of Whisper time per clip. A Tagalog language hint was tested and rejected: it raised the English error from 3 % to 16 %, and `whisper-large-v3-turbo` saves only ~0.1 s. **Known limit:** Tagalog recognition is weak, and very short or fast phrases are sometimes badly misheard in live calls (the caller then reacts to the wrong words). Azure AI Speech (fil-PH, free tier of 5 audio hours a month) is the candidate if the evaluation needs better; it has not been tried.
- **Caller voice (TTS):** Groq's voices have no Tagalog and Windows has no Filipino voice, so the client uses Microsoft's neural voices through `edge-tts` (`fil-PH-BlessicaNeural` for Tagalog and Taglish, Philippine English for English) behind a `Synthesizer` interface. `edge-tts` is an unofficial client of Microsoft's online read-aloud service, acceptable for a thesis prototype if disclosed; Azure AI Speech has the same voices officially. TTS runs on the agent's PC (no key needed), so there is no `speak` function.
- **Groq calls go through Edge Functions** (`caller-turn`, `transcribe`) so the key never reaches a PC. They check the JWT, that the call belongs to the caller, a valid face check and a rate limit, and they write `call_turns` with the secret key, so a client cannot forge transcripts.
- **Latency work (2026-11-04):** both functions make **one** database call per turn (`public.call_turn_context`, callable only with the secret key) that does the agent, face-check, call and rate-limit checks and returns the transcript, scenario and customer. The token is confirmed with Supabase Auth first, so a forged token cannot use up someone else's rate limit. Each reply carries `server_ms`/`prep_ms` timings, the desk sends a warm-up request when it opens, when the agent goes Available and when a call rings, and a long first sentence is cut at its first comma so the voice starts sooner.
- **Caller prompts** are in `simulation/prompts/` (a base prompt plus one style file per language) and the scenarios in `simulation/scenarios/`. Mood is weak in the first version ("frustrated" callers sounded polite), so the mood instructions get strengthened. The model sometimes drops a space ("italready"); that cannot be fixed by rule without a dictionary, so it is watched rather than patched.

**Simulation control:** the SQL functions `start_run` and `stop_run` (team leader only) manage a run with a config (arrival rate per minute, max concurrent calls, language / difficulty / scenario mix, **random seed**), stored in `simulation_runs`. A `pg_cron` job calls the SQL function `sim_tick`, which enqueues calls. Every random choice is drawn from the seed (a hash of seed and counter), so a run is repeatable. `pg_cron` granularity gets checked at build time, with agent heartbeats as the dispatch clock as the fallback. No Edge Function is needed for this.

**Customer lookup:** the agent desktop has a small panel to look up the fictional customer by account number (`customers` table), add notes and choose a disposition (resolved / unresolved / escalated). It gives the agent realistic screen-and-typing work.

**Stretch:**
- Fatigue-aware routing (hard calls skip DROWSY agents), compared with plain longest-idle routing as an A/B for the thesis.
- Headless bot agents (`simulation/agent_bots/`) for dispatch and load tests, against the local stack only.

## Data model (Supabase Postgres)
Row access: an agent reads and writes only their own rows, and a team leader reads their team's rows through `teams.leader_id`. Agent writes also need `face_mfa_ok()`.

**Identity**
- `teams(id, name, leader_id)`
- `profiles(id = auth.users.id, role ['agent'|'team_leader'], team_id, full_name, consent_at, must_change_password, active)`
- `workstations(id, name, token_hash, paired_by, webcam_id, last_seen_at)`
- `setup_codes(id, kind ['enrollment'|'pairing'], agent_id, code_hash, expires_at, used_at, created_by)`

**Biometrics**
- `face_templates(agent_id, embeddings_enc, model_version, enrolled_at)`: no client access; only Edge Functions read it
- `calibration_profiles(agent_id, ear_open, ear_closed, mar_closed, neutral_pitch, neutral_yaw, neutral_roll, updated_at)`
- `face_challenges(id, agent_id, workstation_id, nonce_hash, actions, expires_at, used_at)`
- `face_verifications(id, agent_id, auth_session_id, workstation_id, score, liveness_ok, verified_at, expires_at, revoked_at)`

**Simulated BPO**
- `scenarios(id, category, title, brief, difficulty, languages, facts, resolution_steps)`
- `customers(id, account_no, full_name, plan, balance, status)`: fictional
- `simulation_runs(id, team_id, started_by, seed, config, started_at, ended_at)`
- `calls(id, run_id, team_id, scenario_id, customer_id, language, mood, status, agent_id, queued_at, offered_at, answered_at, ended_at, wrap_up_ended_at, offer_attempts, disposition, end_reason)`
- `call_turns(id, call_id, seq, speaker, text, lang, started_at, ended_at, stt_ms, llm_ms, tts_ms)`: transcripts only; the audio is not stored

**Presence**
- `agent_status(agent_id, team_id, status, reason, status_since, monitor_state, identity_ok, fatigue_state, last_heartbeat_at, current_call_id)`
- `agent_status_log(id, agent_id, status, reason, from_ts, to_ts)`

**Monitoring**
- `sessions(id, agent_id, workstation_id, auth_session_id, started_at, ended_at)`
- `events(id, session_id, call_id [nullable], type, ts, duration_ms, value, severity)`
  - Event types: MICROSLEEP, LONG_BLINK, YAWN, HEAD_NOD, HEAD_TILT, DROWSY, ABSENT, IDENTITY_MISMATCH, MULTIPLE_FACES, SPOOF_SUSPECTED
- `metrics_minute(session_id, minute, status, blink_rate, perclos, mean_ear, yawns, nods)`
- `alerts(id, team_id, agent_id, event_id, kind, created_at, acknowledged_by, acknowledged_at)`
- `audit_log(id, actor_id, action, target_id, ts, detail)`

## Directory structure
The folders exist. Files are added milestone by milestone: milestones 1–3 added `client/vision`, `client/capture`, `client/identity`, `client/tools`, `client/tests`, `scripts/download_models.py` and `research/replay/replay.py`, milestone 4 added `client/auth`, `supabase/` and the owner scripts, and milestone 5 added `client/bpo`, `simulation/` and the call functions. The rest of the tree is still the plan.

```
README.md                      how to set up and run each part
plan.md, walkthrough.md        planning docs
.gitignore                     models, datasets, .env files, .venv, node_modules
docs/
  consent-form.md              research-participation consent (RA 10173): fatigue recording, face matching, fake-face tests, practice calls (DRAFT for ethics review)
  privacy-notes.md             what is stored, where, who sees it, retention (the system and the evaluation)
  evaluation-protocol.md       how to collect the evaluation data, with the exact commands and sample sizes
  demo-script.md               the ten-minute demo: simulation, face check, call, microsleep, dashboard, fake photo
scripts/
  create_team.py               owner-only: create a team + its team-leader account
  leader_cli.py                team-leader actions from the terminal until the dashboard exists (accounts, codes, resets, start-run / stop-run / status / calls / transcript)
  smoke_m4.py                  end-to-end test of milestone 4 against the hosted project (creates and deletes its own data)
  run_pgtap.py                 runs supabase/tests on the hosted project without Docker, reporting every assertion
  _supa.py                     helpers for the owner scripts (owner.env, secret-key client)
  smoke_m6.py                  end-to-end test of the offline queue, uploader and monitor rules against the hosted project
  dashboard_demo.py            a throwaway demo team with a day of realistic data, for trying and testing the dashboard (cleans up after itself)
  download_models.py           fetch face_landmarker.task, ArcFace, MiniFASNet, Silero VAD .onnx
client/                        Python desktop app (agents only)
  app.py                       entry point: pairing, sign-in, face check, then the desk; crash log on failure
  controller.py                connects the desk window to the status RPCs, camera, queue, alert and voice call; carries the camera rules
  config.py                    Supabase URL + publishable key, thresholds (from .env)
  requirements.txt, pytest.ini, .env.example
  auth/
    supabase_client.py         supabase-py client, session kept in memory only
    login.py                   email + password, first-login password change, sign-out
    face_mfa.py                face-challenge -> liveness + challenge -> verify-face
    workstation.py             pairing code -> workstation token (Windows Credential Manager)
  bpo/                         the simulated call-center desktop
    status.py                  AgentDesk (status, heartbeat, call RPCs, warm-up) + the heartbeat thread; offers arrive in the heartbeat answer (Realtime comes with the dashboard)
    shift.py                   this shift in numbers (calls handled, talk time, resolved share) from the agent's own calls
    call_session.py            per-call voice loop: opening, listen, transcribe, caller turn, speak; dead-air prompts; latency log
    customer_panel.py          customer lookup text + disposition parsing (the PySide6 panel comes in milestone 6)
    audio/
      io.py                    sounddevice microphone + speaker, half-duplex gate
      vad.py                   energy-based utterance detector (Silero VAD is the upgrade)
      stt.py                   -> transcribe function
      tts.py                   Synthesizer interface + edge-tts voices, sentence-by-sentence speaking
  monitor/
    core.py                    frames in, events and per-minute metrics out (to the queue); the live-action challenge; heartbeat state
    runner.py                  camera thread + MonitorRunner + CameraGate (camera only while Available / ringing / on a call / wrap-up / on hold)
    reference.py               the sign-in embeddings the identity lock compares against (memory only)
    trace.py                   local log of lock status changes and low liveness scores (no images, no embeddings)
  capture/
    camera.py                  OpenCV capture thread (Media Foundation); picks the physical webcam by name
    camera_guard.py            virtual-camera blocklist + find_physical() (cv2-enumerate-cameras)
  vision/
    landmarks.py               MediaPipe Face Landmarker (num_faces=3)
    metrics.py                 EAR, MAR, head pose, median filter, looking-down guard
    calibration.py             Calibration thresholds + compute_calibration (used by identity/enroll.py)
    drowsiness.py              event state machines, fatigue state
    pipeline.py                frame -> measurements -> engine; shared by overlay, replay and the app
    input_idle.py              GetLastInputInfo false-alarm guard
  tools/
    overlay.py                 dev tool: live webcam overlay + calibration keys (python -m tools.overlay)
    identity_demo.py           dev tool: enroll / login (liveness challenge + match) / watch (identity lock)
    signin_demo.py             dev tool: pair, email + password, face check
    call_demo.py               terminal agent desk: sign in, go Available, take spoken calls (milestone 5)
    ping.py                    plays the alert ping once, to check it can be heard
  identity/
    embedder.py                FaceEmbedder interface; ArcFaceOnnx + landmark-based face alignment
    profile.py                 EnrolledProfile: 5 embeddings + mean + model version + calibration (local file for dev)
    enroll.py                  multi-pose capture, quality checks, 5 diverse embeddings, one-person check
    liveness.py                MiniFASNet passive check + active challenge (blink twice / turn left or right)
    verify.py                  cosine match: median of the best 3 frames against the profile
    lock.py                    identity lock: ABSENT, IDENTITY_MISMATCH, SPOOF_SUSPECTED, MULTIPLE_FACES
  ui/
    theme.py                   the dashboard's design tokens as a Qt style sheet: light / dark / system (remembered), Geist fonts, one red for MICROSLEEP / IDENTITY_MISMATCH
    widgets.py                 micro labels, chips, hairlines, stat grid, meters, theme switch, halftone accent
    window.py                  one window policy for every screen: normal border, resizable (min 1180 x 680), opens maximized, F11 full screen
    sound.py                   the alert ping: its own two-tone WAV played through the Windows mixer (the Qt/Windows default beep can be silent)
    tray.py                    status menu + camera ON/OFF indicator
    login_window.py            pairing code (first run), email + password, new password
    face_window.py             camera preview + prompts for the sign-in face check (enrollment capture still uses the OpenCV window of tools/identity_demo)
    desktop_window.py          agent desktop: status, calls, transcript, customer lookup, wrap-up, hold, quick-check banner, a live camera panel (mirrored, local only) with the monitor's numbers, this shift's stats and the latest events
    alert_window.py            full-screen "are you awake?" + repeating sound
  storage/queue.py             SQLite offline queue (sessions, events, per-minute metrics), kept in order
  sync/uploader.py             queue -> Supabase, oldest first, retry with backoff; resends are safe; a refused row cannot block the rest
  assets/fonts/                Geist, Geist Mono, Geist Pixel as TTF (Qt cannot read the dashboard's web fonts), with their licence
  models/                      .task / .onnx files (git-ignored)
  packaging/client.spec        PyInstaller one-folder build (built outside OneDrive; about 590 MB; put .env next to the exe)
  tests/                       pytest: metrics, state machines, identity lock, status gate, auth (Supabase mocked)
supabase/                      Supabase CLI project (supabase init)
  config.toml                  local stack: sign-up off, password rules
  seed.sql                     demo team, leader, agents, fictional customers (local only)
  migrations/                  timestamped, made with `supabase migration new`
    ..._roles_teams.sql          app_role enum, teams, profiles, private.* RLS helpers
    ..._workstations_codes.sql   workstations, setup_codes
    ..._biometrics.sql           face_templates (no client access), calibration_profiles
    ..._face_mfa.sql             face_challenges, face_verifications, face_mfa_ok()
    ..._bpo_core.sql             scenarios, customers, simulation_runs, calls, call_turns
    ..._agent_status_dispatch.sql  agent_status, agent_status_log, RPCs, dispatch_calls()
    ..._monitoring.sql           sessions, events, metrics_minute, alerts, audit_log + RLS
    ..._sessions_client_id.sql   lets a client pick its own session id (offline queue, safe retries)
    ..._lock_down_function_grants.sql  no client may run system functions; new functions are not executable by default
    ..._pin_helper_search_path.sql     fixed search_path on two helpers
    ..._call_turn_context.sql    one database call per spoken turn (checks + transcript + scenario + customer)
    ..._events_dedupe.sql        unique (session, type, time) so the offline queue can resend safely
    ..._realtime_more_tables.sql publishes call_turns and simulation_runs for the dashboard's live updates
    (agent/team fill, escalation and MFA revocation triggers, and the Realtime publication, are in ..._monitoring.sql; the cron jobs are in ..._agent_status_dispatch.sql)
  functions/                   Edge Functions (Deno / TypeScript)
    _shared/                   http, crypto (AES-GCM, hashes, codes), face maths, decide (verification + lockout rules), config, clients, auth, codes, audit, groq (callers + Whisper), calls (turn rules, one-call context), prompt (+ generated prompts)
    face-challenge/  verify-face/  enroll-face/  pair-workstation/  manage-team/
    caller-turn/               Groq LLM: next caller line + call state
    transcribe/                Groq Whisper: agent audio -> text
    tests/                     deno test for the functions above
  tests/                       pgTAP: RLS, dispatch and status machine (bpo), function grants, call_turn_context
simulation/                    BPO content and test tools (no runtime secrets)
  scenarios/                   one YAML per inquiry type
  prompts/                     caller prompts: base.md, en.md, tl.md, taglish.md, moods.yaml (+ golden file the TypeScript copy is tested against)
  callers.py, build_prompts.py shared prompt builder; generates supabase/functions/_shared/prompts.generated.ts
  seed_customers.py            40 fictional Filipino customers (own name lists; Faker's PH locales return US names) -> customers table
  load_scenarios.py            YAML -> scenarios table
  agent_bots/                  headless bot agents for dispatch/load tests (local stack only)
  spikes/                      S1: Whisper WER (tl / taglish), TTS comparison, latency
dashboard/                     React 19 + TypeScript + Vite + Tailwind 4 (team leaders only)
  package.json, vite.config.ts, tsconfig.json, .env.example
  e2e/run.mjs                  browser test against the hosted project (Edge, headless): sign-in, every page, actions, screenshots
  src/
    index.css                  design tokens (bryl-minimal: monochrome ramp, dark mode by token swap, one red), fonts, halftone, motion
    assets/fonts/              Geist, Geist Mono, Geist Pixel (bundled)
    lib/supabase.ts            supabase-js client (publishable key only)
    lib/types.ts               row types for what a leader can read
    lib/queries.ts             TanStack Query hooks, one Realtime channel per table, the leader actions (manage-team, start_run, stop_run, ack_alert)
    lib/stats.ts               service level, abandonment, fatigue per call kind, per-agent summaries (pure, tested)
    lib/run.ts                 the simulation form's rules (same as start_run in the database)
    lib/format.ts, lib/theme.ts
    auth/                      AuthProvider, LoginPage, ChangePasswordPage, RequireTeamLeader (role gate), password rules
    components/                Shell (numbered nav, theme switch), ui (chips, stat grid, segmented), charts (Recharts, monochrome), Confirm (modal), ErrorBoundary
    features/live/             team now: counts, agent cards (status, fatigue, camera, hold reason), open alerts
    features/queue/            waiting callers with a ticking clock, service level, abandonment, calls per hour
    features/calls/            call log with filters; call page: summary, transcript with per-turn timings, fatigue events during the call
    features/simulation/       start (rate, concurrency, patience, language mix, seed) / stop, earlier runs with "repeat"
    features/alerts/           open / all alerts, acknowledge
    features/agents/           list; one page per agent: eye closure and blink charts, time per status, events, face checks, shifts
    features/reports/          range and agent filters, events by hour, fatigue by language / problem / mood, per-agent table, CSV export
    features/team/             accounts (create, reset password, deactivate, enrollment code, delete face data), pairing code, paired PCs, audit trail
research/
  notebooks/                   threshold tuning, ROC/EER, fatigue by call type / language
  replay/replay.py             replay harness: recorded and public videos in place of the webcam
  eval/                        the evaluation toolkit (tested: 54 tests)
    evalkit/                   metrics (event matching, precision/recall/F1, FAR/FRR/EER, APCER/BPCER, Wilson and bootstrap intervals), protocol (scripted cue schedule), fatigue replay and scoring, identity and spoof trials, latency, bench
    record_session.py          webcam recording with on-screen cues and a consent screen (ground truth = the schedule)
    evaluate_fatigue.py        replay recordings through the real pipeline, score them, threshold sweeps
    identity_trials.py, analyze_identity.py   enrollment and verification attempts (embeddings only), FAR / FRR / EER
    spoof_trials.py, analyze_spoof.py          attack trials against the real sign-in check, APCER / BPCER under five acceptance rules
    latency_report.py          voice-loop delay from the server's call transcripts
    bench_pipeline.py          frame rate, CPU and memory on the agent PC
    make_report.py             gathers all results into results/REPORT.md
    results/                   tables and charts (no faces)
  datasets/                    git-ignored
```

- Put face recognition behind a small `FaceEmbedder` interface. That way OpenCV YuNet + SFace (Apache-2.0, already part of `opencv-python`) can replace InsightFace without other changes. Do the same for caller voices with `Synthesizer`.
- The repo lives at `C:\Users\Mara\Thesis`, outside OneDrive. `.venv`, `node_modules` and `research/datasets/` are kept out of it too.

## Milestones
1. Live webcam overlay: landmarks, EAR, MAR and head angles. **Built; the live webcam check is still to do.**
2. Event state machines (blinks/min, long blinks, microsleep, PERCLOS, yawns, nods/tilts) plus calibration. **Built and unit-tested (68 tests).**
3. Local identity: enrollment capture, liveness and identity lock. **Built and unit-tested (168 tests in total). The live checks on a real face, and the attack tests, are still to do.**
4. Supabase backbone: schema, RLS and pgTAP tests, the auth and face-MFA Edge Functions, client sign-in and workstation pairing. **Deployed to the hosted project and verified: 68 of 68 access-rule tests, 62 of 62 end-to-end checks, 210 Python tests, 24 Deno tests. First real sign-in on the webcam succeeded (2026-11-03): pairing, password change, enrollment on the server, then the face check with match score 0.85 and liveness ok.**
5. Simulated BPO: Spike S1, 7 scenarios and 40 customers, dispatch and status RPCs, simulation runs, `caller-turn` / `transcribe`, and the voice call loop. **Done and deployed (2026-11-04): 240 pgTAP assertions (137 bpo, 14 grants, 68 rls, 21 call_turn_context), 44 Deno tests, 250 Python tests. Tried live by voice in Taglish and English: complete calls from ringing to wrap-up, 3.1–3.3 s per turn. The agent side is a terminal tool (`python -m tools.call_demo`); the PySide6 agent desktop comes with milestone 6. To do later: another voice test (more people, more languages, a headset vs. speakers, the mood strength) and the Tagalog speech-to-text question above.**
6. Camera gating from agent status, tray app and agent desktop, alerts, SQLite queue and the PyInstaller build. **Done (2026-11-04): 318 Python tests, 240 pgTAP assertions, 44 Deno tests, and `scripts/smoke_m6.py` 20 of 20 against the hosted project. Tried live on the webcam: sign-in with the face check, Go available (camera opens after a re-check), a spoken call with wrap-up, the microsleep alert and hold, and a break (camera off). Not tried live yet: working offline and catching up, the packaged exe (built, not launched), the "quick check" banner, a stranger in the seat and a photo or video inside the app. More live testing is still to do.**
7. Team-leader dashboard: queue, live agent grid, calls and transcripts, simulation control, alerts, per-agent pages, reports, team management. **Built (2026-11-04): 36 unit tests (service level and abandonment maths, simulation form rules, the one-red rule, the role gate, dialogs) and 45 of 45 browser checks against the hosted project (sign-in with a temporary password and the forced change, every page, charts, alert acknowledgement, a change made elsewhere appearing live, account creation and deactivation, starting and stopping a run, dark mode, phone width, sign-out). Tried by you on localhost: fine. The Realtime migration for call_turns and simulation_runs is applied and checked (a new transcript line appears without a reload).**
8. Evaluation (below) and threshold tuning. **Toolkit built and tested (2026-11-04): 54 tests, including the analysis scripts run end to end on synthetic trial files. Measured already: voice-loop delay from the real calls (14 exchanges: mean 2.87 s, median 2.39 s server-side) and pipeline performance on this PC (43-44 frames per second with the identity lock, about 2 cores of CPU, 500 MB: above the 15 fps target). The fatigue, face-matching and fake-face experiments need volunteers and recordings (see `docs/evaluation-protocol.md`); results then land in `research/eval/results/REPORT.md`.**

## Verification
- **Unit tests:** pytest on EAR, MAR and Euler angles using fixed landmark fixtures. Feed the state machines synthetic EAR series (for example, a 600 ms closure must produce exactly one MICROSLEEP).
- **Replay harness:** run the pipeline on recorded and public videos and compare its events with the ground truth: Eyeblink8 (blinks), YawDD (yawns), NTHU-DDD and UTA-RLDD (drowsiness). Report precision, recall, F1 and alert latency.
- **Identity:** enroll volunteers, then test someone else sitting in, a printed photo, a phone-screen replay and a virtual camera.
  - Report FAR, FRR and EER for recognition.
  - Report APCER and BPCER (ISO/IEC 30107-3) for liveness.
  - Cover different eye shapes, glasses and lighting conditions.
- **Auth and access:**
  - pgTAP tests for RLS: an agent reads only their own rows, a team B leader sees nothing of team A, and no client reads `face_templates` or edits `role` / `team_id`.
  - Deno tests for `verify-face` (reused or expired nonce, unpaired PC, low score, lockout), with the Groq-proxy functions mocked.
  - The face-MFA attacks (wrong face, photo, phone replay) must fail before any write is accepted.
- **Simulated BPO:**
  - Dispatch tests: an `OFFLINE`, `NOT_READY`, stale-heartbeat, `SYSTEM_HOLD` or unverified agent never gets a call.
  - The camera is off in `NOT_READY` and on in `AVAILABLE` (tested through the status gate with a fake camera).
  - A seeded run produces the same call mix every time.
  - Voice-loop report: `stt_ms / llm_ms / tts_ms` per turn, with the end-of-speech to caller-audio target of ≤ 3 s.
  - Spike S1 results: Whisper word error rate for English, Tagalog and Taglish, and the chosen TTS provider.
- **Performance:** FPS and CPU % on the target agent PC with the voice loop running (at least 15 FPS, aiming for 30).
- **End-to-end demo:**
  - The team leader starts a seeded run. A Taglish call is offered to an Available agent, who answers by voice.
  - Close your eyes for ~1 s: a local alert appears, and the event shows on the dashboard within ~2 s.
  - Stay silent for ~8 s: the caller says "hello?".
  - A second person walks behind you: they are ignored.
  - A second person takes the seat: `IDENTITY_MISMATCH`, the verification is revoked, the client signs out and the team leader is alerted.
  - The agent goes on break: the camera is off and nothing is monitored.

## Risks / notes
- InsightFace's pretrained models are licensed for **non-commercial research only**. That's fine for a thesis; use SFace for anything commercial.
- On Windows, `pip install insightface` needs the Microsoft C++ Build Tools. The client avoids it: it runs the ArcFace ONNX file with `onnxruntime` and aligns faces from MediaPipe's landmarks.
- **Passive liveness is unproven.** MiniFASNet called 96 % of the faces in a flat digital photo "real". That is not a fair attack test (the photo never went through a camera, so there was no glare or screen pattern), but it shows the model alone cannot be trusted. The active challenge carries the weight until the evaluation measures APCER/BPCER with real recaptures (phone screen, printed photo).
  - First live test (2026-11-03): at sign-in, the challenge denied both a phone photo and a phone video of the agent. In `watch` (no challenge), a phone video of the agent's face was accepted by the lock (cosine 0.81) and flagged `SPOOF_SUSPECTED` only about 50 s later, by the old "3 failed checks in a row, 5 s apart" rule.
  - Measured on the first webcam: the real face scored 0.98–1.00 and a phone picture of the same face 0.01, so the 0.5 threshold has a wide margin. That is one person, one phone and one room; the evaluation must repeat it with more people, screens and lighting.
  - Change: the lock runs the passive check every 2 s and flags 3 failures among the last 5. It also has a liveness gate: a face that matches the agent is only accepted, and measured, once it passes the passive check. Before the gate a phone picture was flagged within about 3 s; with it, the picture is never measured as the agent. The overlay shows the live liveness score.
  - Remaining gap: the passive model is one model against one kind of attack. A replay that passes it (for example a good screen at another angle) is stopped at sign-in by the challenge, but not by the lock on its own.
  - **Second live test (2026-11-04, in the app) showed the passive model is too brittle to revoke a sign-in.** With the agent sitting almost straight (yaw -8) it scored 0.30, 0.22, 0.08, and the old rule signed the agent out twice. `monitor.log` recorded it. Changes: (1) the lock no longer closes on passive scores; when 3 of the last 5 fail it asks the monitor for one live action (blink twice, or turn the head left or right, about 9 s). Doing it clears the doubt for 5 minutes; not doing it closes the lock with `SPOOF_SUSPECTED` (a photo or a recording cannot respond on command). (2) Passive scores are not counted while the head is turned or looking down, and a face that passed in the last 30 s is taken back after the track is lost. Third live test (2026-11-04, a USB 640x480 webcam in a dim room, face brightness about 60-67 of 255): the photo check scored a real face anywhere from 0.00 to 0.93, the lock never became verified, and Go available failed five times because the 10 s re-check cut off the quick check it had just asked for. Fixes: the photo-check threshold is 0.35 (phone pictures scored 0.01-0.18), the re-check waits for a quick check in progress (25 s, at most 45 s), a plain banner says what is happening, passing the quick check counts even if the last passive score was low (a bug found by a new test), and the desk shows the light on the face and says when it is too dim. A decision for the evaluation: if "quick check" prompts turn out to be frequent, drop the passive model from the in-shift path and rely on the sign-in challenge, the face match and the no-blink rule.
- **Face match drops with a tilted head:** in the app log the match was 0.88–0.93 facing the camera but 0.50 at pitch -13, close to τ = 0.45. Watch for `IDENTITY_MISMATCH` on the agent; the remedies are a lower τ or more enrollment poses.
- **Identity-lock thresholds are starting values.** τ = 0.45 is from the plan; on a public six-person photo, different people scored at most 0.17 and the same face under blur, shift and dimming scored about 0.98. Quality thresholds (sharpness, brightness) and the head-turn threshold (0.15) are tuned on the first real webcam. A false reject of the real agent triggers a forced re-login after 5 s, so tune the false-reject rate down too.
- The identity lock reacts within 3 s of someone swapping into the seat without a gap: the stranger is not measured as the agent from then on, but `IDENTITY_MISMATCH` fires about 5 s after that.
- Local profile files hold biometric data. The demo keeps them outside OneDrive (`%LOCALAPPDATA%\thesis-microsleep`) and they are git-ignored. They are not encrypted; the Supabase milestone moves templates to the server, encrypted.
- Biometrics are regulated data under privacy laws such as GDPR Art. 9 and the PH Data Privacy Act (RA 10173). Get written consent, store embeddings rather than photos, encrypt them, and set a retention policy.
- **Client trust:** embeddings and heartbeats come from the client, so a modified client on a paired PC could replay an embedding or fake a heartbeat. State this as a thesis limitation.
- **Team-leader passwords:** team leaders sign in with a password only. Supabase TOTP can be switched on later without schema changes.
- **Groq:**
  - Rate limits could throttle tens of agents on the free tier, so cap concurrency and use the 8B fallback.
  - Tagalog speech-to-text is weak (24 % word error, see above); Taglish and English are fine. The callers' Tagalog and Taglish read naturally in live calls.
  - Model IDs change, so keep them in config.
  - Voice audio goes to Groq, so check its data-retention terms and list it as a processor in the consent form. Face data never goes to Groq.
- **Dashboard:** the browser holds only the publishable key; row level security and the `manage-team` function decide everything. Live updates need the table in the Realtime publication, and one unpublished table breaks a shared channel, so each table has its own. Reports read at most the latest 500 calls and 5,000 events of a period. Service level counts a call as in time when answered within 20 s.
- **Voice changes the CV signal:** mouth movement during speech is a harder yawn false-positive case than silent work. Keep the ≥ 2 s rule and check it in the evaluation.
- **Supabase free tier:** projects pause after a week of inactivity.
- **Data location:** templates are stored in the cloud, so the consent form must say so. Self-host if the ethics board requires it.
- **Docker:** `supabase start` needs Docker Desktop with WSL 2.
- **Windows camera backend:** on the first test webcam, DirectShow delivered 8 FPS and Media Foundation 29 FPS, so the client uses Media Foundation. Camera indexes differ between backends, and virtual cameras (OBS Virtual Camera, DroidCam) take indexes too, so the client must pick the physical webcam by name (camera guard, milestone 3).
- Agents know the calls are simulated, and the consent form says so.
- Known limits:
  - A hand covering the mouth during a yawn.
  - A yawn with the head pitched well down: the mouth opening looks smaller to the camera, so MAR can stay under the threshold (seen in the first webcam test).
  - Very low light (consider an IR webcam).
  - Real-time deepfake injection that gets past virtual-camera blocking.

## Key references for the thesis
- Soukupová & Čech 2016 (EAR)
- Kartynnik et al. 2019 (MediaPipe face mesh)
- Deng et al. 2019 (ArcFace)
- Dinges & Grace 1998 (PERCLOS)
- NIST SP 800-63B (digital identity: authenticators and MFA)

## Status and next step
Milestones 1–3 are built and were checked on a real webcam and face: fatigue detection, enrollment, the liveness challenge (a photo and a phone video were denied at sign-in), and the identity lock (liveness 0.98–1.00 for the real face, 0.01 for a phone picture). See README.md for how to run everything.

Milestone 4 (the Supabase backbone) is **deployed to the hosted project "Thesis"** (ref `dglqfoxbwlhmlhtujslh`, Tokyo) and verified there: 68 of 68 access-rule tests, 62 of 62 end-to-end checks through the real server (pairing, enrollment, the face check, every attack, the access rules between two teams, escalation, lockout), and Supabase's own security advisor shows no errors. The first real sign-in on the webcam succeeded on 2026-11-03 (email + password + face, match score 0.85); the server then held the encrypted template (about 64 KB), the calibration, the consent time and a full audit trail.

Milestone 5 (the simulated BPO) is **done and deployed** (2026-11-04). A team leader starts a seeded run (`leader_cli.py start-run`), callers arrive in a queue, the longest-idle Available agent is offered each call, and the agent answers by voice at the terminal desk (`python -m tools.call_demo`): the caller speaks (Microsoft neural voices), the agent's words are transcribed (Groq Whisper), and the caller replies (Groq `qwen/qwen3.8-27b`) until the call ends, with dead-air prompts, wrap-up and a disposition. Live checks passed in Taglish and English. Turn time went from 4.8–8.9 s to 3.1–3.3 s after one-call-per-turn database access, parallel lookups, warm-up and a shorter pause; the target is ≤ 3 s. The Spike S1 findings are in `docs/spike-s1-results.md`.
- **Known limits:** Tagalog speech-to-text is weak (24 % word error, and short phrases are sometimes badly misheard); Azure AI Speech (fil-PH) is the untried alternative. The terminal desk (`tools.call_demo`) still simulates the monitor; the real one is in `app.py`. Voices come from an unofficial client of Microsoft's service (`edge-tts`), to be disclosed.
- **To do later:** another voice test with more people and more call types, and a look at the caller mood strength.
- **Hosted changes made for it:** migrations `..._bpo_core`, `..._agent_status_dispatch`, `..._lock_down_function_grants` (a real hole found by the tests: `system_end_call` was callable by signed-in users), `..._pin_helper_search_path`, `..._call_turn_context`; functions `caller-turn` and `transcribe`; the `GROQ_API_KEY` secret; 7 scenarios and 40 fictional customers.

Milestone 6 (the agent desktop) is **done** (2026-11-04). `python client/app.py` (or the packaged `call-desk.exe`) pairs the PC, signs in with email + password, runs the face check in a Qt window, and opens the desk. The camera is off at first; **Go available** opens it, re-checks that the face at the desk matches the sign-in face, reports a live monitor to the server and only then asks for Available. The camera closes again in Not ready, on a break and on sign-out, and a camera indicator is always visible (desk and tray). A microsleep raises a full-screen "Are you awake?" alert with a sound, and the server puts the agent on hold until they confirm. Events and per-minute metrics go to a SQLite queue first and are uploaded with retry; the server ignores resends (`..._events_dedupe.sql`). A revoked face check returns the agent to sign-in with a message.
- **Found by the live tests:** the desk's status lagged the server by up to a second, so it closed the camera it had just opened and the agent was put on hold (fixed, with a test that reproduces the lag); and the passive photo check signed the agent out twice (see the risk note above; replaced by the live-action challenge).
- **Changes after the first live tests (2026-11-04):** the windows had different fixed sizes, so every window now follows one policy (bordered, resizable, opens maximized, F11 for full screen); the camera picture is now always shown beside the desk while the camera is on, so the agent can see what is being monitored; and the microsleep alert had used the Windows default beep, which is silent under the "No Sounds" scheme, so it now plays its own ping.
- **Same design as the dashboard (2026-11-04):** the agent app now uses the dashboard's tokens, fonts (Geist, Geist Mono, Geist Pixel), numbered sections, hairline cards, chips, stat grids, halftone accent on sign-in and a light / dark / system switch. The desk's side column shows the camera with the values the monitor works from (eye openness, eye closure, blink rate, mouth, head angles relative to the calibrated pose, face match, real-face check, frame rate), and the main column adds "this shift" and "what the monitor noticed" so the screen is no longer mostly empty.
- **Ending a call while the caller is talking (2026-11-04):** End call now cuts the caller off at once: playback stops within about 20 ms, no further sentences are made or played, and no new turn is requested. If the agent ends the call before the caller's opening line has arrived, nothing is asked for at all. A caller reply that is already on its way when End call is pressed is never spoken.
- **Confirming a microsleep alert (2026-11-04):** one click on "I am awake" (or on the hold screen) now returns the agent straight to Available with the camera still on, once the monitor reports a live camera, a confirmed identity and no microsleep state (it holds for 5 s after the eyes reopen, so it waits for that). Only if it cannot confirm does the agent end up Not ready with the camera off. During a call the alert is simply closed.
- **Known limits:** the enrollment capture still opens an OpenCV window instead of a Qt one; the exe is about 590 MB and needs `.env` next to it; the heartbeat and the offline queue are tested in code and against the hosted project, but offline mode has not been tried live.
- **To do later:** more live testing: working offline, the exe, a stranger in the seat, a photo or video inside the app, a long shift and more people.
- **Hosted change made for it:** migration `..._events_dedupe.sql` (unique index on session, type, time so resends are harmless).

Milestone 7 (the team-leader dashboard) is **built and tested** (2026-11-04). `npm run dev` in `dashboard/` (it reads `dashboard/.env`: the project URL and the publishable key). Eight pages in the bryl-minimal language (monochrome, Geist fonts, halftone accent, dark mode, one red for microsleep and identity mismatch): live team status, queue and service level, calls with transcripts, simulation control, alerts, per-agent pages, reports with CSV export, and team management. Charts follow the dataviz rules (thin marks, hairline grid, legends, hover tooltips, a table view of every chart, one axis per chart).
- **Tested against the real thing:** `scripts/dashboard_demo.py` creates a throwaway team with agents who pass the real face check and a day of calls, transcripts, events and metrics; `dashboard/e2e/run.mjs` drives Edge through it (45 checks) and takes light, dark and phone screenshots. The demo deletes everything it made.
- **Found by those tests:** one unpublished table made a shared Realtime channel fail, so no live update arrived (fixed: one channel per table); chart axis labels were clipped; a crashing page left a blank screen (an error boundary now shows the message); the demo's cleanup missed an agent created through the dashboard.
- **Known limits:** the dashboard's `node_modules` and build output live outside OneDrive (a junction and `C:\Users\Mara\build`); account management needs the `manage-team` function, so there is no offline mode; no team-leader MFA (password only, by choice).
- **To do later:** try it live with real calls, a second team leader's view of another team (covered by the database tests, not clicked through), and a longer shift of data in the charts.

## Evaluation session with the groupmates (to do)

The testing tools are built (`research/eval/`, runbook in `docs/evaluation-protocol.md`). What is left is collecting data with people. Plan one session of about 2 to 3 hours for 4 to 6 people, then the analysis takes minutes.

**Before the session**
- [ ] Adviser or ethics board has looked at `docs/consent-form.md`; fill in the names, the retention date and Groq's data terms; print one copy per volunteer.
- [ ] Decide the server's photo-check minimum (`LIVENESS_MIN`, now 0.5; the app's lock uses 0.35). Needs a hosted change with a dry run and your approval, so do it before the session if the dim webcam could fail sign-in.
- [ ] One PC with the venv working, the webcam, a lamp, and a dim room option. Close other apps so the frame rate is steady. Charged laptop.
- [ ] For the fake-face tests: a **printed photo** of each person who will be "the enrolled person" (print at normal size, front-facing), plus their phone with a photo and a short video of themselves (with their permission, form part C), and a second screen or tablet for the replay.
- [ ] Participant codes P01, P02, … on a sheet (codes only in every file, names only on the signed forms).

**Per volunteer (about 25 minutes)**
1. Consent signed (tick only the parts they agree to).
2. Face matching: `identity_trials.py enroll`, then `verify` attempts in normal light, dim light, with and without glasses, and a natural turned head.
3. Fatigue recording: `record_session.py` in normal light, then again in dim light (6 minutes each).
4. Fake-face tests (only if they agreed to part C): `spoof_trials.py` for `bonafide`, `print`, `phone_photo`, `phone_video`, `screen_replay`, 10 trials each (fewer if time is short: 5 is the least that says anything).

**Roles that help:** one person operates the PC, one is the volunteer, one holds the fake (print, phone) and notes anything odd. Rotate so everyone is a volunteer once.

**After the session**
- [ ] `evaluate_fatigue.py`, `analyze_identity.py`, `analyze_spoof.py`, then `make_report.py`; read `research/eval/results/REPORT.md`.
- [ ] Back up the raw data folder (`%LOCALAPPDATA%\thesis-microsleep\eval\`) somewhere private if the adviser wants it; never into a cloud folder.
- [ ] Delete raw recordings and face numbers on the retention date written in the consent form.
- [ ] Write the limits into the thesis (acted behaviour, small sample, one webcam and room) and report the 95% intervals the tools print.

Other open items: try the exe, offline mode and the stranger-in-the-seat case live; try Azure speech for Tagalog (optional); deploy the dashboard to Vercel (last, once everything else is done).

Notes from building it:
- **Tools** live outside OneDrive: the Supabase CLI 3.0 in `C:\Users\Mara\tools\supabase`, Deno in `C:\Users\Mara\tools\deno`. In CLI 3.0, any change to a hosted project needs an explicit `--yes`; targets use `--remote`; `functions deploy --use-api` needs no Docker.
- **Tests without Docker:** `supabase test db` needs Docker even for a remote database, so `scripts/run_pgtap.py` runs the same pgTAP files through `supabase db query` and reports every assertion. The tests roll back and leave no data.
- **What the tests caught:** the first run found 21 failures with one cause (clients could not choose their own session id, which the offline queue needs). Fixed by migration `..._sessions_client_id.sql`; the tests then passed.
- **Function auth:** the platform JWT check is off (`verify_jwt = false`) and every function verifies the caller itself with Supabase Auth (`_shared/auth.ts`), because the docs do not confirm that the platform check works with the new key types. The functions read the secret key from `SUPABASE_SECRET_KEYS` (new) or `SUPABASE_SERVICE_ROLE_KEY` (legacy).
- **Table access:** every table has RLS on and explicit minimal grants; the cloud default would otherwise expose new tables to the data API.
- **Config:** `supabase/config.toml` declares only what this repo manages on the project (no public sign-up, passwords of 12+ characters with upper, lower and digit). The generated full file would have overwritten about 12 project settings with local-development defaults, so it was cut down. `config push` leaves undeclared settings alone.
- **Security advisor:** two warnings, both intended. `ack_alert` and `ack_password_changed` are security-definer functions that signed-in users can call. Each checks the caller itself (a leader of that team; the caller's own row only), and the pgTAP tests confirm it.
- **The encryption key** for face templates is in the Edge Function secret `FACE_TEMPLATE_KEY`, with a backup at `%LOCALAPPDATA%\thesis-microsleep\face_template_key.env`. Copy it to a password manager. Losing it makes stored templates unreadable (agents would re-enroll).
