# Agent Microsleep & Fatigue Monitor

Thesis project. The design lives in [plan.md](plan.md); [walkthrough.md](walkthrough.md) is the plain-language version.

## What works today (milestones 1-8, the evaluation in progress)

- Live webcam overlay with landmarks, EAR, MAR and head angles (`client/tools/overlay.py`)
- Event state machines: blinks, long blinks, microsleep, PERCLOS, yawns, head nods and tilts, plus the ALERT / DROWSY / MICROSLEEP state (`client/vision/drowsiness.py`)
- Per-agent calibration (`client/vision/calibration.py`)
- Face identity, all local (`client/identity/`): enrollment capture, ArcFace matching, a liveness challenge, and the identity lock that ignores everyone except the enrolled agent
- Physical-webcam selection that refuses virtual cameras (`client/capture/camera_guard.py`)
- Replay of recorded videos through the same pipeline (`research/replay/replay.py`)

- The Supabase backend (hosted project "Thesis"): accounts and roles, access rules, encrypted face templates, PC pairing, the face second factor, and monitoring tables (`supabase/`)
- Sign-in on the PC: email + password + face (`client/auth/`, `client/tools/signin_demo.py`)
- The simulated call center: seeded simulation runs, queue and dispatch in the database, AI callers by voice in English, Tagalog and Taglish, a terminal agent desk (`client/bpo/`, `client/tools/call_demo.py`, `simulation/`)
- The agent desktop app (`client/app.py`): pairing, sign-in, face check, a desk with status, calls and wrap-up, the camera on only while working, a microsleep alert, an offline queue with upload, and a PyInstaller build
- The team-leader dashboard (`dashboard/`): live team, queue and service level, calls and transcripts, simulation control, alerts, per-agent pages, reports, team management
- The evaluation toolkit (`research/eval/`): recording protocol, replay scoring, face-matching and fake-face trials, voice delay, performance, one results report

Still to do: collect the volunteer data for the fatigue, face-matching and fake-face experiments (see `docs/evaluation-protocol.md`).

## Setup (Windows, Python 3.12)

The virtual environment lives **outside** OneDrive so thousands of package files don't sync.

```powershell
py -3.12 -m venv C:\Users\Mara\venvs\thesis-client
C:\Users\Mara\venvs\thesis-client\Scripts\python.exe -m pip install -r client\requirements.txt
C:\Users\Mara\venvs\thesis-client\Scripts\python.exe scripts\download_models.py
```

`download_models.py` fetches about 180 MB (the ArcFace model is 174 MB) and checks each file's SHA-256.

## Run the tests

```powershell
cd client
C:\Users\Mara\venvs\thesis-client\Scripts\python.exe -m pytest
```

## Try the fatigue detection on your webcam

```powershell
cd client
C:\Users\Mara\venvs\thesis-client\Scripts\python.exe -m tools.overlay
```

| Key | Action |
|---|---|
| `o` | Capture 10 s with eyes **open**, looking at the screen |
| `c` | Capture 3 s with eyes **closed**. Calibration is computed and saved when this ends |
| `m` | Optional: 3 s with the mouth **wide open** (sharpens the yawn threshold) |
| `r` | Reset the detector |
| `q` / Esc | Quit |

The result is saved to `client/calibration.local.json` and loaded next time.

**Camera not opening or wrong picture?** The client picks the physical webcam by name and refuses virtual cameras (OBS, DroidCam, ...). Pass `--camera N` to choose an index yourself, with `--allow-virtual` if it really is a virtual one. The FPS shown in the overlay should be about 25-30.

## Try the identity features on your webcam

```powershell
cd client
$py = "C:\Users\Mara\venvs\thesis-client\Scripts\python.exe"
& $py -m tools.identity_demo enroll      # 1. capture your face (5 head poses) + calibration
& $py -m tools.identity_demo login       # 2. liveness challenge + face match, like the sign-in check
& $py -m tools.identity_demo watch       # 3. the identity lock, live
```

Add `--glasses` to `enroll` for two rounds (glasses on, then off). Space continues after the glasses prompt.

Things to try in `watch`, and what should happen:

| You do | Expected |
|---|---|
| Sit normally | `identity: VERIFIED`, measured as usual |
| A second person stands behind you | Still VERIFIED, and only your eyes are measured |
| Leave for 10+ seconds | `ABSENT` event; nothing counted as sleep |
| Someone else sits down | They are not measured; `IDENTITY_MISMATCH` after about 5 s, and the lock stays closed |
| Press `r` | Forced re-login: the lock resets |
| Hold a **photo or phone screen** of your face | Never measured as you: `SPOOF_SUSPECTED` within a few seconds, liveness near 0. `login` denies it too |

Your face template is saved to `%LOCALAPPDATA%\thesis-microsleep\profile.local.npz`, outside OneDrive. It holds numbers only, no photos, and it is not encrypted yet.

## The Supabase backend (milestone 4)

