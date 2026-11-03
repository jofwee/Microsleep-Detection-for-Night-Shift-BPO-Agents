# Demo script: the whole story in about ten minutes

For a thesis defence or a supervisor demo. It shows, in order: a team leader starts a simulation, an agent signs in with a face check, a Taglish call is handled by voice, a microsleep is caught and shown on the dashboard, and the fake-photo defence.

## Before you start (once, the day before)

- Headset on, a **lamp in front of you** (the webcam is dim otherwise), phone charged (for the photo attack).
- A team leader and an agent account exist and the agent is **enrolled** (see the README).
- `python app.py` and the dashboard start without errors (`npm run dev` in `dashboard/`).
- Two terminals open at the project root; the dashboard open in a browser on a second screen if you have one.

```powershell
cd "C:\Users\Mara\Thesis"
$py = "C:\Users\Mara\venvs\thesis-client\Scripts\python.exe"
```

## The script

| # | Do | Say | What the audience sees |
|---|---|---|---|
| 1 | Dashboard → **simulation** → set 2 calls a minute, 1 at once, seed **7**, start. | "A team leader starts a repeatable run: the same seed brings the same callers." | the run card with a running chip |
| 2 | Terminal: `& $py client\app.py`. Sign in, do the face check. | "Email, password and a live face check: blink or turn on command." | the sign-in window, the face window with your picture |
| 3 | Click **Go available**. Point at the right-hand panel. | "The camera opens only now. It checks that it is really me, and shows what it measures." | camera on, the numbers (eyes, mouth, head, match) |
| 4 | A call rings: **Accept**. Answer in English or Taglish. | "An AI caller, by voice, in English, Tagalog or Taglish." | the transcript growing; the caller speaking |
| 5 | **Close your eyes for a second** while talking. | "A microsleep." | the full-screen alert and a ping |
| 6 | Click **I am awake**. | "One click and I am back at work; the camera stayed on." | back on the call |
| 7 | Stay silent for 8 seconds. | "Dead air: the caller checks I'm there." | the caller says "hello?" |
| 8 | **End call** while the caller is talking, then finish wrap-up. | "I can hang up even while the caller is mid-sentence." | the voice stops at once |
| 9 | Dashboard: **live** page, then the **calls** page → open that call. | "The team leader sees the same call, the transcript with timings, and the microsleep marked during the call." | a red microsleep chip; the call's events |
| 10 | Dashboard: **alerts** → acknowledge; **reports** → fatigue by hour and by kind of call. | "Fatigue is analysed by hour, language, problem and caller mood." | the charts |
| 11 | Hold a **phone showing your photo** in front of the camera during a **Go available** (or run `spoof_trials.py`). | "A photo cannot blink or turn on command, so it cannot get in." | the quick check failing, the sign-in refused |
| 12 | Terminal: `& $py research\eval\make_report.py` and show `REPORT.md`. | "And these are the measured results: accuracy, delay, performance." | the report |

## If something goes wrong

- **Camera dark or the quick check keeps failing:** add light in front of you (a lamp); the desk shows "light on your face".
- **No calls arrive:** the agent must be Available with the camera on, and a run must be started; check **live** on the dashboard.
- **Signed out unexpectedly:** the face check was revoked (for example by a suspected fake). Sign in again.
- **Voice slow:** the first call after a long pause is slower (cold start); the app warms up when you go Available.

## What to stop and not claim

- Do not claim the system detects *real* drowsiness: the live checks are acted. Say what the evaluation did measure and its limits.
- Do not show a volunteer's picture or video without their separate permission.
