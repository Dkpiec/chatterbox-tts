#!/usr/bin/env python3
"""Chatterbox TTS HTTP server — stdlib only, CPU, lazy model load, EN + multilingual.

Endpoints:
  GET  /health -> {"status":"ok","model_loaded":bool,"model_type":str,"rss_mb":int}
  POST /tts    {"text": "...", "lang": "en"|"hi"|..., "exaggeration":0.4,
                "cfg_weight":0.5, "temperature":0.8}
              -> audio/wav bytes (16-bit PCM mono, 24kHz)
  POST /clone-setup {"audio_wav": "/path/in/mount", "lang": "en"|"hi", "exaggeration":0.4}
  POST /clone-reset {"lang": "en"|"hi"}

- English uses ChatterboxTTS; any other lang uses ChatterboxMultilingualTTS (23 languages).
- Only ONE model is resident at a time (6GB cap); switching drops the other first.
- All synthesis is serialized under one lock (CPU-bound; concurrent mixed-lang would OOM).
- Clones persist in current_clone.json and are re-applied automatically on model load,
  so they survive idle restarts (process self-replace via os.execv).
- Model loads on first request, unloads after 10 min idle.
"""
import json, threading, time, io, wave, os, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socketserver
import sys, types
try:
    import perth
except Exception:
    perth = types.ModuleType("perth")
    sys.modules["perth"] = perth

if not getattr(perth, "PerthImplicitWatermarker", None):
    class DummyWatermarker:
        def __init__(self, *args, **kwargs): pass
        def watermark(self, *args, **kwargs): pass
        def apply(self, *args, **kwargs): pass
        def get_seed(self, *args, **kwargs): return 0
    perth.PerthImplicitWatermarker = getattr(perth, "ImplicitWatermarker", DummyWatermarker)

PORT = int(os.environ.get("PORT", 8001))
IDLE_UNLOAD_S = int(os.environ.get("IDLE_UNLOAD_S", 600))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLONE_MAP_PATH = os.environ.get("CLONE_MAP_PATH", os.path.join(BASE_DIR, "current_clone.json"))
_lock = threading.Lock()          # serializes model swap + synthesis
_models = {}                      # "en" -> ChatterboxTTS, "mtl" -> ChatterboxMultilingualTTS
_last_used = 0.0


def _model_type_for(lang):
    return "en" if (lang or "en").lower() in ("", "en", "eng") else "mtl"


