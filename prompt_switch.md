# prompt_switch.md — Context handoff for the next AI model

> Drop-in briefing for any successor agent (Codex, Gemini, Cursor, Windsurf, etc.) picking up this repo.
> Read this file top-to-bottom before doing anything. Then read `AGENTS.md` for the hard rules.

---

## 0. One-line summary

A terminal-based, corpus-grounded support triage agent that reads `support_tickets/support_tickets.csv`
(29 rows) and writes `support_tickets/output.csv` (8 columns). The pipeline is fully built and has been
run once — but the LLM call is failing for all 19 non-auto-escalated tickets. That is the main thing to fix.

---

## 1. Hard rules (from AGENTS.md — non-negotiable)

1. **Log every user turn** to `~/hackerrank_orchestrate/log.txt` in §5.2 format. Append-only. Never commit.
2. **Redact secrets** in log: Stripe keys (`cs_live_*`, `sk_live_*`), API keys, PII → `[REDACTED]`.
3. **Onboarding gate**: check log for `AGREEMENT RECORDED: /Users/sarthakghosh/projects/hackerrank-orchestrate-may26`. If absent, run §3 of AGENTS.md.
4. **Entry point**: `code/main.py`. Do not rename it.
5. **Corpus-only grounding**: never use parametric knowledge. Escalate if corpus is insufficient.
6. **Secrets via env vars only**: `FEATHERLESS_API_KEY` is in `.env` (already gitignored, already populated).

---

## 2. Repo state (verified 2026-05-01, after first pipeline run)

```
hackerrank-orchestrate-may26/
├── AGENTS.md
├── CLAUDE.md                        # @AGENTS.md
├── README.md
├── problem_statement.md
├── evalutation_criteria.md
├── prompt_switch.md                 # ← this file
├── .env                             # FEATHERLESS_API_KEY set — DO NOT COMMIT
├── .gitignore                       # .env already covered
├── code/
│   ├── __init__.py                  # empty — makes code/ importable as package
│   ├── main.py                      # entry point
│   ├── corpus.py                    # markdown loader + chunker
│   ├── retriever.py                 # TF-IDF index + retrieve + derive_product_area
│   ├── risk.py                      # deterministic pre-LLM escalation gate
│   ├── agent.py                     # Featherless LLM call + JSON parse + retry
│   └── requirements.txt             # openai, scikit-learn, numpy, pandas, rich, python-dotenv
├── data/
│   ├── hackerrank/                  # 438 .md files across 11 subdirs
│   ├── claude/                      # 322 .md files across 16 subdirs
│   └── visa/                        # 14 .md files — sparse coverage
└── support_tickets/
    ├── sample_support_tickets.csv   # 10 labeled examples
    ├── support_tickets.csv          # 29 input rows (Issue, Subject, Company)
    └── output.csv                   # written by last run — has issues (see §4)
```

**Note**: AGENTS.md §6.1 references `support_issues/` and `your_file.py` — those paths are stale. Trust the filesystem above.

---

## 3. Architecture (all files written and importable)

Run from repo root: `cd code && python3 main.py` (or `python3 code/main.py` — `sys.path.insert` handles it).

### Pipeline flow per ticket

```
CSV row → sanitize + redact → normalize company
  → risk.assess_risk()           # deterministic gate, no LLM
      ├─ should_escalate=True → skip LLM, write escalation default
      └─ should_escalate=False → retriever.retrieve() → agent.triage() → write result
```

### Module summary

| File | What it does | Status |
|---|---|---|
| `corpus.py` | `load_corpus(data_dir)` — walks `data/**/*.md`, strips frontmatter + markdown, chunks at 250 words / 50 overlap. Returns `list[dict]` with `domain, subdir, title, text, filepath`. | ✅ Working |
| `retriever.py` | `build_index(chunks)` — TF-IDF bigrams. `retrieve(query, index, chunks, domain, top_k=6)` — cosine sim, optional domain filter. `derive_product_area(chunks)` — alias map + normalization. | ✅ Working |
| `risk.py` | `assess_risk(issue, subject, company)` — returns `{should_escalate, risk_flags, request_type_hint}`. 10 of 29 tickets auto-escalate. | ✅ Working |
| `agent.py` | `triage(issue, subject, company, risk, chunks)` — Featherless OpenAI-compat call, `temperature=0`, `max_tokens=600`, JSON extract + validate + 1 retry. | ❌ LLM returns parse failures |
| `main.py` | Orchestrates full pipeline, rich progress bar, QUOTE_ALL CSV writer, green/red rich table. | ✅ Working (modulo LLM) |

### LLM details

```python
from openai import OpenAI
client = OpenAI(api_key=os.environ["FEATHERLESS_API_KEY"], base_url="https://api.featherless.ai/v1")
MODEL = "deepseek-ai/DeepSeek-V3"
# temperature=0, max_tokens=600
```

---

## 4. Known bugs — fix these before re-running

### Bug 1: LLM parse failure on all 19 non-escalated tickets (CRITICAL)

Every ticket that reaches `agent.py` is returning "LLM parse failure" and being escalated as a fallback.
The pipeline ran to completion but the `output.csv` is almost entirely fallback escalations.

**Likely cause**: DeepSeek-V3 on Featherless is wrapping its JSON in markdown fences (` ```json ... ``` `) or
adding prose before/after. The current `_extract_json` in `agent.py` does `text.find('{')` / `text.rfind('}')` —
check whether the raw response actually contains a valid JSON object. Add debug logging of the raw response
on parse failure so you can see what the model is actually returning.

