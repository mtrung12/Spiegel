# Test Case — Asseco BooX Campaign

Demo walkthrough for Spiegel. Everything below is ready to copy and paste.

| | |
|---|---|
| **Test case ID** | TC-DEMO-ASSECO-01 |
| **Upload file** | `testdata/asseco-campaign-brief.pdf` (5 pages, ~11,800 characters) |
| **Regenerate the PDF** | `pip install reportlab && python testdata/make_pdf.py` |
| **Prerequisites** | `.env` has a working `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL_NAME` and `NEO4J_PASSWORD` with a reachable Neo4j; backend on :5001, frontend on :3000 |
| **Runtime** | ~30–45 min end to end at 15 rounds (the simulation is the slow part) |
| **Cost warning** | Simulations burn tokens. Use **15 rounds** for a demo, not the auto-generated number. |

---

## Step 1 — Home page, create the project

Open `http://localhost:3000`.

**01 / Upload** — drag in `testdata/asseco-campaign-brief.pdf`

**02 / Target customers** — paste this:

```
Decision-makers at mid-sized banks in Central and Eastern Europe who could buy Asseco BooX.

Two groups matter most:
1. Bank executives (CEO, COO, CFO, Chief Digital Officer), age 42-60. They sign the contract. They are
   under pressure from fintech competitors and from regulators, and they are personally accountable if a
   big IT programme fails. They buy certainty, not features.
2. IT leaders and architects (Head of IT, Chief Architect, Head of Digital Channels), age 33-50. They can
   kill the deal. Most have survived a painful vendor migration. They assume "out of the box" means rigid
   and un-customisable, and they will read the API documentation before answering a sales email.

Also present in the audience: fintech and banking journalists, and software engineers in Poland, Czechia
and Romania who judge whether Asseco looks like a modern place to work.

What this campaign needs to answer:
- Does "your bank, out of the box" land as credible, or does it sound like marketing?
- Does the "keep your core, launch in months not years" message convince the executives?
- Which objection does the most damage: legacy reputation, vendor lock-in, no differentiation, cloud
  and the regulator, or regional support outside Poland?
- Do the architects go and read the documentation, or do they dismiss the claim in public?
```

**Project name** (if the form asks): `Asseco BooX Launch`

Press **Start Campaign Simulation**.

**Expect:** the app creates the project, extracts ~11,800 characters, and generates an ontology.
Entity types should include something like Company, Product, Audience Segment, Message, Channel,
Objection, Policy Rule. Relation types should include things like TARGETS, PROMOTES, RAISES_OBJECTION,
GOVERNED_BY.

---

## Step 2 — Graph build

Runs automatically. Watch the SYSTEM DASHBOARD log.

**Expect:** progress goes 0 → 100%, status becomes `graph_completed`, and the node/edge counters show
non-zero values. Asseco, Asseco BooX, def3000 and the audience segments should appear as nodes.

Then press **Enter Environment Setup**.

---

## Step 3 — Environment setup

Runs automatically: it turns graph entities into buyer personas, then generates the simulation config.

**Expect:**
- Agent personas appear one by one, each with age, profession, bio and interests. Bank executives and
  IT architects should both be represented.
- A dual-platform config is generated (Twitter + Reddit) with time settings, per-agent behaviour, and
  initial posts seeded from the campaign creative.

**Before starting:** turn on **Custom** for the round count and set it to **15**.
The auto-generated number is much higher and will be slow and expensive for a demo.

Press **Start Dual-World Simulation**.

---

## Step 4 — Simulation

**Expect:** the feed fills with posts, replies, reposts and likes across the two platforms. Round counter
advances to 15. The KPI panel updates live.

You should see agents arguing about the campaign — some persuaded by "keep your core", others pushing
the objections from section 7 of the brief.

---

## Step 5 — Report

Generated after the run finishes.

**Expect the report to contain:** reach and reach rate, impressions, engagement rate, virality ratio,
sentiment split, share of voice, a per-segment breakdown, a round-by-round curve, the main objections
raised, and recommendations.

Check the JSON too:

```
GET http://localhost:5001/api/simulation/<simulation_id>/campaign-metrics
```

---

## Step 6 — Chat with the report agent

Paste these into the report chat, one at a time:

```
Which of the two audience groups reacted more positively - the bank executives or the IT architects?
```

```
What was the single most damaging objection raised against the campaign, and who raised it?
```

```
Did the message "keep your core, launch in months not years" land, or did the audience ignore it?
```

```
The brief says the campaign should be paused if positive or neutral sentiment falls below 50 percent.
Based on the measured sentiment split, would we have had to pause this campaign?
```

```
If I could change only one thing in this campaign before spending the 4.8 million zloty, what should it be?
```

---

## Step 7 — Interview an individual agent

Open the interaction view and pick an agent. Ask a skeptical architect persona:

```
You saw the Asseco BooX campaign. Would you actually go and read the API documentation, or not? Why?
```

```
What would Asseco have to show you to make you take a meeting?
```

