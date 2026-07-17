# VoiceForge

**Real-time voice agent backend** for live phone and browser conversations.

VoiceForge turns speech (or text) into agent replies with tools, then speaks the answer back — with barge-in, PII redaction, human handoff, session persistence, and Twilio telephony support.

```text
Audio / Text  →  STT  →  Tool-calling Agent  →  TTS  →  Client
```

| | |
|---|---|
| **Stack** | Python 3.11 · FastAPI · SQLAlchemy · Redis · Uvicorn |
| **Voice** | Deepgram Live STT · Google TTS · Gemini |
| **Telephony** | Twilio Voice + Media Streams |
| **License** | MIT |

---

## Table of contents

1. [How it works](#how-it-works)
2. [Architecture](#architecture)
3. [Features](#features)
4. [Project layout](#project-layout)
5. [Quick start](#quick-start)
6. [API surface](#api-surface)
7. [WebSocket protocol](#websocket-protocol)
8. [Twilio phone setup](#twilio-phone-setup)
9. [Configuration](#configuration)
10. [Docker](#docker)
11. [Security model](#security-model)
12. [Observability](#observability)
13. [Development](#development)
14. [Production checklist](#production-checklist)
15. [Roadmap](#roadmap)
16. [License](#license)

---

## How it works

Every conversation is a **session**. A turn is one user utterance → agent reply cycle.

### Turn pipeline

```text
1. Input arrives          audio chunk / text / Twilio μ-law frame
2. Speech-to-text         Deepgram Live (phone) or REST/mock (browser)
3. PII redaction          emails, phones, SSN-like, card-like patterns
4. Handoff policy         user asks for human · low confidence · tool failure · max turns
5. Agent + tools          mock keyword agent or Gemini + CRM / tickets / knowledge
6. Text-to-speech         Google TTS or mock WAV
7. Events emitted         transcript · tool.result · tts.audio · metrics · handoff
```

### Three client paths

| Path | Who uses it | Entry |
|------|-------------|--------|
| **HTTP text turn** | Demos, integration tests | `POST /sessions/{id}/turns/text` |
| **Browser WebSocket** | Web / sim clients | `WS /ws/voice/{session_id}` |
| **Twilio Media Stream** | Real phone calls | `WS /twilio/media-stream/{session_id}` |

All three share the same `ConversationPipeline` — telephony only adds audio bridging and Deepgram Live.

### Phone call (happy path)

1. Caller dials your Twilio number.
2. Twilio hits `POST /api/v1/twilio/voice`.
3. VoiceForge creates a session and returns TwiML with `<Connect><Stream>`.
4. Twilio opens a bidirectional Media Stream WebSocket (signed token).
5. Inbound μ-law audio is forwarded to **Deepgram Live**.
6. On final transcript, the pipeline runs agent + tools + TTS.
7. Reply audio is converted to μ-law and streamed back to the caller.
8. If the caller speaks while the agent is talking, **barge-in** clears playback.
9. Silence watchdog nudges a few times, then ends the call if still quiet.
10. Status callback / disconnect ends the session cleanly.

### Human handoff

Handoff triggers when:

- the user asks for a human (“real person”, “speak to a human”, …)
- STT/agent confidence is below threshold
- a tool call fails
- the conversation hits the max-turn policy

Optional **warm transfer**: park the caller in a Twilio conference, dial an agent with a whispered summary, then join both.

---

## Architecture

### System view

```text
                    ┌─────────────────────────────────────────┐
                    │              VoiceForge API             │
                    │                                         │
  Browser / sim ───►│  /ws/voice/{session}                    │
                    │           │                             │
  HTTP client   ───►│  /sessions …/turns/text                 │
                    │           │                             │
  Twilio Voice  ───►│  /twilio/voice  →  Media Stream WSS     │
                    │           │              │              │
                    │           ▼              ▼              │
                    │     ConversationPipeline                │
                    │      STT → redact → handoff             │
                    │           → agent/tools → TTS           │
                    │           │                             │
                    │     SessionManager                      │
                    │      Redis (hot) + Postgres/SQLite      │
                    └───────────┬─────────────────────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
         Deepgram           Gemini            Google TTS
         (Live STT)         (LLM)             (optional)
```

### Internal layers

```text
app/
├── api/              HTTP + WebSocket routes, DI container
├── core/             settings, auth, logging, exceptions
├── domain/           session / turn / event models
├── schemas/          request/response contracts
├── services/
│   ├── conversation_pipeline.py   ← orchestrates a turn
│   ├── session_manager.py         ← lifecycle + persistence
│   ├── handoff.py / redaction.py
│   ├── agent/                     VoiceAgent (mock | Gemini)
│   ├── stt/                       mock | Deepgram REST | Deepgram Live
│   ├── tts/                       mock | Google
│   ├── telephony/                 Media bridge, μ-law, tokens, warm transfer
│   └── tools/                     CRM, tickets, knowledge (+ authz)
└── infrastructure/   DB, Redis, ORM, Prometheus / OTEL
```

### Design choices

| Concern | Approach |
|---------|----------|
| Latency | Async FastAPI; Deepgram Live for phone; barge-in drops in-flight TTS |
| Consistency | Per-connection turn locks (browser WS + Twilio bridge) |
| Durability | Redis for hot session state; Postgres/SQLite for transcript + audit |
| Safety | API keys, Twilio signatures, HMAC stream tokens, tool authz + rate limits |
| Privacy | PII redacted before storage **and** before the LLM |
| Ops | `/health`, `/ready`, `/metrics`, structured logs, optional OpenTelemetry |

---

## Features

- **Real-time turns** — STT → agent/tools → TTS with event streaming
- **Barge-in** — caller can interrupt agent speech (Twilio `clear` + pipeline flags)
- **PII redaction** — email / phone / SSN-like / card-like patterns
- **Human handoff** — policy-driven; optional warm transfer to a live agent
- **Tool calling** — demo CRM lookup, tickets, knowledge search with caller binding + rate limits
- **Multi-channel** — simulation, WebSocket, Twilio
- **Session store** — Redis cache with DB fallback (including CallSid lookup)
- **Stream security** — signed Media Stream tokens; same token may reconnect after a network blip
- **Silence handling** — repeated nudges, then polite call end
- **Observability** — Prometheus metrics, optional OTEL traces, structured logging

> **Note:** CRM / ticket / knowledge tools ship as **in-memory demos**. Swap them for your real systems before production. Defaults can run fully on **mock** STT/TTS/LLM for local development.

---

## Project layout

```text
VoiceForge-Backend/
├── app/                    Application code
├── tests/                  Pytest suite
├── scripts/                Demo helpers (text turn, Twilio protocol)
├── .github/workflows/      CI (lint, test, Docker build)
├── Dockerfile
├── docker-compose.yml      api + Postgres 16 + Redis 7
├── Makefile
├── pyproject.toml
├── requirements.txt        Exact pinned dependencies
├── .env.example
└── README.md
```

---

## Quick start

### Prerequisites

- Python **3.11+** (3.10 may work; CI/Dockerfile target 3.11)
- Optional: Docker, Redis, ngrok (for Twilio)

### Local (mock providers)

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env          # Windows
# cp .env.example .env          # macOS / Linux

make run
# or: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open interactive docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

### Demo turn (no microphone)

```bash
# terminal 1 — server running
# terminal 2
make demo
# or: python scripts/demo_text_turn.py
```

### Manual HTTP demo

```bash
# Create session
curl -X POST http://127.0.0.1:8000/api/v1/sessions \
  -H "X-API-Key: change-me-in-production" \
  -H "Content-Type: application/json" \
  -d "{\"channel\":\"simulation\"}"

# Send a text turn (replace SESSION_ID)
curl -X POST http://127.0.0.1:8000/api/v1/sessions/SESSION_ID/turns/text \
  -H "X-API-Key: change-me-in-production" \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"Where is my order ORD-10482?\"}"
```

---

## API surface

Base prefix: `/api/v1`  
Auth: `X-API-Key` header (dev allows missing/`dev` when not production).

### Health & root

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Service info |
| GET | `/metrics` | Prometheus metrics |
| GET | `/api/v1/health` | Liveness |
| GET | `/api/v1/ready` | Readiness (deps) |

### Sessions

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/sessions` | Create session |
| GET | `/api/v1/sessions` | List recent sessions |
| GET | `/api/v1/sessions/{id}` | Get session |
| POST | `/api/v1/sessions/{id}/end` | End session |
| POST | `/api/v1/sessions/{id}/turns/text` | Run a text turn |

### Voice WebSocket

| | Path |
|--|------|
| WS | `/api/v1/ws/voice/{session_id}` |

### Twilio

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/twilio/voice` | Incoming call → TwiML + Media Stream |
| POST | `/api/v1/twilio/status` | Call status → end session |
| POST | `/api/v1/twilio/transfer/{id}/caller` | Warm-transfer caller TwiML |
| POST | `/api/v1/twilio/transfer/{id}/agent` | Warm-transfer agent TwiML |
| WS | `/api/v1/twilio/media-stream/{id}` | Bidirectional audio bridge |

---

## WebSocket protocol

### Connect

```text
ws://127.0.0.1:8000/api/v1/ws/voice/{session_id}?api_key=YOUR_KEY
```

Prefer `X-API-Key` header when the client supports it (query keys can leak in logs/proxies).

Create a session first via `POST /api/v1/sessions`.

### Client → server

```json
{"type":"control","action":"text_turn","text":"I need help with my order"}
```

```json
{"type":"audio.chunk","data":"<base64>","sequence":1,"is_final":true}
```

```json
{"type":"control","action":"barge_in"}
```

```json
{"type":"control","action":"ping"}
```

```json
{"type":"control","action":"end"}
```

Raw binary frames are treated as final audio chunks. Plain non-JSON text is treated as a text turn.

Turns are **serialized with a lock** so back-to-back messages cannot interleave LLM/TTS. Barge-in and ping bypass the lock.

### Server → client events

| Event | Meaning |
|-------|---------|
| `session.started` | Socket ready |
| `transcript.partial` / `transcript.final` | STT output |
| `agent.thinking` / `agent.response` | Agent progress |
| `tool.result` | Tool execution result |
| `tts.audio` | Base64 audio + content type |
| `barge_in` | Playback cancelled / interrupt |
| `handoff` | Human transfer requested |
| `metrics` | Per-turn latency breakdown |
| `error` | Recoverable message error |
| `session.ended` | Client requested end |

---

## Twilio phone setup

### 1. Environment

```env
APP_ENV=development
API_KEY=use-a-long-random-secret
STT_PROVIDER=deepgram
DEEPGRAM_API_KEY=your_deepgram_key
DEEPGRAM_MODEL=nova-2
TTS_PROVIDER=google          # or mock for early tests
LLM_PROVIDER=gemini          # or mock
GOOGLE_API_KEY=...
TWILIO_ACCOUNT_SID=ACxxxx
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_PHONE_NUMBER=+1...
PUBLIC_BASE_URL=https://YOUR_SUBDOMAIN.ngrok-free.app
```

### 2. Expose locally

```bash
ngrok http 8000
```

Set `PUBLIC_BASE_URL` to the ngrok **HTTPS** URL (no trailing slash issues — path is joined by the app).

### 3. Twilio console

1. Use a Twilio voice number.
2. **A call comes in** webhook (POST):  
   `https://YOUR_SUBDOMAIN.ngrok-free.app/api/v1/twilio/voice`
3. Optional status callback:  
   `https://YOUR_SUBDOMAIN.ngrok-free.app/api/v1/twilio/status`

### 4. What happens on a call

```text
Caller
  → Twilio Voice webhook (/twilio/voice)
  → TwiML <Say> greeting + <Connect><Stream>
  → WSS /twilio/media-stream/{session}?token=…
  → μ-law 8 kHz → Deepgram Live
  → final transcript → pipeline → TTS
  → μ-law frames back on the Media Stream
```

Media Stream URLs use an **HMAC-signed token**. First connect binds the session; the **same token can reconnect** after a blip. A different token for that session is rejected.

### Warm transfer (optional)

```env
WARM_TRANSFER_ENABLED=true
TWILIO_AGENT_NUMBER=+1...    # human agent or queue
```

On handoff:

1. Caller is redirected into a conference (`/twilio/transfer/{session}/caller`).
2. Agent is dialed with a whispered summary (`/twilio/transfer/{session}/agent`).
3. Agent joins the same conference.

---

## Configuration

Copy `.env.example` → `.env`. Important knobs:

| Variable | Default | Meaning |
|----------|---------|---------|
| `APP_ENV` | `development` | `development` / `staging` / `production` |
| `API_KEY` | (example) | Client auth; must be strong in production |
| `DATABASE_URL` | SQLite | Use Postgres async URL in real deploys |
| `REDIS_URL` | localhost | Hot sessions + stream tokens + rate limits |
| `STT_PROVIDER` | `mock` / example | `mock` · `deepgram` · `google` (stub→mock) |
| `TTS_PROVIDER` | `mock` | `mock` · `google` (`elevenlabs` reserved, not wired) |
| `LLM_PROVIDER` | `mock` | `mock` · `gemini` |
| `DEEPGRAM_API_KEY` | — | Required for Twilio Media Streams |
| `PUBLIC_BASE_URL` | — | Public HTTPS base for TwiML + WSS |
| `TWILIO_AUTH_TOKEN` | — | Validates webhook signatures (skipped only in dev if empty) |
| `BARGE_IN_ENABLED` | `true` | Allow interrupts |
| `ENABLE_PII_REDACTION` | `true` | Redact before DB + LLM |
| `HANDOFF_CONFIDENCE_THRESHOLD` | `0.35` | Auto handoff below this |
| `SILENCE_TIMEOUT_SECONDS` | `45` | Idle nudge window |
| `MAX_TURN_LATENCY_MS` | `1500` | SLA warning log threshold |
| `TOOL_AUTHZ_ENABLED` | `true` | Enforce tool caller binding |
| `TOOL_RATE_LIMIT_PER_MINUTE` | `20` | Per-session tool rate limit |
| `OTEL_ENABLED` | `false` | Export traces |
| `PROMETHEUS_ENABLED` | `true` | Expose `/metrics` |

---

## Migrations (Alembic)

Schema is managed with **Alembic**. With `AUTO_MIGRATE=true` (default in development), the API upgrades to `head` on startup.

```bash
make migrate          # alembic upgrade head
make migrate-down     # alembic downgrade -1
make migrate-stamp    # stamp existing DB created via create_all
```

Initial revision: `001_initial` (`voice_sessions`, `call_events`, `audit_logs`).

`/api/v1/health` reports `database_revision` and pool stats. In **staging/production**, `/ready` returns **503** if Redis is in memory fallback mode.

---

## Providers

| Kind | Options |
|------|---------|
| STT | `mock` · `deepgram` · `google` (Cloud Speech REST) |
| TTS | `mock` · `google` · `elevenlabs` |
| LLM | `mock` · `gemini` |

```env
STT_PROVIDER=google
TTS_PROVIDER=elevenlabs
GOOGLE_API_KEY=...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM
```

Twilio Media Streams still require **Deepgram Live** (`DEEPGRAM_API_KEY`) for low-latency phone STT.

---

## Load / chaos / soak

In-process soak tests:

```bash
pytest -q -m soak
```

Against a running server:

```bash
# terminal 1
make run

# terminal 2
make soak
# or: python scripts/load_soak.py --sessions 20 --turns 5 --chaos
```

Reports success rate and turn latency p50/p95.

---

```bash
copy .env.example .env
make docker-up
# or: docker compose up --build -d
```

Compose runs:

- **api** — VoiceForge on `:8000` (mock providers by default in compose)
- **postgres:16**
- **redis:7**

Tear down:

```bash
make docker-down
```

---

## Security model

| Layer | Behavior |
|-------|----------|
| **API key** | `X-API-Key` on HTTP/session routes; WS accepts header or query |
| **Production guard** | Refuses weak/short API keys when `APP_ENV=production` |
| **Twilio webhooks** | `X-Twilio-Signature` validated (candidate URLs for proxy/ngrok) |
| **Media Stream** | HMAC token + Redis binding; reconnect with same token only |
| **Tools** | Caller-ID bind for CRM phone/email; session-scoped tickets; rate limits |
| **PII** | Redacted in persisted turns and LLM input |
| **CORS** | Configurable allow-list |

Never commit `.env`. Rotate `API_KEY`, Twilio tokens, and provider keys regularly.

---

## Observability

- **Logs** — `structlog` JSON-friendly events (`session_created`, `turn_latency_sla_miss`, …)
- **Metrics** — Prometheus at `/metrics` (sessions, turns, handoffs, latency histograms)
- **Tracing** — set `OTEL_ENABLED=true` and point `OTEL_EXPORTER_OTLP_ENDPOINT` at your collector

---

## Development

```bash
make install    # pip install -r requirements.txt
make run        # uvicorn --reload :8000
make test       # pytest -q
make lint       # ruff check app tests
make format     # ruff format app tests
make demo       # scripts/demo_text_turn.py
```

CI (GitHub Actions) on push/PR:

1. Ruff lint  
2. Pytest with coverage  
3. Docker image build  

Dependencies are **exact-pinned** in `requirements.txt` for reproducible installs.

---

## Production checklist

This repo is a strong **MVP / pilot** foundation. Before public production:

- [ ] Commit and tag a release; run CI green on `main`
- [ ] `APP_ENV=production` with a strong unique `API_KEY` (16+ chars)
- [ ] Postgres + managed Redis (no SQLite / in-memory Redis fallback)
- [ ] Real `DEEPGRAM_API_KEY`, LLM, and TTS providers (not mock)
- [ ] `PUBLIC_BASE_URL` on a stable HTTPS host (not a disposable ngrok URL)
- [ ] Twilio signature validation with real `TWILIO_AUTH_TOKEN`
- [ ] TLS terminator in front of HTTP + WebSockets
- [ ] Restrict `CORS_ORIGINS`
- [ ] Replace demo tools with real CRM / ticketing / knowledge backends
- [ ] Enable OTEL + alert on `/ready` and latency SLA misses
- [ ] Load-test Media Streams (concurrent calls, barge-in, reconnect)
- [ ] Backup/restore plan for Postgres; Redis persistence/HA as needed

---

## Roadmap

- [x] Deepgram Live WebSocket STT for Twilio
- [x] Twilio Media Streams bridge + barge-in
- [x] Warm transfer helpers
- [x] CI pipeline + pinned dependencies
- [x] Alembic migrations + DB/Redis ops hardening
- [x] Google STT + ElevenLabs TTS providers
- [x] Load / chaos soak harness
- [ ] Full LangGraph tool loop with durable checkpoints
- [ ] Real CRM / ticketing adapters
- [ ] Online eval harness (transcript quality / task success)
- [ ] Horizontal scaling guide (sticky WS / shared Redis)

---

## License

MIT © Mayur Gohane
