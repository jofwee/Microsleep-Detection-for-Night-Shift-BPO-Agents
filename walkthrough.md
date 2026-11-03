# Walkthrough: plan.md in Caveman Terms

Written for: you, as a plain-language companion to plan.md. Nothing is built yet.

---

## What we make?

Camera watch worker face. Worker eyes shut too long = camera yell "WAKE UP!"

Camera also know *which* worker is who. Wrong person sit down = camera say "THIS NOT YOU!"

Worker work in a **fake call center** (BPO). AI robots call in with problems. Worker talk to robot by voice. Camera watch worker while worker take calls.

Camera watch for 4 bad signs:
1. **Eyes shut long** (microsleep) - worker nap with eyes open-ish
2. **Blink too much or too little** - tired brain blink weird
3. **Big yawn** - mouth wide, tired
4. **Head tilt or nod** - head fall down, "head bob"

---

## Who is "agent"? And who is boss?

Only two kinds of people. No third kind.

- **Agent** = the worker. Take calls. Use the app on a Windows computer.
- **Team leader** = the boss (also called supervisor). Watch own team on a web page.

No admin. You (owner) make the first team and first boss account once, with a small script.

We guess: call-center worker, Windows computer, normal webcam.
If you mean truck drivers, tell me. Camera box change and fake call center go away. Rest same.

---

## The tools (the "stack")

Think of it like caveman toolbox. Each tool do one job.

| Job | Tool | Caveman words |
|---|---|---|
| Write the code | **Python** (worker app) and **TypeScript** (server programs, boss page) | Two languages now. Python for camera brain. TypeScript for server and web. |
| Grab camera picture | **OpenCV** | Hand that grab webcam picture. |
| Find face dots | **MediaPipe** | Magic eye. Put 478 dots on face. Know where eyes, mouth, nose be. |
| Do the numbers | **NumPy** | Rock-counting tool. Fast math. |
| Know WHO face is | **InsightFace (ArcFace)** | Face-remember tool. Turn face into 512 numbers. Same person = similar numbers. |
| Check face is real | **MiniFASNet** | Anti-cheat. Photo of face? Phone screen? Say "FAKE!" |
| Show window and alarm | **PySide6** | Make the app window, the call screen and loud alarm. |
| Hear and talk | **sounddevice + Silero VAD** | Ears and mouth of app. Know when worker stop talking. |
| Save when no internet | **SQLite** | Small cave-notebook on the computer. |
| Make .exe file | **PyInstaller** | Wrap app in box so double-click run. |
| Login, server, big memory | **Supabase** | One big helper. Do login, big memory, live updates, small server programs. Replace old FastAPI chief. |
| Memory guard | **Row Level Security (RLS)** | Guard on the stone wall. Agent see own stones only. Boss see own team stones only. |
| Robot caller brain | **Groq API** | Very fast AI. Make robot caller think and answer. Also hear worker voice (Whisper). |
| Robot caller voice | **Voice tool (pick in Spike S1)** | Groq voice speak English only. Tagalog voice must come from another tool. |
| Boss screen | **React** dashboard | Boss see call line, who sleepy, what was said in calls. |
| Pack and ship | **Supabase Cloud** or `supabase start` | Replace Docker Compose. `supabase start` on own computer need Docker Desktop. |
| Test and grade | **Jupyter, pandas, scikit-learn, pytest** | For thesis: prove it work, make graphs. |

**Why app on computer, not website?**
Website tab go to sleep when worker open other thing. App always awake. Also: video stay on worker computer. Only small reports and face numbers go to server. Call voice go to Groq (the robot caller helper). No video spy. Good for privacy.

---

## How the pieces connect

```
WORKER COMPUTER                          SUPABASE                         BOSS
login: password + face (camera once) --> login + guard rules
go AVAILABLE -> camera ON                big memory ------------------->  dashboard
camera -> dots -> right person?          call line (queue)
        -> math -> sleepy? -> ALARM      small server programs <-> GROQ (robot caller)
mic + speaker <-> talk to robot
```

