# Diagnosis Report

Audit of `code/{corpus,retriever,risk,agent,main}.py` — read-only, no fixes applied.

---

## 1. Bugs

### [agent.py:132–142] Broad `except Exception` in `_call_llm` silently swallows rate-limit, auth, and network errors
The `try/except Exception` block was written assuming Featherless might reject `response_format={"type":"json_object"}`. But it catches **every** exception — 401 (bad key), 429 (rate limit), 500 (server error), connection timeout, etc. The fallback then re-issues the request without `response_format` and the same error fires again, which then propagates up to `triage()` and gets re-caught there. Net effect at runtime: a rate-limit error looks indistinguishable from a "response_format not supported" error, and the user sees `"LLM parse failure"` in `output.csv` for what is actually an HTTP 429. This is the **most likely root cause** of the 19 parse failures observed in the first run.

### [agent.py:209–215] Dead code — risk-override branch can never execute
Line 180 already short-circuits and returns `_risk_escalation_default(...)` whenever `risk.get("should_escalate")` is true. By line 209 we know `should_escalate` is false, yet line 210 checks `if risk.get("should_escalate") and result["status"] == "replied"`. The `and` is unreachable. Doesn't cause a wrong answer but is misleading and signals a stale invariant.

### [agent.py:102] Fuzzy status matcher accepts nonsense
`if "escal" in status or "recal" in status: status = "escalated"` — `"recal"` is not a typo of `"escalated"`. Inputs like `"recalibrate"`, `"recall"`, `"recalculate"` would all be silently rewritten to `"escalated"`. Causes incorrect escalation if the LLM ever emits one of these strings.

### [main.py:214] `time.sleep(2)` runs after every ticket, including auto-escalated ones
The risk gate handles 10 of 29 tickets without an LLM call. The 2-second sleep was added to avoid 429s on LLM calls — but it now runs unconditionally, wasting ~20 seconds total on rows that didn't hit the API. Doesn't break correctness but inflates wall-clock time and confuses anyone profiling the run.

### [agent.py:60–61] Empty retrieval still calls the LLM
When `chunks == []` (no domain match or empty corpus), `chunk_block` is set to `"[No corpus chunks retrieved]"` and the LLM is invoked anyway. The system prompt tells it to escalate in this case, but a deterministic short-circuit would be cheaper and avoid one wasted API call + 2-second sleep per such ticket.

### [agent.py:90] `_extract_json` produces invalid JSON when LLM emits multiple `{...}` blocks
`text[start:end+1]` slices from the **first** `{` to the **last** `}`. If DeepSeek-V3 emits a chain-of-thought block inside `{}` followed by the answer block, this returns the concatenation `{thought}{answer}` which is not valid JSON. The retry helps but isn't guaranteed.

### [risk.py:118] `score_manipulation` `elif` masks the second condition
```python
if _any(combined, _SCORE_KEYWORDS):
    flags.append("score_manipulation")
elif "graded unfairly" in combined and "increase" in combined:
    flags.append("score_manipulation")
```
If `_SCORE_KEYWORDS` matches AND `graded unfairly + increase` also matches, the flag is appended only once — fine. But the `elif` means the second condition is dead whenever the first fires. Logically equivalent right now, but if anyone refactors `_SCORE_KEYWORDS` into a more selective set, the second branch's coverage silently shrinks.

### [risk.py:60–63] `_BUG` keywords are too broad
`"down"`, `"stopped"`, `"error"`, `"crash"`, `"failing"` as bare substrings will match plenty of non-bug text: `"I tracked down the issue"`, `"I stopped using this feature"`, `"there was no error in my mind"`, etc. Causes false `request_type=bug` classifications for ordinary product questions.

### [risk.py:50] `"thanks"` in `_GREETINGS` is too greedy
`_any(issue_l, _GREETINGS)` does a substring match. `"thanks"` matches `"no thanks, your refund process is broken"` (5 words → under the threshold) and short-circuits to `replied/invalid` even though it's a real complaint. The 10-word guard is too lenient; should require the greeting to be the **entire** message.

### [risk.py:13–17] `_NON_OWNER_PHRASES` is asymmetric
Includes `"not an admin"` and `"not the owner"` / `"not the workspace owner"` — but **not** `"not the admin"` or `"not an owner"`. A user writing `"I am not the admin"` slips past the gate.

### [main.py:170] `pd.read_csv` doesn't strip BOM from utf-8-with-BOM CSVs
Should use `encoding="utf-8-sig"`. If a CSV is exported from Excel on Windows with BOM, the first column header becomes `"﻿Issue"` and `_read_str(row, "Issue")` returns `""` for every row. Edge case but production-relevant.

### [main.py:67] Unknown company names silently become `"None"`
`COMPANY_MAP.get(str(raw).strip().lower(), "None")` — if `support_tickets.csv` ever introduces a 4th company (say `"Stripe"`), the agent treats it as no-domain and searches all three corpora. No warning is emitted. Masks data issues.

