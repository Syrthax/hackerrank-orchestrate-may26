import argparse
import csv
import os
import re
import sys
import time
import traceback
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

sys.path.insert(0, os.path.dirname(__file__))

from agent import triage
from corpus import load_corpus
from retriever import build_index, derive_product_area, retrieve
from risk import assess_risk

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

console = Console()

REPO_ROOT = Path(__file__).parent.parent
DATA_DIR = REPO_ROOT / "data"
TICKETS_PATH = REPO_ROOT / "support_tickets" / "support_tickets.csv"
OUTPUT_PATH = REPO_ROOT / "support_tickets" / "output.csv"

OUTPUT_COLUMNS = [
    "issue", "subject", "company",
    "response", "product_area", "status",
    "request_type", "justification",
]

SECRET_REDACTION = re.compile(
    r"\b(cs_live_|cs_test_|sk_live_|sk_test_|pk_live_|pk_test_)\w+",
    re.IGNORECASE,
)

COMPANY_MAP = {
    "hackerrank": "HackerRank",
    "claude": "Claude",
    "visa": "Visa",
    "none": "None",
    "": "None",
}


def _redact(text: str) -> str:
    return SECRET_REDACTION.sub("[REDACTED]", text or "")


def _normalize_company(raw: object) -> str:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return "None"
    return COMPANY_MAP.get(str(raw).strip().lower(), "None")


def _read_str(row: pd.Series, key: str) -> str:
    val = row.get(key, "")
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    return str(val).strip()


def _row_failure_default(issue: str, subject: str, company: str, error_msg: str) -> dict:
    return {
        "status": "escalated",
        "product_area": "general_support",
        "response": (
            "We were unable to process this request automatically. "
            "Our support team will review it and follow up with you."
        ),
        "justification": f"Pipeline error — escalated for human review: {error_msg}",
        "request_type": "product_issue",
    }


def _dry_run_ticket(
    issue: str, subject: str, company: str,
    risk: dict, top_chunks: list[dict],
) -> dict:
    auto_escalate = risk.get("should_escalate", False)
    flags = ", ".join(risk.get("risk_flags") or []) or "none"
    rtype_hint = risk.get("request_type_hint", "product_issue")

    console.print(f"\n[bold cyan]{'─' * 60}[/bold cyan]")
    issue_preview = issue[:80] + "..." if len(issue) > 80 else issue
    console.print(f"[bold]Issue:[/bold] {issue_preview}")
    console.print(f"[bold]Company:[/bold] {company}  |  [bold]request_type_hint:[/bold] {rtype_hint}")
    console.print(f"[bold]Risk flags:[/bold] {flags}")
    if auto_escalate:
        console.print("[bold red]→ WOULD BE AUTO-ESCALATED (risk gate)[/bold red]")
    else:
        console.print("[bold green]→ WOULD REACH LLM[/bold green]")
        console.print("[bold]Top-3 retrieved chunks:[/bold]")
        for i, chunk in enumerate(top_chunks[:3], 1):
            title = chunk.get("title") or chunk.get("subdir") or "untitled"
            preview = chunk["text"][:100].replace("\n", " ")
            score = chunk.get("score", 0.0)
            console.print(
                f"  [{i}] [dim]{chunk['domain']}/{chunk['subdir']}[/dim] "
                f"[italic]{title}[/italic] (score={score:.3f})\n"
                f"      {preview}…"
            )

    return {"status": "dry_run", "_auto_escalate": auto_escalate}


def _process_ticket(
    issue_raw: str, subject_raw: str,
    issue: str, subject: str,
    company: str, index, chunks,
    dry_run: bool = False,
) -> dict:
    # Run risk assessment on RAW text so Stripe keys etc. are visible
    risk = assess_risk(issue_raw, subject_raw, company)

    # Use redacted text for retrieval & LLM prompt
    query = (issue + " " + subject).strip()
    domain = company if company in ("HackerRank", "Claude", "Visa") else None
    top_chunks = retrieve(query, index, chunks, domain=domain, top_k=6)

    if dry_run:
        return _dry_run_ticket(issue, subject, company, risk, top_chunks)

    return triage(issue, subject, company, risk, top_chunks)