1. Worker log in: password, then face check. Camera then turn off.
2. Worker press "Available". Camera turn on.
3. MediaPipe put dots on face. Identity check: right worker? If no, ignore face.
4. Do math on dots. Sleepy? Alarm on worker screen. Send small report to server.
5. Call line give robot caller to worker. Worker talk by voice.
6. Boss dashboard light up.

---

## Login: password AND face (two locks)

**Agent** (worker):
1. Type email and password.
2. Camera say random thing: "turn left, blink twice". Real face can. Photo cannot.
3. Face numbers go to server. Server compare with saved numbers. Match = door open for 12 hours.
4. Camera turn off again.

**Team leader** (boss): email and password only. You chose this.

Safety rules:
- Bad face 5 times in 15 minutes = face login locked 15 minutes. Boss get told.
- Server refuse worker reports unless face check is fresh. Wrong face or fake found = door slam shut, worker signed out.
- Boss make worker accounts. Boss give one-time codes (work 15 minutes, one use only):
  - **Enrollment code** - needed to save a face. Stolen password alone cannot save someone else face.
  - **Pairing code** - link the computer to its real webcam.

---

## The fake call center (BPO)

Boss press START on dashboard. Robot callers join a line (queue). Line give call to a free worker.

**Problems robot bring:** bill fight, internet broken, cannot log in, where is my order or refund, change my plan, angry complaint, stolen card.
**Language:** English, Tagalog, or Taglish (mix of both). Start mix: 40% English, 40% Taglish, 20% Tagalog.
**Mood:** calm, confused, in a hurry, frustrated.
All customers are fake. Workers know it is pretend (consent form say so).

### When is camera ON?

| Worker status | Camera | Meaning |
|---|---|---|
| OFFLINE | off | Signed out |
| NOT_READY (break, lunch, back-office work) | **off** | Worker chose. No calls. |
| AVAILABLE | **ON** | Ready for calls |
| RINGING | **ON** | Call being offered |
| ON_CALL | **ON** | Talking to robot |
| WRAP_UP | **ON** | Writing notes after call |
| SYSTEM_HOLD | **ON** | Computer stopped worker (see below). Worker must click "I am awake". |

- Camera only ON when worker ready to work. Break = camera off, nobody watch, time not counted as sleep.
- Tray icon always show "Camera ON" or "Camera OFF".
- Press "Available": camera open, quick check (same face? real face?), then status change.
- SYSTEM_HOLD happen after: worker gone 10+ seconds, wrong face, microsleep, or 2 missed calls.
- Worker fall asleep **during** call: call not dropped. After call end, worker go to SYSTEM_HOLD.

### Who get next call?
Server pick worker who wait longest. But only if: status AVAILABLE, camera working (heartbeat every ~5 seconds), face check fresh, not on hold. Worker have 20 seconds to pick up. No pick up = next worker.

### How one call talk (voice loop)
1. Worker pick up. Robot say first line.
2. Worker talk. App know worker stop when quiet for about half a second.
3. Groq Whisper turn voice into words. Groq brain make robot answer. Voice tool say it out loud.
4. Repeat until robot hang up or worker end call.
5. Worker quiet 8 seconds? Robot say "hello? andyan pa po ba kayo?" Test for dead air.

Goal: robot answer within about 3 seconds. **Real test: 3.1 to 3.3 seconds per turn** (it was 5 to 9 before we fixed it). Robot talk, mic muted (take turns). Headset good.
After call: look up fake customer by account number, write notes, pick result (resolved, unresolved, escalated).

### Spike S1 (first job, DONE): test Tagalog
Groq voice cannot speak Tagalog, so robot voice come from Microsoft voices (edge-tts, free, no key). We recorded 24 sentences and tested Groq Whisper:
- English: 3% words wrong. Taglish: 10% wrong. **Tagalog: 24% wrong** (mostly spelling, but short fast phrases sometimes badly misheard).
- A better hint helped Taglish a little. A Tagalog language switch broke English, so we do not use it.
- Maybe later: try Azure speech (has a Filipino model, free 5 hours a month). Not tried.

