"""Piper wrapper for local text-to-speech."""

import re
from pathlib import Path

import numpy as np
from piper import PiperVoice

from ..config import PIPER_VOICE, VOICE_MODELS_DIR
from .audio_io import play_pcm

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MODEL_PATH = _PROJECT_ROOT / VOICE_MODELS_DIR / f"{PIPER_VOICE}.onnx"
_CONFIG_PATH = _PROJECT_ROOT / VOICE_MODELS_DIR / f"{PIPER_VOICE}.onnx.json"

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

_voice = None


def _load_voice() -> PiperVoice:
    global _voice
    if _voice is not None:
        return _voice
    if not _MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Piper voice model not found at {_MODEL_PATH}. "
            f"Expected it alongside {_CONFIG_PATH}."
        )
    _voice = PiperVoice.load(str(_MODEL_PATH), config_path=str(_CONFIG_PATH))
    return _voice


def synthesize(text: str) -> tuple[np.ndarray, int]:
    """Synthesizes text to float32 PCM samples. Returns (samples, sample_rate)."""
    voice = _load_voice()
    chunks = list(voice.synthesize(text))
    if not chunks:
        return np.zeros(0, dtype=np.float32), 22050
    sample_rate = chunks[0].sample_rate
    audio = np.concatenate([chunk.audio_float_array for chunk in chunks])
    return audio, sample_rate


def speak(text: str) -> None:
    """Synthesizes and plays text sentence-by-sentence so playback starts
    as soon as the first sentence is ready, instead of waiting for the
    whole response to be synthesized."""
    text = text.strip()
    if not text:
        return
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    if not sentences:
        sentences = [text]
    for sentence in sentences:
        audio, sample_rate = synthesize(sentence)
        if audio.size:
            play_pcm(audio, sample_rate)
