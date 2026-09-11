# chatterbox-tts

CPU-only TTS HTTP server with **voice cloning** (English via ChatterboxTTS, Hindi + 22 more languages via ChatterboxMultilingualTTS).

This repo packages the working production setup for the **Dharmendra voice clone** (EN + Hindi reference wavs included).

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | – | `{status, model_loaded, model_type, rss_mb}` |
| POST | `/tts` | `{text, lang, exaggeration, cfg_weight, temperature}` | `audio/wav` (16-bit PCM mono, 24kHz) |
| POST | `/clone-setup` | `{audio_wav, lang, exaggeration}` | clone persisted (survives restarts) |
| POST | `/clone-reset` | `{lang}` | reset to builtin voice |

- `lang="en"` → English model. Any other lang code (`hi`, `fr`, …) → multilingual model (23 languages). Only ONE model resident at a time; switching swaps (~2 min on CPU).
- Clone state persists in `current_clone.json` (path via `CLONE_MAP_PATH` env) and is auto re-applied on model load.
- Models lazy-load on first request; unload after `IDLE_UNLOAD_S` (default 600s) idle.

## Requirements

- **RAM: ~6 GB** (MTL model spikes ~5.7 GB during load, ~5 GB steady during synthesis)
- **Disk: ~8 GB** for HF model cache (first-run download)
- CPU-only; RTF ~12–16× on 4-core ARM → 1 min of speech ≈ 12–16 min compute. Batch/overnight jobs, not realtime.

## Run

```bash
# Docker (recommended)
docker build -t chatterbox-tts .
docker run -p 8001:8001 -v chatterbox-hf:/data chatterbox-tts

# Or bare venv (see run_server.sh — installs chatterbox-tts 0.1.7 + torch CPU on first boot)
./run_server.sh
```

## Client

```bash
python client.py "Hello, this is a clone test." test.wav
python client.py "नमस्ते, यह क्लोन टेस्ट है।" test_hi.wav --lang hi
```

## Hosting notes

| Platform | Free tier verdict |
|---|---|
| Render | ❌ free = 512 MB RAM — cannot load the model. Paid `standard` (≥2 CPU/4GB) marginal; recommend 8 GB instance. |
| **Hugging Face Spaces** | ❌ as of 2026-09, Docker & Gradio Spaces on free `cpu-basic` require a **PRO subscription** (API returns 402; only Static Spaces are free). Cannot host this server for free. |
| **Kaggle** | ✅ 30 free GPU/CPU hrs/week — 16 GB RAM CPU instance. |
| Google Colab | ⚠️ free CPU runtime ~13 GB RAM works, but sessions die after 12 h / inactivity. |
| Your own Coolify | ✅ how this was originally run — 6 GB memory cap, models on persistent mount. |

Models are NOT committed — they download from Hugging Face into `HF_HOME` on first start.

## Included voice references

- `voice_ref.wav` — 54.6 s English reference (Dharmendra)
- `voice_ref_hi.wav` — 137 s Hindi reference (Dharmendra)

Both are already wired via a `current_clone.json` you create with `/clone-setup`, e.g.:

```bash
curl -X POST http://localhost:8001/clone-setup \
  -H 'Content-Type: application/json' \
  -d '{"audio_wav": "/app/voice_ref.wav", "lang": "en", "exaggeration": 0.4}'
```