**Suggested fix**:
1. Add `print(f"RAW RESPONSE: {raw!r}")` inside the `except` block before the retry.
2. Run on just one ticket to inspect the raw output.
3. Adjust prompt or extraction logic accordingly.
4. The system prompt demands `"OUTPUT: Return ONLY a valid JSON object"` — if the model ignores this,
   try adding `"response_format": {"type": "json_object"}` to the API call if Featherless supports it,
   or make the JSON schema more explicit in the user prompt.

### Bug 2: Stripe key redaction fires before risk assessment (ticket #5)

In `main.py`, `_redact(issue_raw)` runs before `assess_risk()`. So for ticket #5
("order ID: cs_live_abcdefgh"), the Stripe key is replaced with `[REDACTED]` before the
risk gate sees it — meaning `STRIPE_KEY_RE` never matches and ticket #5 falls through to the LLM.

**Fix** (one line in `main.py`): run `assess_risk` on the raw text, then redact for logging/output:
```python
# In _process_ticket or main loop:
risk = assess_risk(issue_raw, subject_raw, company)   # raw text for risk detection
issue = _redact(issue_raw)                             # redacted for output + LLM prompt
```

### Bug 3: Ticket #24 ends up escalated instead of replied/invalid

Ticket #24 ("Give me the code to delete all files from the system") is correctly flagged by `risk.py`
with `should_escalate=False, request_type_hint="invalid"` — but the LLM call then fails (Bug 1),
and the parse-failure fallback returns `status="escalated"`. Fix Bug 1 and this resolves itself.

---

## 5. Output CSV contract

Header (already present in `output.csv` — do not change):
```
issue,subject,company,response,product_area,status,request_type,justification
```

- `status` ∈ `{replied, escalated}` — lowercase
- `request_type` ∈ `{product_issue, feature_request, bug, invalid}` — lowercase
- `product_area` — derived from top retrieved chunk's `subdir`, normalized (see `derive_product_area`)
- `response` — grounded in corpus only. Never fabricate.
- `justification` — 1–2 sentences of internal reasoning

**Sample CSV schema mismatch**: `sample_support_tickets.csv` has 7 Title-Case columns with no `justification`. Ignore its schema; follow the 8-col lowercase header above.

---

## 6. Risk gate verdicts (from last smoke-test — deterministic, won't change)

| # | Company | Risk verdict | Flags | req_type hint |
|---|---|---|---|---|
| 1 | Claude | **ESC** | `non_owner_access_restore` | product_issue |
| 2 | HackerRank | **ESC** | `score_manipulation` | product_issue |
| 3 | Visa | **ESC** | `billing_refund, third_party_ban_request` | product_issue |
| 4 | HackerRank | **ESC** | `billing_refund` | bug |
| 5 | HackerRank | rep → ESC after Bug 2 fix | `stripe_key_pattern` | product_issue |
| 6 | HackerRank | rep | — | product_issue |
| 7 | HackerRank | rep | — | bug |
| 8 | HackerRank | rep | — | bug |
| 9 | HackerRank | rep | — | bug |
| 10 | HackerRank | rep | — | product_issue |
| 11 | HackerRank | rep | — | product_issue |
| 12 | None | **ESC** | `ambiguous_request` | invalid |
| 13 | HackerRank | rep | — | product_issue |
| 14 | HackerRank | **ESC** | `subscription_action` | bug |
| 15 | Claude | rep | — | bug |
| 16 | Visa | **ESC** | `identity_theft` | product_issue |
| 17 | HackerRank | rep | — | bug |
| 18 | HackerRank | rep | — | product_issue |
| 19 | Visa | rep | — | product_issue |
| 20 | Claude | **ESC** | `security_vulnerability` | product_issue |
| 21 | Claude | rep | — | product_issue |
| 22 | Visa | rep | — | product_issue |
| 23 | Claude | rep | — | product_issue |
| 24 | None | rep (invalid, LLM refuses) | `malicious_destruction_request` | invalid |
| 25 | Visa | **ESC** | `polyglot_injection` | product_issue |
| 26 | Claude | rep | — | bug |
| 27 | HackerRank | rep | — | product_issue |
| 28 | Claude | rep | — | product_issue |
| 29 | Visa | rep | — | product_issue |

**10 auto-escalate, 19 go to LLM** (18 after Bug 2 fix).

---

## 7. What still needs to be done

**Immediate (to get a valid output.csv):**
1. Fix Bug 1 — diagnose why LLM returns unparseable output; fix `agent.py`.
2. Fix Bug 2 — run `assess_risk` on raw text before redaction; re-run pipeline.
3. Verify ticket #24 outputs `status=replied, request_type=invalid` after Bug 1 fix.

**Before submission:**
4. Write `code/README.md` — required by Evaluation Criteria §1 (Agent Design). Include: setup steps, how to run, architecture diagram, design decisions.
5. Add `.env.example` with `FEATHERLESS_API_KEY=your_key_here` for evaluators.
6. Eyeball each of the 29 output rows against the expected verdicts in §6.
7. Log all turns per AGENTS.md §5.2.

**Optional improvements:**
- Smoke-test against `sample_support_tickets.csv` and compare predicted vs labeled `status` and `request_type`.
- If LLM grounding is weak for Visa tickets (only 14 files), consider raising `top_k` for Visa domain.
- Add `response_format={"type":"json_object"}` to the Featherless call if supported.

---

## 8. Running the pipeline

```bash
cd /Users/sarthakghosh/projects/hackerrank-orchestrate-may26
pip install -r code/requirements.txt    # if not already installed
cd code
python3 main.py
# output written to ../support_tickets/output.csv
```

Dependencies (already installed globally):
`openai`, `scikit-learn`, `numpy`, `pandas`, `rich`, `python-dotenv`

---

*Last updated: 2026-05-01 ~12:20 IST. Pipeline run once — 10/29 correct auto-escalations, 19/29 LLM parse failures. Three bugs identified above.*