**Milestone 5 is DONE.** Boss can start a run, robots call, worker answer by voice in the terminal, and the whole call works. Still to do later: one more voice test with more people and call types.

---

## The math (easy version)

### EAR - Eye Aspect Ratio
Eye open = tall. Eye shut = flat.
EAR = how tall eye is divided by how wide eye is.
- Big number = eye open
- Small number = eye shut

**Every person eye different shape**, so we measure each worker once ("calibration") to know *their* open number and shut number. Not one number for everybody.

### Microsleep
Eye shut for **half a second or more** = BIG ALARM. (Can tune to 1 second.)

### Blink count
Normal blink is quick. Count blinks per minute. Compare to worker own normal.
Slow blink (0.3 to 0.5 sec) = "long blink" = sign of tired.

### PERCLOS
Fancy word: "how much of the last minute were eyes mostly shut?"
Over 15% = worker getting DROWSY.

### Yawn
Mouth open wide for 2+ seconds. Worker talk a lot on calls, but talking mouth open short. So short open = talking, not yawn.

### Head tilt and nod
Camera figure out head angle.
- Head tilted sideways more than ~15 degrees for 2 seconds = tilt
- Head drop down then snap back = nod ("head bob")

### Do not cry wolf (false alarms)
- Worker look down at keyboard? Not sleeping. Check if keyboard or mouse just used.
- Head turned far away? Pause or use just the visible eye.

---

## Worker states (tired level)

```
ALERT  ->  DROWSY  ->  MICROSLEEP
 good      getting      BIG ALARM
           tired
```
- Worker must click "I am awake" on alarm.
- If microsleep happen 2+ times in 10 minutes, tell team leader too.

---

## The face-ID part (the clever bit)

### Step 1: Enrollment (one time, per worker)
1. Boss give worker an enrollment code. Worker sign in, say yes (consent), type code.
2. Take ~20 face pictures at different head angles (with and without glasses).
3. Turn into numbers (face "fingerprint"). Keep 5 plus an average.
4. **Calibrate**: 10 seconds eyes open, 3 seconds eyes shut. Learn worker own numbers.
5. Save locked up (encrypted) on server. **Photos not saved, only numbers.**

### Step 2: Login every shift
Password, then face check with random challenge (see "Login" above). Then worker press "Available" and camera start.

### Step 3: During work (the fallback / identity lock)
**Only the enrolled worker face get measured.** Everyone else ignored. This run only when camera is ON.

| What happen | What camera do |
|---|---|
| Other person walk behind worker | Ignore them. |
| Face re-check | Every ~3 seconds, quietly. |
| No right face for 10+ seconds | Say "ABSENT". Pause. Do not count as sleeping. Worker go to SYSTEM_HOLD. |
| Wrong face in the seat 5+ seconds | Alarm boss: "IDENTITY MISMATCH". Face pass cancelled. Worker signed out. |
| No blink for 90 seconds | Suspicious. Maybe photo or video. "SPOOF_SUSPECTED". |
| Fake camera program (OBS, ManyCam, etc.) | Blocked. Only real webcam allowed. |

That last row stop face-swap software that pretend to be a camera.

---

## What gets saved (server database)

20 stone tablets, in 5 piles:

1. **Who** - teams, profiles, workstations (computers), setup_codes (one-time codes)
2. **Face** - face_templates (locked, only server can read), calibration_profiles, face_challenges, face_verifications
3. **Fake call center** - scenarios (problem types), customers (fake), simulation_runs, calls, call_turns (the words said, no voice recording)
4. **Where worker is** - agent_status, agent_status_log
5. **Bad things seen** - sessions, events, metrics_minute, alerts, audit_log (who did what)

Photos not saved. Voice not saved. Face numbers locked.

---

## Folders we will make

```
client/      the app on worker computer (login, call screen, voice, camera brain)
supabase/    the server: tables, guard rules, small server programs
simulation/  the fake callers: problems, moods, prompts, tests
dashboard/   the boss page
research/    thesis experiments and graphs
scripts/     one-time helpers (make first team, download models)
docs/        consent form and privacy notes
```

