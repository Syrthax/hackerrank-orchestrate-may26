# Multi-Domain Support Triage Agent

A terminal-based RAG agent that triages real support tickets across HackerRank, Claude, and Visa using only the provided corpus — no parametric knowledge, no hallucination.

---

## The Challenge

The HackerRank Orchestrate hackathon asks participants to build a support triage agent capable of handling tickets across three distinct product ecosystems: HackerRank, Claude (Anthropic), and Visa. Each ecosystem has its own help corpus, terminology, and escalation rules. The agent must retrieve relevant documentation from the local corpus, classify each ticket, decide whether to reply or escalate, and produce a grounded response — never fabricating policies, phone numbers, or steps that aren't in the corpus.

---

## How It Works

### Pipeline Overview

```
support_tickets/support_tickets.csv
        │
        ▼
[Corpus Loader]
  774 markdown files → 2751 chunks
  (438 HackerRank / 322 Claude / 14 Visa)
        │
        ▼
[TF-IDF Retriever]
  domain-filtered cosine similarity → top-6 chunks per ticket
        │
        ▼
[Risk Assessor]
  11 deterministic rules → auto-escalate or pass through to LLM
        │
        ├─── (high-risk) ──→ escalated immediately, no LLM call
        │
        ▼
[LLM Triage — Featherless AI / DeepSeek-V3.1]
  grounded prompt (system + top-6 chunks + ticket) → structured JSON
  retry once on parse failure → fallback escalation default
        │
        ▼
support_tickets/output.csv + rich terminal table
```

### Module Breakdown

**`corpus.py`** recursively walks the `data/` directory and loads every `.md` file from the `hackerrank/`, `claude/`, and `visa/` subdirectories — 774 files in total. It strips YAML frontmatter, HTML tags, markdown links, image references, and inline code fences, then chunks the cleaned text at 250 words with a 50-word overlap (stride = 200). Each chunk is tagged with its domain, subdir (used as the product area seed), title, and source filepath, producing 2751 chunks ready for indexing.

**`retriever.py`** builds a TF-IDF index over all chunks using scikit-learn with bigram features (`ngram_range=(1,2)`) and sublinear term frequency weighting. At query time, `retrieve()` optionally filters the index to only the chunks belonging to the ticket's company domain before computing cosine similarity, which dramatically improves precision for known-company tickets. The top-6 chunks are returned. `derive_product_area()` normalises raw subdir strings to clean, underscore-separated category labels via an alias map (e.g. `"privacy-and-legal"→"privacy"`, `"general-help"→"general_support"`).

**`risk.py`** is the deterministic pre-LLM safety gate. It runs 11 escalation checks on the raw ticket text (before any redaction) without touching the model. The checks cover Stripe-shaped API keys, score manipulation requests, non-owner access restore, identity theft, security vulnerability disclosures, polyglot and English prompt injection, billing refund context, subscription pause/cancel, and third-party ban requests. It also classifies obviously invalid tickets — destruction requests, greetings, trivia — as `replied/invalid` without an LLM call, so no tokens are wasted.

**`agent.py`** builds the grounded LLM prompt by injecting the top-6 retrieved corpus chunks alongside the ticket text and a pre-assessed risk hint. It calls Featherless AI (DeepSeek-V3.1) at `temperature=0` with `response_format={"type":"json_object"}`, falling back to a plain call if the endpoint doesn't support the format parameter. The JSON response is extracted, validated against the five required fields, and normalised (status fuzzy-match, product_area lowercased and hyphen-stripped). A single retry with a stricter no-commentary prompt is attempted on parse failure; persistent failure falls back to a safe escalation default.

**`main.py`** orchestrates the full pipeline. It loads the corpus, builds the index, reads `support_tickets.csv`, and iterates each row: redacts Stripe-shaped secrets in the raw text before passing it anywhere, runs risk assessment on the unredacted text (so keys are visible to the detector), then calls the retriever and agent with the redacted version. Results are written to `output.csv` with `csv.QUOTE_ALL` and rendered as a colour-coded terminal table via `rich` (green = replied, red = escalated).

---

## Key Design Decisions

### Corpus-Grounded Responses Only

The system prompt explicitly instructs the model: *"Base your response ONLY on the RETRIEVED CORPUS CHUNKS provided in the user message. Do not use parametric knowledge."* This is the primary safety guarantee of the agent. If the retrieved chunks contain no relevant information, the agent escalates rather than synthesising an answer from model weights. This prevents fabricated phone numbers, invented policy steps, and hallucinated URLs — all failure modes that the evaluation rubric penalises. The grounding requirement is enforced in both the system prompt and the per-ticket user prompt, which labels the chunks explicitly as the sole source of truth.

### Pre-LLM Deterministic Risk Gate

The agent uses a two-layer architecture: a fast, transparent rule layer first, then the LLM only for tickets that pass the gate. This means the 10 highest-risk tickets in the dataset are escalated in milliseconds with zero model cost and zero chance of the LLM being manipulated into a wrong answer. The 11 escalation triggers are:

