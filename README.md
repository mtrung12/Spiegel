# Spiegel

Test a marketing campaign against a simulated audience before you spend the budget.

Upload the campaign brief and creative, describe the target audience, and the engine builds a synthetic audience of buyer personas, releases the campaign into a simulated social feed, and reports what happened — reach, engagement, sentiment split, virality, objections and recommendations.

## How it works

1. **Graph building** — the uploaded material (PDF/MD/TXT) is extracted into a Zep Cloud GraphRAG knowledge graph
2. **Audience setup** — entities become buyer personas: demographics, needs, brand attitude, purchase behaviour, media habits
3. **Simulation** — the campaign creative is seeded into simulated Twitter and Reddit feeds, and the audience agents react over N rounds
4. **Measurement** — every action is logged and counted into marketing metrics
5. **Report** — a ReportAgent reads the measured metrics plus the graph and writes the assessment; you can then chat with it or interview individual audience agents

### What is real and what is simulated

- The social platforms are **simulated** ([OASIS](https://github.com/camel-ai/oasis)). Nothing reads or posts to real Twitter or Reddit, and there is no crawler — input is manual file upload only.
- The knowledge graph lives on **Zep Cloud** (SaaS), not a local database.
- The metrics are **counted** from the action log, not estimated by an LLM. Purchase intent and objections are the exception — they aren't countable from likes and reposts, so they come from agent interviews and what the agents actually wrote.

## Metrics produced

Reach and reach rate · impressions · engagement rate · passive share · virality ratio and share cascade depth · sentiment split · share of voice · per-segment breakdown · round-by-round curve

Available in the report, and as JSON from `GET /api/simulation/<simulation_id>/campaign-metrics`.

## Quick start

### Prerequisites

| Tool | Version |
|------|---------|
| Node.js | 18+ |
| Python | ≥3.11, ≤3.12 |
| uv | latest |

### 1. Configure

```bash
cp .env.example .env
```

Fill in:

```env
# Any OpenAI-SDK-compatible LLM API
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL_NAME=qwen-plus

# Zep Cloud — the free tier is enough to start: https://app.getzep.com/
ZEP_API_KEY=your_zep_api_key
```

Never commit `.env`.

Running a local model instead (LM Studio, Ollama, vLLM, llama.cpp)? Point
`LLM_BASE_URL` at it — `LLM_API_KEY` is then optional:

```env
LLM_BASE_URL=http://192.168.1.10:1234/v1   # LM Studio on another machine
LLM_MODEL_NAME=qwen2.5-7b-instruct
```

In LM Studio, load the model, start the server from the Developer tab, and turn
on "Serve on Local Network" if the backend runs elsewhere. The key is skipped
only for loopback, private-range IPs, and `*.local` hosts — a public URL still
needs one.

The chatbot (`CHATBOT_LLM_*`) is configured separately from the agents and can
sit on the other kind of endpoint — e.g. chat on a local model, agents on the
cloud. It inherits `LLM_API_KEY` only when both base URLs match; on different
endpoints each needs its own key, and a local base URL supplies its own.

> Simulations consume a lot of tokens. Start with fewer than 40 rounds.

### 2. Install

```bash
npm run setup:all
```

### 3. Run

```bash
npm run dev
```

Frontend on `http://localhost:3000`, backend on `http://localhost:5001`. Run them separately with `npm run backend` / `npm run frontend`.

### Docker

```bash
cp .env.example .env
docker compose up -d
```

Reads `.env` from the project root, maps ports 3000 and 5001.

## Pipeline logs

Every pipeline run writes to `local-doc/logs/` (gitignored), split so the two
debugging questions do not fight for the same file:

| File | Holds |
|------|-------|
| `actions.jsonl` | What each step *did*: component, action, target, duration, status, counts. No agent prose, so it reads as a timeline. |
| `debug.jsonl` | Why it did that: full inputs, the input text, the prompts, intermediate notes, and the raw model output. Joined to the action stream on `action_id` / `debug_id`. |
| `pipeline.log` | Human-readable mirror of the action stream. |
| `runs/<run_id>/` | The same three files scoped to one run — one project id, simulation id, or report id. |

Covered stages: ontology generation, graph build, entity read, persona
generation, simulation config, the running simulation (one line per agent
action, with post bodies going to the debug stream only), and report
generation including every ReACT iteration and tool call.

Tuning, all optional:

```env
PIPELINE_LOG_DIR=local-doc/logs   # where the logs go
PIPELINE_LOG_ENABLED=true         # false disables all pipeline logging
PIPELINE_LOG_PAYLOADS=true        # false keeps actions.jsonl, drops debug.jsonl
PIPELINE_LOG_MAX_TEXT=20000       # per-field character cap in debug.jsonl
PIPELINE_LOG_MAX_BYTES=52428800   # rotate a log file past this size
```

Credentials are scrubbed from every record before it is written.

Inspect a run:

```bash
# the timeline
cat local-doc/logs/runs/<run_id>/pipeline.log

# slowest steps
jq -s 'sort_by(-.duration_ms)[:10] | .[] | "\(.duration_ms)ms \(.component).\(.action)"' \
  local-doc/logs/actions.jsonl

# the prompt and reply behind one action
jq 'select(.action_id == "act_xxxxxxxx")' local-doc/logs/debug.jsonl
```

## Acknowledgments

Forked from [MiroFish](https://github.com/666ghj/MiroFish) and retargeted from general-purpose prediction to marketing campaign assessment. The simulation engine is [OASIS](https://github.com/camel-ai/oasis) by the CAMEL-AI team. Memory and retrieval are powered by [Zep](https://www.getzep.com/).
