# Evaluation protocol: how to collect the data

All commands run from the project root. `$py` is the venv Python:

```powershell
cd "C:\Users\Mara\Thesis"
$py = "C:\Users\Mara\venvs\thesis-client\Scripts\python.exe"
```

Before anyone is recorded: **a signed consent form** (`docs/consent-form.md`, reviewed by your adviser) for every volunteer. Use a participant code (P01, P02, …), never a name. Raw data (video, face numbers, trial logs) stays in `%LOCALAPPDATA%\thesis-microsleep\eval\` (outside OneDrive and git). Results with no faces go to `research/eval/results/`.

## What you need

| Experiment | People | Time each | Why this many |
|---|---|---|---|
| 1. Fatigue recordings | 3 or more (you count as one) | 7 min per recording, 2 lighting conditions | enough events per kind (6 asked × 3 people × 2 conditions = 36 per kind) |
| 2. Face matching | 5 or more | 10 min | impostor trials grow with the square of the people: 5 people give 5 × 4 × (attempts) |
| 3. Fake-face tests | 2 or more as the "enrolled person" | 15 min | each fake type needs at least 10 trials to say anything |
| 4. Voice delay | you | already collected, add more calls any time | |
| 5. Performance | you | 2 min | |

## 1. Fatigue recordings (scripted, ground truth known)

Each recording is about 6 minutes. The screen shows cues: first the calibration phases (eyes open, eyes closed, mouth wide open), then six of each of: close eyes about one second, yawn, head nod, head tilt, slow blink, plus control periods (talking, looking down). Sit as an agent would: at the desk, at a normal distance.

```powershell
& $py research\eval\record_session.py --subject P01 --lighting normal --glasses no
& $py research\eval\record_session.py --subject P01 --lighting dim --glasses no
```

Use a lamp-lit and a dim room, and glasses on and off if the volunteer wears them. Then score all recordings:

```powershell
& $py research\eval\evaluate_fatigue.py
& $py research\eval\evaluate_fatigue.py --sweep microsleep_ms=300,400,500,600,800,1000   # threshold tuning chart data
```

**Public data as a second source:** YawDD (about 4.9 GB, one archive at the UMass server named on its IEEE DataPort page, or free with an IEEE account) has labelled yawning, talking and normal driving videos. The other public sets are impractical (UTA-RLDD is 111 GB, NTHU-DDD needs a request, Eyeblink8 has no clear download). Say in the thesis that acted behaviour is not real tiredness, and that YawDD covers real yawns.

## 2. Face matching (FAR, FRR, EER)

Each volunteer enrolls once, then makes three attempts per condition:

```powershell
& $py research\eval\identity_trials.py enroll --subject P01
& $py research\eval\identity_trials.py verify --subject P01 --lighting normal --glasses no --attempts 3
& $py research\eval\identity_trials.py verify --subject P01 --lighting dim --glasses no --attempts 3
& $py research\eval\identity_trials.py verify --subject P01 --lighting normal --glasses yes --attempts 3   # if they wear glasses
& $py research\eval\identity_trials.py verify --subject P01 --pose turned --attempts 3                      # a natural turned or tilted head
& $py research\eval\analyze_identity.py
```

Only numbers are saved. Every attempt is scored against every profile: your own profile gives a genuine trial, anyone else's an impostor trial.

## 3. Fake-face tests (APCER, BPCER)

Needs an enrolled volunteer whose photo or video you can show (with their permission, form part C). Run the real sign-in check against the real person and against each fake, 10 trials each:

```powershell
& $py research\eval\spoof_trials.py --subject P01 --attack bonafide      --trials 10
& $py research\eval\spoof_trials.py --subject P01 --attack print         --trials 10   # a printed photo
& $py research\eval\spoof_trials.py --subject P01 --attack phone_photo   --trials 10   # a photo on a phone screen
& $py research\eval\spoof_trials.py --subject P01 --attack phone_video   --trials 10   # a video on a phone
& $py research\eval\spoof_trials.py --subject P01 --attack screen_replay --trials 10   # a video on a second monitor or tablet
& $py research\eval\spoof_trials.py --subject P01 --attack bonafide --lighting dim --trials 10   # the real person in the dim room
& $py research\eval\analyze_spoof.py
```

For an attack, hold the fake in front of the camera when the window opens, as an attacker would, and try to look as if it were responding. The same trials are scored under five rules (the deployed sign-in, a lower passive threshold, the challenge alone, the passive model alone, and no liveness at all), so the thesis can say how much each defence adds.

## 4 and 5. Voice delay and performance

```powershell
& $py research\eval\latency_report.py      # from the calls already on the server
& $py research\eval\bench_pipeline.py      # frame rate, CPU and memory on this PC
```

## Report

```powershell
& $py research\eval\make_report.py          # gathers everything into research\eval\results\REPORT.md
& $py -m pytest research\eval -q            # the measuring tools themselves are tested
```

## What to write in the thesis about limits

- Acted behaviour is not real drowsiness; the fatigue recordings are controlled tests, YawDD adds real yawns.
- Small samples: always give the 95% interval (the tools do) and say how many people took part.
- One webcam model (a dim 640×480 USB camera) and one room: lighting and camera quality change the liveness scores a lot (seen in the live tests: 0.0 to 0.93 for a real face in dim light).
- The passive photo-check model alone is not reliable enough to sign someone out; that is why the app asks for a live action first.
- Identity thresholds (0.45) are starting values: report the EER threshold next to it.
