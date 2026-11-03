# Privacy notes: what is stored, where, who sees it

Based on how the system is actually built (see `plan.md`). Face templates and voice are the sensitive parts. This is a working summary for the thesis and the consent form, not legal advice.

## The call-desk system (agents and team leaders)

| Data | Where | Who can see it | How long | Notes |
|---|---|---|---|---|
| Face template (512 numbers per photo, about 5 photos) | Supabase Postgres, **encrypted** (AES-256-GCM, key held only as an Edge Function secret) | nobody directly: only the verification function reads it; not even the team leader | until the agent's data is deleted | the template never goes back to a PC; a team leader can delete it (`delete face data`) |
| Calibration (eye and mouth ranges) | Supabase | the agent, their team leader | until deleted | numbers only |
| Face check results (time, match score, revoked or not) | Supabase | the agent, their team leader | indefinite until cleaned up | no images |
| Camera video | **never leaves the agent's PC**; processed in memory | nobody (the agent sees it live on their own screen) | not stored | only events and per-minute averages are sent |
| Fatigue events and per-minute metrics | Supabase | the agent, their team leader | until cleaned up | no faces |
| Call audio | sent through an Edge Function to Groq to be turned into text; **not stored by this system** | Groq (its own retention terms apply) | not stored here | the written transcript is stored |
| Call transcripts and timings | Supabase | the agent, their team leader | until cleaned up | the callers are fictional |
| Audit log (who created accounts, codes, deletions) | Supabase | the team leader | indefinite | |
| Local copies on the PC (`%LOCALAPPDATA%\thesis-microsleep`) | the agent's PC | the Windows user | until removed | the offline queue (events, no faces) and a trace log; the old enrollment tool also saved a local face profile for development |

**Consent.** Agents give consent before enrollment (shown in the sign-in flow); the team leader issues a one-time enrollment code so a stolen password cannot enroll another person's face. Calls are simulated and agents know it.

**Processors.** Supabase (database, logins, functions) and Groq (speech to text and the AI caller's lines); the synthetic voice comes from Microsoft's online read-aloud service through `edge-tts`, which receives only the **caller's** text, not the agent's speech or any personal data. Face data never goes to Groq or Microsoft.

**Where the data is.** The Supabase project runs in Tokyo (ap-northeast-1). The consent form must say templates are stored in the cloud. Self-hosting is the alternative if an ethics board requires it.

**Known limits.** A modified app on a paired PC could fake heartbeats or replay an embedding (the thesis states this). Team leaders sign in with a password only (by choice; Supabase TOTP can be added without schema changes).

## The evaluation (volunteers)

| Data | Where | Who can see it | How long |
|---|---|---|---|
| Fatigue recordings (video, no sound) | researcher's PC, `%LOCALAPPDATA%\thesis-microsleep\eval\fatigue` (outside OneDrive and git) | the researcher | until the retention date in the consent form |
| Face numbers and verification attempts | `...\eval\identity` | the researcher | same |
| Attack trial logs (numbers) | `...\eval\spoof` | the researcher | same |
| Photos or videos of a volunteer for fake-face tests | kept by the researcher only for the tests | the researcher | deleted after the tests |
| Results (tables, charts) | `research/eval/results/` in the project | anyone with the project | no faces; participant codes only |

Participants are named by a code (P01, P02), never by name, in every file.
