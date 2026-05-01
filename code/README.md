# Multi-Domain Support Triage Agent

## What it does

Resolves support tickets for HackerRank, Claude (Anthropic), and Visa by retrieving
relevant documentation from a local corpus and generating grounded responses via
DeepSeek-V3 on Featherless AI. Every response is anchored exclusively to retrieved
corpus chunks — the agent is explicitly forbidden from using parametric knowledge.
High-risk tickets (billing, fraud, score manipulation, prompt injection) are caught by
a deterministic pre-LLM gate and escalated without touching the model.

## Architecture

- **corpus.py** — Loads 774 markdown files (438 HackerRank, 322 Claude, 14 Visa)
  into 2751 chunks. Strips YAML frontmatter, HTML, and markdown formatting. Chunks at
  250 words with 50-word overlap; each chunk is tagged with domain, subdir, title, and
  filepath.
- **retriever.py** — Builds a TF-IDF index with bigrams (`ngram_range=(1,2)`). `retrieve()`
  filters by domain (when company is known) then ranks by cosine similarity, returning
  the top-6 most relevant chunks per ticket. `derive_product_area()` normalises subdir
  names via an alias map.
- **risk.py** — Deterministic pre-LLM gate with 11 escalation rules (Stripe-key pattern,
  score manipulation, non-owner access restore, identity theft, security vulnerability,
  polyglot/prompt injection, billing refund with context, subscription pause/cancel,
  third-party ban request, ambiguous <5-word tickets). Classifies non-escalating invalid
  requests (destruction requests, greetings, trivia) without calling the model.
- **agent.py** — Builds a grounded prompt from the top-6 retrieved chunks; calls
  Featherless AI (`deepseek-ai/DeepSeek-V3.1`, `temperature=0`, `response_format=json_object`);
  parses and validates the structured JSON response; retries once with a stricter prompt
  on failure; falls back to a safe escalation default if both attempts fail.
- **main.py** — Orchestrates the full pipeline: loads corpus → builds index → reads
  `support_tickets.csv` → redacts Stripe-shaped secrets → runs risk gate → retrieves
  chunks → calls agent → writes `output.csv` with `csv.QUOTE_ALL`; renders a
  colour-coded terminal table via `rich`.

## Setup

```bash
pip install -r code/requirements.txt
cp .env.example .env          # then fill in your Featherless API key
```

Or export directly:

```bash
export FEATHERLESS_API_KEY=your-key-here
```

## Run

```bash
python3 code/main.py
# output written to support_tickets/output.csv
```

## Design decisions

- **Corpus-grounded only** — The system prompt explicitly instructs the LLM to use only
  the retrieved chunks, never parametric knowledge. If the corpus has no relevant content,
  the agent escalates rather than hallucinating an answer.
- **Pre-LLM deterministic risk gate** — `risk.py` catches safety-critical tickets before
  the LLM is invoked. This makes escalation decisions fast, transparent, reproducible, and
  immune to prompt injection.
- **Stripe-key detection and redaction** — The Stripe-key pattern is checked on the raw
  ticket text (before any processing) for escalation, then replaced with `[REDACTED]` in
  all LLM prompts and log output so secrets never leave the machine.
- **Polyglot and prompt-injection detection** — Non-ASCII characters combined with
  French/Spanish keywords flag cross-lingual injection attempts. Six English injection
  phrases (e.g. "ignore previous instructions") are blocked independently.

## Escalation criteria

| Always escalate (risk gate) | Always reply (LLM) |
|---|---|
| Stripe / payment key in ticket text | Valid questions with corpus coverage |
| Score or evaluation manipulation request | Out-of-scope / invalid requests (polite refusal) |
| Non-owner requesting account access restore | Greetings and trivial messages |
| Identity theft or stolen credentials | Destruction requests (classified invalid) |
| Security vulnerability / bug bounty disclosure | |
| Subscription pause or cancellation | |
| Request to ban a third-party seller | |
| Polyglot or English prompt injection | |
| Ambiguous ticket (<5 words, no company) | |

## Results (sample run, 29 tickets)

**19 replied | 10 escalated | 2 invalid**

Product areas observed (15 unique): `account_management`, `amazon_bedrock`,
`claude_api_and_console`, `claude_for_education`, `connector`, `general_support`,
`hackerrank_community`, `integration`, `interviews`, `privacy`, `safeguard`,
`screen`, `setting`, `support`, `travel_support`
