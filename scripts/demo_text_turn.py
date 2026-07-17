#!/usr/bin/env python
"""Quick local demo: create a session and run a text turn."""

from __future__ import annotations

import json
import os
import sys

import httpx

BASE = os.getenv("VOICEFORGE_BASE_URL", "http://127.0.0.1:8000")
API_KEY = os.getenv("API_KEY", "dev")
PREFIX = "/api/v1"


def main() -> int:
    headers = {"X-API-Key": API_KEY}
    with httpx.Client(base_url=BASE, timeout=30.0) as client:
        health = client.get(f"{PREFIX}/health")
        health.raise_for_status()
        print("health:", health.json()["status"])

        created = client.post(
            f"{PREFIX}/sessions",
            headers=headers,
            json={"channel": "simulation", "caller_id": "demo"},
        )
        created.raise_for_status()
        session_id = created.json()["id"]
        print("session:", session_id)

        turn = client.post(
            f"{PREFIX}/sessions/{session_id}/turns/text",
            headers=headers,
            json={"text": "Where is my order ORD-10482?"},
        )
        turn.raise_for_status()
        events = turn.json()["events"]
        print("events:")
        for event in events:
            slim = {k: event[k] for k in ("type", "payload") if k in event}
            if event["type"] == "tts.audio":
                slim["payload"] = {
                    "content_type": event["payload"].get("content_type"),
                    "transcript": event["payload"].get("transcript"),
                    "audio_b64_len": len(event["payload"].get("audio_b64", "")),
                }
            print(json.dumps(slim, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