Secrets live **outside** the repo and OneDrive, in `%LOCALAPPDATA%\thesis-microsleep\`: `owner.env` (project URL, publishable key, SECRET key) and `face_template_key.env` (the template encryption key; keep a copy in a password manager). The desktop client only ever gets `client/.env`, which holds the URL and the publishable key.

Tools are in `C:\Users\Mara\tools\` (Supabase CLI, Deno), also outside OneDrive. Run from the project root; `$py` is the venv Python used above.

```powershell
$py = "C:\Users\Mara\venvs\thesis-client\Scripts\python.exe"

# 1. Once: create your team and its team leader (owner-only; prints a temporary password once)
& $py scripts\create_team.py --team "Team A" --leader-email you@example.com --leader-name "Your Name"

# 2. As the team leader: set your own password, then create an agent and get codes
& $py scripts\leader_cli.py --email you@example.com change-password
& $py scripts\leader_cli.py --email you@example.com pairing-code
& $py scripts\leader_cli.py --email you@example.com create-agent --name "Ana Cruz" --agent-email ana@example.com
& $py scripts\leader_cli.py --email you@example.com enrollment-code --agent "Ana"

# 3. As the agent, on the webcam: pair this PC, sign in, change the temporary password, enroll, face check
cd client
& $py -m tools.signin_demo
```

Checks against the hosted project (the tests roll back or clean up after themselves):

```powershell
& $py scripts\run_pgtap.py        # 240 assertions in four suites (access rules, call center, grants, call turns), no Docker needed
& $py scripts\smoke_m4.py         # 62 end-to-end checks through the real server
```

Deploying changes (each hosted change needs `--yes`; dry-run first):

```powershell
$sb = "C:\Users\Mara\tools\supabase\supabase.exe"
& $sb db push --remote --dry-run ; & $sb db push --remote --yes
& $sb functions deploy --use-api --yes
& $sb config diff ; & $sb config push --yes
```

## Spike S1: Tagalog speech and caller voices (milestone 5)

Four small tools in `simulation/spikes/` answer the open questions of the simulated call center: does Groq's Whisper understand Tagalog and Taglish, which Groq model plays a believable customer, and which voice should the callers have. Your recordings are saved in `%LOCALAPPDATA%\thesis-microsleep\spike_s1`, outside OneDrive and git. The Groq key goes after `GROQ_API_KEY=` in `owner.env`.

```powershell
cd "C:\Users\Mara\Thesis\simulation\spikes"
$py = "C:\Users\Mara\venvs\thesis-client\Scripts\python.exe"
& $py tts_samples.py          # no key needed: listen to the voices, score each 1-5
& $py record_utterances.py    # read 24 sentences aloud (about 5 minutes)
& $py stt_eval.py             # needs the Groq key: how well does Whisper understand you
& $py stt_context_eval.py     # needs the Groq key: do other Whisper prompts help Tagalog (a little, for Taglish)
& $py llm_callers.py          # needs the Groq key: three models play a customer in 3 languages
```

## The simulated call center (milestone 5)

Callers (Groq) ring Available agents and talk by voice. Use a **headset**: the microphone is muted while the caller speaks. Set up once (already done on the hosted project): the `GROQ_API_KEY` secret, the `caller-turn` and `transcribe` functions, and `python simulation\load_scenarios.py` plus `python simulation\seed_customers.py` for the 7 scenarios and 40 fictional customers.

```powershell
$py = "C:\Users\Mara\venvs\thesis-client\Scripts\python.exe"

# Terminal 1, the team leader: start a seeded run (the same seed gives the same callers)
& $py scripts\leader_cli.py --email you@example.com start-run --rate 2 --max-concurrent 1 --seed 7
& $py scripts\leader_cli.py --email you@example.com status          # agents and the queue
& $py scripts\leader_cli.py --email you@example.com calls           # recent calls
& $py scripts\leader_cli.py --email you@example.com transcript --call 3f2a
& $py scripts\leader_cli.py --email you@example.com stop-run

