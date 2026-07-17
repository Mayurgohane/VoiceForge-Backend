from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from websockets.exceptions import ConnectionClosed

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover
    from websockets import connect as ws_connect  # type: ignore

from app.core.exceptions import ProviderError
from app.core.logging import get_logger
from app.services.stt.base import TranscriptResult

logger = get_logger(__name__)

TranscriptHandler = Callable[[TranscriptResult], Awaitable[None]]


@dataclass(slots=True)
class DeepgramLiveConfig:
    api_key: str
    model: str = "nova-2"
    language: str = "en"
    encoding: str = "mulaw"
    sample_rate: int = 8000
    channels: int = 1
    endpointing_ms: int = 300
    interim_results: bool = True
    punctuate: bool = True
    smart_format: bool = True
    utterance_end_ms: int = 1000
    max_reconnect_attempts: int = 3


@dataclass
class _UtteranceBuffer:
    parts: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def add_final_segment(self, text: str, confidence: float) -> None:
        if text:
            self.parts.append(text)
            self.confidence = max(self.confidence, confidence)

    def preview(self, interim: str = "") -> str:
        chunks = list(self.parts)
        if interim:
            chunks.append(interim)
        return " ".join(chunks).strip()

    def flush(self, extra: str = "") -> tuple[str, float]:
        if extra:
            self.parts.append(extra)
        text = " ".join(self.parts).strip()
        confidence = self.confidence
        self.parts.clear()
        self.confidence = 0.0
        return text, confidence


class DeepgramLiveSession:
    """Bidirectional Deepgram live transcription over WebSocket."""

    def __init__(self, config: DeepgramLiveConfig) -> None:
        if not config.api_key:
            raise ProviderError("DEEPGRAM_API_KEY is required for Deepgram live STT")
        self._config = config
        self._ws: Any = None
        self._receiver_task: asyncio.Task[None] | None = None
        self._on_transcript: TranscriptHandler | None = None
        self._closed = asyncio.Event()
        self._started_at = 0.0
        self._utterance = _UtteranceBuffer()
        self._reconnect_attempts = 0

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._closed.is_set()

    def _url(self) -> str:
        params = {
            "model": self._config.model,
            "language": self._config.language,
            "encoding": self._config.encoding,
            "sample_rate": str(self._config.sample_rate),
            "channels": str(self._config.channels),
            "punctuate": str(self._config.punctuate).lower(),
            "smart_format": str(self._config.smart_format).lower(),
            "interim_results": str(self._config.interim_results).lower(),
            "endpointing": str(self._config.endpointing_ms),
            "utterance_end_ms": str(self._config.utterance_end_ms),
            "vad_events": "true",
        }
        return f"wss://api.deepgram.com/v1/listen?{urlencode(params)}"

    async def start(self, on_transcript: TranscriptHandler) -> None:
        self._on_transcript = on_transcript
        self._started_at = time.perf_counter()
        self._closed.clear()
        await self._connect()

    async def _connect(self) -> None:
        self._ws = await ws_connect(
            self._url(),
            additional_headers={"Authorization": f"Token {self._config.api_key}"},
            max_size=8 * 1024 * 1024,
        )
        self._receiver_task = asyncio.create_task(self._receive_loop(), name="deepgram-receiver")
        logger.info("deepgram_live_connected", model=self._config.model)

    async def send_audio(self, audio: bytes) -> None:
        if not self._ws or self._closed.is_set():
            return
        try:
            await self._ws.send(audio)
        except Exception:  # noqa: BLE001
            logger.warning("deepgram_send_failed_attempting_reconnect")
            await self._try_reconnect()
            if self._ws and not self._closed.is_set():
                await self._ws.send(audio)

    async def finalize(self) -> None:
        if not self._ws or self._closed.is_set():
            return
        try:
            await self._ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:  # noqa: BLE001
            logger.debug("deepgram_close_stream_failed")

    async def close(self) -> None:
        self._closed.set()
        if self._receiver_task:
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass
            self._receiver_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None
        logger.info("deepgram_live_closed")

    async def _try_reconnect(self) -> None:
        if self._closed.is_set():
            return
        if self._reconnect_attempts >= self._config.max_reconnect_attempts:
            logger.error("deepgram_reconnect_exhausted")
            return
        self._reconnect_attempts += 1
        delay = min(2 ** self._reconnect_attempts, 8)
        await asyncio.sleep(delay)
        try:
            if self._receiver_task:
                self._receiver_task.cancel()
                try:
                    await self._receiver_task
                except asyncio.CancelledError:
                    pass
            if self._ws is not None:
                try:
                    await self._ws.close()
                except Exception:  # noqa: BLE001
                    pass
            await self._connect()
            self._reconnect_attempts = 0
            logger.info("deepgram_reconnected")
        except Exception:  # noqa: BLE001
            logger.exception("deepgram_reconnect_failed")

    async def _receive_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if self._closed.is_set():
                    break
                if isinstance(raw, bytes):
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                result = self._parse_message(message)
                if result and self._on_transcript:
                    await self._on_transcript(result)
        except ConnectionClosed:
            logger.info("deepgram_live_connection_closed")
            if not self._closed.is_set():
                await self._try_reconnect()
                return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("deepgram_live_receive_error")
        finally:
            if self._closed.is_set():
                return

    def _parse_message(self, message: dict[str, Any]) -> TranscriptResult | None:
        msg_type = message.get("type")
        latency_ms = int((time.perf_counter() - self._started_at) * 1000)

        if msg_type == "Results":
            channel = message.get("channel") or {}
            alts = channel.get("alternatives") or []
            if not alts:
                return None
            alt = alts[0]
            text = (alt.get("transcript") or "").strip()
            confidence = float(alt.get("confidence") or 0.0)
            segment_final = bool(message.get("is_final"))
            speech_final = bool(message.get("speech_final"))

            # Interim: barge-in / UI only — never start an agent turn.
            if not segment_final and not speech_final:
                if not text and not self._utterance.parts:
                    return None
                return TranscriptResult(
                    text=self._utterance.preview(text),
                    is_final=False,
                    confidence=confidence,
                    latency_ms=latency_ms,
                    speech_final=False,
                )

            # Finalized segment within an utterance — accumulate, still not a turn.
            if segment_final and not speech_final:
                self._utterance.add_final_segment(text, confidence)
                preview = self._utterance.preview()
                if not preview:
                    return None
                return TranscriptResult(
                    text=preview,
                    is_final=False,
                    confidence=self._utterance.confidence or confidence,
                    latency_ms=latency_ms,
                    speech_final=False,
                )

            # End of spoken utterance → agent turn.
            if speech_final:
                full, conf = self._utterance.flush(text)
                if not full:
                    return None
                return TranscriptResult(
                    text=full,
                    is_final=True,
                    confidence=conf or confidence,
                    latency_ms=latency_ms,
                    speech_final=True,
                )
            return None

        if msg_type == "UtteranceEnd":
            full, conf = self._utterance.flush()
            if not full:
                return None
            return TranscriptResult(
                text=full,
                is_final=True,
                confidence=conf,
                latency_ms=latency_ms,
                speech_final=True,
            )

        if msg_type == "Error":
            logger.error("deepgram_live_error", payload=message)
        return None


async def iter_deepgram_transcripts(
    session: DeepgramLiveSession,
) -> AsyncIterator[TranscriptResult]:
    queue: asyncio.Queue[TranscriptResult | None] = asyncio.Queue()

    async def _handler(result: TranscriptResult) -> None:
        await queue.put(result)

    await session.start(_handler)
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
    finally:
        await session.close()
