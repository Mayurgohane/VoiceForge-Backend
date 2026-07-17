from __future__ import annotations

import audioop
import io
import wave
from array import array


def _even_pcm16(pcm16le: bytes) -> bytes:
    if len(pcm16le) % 2 == 1:
        return pcm16le[:-1]
    return pcm16le


def mulaw_decode(mulaw: bytes) -> bytes:
    """μ-law 8-bit → PCM16LE."""
    return audioop.ulaw2lin(mulaw, 2)


def mulaw_encode(pcm16le: bytes) -> bytes:
    """PCM16LE → μ-law 8-bit."""
    return audioop.lin2ulaw(_even_pcm16(pcm16le), 2)


def resample_pcm16(pcm16le: bytes, *, src_rate: int, dst_rate: int) -> bytes:
    pcm16le = _even_pcm16(pcm16le)
    if src_rate == dst_rate:
        return pcm16le
    converted, _ = audioop.ratecv(pcm16le, 2, 1, src_rate, dst_rate, None)
    return converted


def pcm16_to_twilio_mulaw(pcm16le: bytes, *, src_rate: int = 16000) -> bytes:
    """Convert PCM16 to Twilio Media Streams format (μ-law @ 8 kHz)."""
    pcm8k = resample_pcm16(pcm16le, src_rate=src_rate, dst_rate=8000)
    return mulaw_encode(pcm8k)


def wav_bytes_to_pcm16(wav_bytes: bytes) -> tuple[bytes, int]:
    """Extract mono PCM16 and sample rate from a WAV container."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

    if sample_width != 2:
        frames = audioop.lin2lin(frames, sample_width, 2)
    if channels > 1:
        frames = audioop.tomono(frames, 2, 0.5, 0.5)
    return _even_pcm16(frames), sample_rate


def audio_to_twilio_payload(audio: bytes, content_type: str) -> bytes:
    """Normalize TTS output into Twilio μ-law 8 kHz bytes."""
    ctype = content_type.lower()
    if "wav" in ctype or audio[:4] == b"RIFF":
        pcm, rate = wav_bytes_to_pcm16(audio)
        return pcm16_to_twilio_mulaw(pcm, src_rate=rate)
    if "mulaw" in ctype or "ulaw" in ctype:
        return audio
    return pcm16_to_twilio_mulaw(_even_pcm16(audio), src_rate=16000)


def split_mulaw_for_twilio(mulaw: bytes, *, frame_ms: int = 20) -> list[bytes]:
    """Chunk μ-law audio into ~20ms frames (160 bytes @ 8kHz)."""
    frame_size = int(8000 * (frame_ms / 1000.0))
    if frame_size <= 0:
        return [mulaw]
    return [mulaw[i : i + frame_size] for i in range(0, len(mulaw), frame_size)]


def rms_level(pcm16le: bytes) -> float:
    pcm16le = _even_pcm16(pcm16le)
    if not pcm16le:
        return 0.0
    samples = array("h")
    samples.frombytes(pcm16le)
    if not samples:
        return 0.0
    total = sum(s * s for s in samples)
    return (total / len(samples)) ** 0.5
