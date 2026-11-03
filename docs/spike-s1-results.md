# Spike S1 results: Tagalog and Taglish speech, caller models, caller voices

Run on 2026-11-03 with one speaker (the project owner), one USB headset microphone, and the Groq and Microsoft services named below. Scripts: `simulation/spikes/`. This is a small test (24 recordings, one speaker), enough to choose a design, not enough for conclusions about speech recognition in general.

## Speech-to-text (Groq Whisper)

24 recordings (8 English, 8 Tagalog, 8 Taglish) of agent-style phrases, scored in six settings. WER is the share of words wrong, CER the share of characters wrong, on normalised text. Lower is better.

| Setting | English | Tagalog | Taglish | Seconds per clip |
|---|---|---|---|---|
| turbo, auto language | 5% / 4% | 24% / 6% | 15% / 8% | 0.40 |
| v3, auto language | 5% / 4% | 28% / 8% | 14% / 7% | 0.45 |
| v3, language hint | 5% / 4% | 28% / 8% | 14% / 7% | 0.49 |
| **v3, auto + prompt** | **3% / 3%** | 24% / 7% | **13% / 7%** | 0.51 |
| v3, hint + prompt | 3% / 3% | 24% / 7% | 13% / 7% | 0.51 |
| turbo, hint + prompt | 5% / 4% | 26% / 6% | 14% / 7% | 0.40 |

(Each cell is WER / CER.)

- The Tagalog word error rate is high mostly because of spelling: Whisper writes "may tutulong" for "maitutulong" and "nya" for "ninyo". The character error rate is only 6 to 8 %, and the meaning survives.
- A language hint made no difference. A short prompt describing the setting (Filipino agent, Taglish, words like po, yung, refund) helped English and Taglish a little.
- **Decision:** `whisper-large-v3`, automatic language detection, with the Taglish prompt (`simulation/spikes/stt_eval.py`, constant `PROMPT`).

## Caller models (Groq chat)

The Llama models listed in Groq's documentation pages are **not** available to this account; the live `GET /models` list is the source of truth. Available chat models: `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`.

All three returned valid JSON in 100 % of replies and answered in 0.3 to 1.5 s. The owner judged naturalness of the Tagalog and Taglish.

- **Decision:** `qwen/qwen3.8-27b` as the caller model (most natural, fastest, 0.3 to 1.1 s per reply), `openai/gpt-oss-120b` as the fallback.
- Weakness found: with the mood set to "frustrated", none of the models sounded frustrated. The prompts need stronger mood instructions.

## Caller voices

- Groq's text-to-speech has no Tagalog (English and Saudi Arabic only), and Windows has no Filipino voice.
- Microsoft's neural voices through the `edge-tts` package cover all three languages: Philippine English (`en-PH-RosaNeural`, `en-PH-JamesNeural`) and Filipino (`fil-PH-BlessicaNeural`, `fil-PH-AngeloNeural`). Synthesis takes about 1 to 2 s for a 5 s sentence. `edge-tts` is an unofficial client of Microsoft's online read-aloud service (no account, no key); Azure AI Speech offers the same voices officially.
- **Decision:** `fil-PH-BlessicaNeural` for Tagalog and Taglish callers (preferred by the owner). The voice provider sits behind a `Synthesizer` interface so Azure can replace `edge-tts` later. Because synthesis takes 1 to 2 s, replies are spoken sentence by sentence.

## Consequences for the design

- Text-to-speech runs **on the agent's PC** (the `edge-tts` voices need no key), so there is no `speak` Edge Function. The key-protecting proxy is needed only for the Groq calls: `caller-turn` and `transcribe`.
- Turn time budget, from the end of the agent's speech: silence detection ~0.7 s + speech-to-text ~0.5 s + caller model ~0.5 s + first sentence of speech ~1 s = about 2.7 s, close to the 3 s target.