def _write_output(rows: list[dict]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in OUTPUT_COLUMNS})


def _render_table(rows: list[dict]) -> None:
    table = Table(title="Triage Results", show_lines=False)
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Company", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Product Area", no_wrap=True)
    table.add_column("Request Type", no_wrap=True)
    table.add_column("Issue", overflow="fold")

    for i, row in enumerate(rows, 1):
        style = "green" if row["status"] == "replied" else "red"
        issue_short = row["issue"]
        if len(issue_short) > 60:
            issue_short = issue_short[:57] + "..."
        table.add_row(
            str(i),
            row["company"],
            row["status"],
            row["product_area"],
            row["request_type"],
            issue_short,
            style=style,
        )

    console.print(table)

    n_replied = sum(1 for r in rows if r["status"] == "replied")
    n_escalated = sum(1 for r in rows if r["status"] == "escalated")
    n_invalid = sum(1 for r in rows if r["request_type"] == "invalid")
    console.print(
        f"\n[bold]{n_replied} replied | {n_escalated} escalated | "
        f"{n_invalid} invalid[/bold]"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Support triage agent")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect retrieval and risk gate without making LLM calls or writing output.csv",
    )
    args = parser.parse_args()
    dry_run: bool = args.dry_run

    if not dry_run and not os.environ.get("FEATHERLESS_API_KEY"):
        console.print(
            "[bold red]ERROR:[/bold red] FEATHERLESS_API_KEY environment variable is not set.\n"
            "Set it in your shell or in a .env file at the repo root.\n"
            "Example: export FEATHERLESS_API_KEY=your-key-here"
        )
        return 1

    if not TICKETS_PATH.exists():
        console.print(f"[bold red]ERROR:[/bold red] tickets file not found: {TICKETS_PATH}")
        return 1

    if dry_run:
        console.print("[bold yellow]DRY RUN MODE — no LLM calls, no output.csv written[/bold yellow]")

    console.print(f"[bold]Loading corpus from[/bold] {DATA_DIR}")
    chunks = load_corpus(str(DATA_DIR))

    console.print("[bold]Building TF-IDF index...[/bold]")
    index = build_index(chunks)

    console.print(f"[bold]Reading tickets from[/bold] {TICKETS_PATH}")
    df = pd.read_csv(TICKETS_PATH, encoding="utf-8")

    output_rows: list[dict] = []

    progress_columns = [
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
    ]

    with Progress(*progress_columns, console=console) as progress:
        task_id = progress.add_task(
            "Dry run — inspecting tickets" if dry_run else "Triaging tickets",
            total=len(df),
        )

        for _, row in df.iterrows():
            issue_raw = _read_str(row, "Issue")
            subject_raw = _read_str(row, "Subject")
            company_raw = row.get("Company")

            issue = _redact(issue_raw)
            subject = _redact(subject_raw)
            company = _normalize_company(company_raw)

            try:
                result = _process_ticket(
                    issue_raw, subject_raw, issue, subject, company, index, chunks,
                    dry_run=dry_run,
                )
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
                console.print(f"[yellow]Row error — escalating:[/yellow] {err}")
                traceback.print_exc(file=sys.stderr)
                result = _row_failure_default(issue, subject, company, err)

            output_rows.append({
                "issue": issue,
                "subject": subject,
                "company": company,
                "response": result.get("response", ""),
                "product_area": result.get("product_area", ""),
                "status": result["status"],
                "request_type": result.get("request_type", ""),
                "justification": result.get("justification", ""),
                "_auto_escalate": result.get("_auto_escalate", False),
            })

            progress.advance(task_id)

    if dry_run:
        n_gate = sum(1 for r in output_rows if r.get("_auto_escalate"))
        n_llm = len(output_rows) - n_gate
        console.print(f"\n[bold cyan]{'─' * 60}[/bold cyan]")
        console.print(
            f"[bold]Dry run complete.[/bold] "
            f"[red]{n_gate} would be escalated by risk gate[/red], "
            f"[green]{n_llm} would reach LLM[/green]."
        )
    else:
        _write_output(output_rows)
        console.print()
        _render_table(output_rows)
        console.print(f"\n[bold]Output written to[/bold] {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