def _load_clone_map():
    try:
        with open(CLONE_MAP_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_clone_map(m):
    try:
        with open(CLONE_MAP_PATH, "w") as f:
            json.dump(m, f, indent=1)
    except Exception as e:
        print("clone map save failed:", e, flush=True)


def get_device():
    env_dev = os.environ.get("DEVICE", "").lower()
    if env_dev:
        return env_dev
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def get_model(model_type="en"):
    """Caller must hold _lock. Loads requested model, dropping the other if resident.
    Re-applies any persisted voice clone for this model type."""
    global _last_used
    if model_type not in _models:
        for other in [k for k in _models if k != model_type]:
            print(f"dropping '{other}' model (swap)", flush=True)
            del _models[other]
            import gc
            gc.collect()
        device = get_device()
        print(f"loading '{model_type}' model on device '{device}'...", flush=True)
        t0 = time.time()
        try:
            import perth
            if not hasattr(perth, "PerthImplicitWatermarker") and hasattr(perth, "ImplicitWatermarker"):
                perth.PerthImplicitWatermarker = perth.ImplicitWatermarker
        except Exception:
            pass
        if model_type == "en":
            from chatterbox.tts import ChatterboxTTS
            _models["en"] = ChatterboxTTS.from_pretrained(device=device)
        else:
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS
            _models["mtl"] = ChatterboxMultilingualTTS.from_pretrained(device=device)
        print(f"'{model_type}' model loaded on {device} in {time.time()-t0:.1f}s", flush=True)
        # auto re-apply persisted clone for this model type
        cm = _load_clone_map().get(model_type)
        if cm and os.path.exists(cm.get("audio_wav", "")):
            try:
                _models[model_type].prepare_conditionals(cm["audio_wav"],
                                                         exaggeration=float(cm.get("exaggeration", 0.4)))
                print(f"clone re-applied for '{model_type}': {cm['audio_wav']}", flush=True)
            except Exception as e:
                print("clone re-apply failed:", e, flush=True)
    _last_used = time.time()
    return _models[model_type]


def unload_if_idle():
    with _lock:
        if _models and time.time() - _last_used > IDLE_UNLOAD_S:
            _models.clear()
            import gc
            gc.collect()
            print("models unloaded (idle)", flush=True)
            # CRITICAL: python/torch do not return RSS to the OS after unload
            # (observed 1.6GB retained). A fresh process is the only clean reset.
            import sys
            os.execv(sys.executable, [sys.executable] + sys.argv)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            # lock-free read (CPython dict/attr reads are atomic enough for status)
            try:
                rss_kb = int(next(l.split()[1] for l in open("/proc/self/status") if l.startswith("VmRSS")))
            except Exception:
                rss_kb = 0
            self._send(200, json.dumps({"status": "ok",
                                        "model_loaded": bool(_models),
                                        "model_type": ",".join(_models) or None,
                                        "rss_mb": round(rss_kb/1024)}).encode())
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        if self.path not in ("/tts", "/clone-setup", "/clone-reset"):
            return self._send(404, b'{"error":"not found"}')
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n))
            lang = str(req.get("lang", "en"))
            model_type = _model_type_for(lang)

            if self.path == "/clone-reset":
                with _lock:
                    m = get_model(model_type)
                    m.conds = None
                    cm = _load_clone_map()
                    cm.pop(model_type, None)
                    _save_clone_map(cm)
                return self._send(200, b'{"status":"clone reset to builtin voice"}')

            if self.path == "/clone-setup":
                audio_path = str(req["audio_wav"])
                with _lock:
                    m = get_model(model_type)
                    t0 = time.time()
                    m.prepare_conditionals(audio_path,
                                           exaggeration=float(req.get("exaggeration", 0.4)))
                    cm = _load_clone_map()
                    cm[model_type] = {"audio_wav": audio_path,
                                      "exaggeration": float(req.get("exaggeration", 0.4))}
                    _save_clone_map(cm)
                return self._send(200, json.dumps(
                    {"status": "voice cloned", "model": model_type, "ref": audio_path,
                     "prep_s": round(time.time()-t0, 1)}).encode())

            # ---- /tts ----
            text = str(req["text"])
            kw = {k: float(req[k]) for k in ("exaggeration", "cfg_weight", "temperature") if k in req}
            with _lock:
                m = get_model(model_type)
                t0 = time.time()
                import numpy as np
                # chunked synthesis: per-sentence generate keeps peak RAM low
                parts = [p.strip() for p in re.split(r'(?<=[.!?])\s+', text) if p.strip()]
                chunks, cur = [], ""
                for p in parts:
                    if len(cur) + len(p) + 1 <= 180:  # 250 -> 180: scene 3 OOMed at 250
                        cur = (cur + " " + p).strip()
                    else:
                        if cur: chunks.append(cur)
                        cur = p
                if cur: chunks.append(cur)
                if not chunks: chunks = [text]

                wavs = []
                for ch in chunks:
                    import gc
                    gc.collect()  # release prior chunk's tensors before next alloc
                    if model_type == "en":
                        w = m.generate(ch, **kw)
                    else:
                        w = m.generate(ch, language_id=lang, **kw)
                    wavs.append(w.cpu().numpy().squeeze())
                    wavs.append(np.zeros(int(0.25 * m.sr), dtype=wavs[0].dtype))
                    del w
                arr = np.concatenate(wavs)
                gen_s = time.time() - t0
                sr = m.sr
                model_info = f"'{model_type}'{'' if model_type=='en' else f' lang={lang}'}"
                print(f"synth [{model_info}]: {len(arr)/sr:.1f}s audio ({len(chunks)} chunks) in {gen_s:.1f}s", flush=True)

            pcm16 = (np.clip(arr, -1.0, 1.0) * 32767).astype("<i2")
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(pcm16.tobytes())
            self._send(200, buf.getvalue(), "audio/wav")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send(500, json.dumps({"error": str(e)[:300]}).encode())

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    try:
        print("cgroup memory.max =", open("/sys/fs/cgroup/memory.max").read().strip(), flush=True)
    except Exception as e:
        print("cgroup read failed:", e, flush=True)

    def reaper():
        while True:
            time.sleep(60)
            unload_if_idle()

    threading.Thread(target=reaper, daemon=True).start()
    socketserver.TCPServer.allow_reuse_address = True
    print(f"Chatterbox TTS server on :{PORT} (en+mtl, lazy load, {IDLE_UNLOAD_S}s idle unload)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()