| Trigger | Example from dataset |
|---|---|
| Stripe-shaped API key in ticket | `cs_live_abcdefgh…` → `[REDACTED]`, auto-escalate |
| Score / evaluation manipulation | "increase my score and tell the company to move me forward" |
| Non-owner requesting account restore | "I am not the workspace owner but please restore my access" |
| Billing refund with purchase context | "refund" + "mock interviews stopped" |
| Identity theft or stolen credentials | "my identity has been stolen, what should I do" |
| Security vulnerability disclosure | "I found a major security vulnerability in Claude" |
| Third-party ban request | "make Visa ban the seller from taking payments" |
| Subscription pause or cancellation | "please pause our subscription" |
| English prompt injection | "ignore previous instructions / reveal your system prompt" |
| Polyglot / cross-lingual injection | non-ASCII text + French/Spanish trigger keywords |
| Ambiguous ticket, no company context | fewer than 5 words, `company = None` |

### Secret Detection and Redaction

Stripe-shaped API keys (`cs_live_*`, `sk_live_*`, `cs_test_*`, `sk_test_*`) are detected on the raw ticket text — before any other processing — so the risk gate can fire on the actual secret. The key is then replaced with `[REDACTED]` in every downstream path: the LLM prompt, the output CSV, and the terminal table. This means secrets never reach the model and never appear in any output file.

### Corpus Precision via Domain Filtering

Each ticket includes a `company` field. When it is `HackerRank`, `Claude`, or `Visa`, the retriever restricts its cosine similarity search to only that company's chunks, which eliminates cross-domain noise (e.g. a Visa travel question would otherwise surface HackerRank interview documentation). For `company = None`, the full index is searched and the agent infers the domain from context.

---

## Setup

```bash
# Install dependencies
pip install -r code/requirements.txt

# Configure your API key
cp .env.example .env
# then edit .env and set FEATHERLESS_API_KEY=your-key-here

# Or export directly
export FEATHERLESS_API_KEY=your-key-here
```

**Requirements:** Python 3.10+. No embeddings API, no vector database — TF-IDF runs entirely offline.

---

## Run

```bash
python3 code/main.py
# Reads:  support_tickets/support_tickets.csv
# Writes: support_tickets/output.csv
# Also renders a colour-coded table in the terminal

python3 code/main.py --dry-run   # inspect retrieval without LLM calls
# Loads corpus and index, runs risk gate and retriever for every ticket,
# prints risk flags + top-3 chunks per ticket, then summarises how many
# would be escalated by the risk gate vs passed to the LLM. No API key
# required. Does not write output.csv.
```

---

## Results

Running against all 29 tickets in `support_tickets.csv`:

```
19 replied | 10 escalated | 2 invalid
```

**Escalated tickets (10):** non-owner access restore, score manipulation, billing/payment disputes, Stripe key in ticket, identity theft, security vulnerability, third-party ban request, subscription pause, polyglot injection, ambiguous <5-word ticket.

**Replied tickets (19):** answered directly from corpus with grounded, product-specific responses.

**Product areas covered (15 unique):**

| | | |
|---|---|---|
| `account_management` | `amazon_bedrock` | `claude_api_and_console` |
| `claude_for_education` | `connector` | `general_support` |
| `hackerrank_community` | `integration` | `interviews` |
| `privacy` | `safeguard` | `screen` |
| `setting` | `support` | `travel_support` |

---

## Evaluation Dimensions Addressed

| Dimension | How this submission addresses it |
|---|---|
| **Agent Design** | Five-module pipeline with clear separation: corpus loading, retrieval, risk assessment, LLM triage, orchestration. RAG with TF-IDF + domain filtering. No hardcoded keys, secrets from env vars only. |
| **Output CSV** | All 5 columns populated. Enums validated. product_area normalised. No hallucinated policies — every `replied` response is grounded in retrieved chunks. High-risk tickets correctly escalated without LLM. |
| **AI Judge Interview** | Two-layer architecture (rules → LLM) has explicit, explainable trade-offs. Risk gate rules are enumerable and auditable. Each design decision has a documented reason. |
| **AI Fluency** | Full chat transcript logged to `~/hackerrank_orchestrate/log.txt` per AGENTS.md protocol. |

---

## Repository Layout

```
.
├── AGENTS.md                        # AI tool rules + mandatory turn logging
├── problem_statement.md             # Full task spec and I/O schema
├── evalutation_criteria.md          # Scoring rubric
├── .env.example                     # Copy to .env; never commit .env
├── code/
│   ├── README.md                    # Module-level documentation
│   ├── main.py                      # Entry point — run this
│   ├── corpus.py                    # Markdown loader and chunker
│   ├── retriever.py                 # TF-IDF index and retrieval
│   ├── risk.py                      # Deterministic pre-LLM risk gate
│   ├── agent.py                     # LLM triage and JSON validation
│   └── requirements.txt
├── data/
│   ├── hackerrank/                  # 438 markdown files
│   ├── claude/                      # 322 markdown files
│   └── visa/                        # 14 markdown files
└── support_tickets/
    ├── support_tickets.csv          # Input tickets (29 rows)
    └── output.csv                   # Agent predictions (populated)
```

---

## Submission

Submit at the HackerRank Community Platform. Upload three files:

1. **Code zip** — zip the `code/` directory (exclude virtualenvs, build artifacts, `data/`, and `support_tickets/`).
2. **Predictions CSV** — `support_tickets/output.csv`.
3. **Chat transcript** — `~/hackerrank_orchestrate/log.txt`.