---

## What counts as a pass

| # | Check | Pass condition |
|---|---|---|
| 1 | PDF upload and extraction | ~11,800 characters extracted, no error |
| 2 | Ontology | Entity and relation types generated, relevant to a marketing campaign |
| 3 | Graph build | Reaches `graph_completed`, non-zero nodes and edges |
| 4 | Personas | Both executives and IT architects appear among the agents |
| 5 | Simulation | Completes all 15 rounds without crashing |
| 6 | KPIs | All headline KPIs are populated and numerically sane (rates within 0–100%) |
| 7 | Report | Names real objections from the brief, not invented ones |
| 8 | Chat | Answers cite what actually happened in the run |
| 9 | Interview | The agent answers in character, consistent with its persona |

---

## Extra input variants

Swap the **02 / Target customers** text to test different angles with the same PDF.

**A. Narrow — architects only**

```
Only the technical evaluators: Head of IT, Chief Architect and Head of Digital Channels at mid-sized
banks in Poland, Czechia and Romania. Age 33-50, most have survived one painful core migration and are
deeply sceptical of "out of the box" claims. Ignore executives and press entirely.

Question this campaign must answer: does the architect audience find the claim credible enough to read
the documentation, or do they publicly call it marketing?
```

**B. Hostile — objection stress test**

```
Mid-sized CEE bank decision-makers who currently have a negative view of Asseco. They believe Asseco is a
slow legacy vendor associated with troubled public-sector IT projects, they are worried about vendor
lock-in, and they doubt a Polish company can support them properly in Romania or Serbia.

Question this campaign must answer: can the "your bank, out of the box" message survive a hostile
audience, and which of the pre-approved responses in section 8 actually works?
```

**C. Talent — engineering audience**

```
Software engineers and tech leads in Poland, Czechia and Romania, age 25-40, currently working at product
companies, consultancies and other banks. They are choosing where to work next and have no purchasing
power over banking software.

Question this campaign must answer: does this campaign make Asseco look like a modern engineering
organisation worth joining, or like a legacy enterprise IT shop?
```

---

## Negative test cases

| # | Input | Expected result |
|---|---|---|
| N1 | Upload the PDF, leave **02 / Target customers** empty | 400, "simulation requirement required" |
| N2 | Fill in the audience, upload nothing | 400, "file upload required" |
| N3 | Upload a `.docx` or `.png` | Rejected — only `.pdf`, `.md`, `.markdown`, `.txt` are allowed |
| N4 | Blank `NEO4J_PASSWORD` in `.env`, then build the graph | 500, "Knowledge graph store is not configured" |
| N5 | Delete a project while its graph build is running | 409, "graph is building" |
| N6 | `POST /api/graph/build` with `chunk_overlap` >= `chunk_size` | 400, "chunk_overlap must satisfy 0 <= chunk_overlap < chunk_size" |

---

## API-only path

If you want to skip the UI:

```bash
# 1. Upload + ontology
curl -X POST http://localhost:5001/api/graph/ontology/generate \
  -F "files=@testdata/asseco-campaign-brief.pdf" \
  -F "project_name=Asseco BooX Launch" \
  -F "simulation_requirement=Decision-makers at mid-sized banks in Central and Eastern Europe: bank executives (CEO, COO, CFO, CDO) who sign the contract, and IT leaders and architects who can kill the deal. Does the message 'your bank, out of the box - keep your core, launch in months not years' land as credible, and which objection does the most damage?"

# 2. Build the graph  (project_id comes from step 1)
curl -X POST http://localhost:5001/api/graph/build \
  -H "Content-Type: application/json" \
  -d '{"project_id":"proj_xxxx","graph_name":"Asseco BooX Launch"}'

# 3. Poll the build
curl http://localhost:5001/api/graph/task/task_xxxx

# 4. Create the simulation
curl -X POST http://localhost:5001/api/simulation/create \
  -H "Content-Type: application/json" \
  -d '{"project_id":"proj_xxxx","graph_id":"graph_xxxx","enable_twitter":true,"enable_reddit":true}'

# 5. Prepare, then start with 15 rounds
curl -X POST http://localhost:5001/api/simulation/prepare \
  -H "Content-Type: application/json" \
  -d '{"simulation_id":"sim_xxxx","use_llm_for_profiles":true,"parallel_profile_count":5}'

curl -X POST http://localhost:5001/api/simulation/start \
  -H "Content-Type: application/json" \
  -d '{"simulation_id":"sim_xxxx","max_rounds":15}'

# 6. KPIs
curl http://localhost:5001/api/simulation/sim_xxxx/campaign-metrics
```

---

## Files

| File | What it is |
|---|---|
| `asseco-campaign-brief.pdf` | The upload — campaign brief and communication policy |
| `asseco-campaign-brief.md` | Markdown source of the same document (also uploadable) |
| `make_pdf.py` | Regenerates the PDF from the Markdown |
| `TEST-CASE.md` | This file |
