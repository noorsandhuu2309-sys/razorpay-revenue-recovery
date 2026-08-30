"""faster-whisper wrapper for local speech-to-text."""

import numpy as np
from faster_whisper import WhisperModel

from ..config import WHISPER_DEVICE, WHISPER_MODEL_SIZE

_model = None


def _load_model():
    global _model
    if _model is not None:
        return _model
    try:
        _model = WhisperModel(WHISPER_MODEL_SIZE, device=WHISPER_DEVICE, compute_type="float16")
    except Exception:
        _model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    return _model


def transcribe(audio: np.ndarray) -> str:
    """Transcribes float32 mono 16kHz samples to text."""
    global _model
    model = _load_model()
    try:
        segments, _ = model.transcribe(audio, language="en")
        return " ".join(segment.text.strip() for segment in segments).strip()
    except RuntimeError:
        # CUDA runtime libraries missing/broken even though the model loaded -
        # rebuild on CPU and retry once.
        _model = None
        model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
        _model = model
        segments, _ = model.transcribe(audio, language="en")
        return " ".join(segment.text.strip() for segment in segments).strip()