### [corpus.py:12] Frontmatter regex requires trailing newline after closing `---`
`r"^---\s*\n.*?\n---\s*\n"` — if a markdown file has frontmatter but the closing `---` is on the very last line of the file with no trailing newline, the regex fails to match and the frontmatter leaks into the body. Then `_extract_title` returns `"---"` instead of the real title.

---

## 2. Duplicate / Redundant Code

### Stripe-key regex defined in two places
- [risk.py:3] `STRIPE_KEY_RE = re.compile(r"\b(cs_live_|cs_test_|sk_live_|sk_test_)\w+", re.IGNORECASE)`
- [main.py:46–49] `SECRET_REDACTION = re.compile(r"\b(cs_live_|cs_test_|sk_live_|sk_test_|pk_live_|pk_test_)\w+", ...)`

Two sources of truth for "Stripe-shaped secret". Consolidate to a single regex in a shared `secrets.py` (or in `risk.py`) and import from both places. Note the inconsistency: `main.py` includes `pk_*` (publishable, not actually secret); `risk.py` does not.

### Three near-identical "escalation default" dicts
- [agent.py:146–157] `_risk_escalation_default`
- [agent.py:160–170] `_parse_failure_default`
- [main.py:77–87] `_row_failure_default`

All three return the same 5-key dict shape with `status="escalated"`, generic message, and `request_type` from a hint. Consolidate into one helper, e.g. `escalation_default(reason: str, chunks=None, hint="product_issue")`.

### Domain → display-name mapping
- [corpus.py:4–8] `DOMAIN_MAP` (folder → display name)
- [main.py:51–57] `COMPANY_MAP` (display name + None → display name)

Different shapes but overlapping data. The valid-domain set `("HackerRank", "Claude", "Visa")` is also hardcoded inline in [main.py:100]. Consolidate.

