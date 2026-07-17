"""Concurrent load / soak harness for staging (mock providers).

Usage (server must be running):
  python scripts/load_soak.py --base-url http://127.0.0.1:8000 --sessions 20 --turns 5

Reports success rate and p50/p95 turn latency from response metrics events.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import httpx


async def one_session(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    turns: int,
    chaos: bool,
) -> list[float]:
    headers = {"X-API-Key": api_key}
    created = await client.post("/api/v1/sessions", json={"channel": "simulation"}, headers=headers)
    created.raise_for_status()
    session_id = created.json()["id"]
    latencies: list[float] = []

    phrases = [
        "Where is my order ORD-10482?",
        "I need help with my ticket",
        "What is your return policy?",
        "Thanks that helps",
    ]
    if chaos:
        phrases.append("I want to speak to a human please")

    for i in range(turns):
        text = phrases[i % len(phrases)]
        started = time.perf_counter()
        turn = await client.post(
            f"/api/v1/sessions/{session_id}/turns/text",
            json={"text": text},
            headers=headers,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        if turn.status_code >= 400:
            raise RuntimeError(f"turn failed: {turn.status_code} {turn.text}")
        events = turn.json().get("events") or []
        metrics = next((e for e in events if e.get("type") == "metrics"), None)
        if metrics:
            latencies.append(float(metrics["payload"].get("total_ms", elapsed_ms)))
        else:
            latencies.append(elapsed_ms)
        # Handoff ends the session early in chaos mode.
        if any(e.get("type") == "handoff" for e in events):
            break

    await client.post(f"/api/v1/sessions/{session_id}/end", headers=headers)
    return latencies


async def run(args: argparse.Namespace) -> int:
    headers_ok = 0
    failures = 0
    all_latencies: list[float] = []
    started = time.perf_counter()

    limits = httpx.Limits(max_connections=args.sessions + 10, max_keepalive_connections=args.sessions)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=60.0, limits=limits) as client:
        # Warm health
        health = await client.get("/api/v1/health")
        health.raise_for_status()

        tasks = [
            one_session(client, api_key=args.api_key, turns=args.turns, chaos=args.chaos)
            for _ in range(args.sessions)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                failures += 1
                print(f"FAIL: {result}")
            else:
                headers_ok += 1
                all_latencies.extend(result)

    wall_s = time.perf_counter() - started
    print("\n=== VoiceForge soak report ===")
    print(f"sessions_ok={headers_ok}/{args.sessions} failures={failures} wall_s={wall_s:.2f}")
    if all_latencies:
        all_latencies.sort()
        p50 = statistics.median(all_latencies)
        idx95 = min(len(all_latencies) - 1, int(len(all_latencies) * 0.95))
        p95 = all_latencies[idx95]
        print(
            f"turns={len(all_latencies)} latency_ms p50={p50:.0f} p95={p95:.0f} "
            f"max={max(all_latencies):.0f}"
        )
    return 0 if failures == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="VoiceForge load / soak harness")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default="change-me-in-production")
    parser.add_argument("--sessions", type=int, default=10)
    parser.add_argument("--turns", type=int, default=3)
    parser.add_argument("--chaos", action="store_true", help="Mix in handoff phrases")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