# Terminal 2, the agent: the terminal desk (pair, password, face check, then the keys below)
cd client
& $py -m tools.call_demo
```

Desk keys: `g` go Available, `b` break, `q` sign out; while a call rings `a` accept or `d` decline; during a call `e` ends it. At wrap-up you can look up the customer by account number and choose a result. Each turn prints how long it took (speech to text, caller, first spoken sentence, and the time inside each function). The camera monitor is simulated here (always LIVE and ALERT) until milestone 6.

## The agent desktop app (milestone 6)

```powershell
cd client
C:\Users\Mara\venvs\thesis-client\Scripts\python.exe app.py
```

Pair this PC with a code from your team leader (first time), sign in, do the face check, then use the desk: **Go available** opens the camera after a quick "is it you" check; **Take a break** closes it. While a call is on you hear the caller and answer by voice (headset). A microsleep raises the full-screen "Are you awake?" alert; confirm it and then "I am awake and ready". If the app asks for a "Quick check" (blink twice or turn your head), do it within about 9 seconds. Start a simulation run as the team leader first (see the previous section) so calls arrive.

The app has the same design as the team-leader dashboard (Geist fonts, numbered sections, light / dark / system switch at the top right, remembered between runs). Every window opens maximized with its normal border and title bar, can be resized (down to 1180 x 680) and toggles full screen with F11. While the camera is on, a live picture of you is shown beside the desk with the numbers the monitor is working from: eye openness, eye closure over the last minute, blinks per minute, mouth opening, head angles (relative to your calibrated pose), face match, the real-face check and the frame rate. The picture stays on this PC; it says "Camera is off" otherwise. Below the work area: this shift (calls handled, average talk time, resolved share, time on shift, microsleeps, yawns, nods, long blinks) and the latest events the monitor noticed. The alert sound is its own two-tone ping, not the Windows default beep (which is silent when the sound scheme is set to "No Sounds"). Check you can hear it with `python -m tools.ping` (from `client/`).

Events and metrics are saved in `%LOCALAPPDATA%\thesis-microsleep\queue.sqlite` first and uploaded in the background. `monitor.log` in the same folder explains what the identity lock saw; `crash.log` appears if the app fails to start.

Check the upload path against the hosted project (creates and deletes its own data):

```powershell
C:\Users\Mara\venvs\thesis-client\Scripts\python.exe scripts\smoke_m6.py     # 20 checks
```

Package it (outside OneDrive; about 590 MB; put `.env` next to the exe):

```powershell
cd client
C:\Users\Mara\venvs\thesis-client\Scripts\python.exe -m PyInstaller packaging\client.spec --noconfirm --distpath C:\Users\Mara\build\thesis-client\dist --workpath C:\Users\Mara\build\thesis-client\work
```

## The team-leader dashboard (milestone 7)

A web app for team leaders (agents use the desktop app instead). It needs Node 20+. `dashboard/node_modules` and the build output live **outside** OneDrive (a junction to `C:\Users\Mara\build\dashboard-deps\node_modules`, build output in `C:\Users\Mara\build\dashboard-dist`).

```powershell
cd dashboard
copy .env.example .env          # then fill in the project URL and the PUBLISHABLE key (never the secret key)
npm run dev                     # http://localhost:5173
npm test                        # 36 unit tests
npm run build                   # type-check + production build
```

Sign in with a team-leader account (see "The Supabase backend"). The first sign-in on a temporary password asks for a new one. After `npm install`, check that `dashboard\node_modules` is still a junction (`(Get-Item node_modules).LinkType`); if npm replaced it with a real folder, move that folder to `C:\Users\Mara\build\dashboard-deps\node_modules` and recreate the junction.

Try it with realistic data, then run the browser test (Edge, headless; needs `playwright-core` in `C:\Users\Mara\build\e2e`):

```powershell
$py = "C:\Users\Mara\venvs\thesis-client\Scripts\python.exe"
& $py scripts\dashboard_demo.py --out C:\Users\Mara\build\demo.json     # creates a throwaway team; prints READY; keeps agents alive
cd dashboard; npm run build; node e2e\run.mjs                              # 45 checks + screenshots in C:\Users\Mara\build\shots
New-Item C:\Users\Mara\build\demo.stop                                 # ends the demo and deletes everything it made
```

## The evaluation (milestone 8)

```powershell
$py = "C:\Users\Mara\venvs\thesis-client\Scripts\python.exe"
& $py research\eval\latency_report.py      # voice delay, from the calls on the server
& $py research\eval\bench_pipeline.py      # frame rate, CPU, memory on this PC (films about 6 s from the webcam, saves nothing)
& $py research\eval\record_session.py --subject P01 --lighting normal --glasses no   # a scripted, labelled recording (consent screen first)
& $py research\eval\evaluate_fatigue.py    # score the recordings; add --sweep microsleep_ms=300,500,800 to tune a threshold
& $py research\eval\make_report.py         # everything into research\eval\results\REPORT.md
& $py -m pytest research\eval -q            # the measuring tools are tested (54 tests)
```

The step-by-step runbook, with volunteer numbers and the face-matching and fake-face commands, is in [docs/evaluation-protocol.md](docs/evaluation-protocol.md); the checklist for the session with the groupmates is in plan.md under "Evaluation session with the groupmates". Volunteers sign [docs/consent-form.md](docs/consent-form.md) first (a draft: have your adviser review it). Raw recordings and face numbers stay in `%LOCALAPPDATA%\thesis-microsleep\eval\` (outside OneDrive and git); only face-free tables and charts go into the project. The ten-minute demo is in [docs/demo-script.md](docs/demo-script.md).

## Replay a video

```powershell
C:\Users\Mara\venvs\thesis-client\Scripts\python.exe research\replay\replay.py path\to\video.mp4 --out events.csv
```

## Layout

See "Directory structure" in [plan.md](plan.md). Empty folders are placeholders for later milestones.