### `derive_product_area` imported in main.py but never used
[main.py:24] imports `derive_product_area` from `retriever`, but main.py only calls `retrieve` and `build_index`. Dead import (it's used inside `agent.py` already).

### Imports of `sys` in two places in agent.py
[agent.py:193] does `import sys` inside an `except` block; should be at module top. Currently the module already has `import os` and other stdlib imports — `sys` should join them.

---

## 3. Hardcoded Values That Should Be Config

| File | Line | Value | Suggested Constant |
|---|---|---|---|
| corpus.py | 34 | `size: int = 250, overlap: int = 50` | `CHUNK_SIZE`, `CHUNK_OVERLAP` |
| retriever.py | 33 | `ngram_range=(1, 2)` | `TFIDF_NGRAM_RANGE` |
| retriever.py | 34 | `min_df=1` | `TFIDF_MIN_DF` |
| retriever.py | 47 | `top_k: int = 6` | `RETRIEVE_TOP_K` |
| risk.py | 95 | `< 10` (greeting word cap) | `MAX_GREETING_WORDS` |
| risk.py | 104 | `< 5` (ambiguous word cap) | `MIN_AMBIGUOUS_WORDS` |
| agent.py | 10 | `"deepseek-ai/DeepSeek-V3.1"` | `MODEL` (already const, but should be env-overridable) |
| agent.py | 11 | `"https://api.featherless.ai/v1"` | `BASE_URL` (already const, env-overridable) |
| agent.py | 128–129 | `temperature=0, max_tokens=600` | `LLM_TEMPERATURE`, `LLM_MAX_TOKENS` |
| main.py | 214 | `time.sleep(2)` | `LLM_RATE_LIMIT_SLEEP_SECONDS` |
| main.py | 127 | issue truncation `60` | `TABLE_ISSUE_MAX_CHARS` |
| main.py | 36–38 | three Path constants | already top-level — fine, but consider env override for evaluators |

A `code/config.py` with these constants — read once, import everywhere — would be the standard fix.

---

## 4. Bad Practices

### [agent.py:132] Bare `except Exception` without inspection
Catches `KeyboardInterrupt`-adjacent errors and silently degrades. Should narrow to `(BadRequestError, UnprocessableEntityError, TypeError)` from `openai` or check `e.status_code` before retrying.

### [agent.py:192–194] Debug `print` statements to stderr in production
`print(f"LLM attempt 1 failed ({e1}). Raw response: {raw!r}", file=sys.stderr)` leaks the raw LLM output (potentially with PII or secrets that the corpus quoted back) to stderr. Use `logging` with a configurable level. Also: this prints on **every** retry across **every** ticket — 19 stderr lines per run minimum.

### [agent.py:193] `import sys` inside an `except` block
Should be at module top with the other stdlib imports.

### [main.py:199] `traceback.print_exc(file=sys.stderr)` inside the row loop
Will dump full tracebacks for every failing row, hiding the rich progress bar and making the output noisy. Use logging.

### [main.py:60] `_redact` only handles Stripe-shaped keys
Won't redact AWS access keys (`AKIA...`), GitHub tokens (`gh[ps]_...`), JWTs, email addresses, phone numbers, or credit-card numbers. The PII risk is real for Visa tickets and `evaluation_criteria.md` may penalize leakage.

### [risk.py:50, 60, 67] All keyword groups use unbounded substring matching
`"down"` matches `"shut down"`, `"down the road"`, `"breakdown"`. `"thanks"` matches `"no thanks"`. Stronger: use `\b` word boundaries with `re.search`.

### [retriever.py:28] `matrix: object` type hint is meaningless
Should be `scipy.sparse.csr_matrix` or `Any`.

### [retriever.py:46] `domain: str = None` is not type-correct
Should be `Optional[str] = None` (or `str | None = None` on 3.10+).

### [main.py:27–31] `try/except ImportError: pass` for `python-dotenv`
Silently fails if dotenv is missing — but the package is in `requirements.txt`, so the silent fallback masks a real install problem rather than helping. Better to import unconditionally and let the error surface.

### [corpus.py:85–88] `print` calls in a library function
`load_corpus` prints to stdout. Belongs at the call site (`main.py`), not inside the library. Returns no stats dict for callers who want to do their own reporting.

### [agent.py:16–36] System prompt is a 20-line module-level string
Hard to lint, hard to A/B test. Should live in a `prompts/` directory as a `.txt` file loaded at import time.

---

## 5. Fragile Assumptions

### corpus.py — assumes every markdown file is utf-8 decodable
`read_text(encoding="utf-8", errors="replace")` silently replaces bad bytes with `�`. A binary file mistakenly named `*.md` would still load with garbage text and pollute the index.

### corpus.py — assumes domain folder names are exactly `hackerrank`, `claude`, `visa`
A renamed folder (e.g., `Hackerrank/` or `claude-help/`) breaks `DOMAIN_MAP` silently — the folder is skipped without warning.

### retriever.py — assumes every chunk has a non-empty `subdir`
`derive_product_area` filters `if c.get("subdir")`. Fine. But if `subdir == ""`, the alias map's empty-string fallback (none) would yield "" as the product_area. Edge case.

### risk.py — assumes ticket text is in English unless it has non-ASCII chars
The polyglot detection is "non-ASCII present + French/Spanish keyword". A ticket fully in Russian or Hindi (which would be non-ASCII) but **without** any of the 6 polyglot triggers would be processed as English. A sufficiently sophisticated injection in Russian would bypass the gate.

### risk.py — assumes Stripe keys appear before redaction
Risk runs on `issue` parameter. `main.py:96` correctly passes `issue_raw` (un-redacted) to `assess_risk`. ✅ (verified, this assumption holds — but it's fragile: if `_process_ticket`'s arg order is ever shuffled, the bug returns silently).

### agent.py — assumes Featherless returns valid OpenAI-shaped JSON in `choices[0].message.content`
If Featherless returns an error wrapped in `choices[0]` with empty content, `raw == ""` and `_extract_json` raises `"empty response"`, retry runs, fails again, returns parse-failure default. **Silently** loses the actual error info (HTTP code, error body).

### agent.py — assumes 600 max_tokens is enough for a 200-word response + JSON wrapper
~200 words is ~270 tokens of English; JSON keys and quotes add ~50; the `justification` adds ~30. Total ~350 tokens. 600 is safe but tight if the LLM emits a chain-of-thought preamble before the JSON.

### main.py — assumes the input CSV has columns `Issue, Subject, Company` exactly
`_read_str(row, "Issue")` is case-sensitive. If the evaluator uploads a CSV with `issue, subject, company` (lowercase), every row reads as empty and every ticket auto-escalates as ambiguous.

### main.py — assumes the input CSV is small enough to fit in memory
`pd.read_csv` then `df.iterrows` loads everything. Fine for 29 rows; would OOM on 100k. Not relevant to this hackathon but worth noting.

### main.py — assumes the LLM finishes within the implicit `openai` SDK timeout
No explicit `timeout=` is passed. The SDK default is 600s. A hung connection blocks the entire run.

---

## 6. Priority Fix List

1. **CRITICAL** — `agent.py:132–142` — narrow the `except Exception` in `_call_llm`; rate-limit / auth / network errors are being mis-classified as "response_format not supported" and producing all 19 parse failures observed in `output.csv`.
2. **HIGH** — `agent.py:102` — remove `"recal" in status` fuzzy-match; it sends `recalibrate` → `escalated`.
3. **HIGH** — `main.py:214` — gate `time.sleep(2)` behind "did we actually call the LLM?" (i.e., move it inside `agent.triage` or skip on auto-escalate); saves ~20s per run and clarifies intent.
4. **HIGH** — `risk.py:50, 60, 67` — replace bare-substring keyword scans with `\b`-bounded `re.search`; the current `_BUG` and `_GREETINGS` lists produce false positives ("track down", "no thanks").
5. **MEDIUM** — Consolidate the three near-identical escalation-default dicts (`agent._risk_escalation_default`, `agent._parse_failure_default`, `main._row_failure_default`) into one helper; drift between them is already producing slightly different `response` strings for what is the same outcome.

---

*Audit performed 2026-05-01 ~16:42 IST. Read-only — no source files modified.*
