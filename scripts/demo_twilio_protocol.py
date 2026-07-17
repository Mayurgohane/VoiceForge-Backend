#!/usr/bin/env python
"""Simulate Twilio Media Stream events against a running VoiceForge server.

This does NOT call Deepgram/Twilio cloud — it only validates our WS protocol shape.
For a full E2E phone test, use a real Twilio number + Deepgram key + ngrok.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys

import httpx
import websockets

BASE = os.getenv("VOICEFORGE_BASE_URL", "http://127.0.0.1:8000")
API_KEY = os.getenv("API_KEY", "change-me-in-production")
PREFIX = "/api/v1"


async def main() -> int:
    # Create a session the same way the voice webhook would.
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as client:
        created = await client.post(
            f"{PREFIX}/sessions",
            headers={"X-API-Key": API_KEY},
            json={"channel": "twilio", "caller_id": "+15550001111"},
        )
        created.raise_for_status()
        session_id = created.json()["id"]
        print("session:", session_id)

    # Browser WS path still works without Deepgram for text turns.
    ws_url = BASE.replace("http://", "ws://").replace("https://", "wss://")
    uri = f"{ws_url}{PREFIX}/ws/voice/{session_id}?api_key={API_KEY}"
    async with websockets.connect(uri) as ws:
        print(await ws.recv())
        await ws.send(
            json.dumps(
                {
                    "type": "control",
                    "action": "text_turn",
                    "text": "Where is my order ORD-10482?",
                }
            )
        )
        for _ in range(8):
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(msg)
            print("event:", data.get("type"))
            if data.get("type") in {"metrics", "handoff", "error"}:
                break
    print("done")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001
        print("demo failed:", exc, file=sys.stderr)
        raise
