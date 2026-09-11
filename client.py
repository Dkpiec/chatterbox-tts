#!/usr/bin/env python3
"""Minimal CLI client: chatterbox-tts.py "text" out.wav [--lang hi] [--text-file f]"""
import argparse, json, urllib.request

def main():
    p = argparse.ArgumentParser()
    p.add_argument("text", nargs="?", default="")
    p.add_argument("out", nargs="?", default="out.wav")
    p.add_argument("--lang", default="en")
    p.add_argument("--exaggeration", type=float, default=0.4)
    p.add_argument("--text-file", default=None)
    p.add_argument("--base", default="http://127.0.0.1:8001")
    a = p.parse_args()
    text = open(a.text_file).read() if a.text_file else a.text
    body = json.dumps({"text": text, "lang": a.lang, "exaggeration": a.exaggeration}).encode()
    req = urllib.request.Request(a.base + "/tts", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3600) as r:
        wav = r.read()
    open(a.out, "wb").write(wav)
    print(f"wrote {len(wav)} bytes to {a.out}")

if __name__ == "__main__":
    main()