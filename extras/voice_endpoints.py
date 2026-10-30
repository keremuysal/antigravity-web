"""
Optional voice bridge endpoints.

Antigravity Web's 🎙️ / 🔊 buttons call a separate voice server at VOICE_SERVER_URL.
If you already run a FastAPI voice server that has `synthesize_turkish_wav(text) -> bytes`
(TTS, e.g. Antalia-2 Mini) and `transcribe_pcm_audio(pcm_bytes, rate) -> str`
(STT, e.g. faster-whisper), append these two routes to it.

Contract:
  POST /tts-only  JSON {"text": "..."}           -> audio/wav
  POST /stt-only  raw 16 kHz mono 16-bit PCM body -> JSON {"text": "..."}
"""
import asyncio

from fastapi import HTTPException, Request
from fastapi.responses import Response

# from your_voice_server import app, synthesize_turkish_wav, transcribe_pcm_audio


@app.post("/tts-only")  # noqa: F821  (app comes from your voice server)
async def tts_only(request: Request):
    data = await request.json()
    text = (data.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text.")
    wav_bytes = await asyncio.to_thread(synthesize_turkish_wav, text)  # noqa: F821
    return Response(content=wav_bytes, media_type="audio/wav")


@app.post("/stt-only")  # noqa: F821
async def stt_only(request: Request):
    raw_pcm = await request.body()
    if len(raw_pcm) < 800:
        return {"text": ""}
    text = await asyncio.to_thread(transcribe_pcm_audio, raw_pcm, 16000)  # noqa: F821
    return {"text": text}
