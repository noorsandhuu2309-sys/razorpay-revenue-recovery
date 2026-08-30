"""Microphone recording with VAD-based auto-stop, plus speaker playback."""

import queue

import numpy as np
import sounddevice as sd

from ..config import MAX_RECORD_SECONDS, SILENCE_MS

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)
SILENCE_FRAMES = SILENCE_MS // FRAME_MS
VOICED_START_FRAMES = 3
MAX_FRAMES = int(MAX_RECORD_SECONDS * 1000 / FRAME_MS)
MAX_WAIT_FOR_SPEECH_SECONDS = 20
MAX_WAIT_FRAMES = int(MAX_WAIT_FOR_SPEECH_SECONDS * 1000 / FRAME_MS)

try:
    import webrtcvad

    _vad = webrtcvad.Vad(2)

    def _is_speech(frame_bytes: bytes) -> bool:
        return _vad.is_speech(frame_bytes, SAMPLE_RATE)

except ImportError:

    def _is_speech(frame_bytes: bytes) -> bool:
        frame = np.frombuffer(frame_bytes, dtype=np.int16).astype(np.float32)
        rms = np.sqrt(np.mean(frame**2))
        return rms > 500


def record_until_silence() -> np.ndarray | None:
    """Records from the default mic until speech starts and then trails off
    into ~SILENCE_MS of silence. Returns float32 samples at 16kHz mono, or
    None if no speech was detected within MAX_WAIT_FOR_SPEECH_SECONDS."""
    frame_q: queue.Queue = queue.Queue()

    def callback(indata, frames, time_info, status):
        frame_q.put(bytes(indata))

    frames: list[bytes] = []
    voiced_run = 0
    silence_run = 0
    waited_frames = 0
    speech_started = False

    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=FRAME_SAMPLES,
        dtype="int16",
        channels=1,
        callback=callback,
    ):
        while len(frames) < MAX_FRAMES:
            frame_bytes = frame_q.get()
            is_speech = _is_speech(frame_bytes)

            if not speech_started:
                if is_speech:
                    voiced_run += 1
                    frames.append(frame_bytes)
                    if voiced_run >= VOICED_START_FRAMES:
                        speech_started = True
                else:
                    voiced_run = 0
                    frames.clear()
                    waited_frames += 1
                    if waited_frames >= MAX_WAIT_FRAMES:
                        return None
                continue

            frames.append(frame_bytes)
            if is_speech:
                silence_run = 0
            else:
                silence_run += 1
                if silence_run >= SILENCE_FRAMES:
                    break

    audio_bytes = b"".join(frames)
    audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    return audio


def play_pcm(data: np.ndarray, samplerate: int) -> None:
    sd.play(data, samplerate)
    sd.wait()