Face-ID tool and caller-voice tool each hidden behind a "plug". Swap later (InsightFace for free-license SFace, or a different voice tool) without touching rest.

---

## Build order (8 steps)

1. See camera, dots, and live EAR numbers on screen.
2. Teach it to spot microsleep, blinks, yawns, nods. Add calibration.
3. Add face-ID, anti-fake, and identity lock.
4. Build the Supabase base: tables, guard rules, login with face check, link computer.
5. Build the fake call center: Spike S1 first, then call line, worker status, robot callers, voice loop. **DONE (tried live by voice).**
6. Camera turn on only when worker AVAILABLE. Real app with alarm, offline notebook, and .exe. **DONE (tried live: sign-in, camera, call, alarm, break).** Not tried live yet: offline mode, the .exe, more people.
7. Boss dashboard: call line, who sleepy, call words, START button. **DONE. Tested in a real browser and tried by you on localhost.**
8. Test everything. Make graphs. Tune numbers. Write thesis.

---

## How we prove it work (for thesis)

- **Small tests**: fake eye numbers in, check right alarm out.
- **Video tests**: run on public sleepy-driver video sets (Eyeblink8, YawDD, NTHU-DDD, UTA-RLDD). Count right and wrong.
- **Cheat tests**: try photo, phone screen, other person, fake camera. See if it catch them.
- **Guard tests**: team B boss must see nothing of team A. Worker on break or signed out must never get a call.
- **Call tests**: same seed = same calls every time. Measure how fast robot answers.
- **Speed test**: must run at least 15 pictures per second, even while talking.
- **Final demo**:
  - Boss press START. Taglish robot call arrive. Worker answer by voice.
  - Shut eyes 1 second -> alarm, boss see it in about 2 seconds.
  - Worker quiet 8 seconds -> robot say "hello?"
  - Other person walk behind -> ignored.
  - Other person sit down -> IDENTITY MISMATCH.
  - Worker go on break -> camera off.

---

## Danger and warnings

- **InsightFace license**: free for research only. Fine for thesis. Selling it later = need different tool (SFace).
- **Windows install**: InsightFace need Microsoft C++ Build Tools first.
- **Privacy law**: face data is very private (GDPR, Philippine Data Privacy Act RA 10173). Need written consent, encrypt, save numbers not photos, set how long to keep.
- **Groq hear worker voice**: check how long Groq keep data. Write Groq in the consent form. Face numbers never go to Groq.
- **Groq limits**: many workers at once can hit speed limits. Cap number of calls.
- **Tagalog hearing is weak**: Groq mishear Tagalog about 1 word in 4. English and Taglish fine. Robot talking in Tagalog sound natural.
- **Fake-photo check is shaky**: the small model that spots photos gave low scores to the real worker sitting normally and signed them out. Fix: now the app asks the worker to blink or turn the head (about 9 seconds). A photo or video cannot do that on command, so the worker is signed out only if they do not.
- **Talking mouth**: worker talk on calls, so mouth moves a lot. Harder to tell yawn from talk.
- **Modified app**: a worker who hack the app on their own computer could fake reports. We write this as a known limit.
- **Boss login is password only**: easy to add authenticator app later.
- **Supabase free plan**: project sleep after a week of nobody using it.
- **Face numbers live in the cloud**: consent form must say so.
- **Things that still trick it**:
  - Hand over mouth when yawn
  - Very dark room (IR camera help)
  - Very good live deepfake that sneaks past the fake-camera block

---

## Papers to cite

- Soukupova and Cech 2016 - the EAR idea
- Kartynnik et al. 2019 - MediaPipe face dots
- Deng et al. 2019 - ArcFace
- Dinges and Grace 1998 - PERCLOS
- NIST SP 800-63B - rules for login with passwords, codes and faces

---

## Next step

Steps 1 to 7 are built. Step 8 (testing everything for the thesis, graphs, tuning) is next. I start only when you say go.
