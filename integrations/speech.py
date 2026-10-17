"""Local speech-to-text for voice prompts (Hindi, Marathi, English) with faster-whisper. Nothing leaves the machine.

Install: ``pip install faster-whisper`` (bundles its own audio decoder, no ffmpeg needed). The model downloads
once on first use. ``WARRANT_STT_MODEL`` picks the size (default ``medium``, ~1.5 GB, ~5 s a clip on CPU;
``small`` is faster but mangles Marathi), ``WARRANT_STT_DEVICE`` is ``auto`` | ``cuda`` | ``cpu``.
"""
from __future__ import annotations

import io
import os
import threading

LANGS = {"hi", "mr", "en"}
_model = None
_model_device = ""
_lock = threading.Lock()


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _load(device: str = ""):
    global _model, _model_device
    with _lock:
        if _model is None or (device and device != _model_device):
            from faster_whisper import WhisperModel
            device = device or os.getenv("WARRANT_STT_DEVICE", "auto")
            _model_device = device
            _model = WhisperModel(os.getenv("WARRANT_STT_MODEL", "medium"), device=device,
                                  compute_type="int8_float16" if device != "cpu" else "int8")
        return _model


def transcribe(audio: bytes, lang: str = "") -> dict:
    """``lang`` is hi | mr | en, or empty to auto-detect (restricted to those three)."""
    try:
        return _transcribe(_load(), audio, lang)  # after a CPU fallback _load() keeps returning the CPU model
    except RuntimeError as exc:
        # A GPU without the CUDA runtime DLLs (cublas, cudnn) fails only at inference; use the CPU from then on.
        if not any(k in str(exc).lower() for k in ("cuda", "cublas", "cudnn")):
            raise
        return _transcribe(_load("cpu"), audio, lang)


def _transcribe(model, audio: bytes, lang: str) -> dict:
    if lang not in LANGS:
        lang = ""
    if not lang:
        # Whisper often mislabels Marathi as Hindi (same script) and accented English as anything; keep it to our three.
        _, _, probs = model.detect_language(audio=_decode(audio))
        lang = max(LANGS, key=lambda c: dict(probs).get(c, 0.0))
    segments, _ = model.transcribe(io.BytesIO(audio), language=lang, beam_size=5, vad_filter=True)
    return {"text": " ".join(s.text.strip() for s in segments).strip(), "lang": lang}


def _decode(audio: bytes):
    from faster_whisper.audio import decode_audio
    return decode_audio(io.BytesIO(audio))
