import re

STRIPE_KEY_RE = re.compile(r"\b(cs_live_|cs_test_|sk_live_|sk_test_)\w+", re.IGNORECASE)

# ── Escalation keyword groups ──────────────────────────────────────────────────

_SCORE_KEYWORDS = [
    "increase my score",
    "change my score",
    "tell the company to move me",
]

_NON_OWNER_PHRASES = [
    "not the owner",
    "not an admin",
    "not the workspace owner",
]

_ACCESS_RESTORE_PHRASES = ["restore", "grant", "give me access"]

_IDENTITY_THEFT = ["identity theft", "identity has been stolen"]

_SECURITY_VULN = ["security vulnerability", "bug bounty", "data breach"]

_POLYGLOT_TRIGGERS = [
    "règles", "logique", "interne",
    "fraud logic", "system prompt", "retrieved",
]

_REFUND_CONTEXT = ["mock interview", "payment", "subscription", "billing"]

_THIRD_PARTY_BAN = ["ban the seller", "ban the merchant", "block the seller"]

_PROMPT_INJECTION = [
    "ignore previous instructions",
    "ignore your instructions",
    "reveal system prompt",
    "show internal rules",
    "forget your instructions",
    "bypass filters",
]

# ── Non-escalation / special-case keywords ────────────────────────────────────

_DESTRUCTION = [
    "delete all files", "delete all the files", "rm -rf",
    "wipe all files", "code to delete", "code to wipe",
]

_GREETINGS = ["thank you", "thanks", "happy to help", "hello", "hi there"]

_TRIVIA = [
    "actor in", "who played", "what is the name of the actor",
    "what year did", "who won the", "capital of",
]

# ── Request-type hint keywords ─────────────────────────────────────────────────

_BUG = [
    "not working", "isn't working", "isnt working",
    "broken", "down", "failing", "stopped", "crash",
    "error", "outage", "inaccessible",
    "submissions not working", "none of the submissions",
]

_FEATURE = [
    "can you add", "would be great", "feature request",
    "please add", "suggestion",
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _any(text: str, phrases: list[str]) -> bool:
    return any(p in text for p in phrases)


def _word_count(text: str) -> int:
    return len(text.split())


def _has_non_ascii(text: str) -> bool:
    return any(ord(c) > 127 for c in text)


# ── Public API ─────────────────────────────────────────────────────────────────

def assess_risk(issue: str, subject: str, company: str) -> dict:
    issue_l = (issue or "").lower()
    combined = (issue_l + " " + (subject or "").lower()).strip()
    company_clean = (company or "").strip()

    # ── Special non-escalation short-circuits ──────────────────────────────────

    if _word_count(issue or "") < 10 and _any(issue_l, _GREETINGS):
        return _result(False, [], "invalid")

    if _any(combined, _DESTRUCTION):
        return _result(False, ["malicious_destruction_request"], "invalid")

    if _any(issue_l, _TRIVIA):
        return _result(False, [], "invalid")

    if _word_count(issue or "") < 5 and company_clean in ("None", ""):
        return _result(True, ["ambiguous_request"], "invalid")

    # ── Escalation checks ──────────────────────────────────────────────────────

    flags: list[str] = []

    # Stripe key leak in ticket text
    if STRIPE_KEY_RE.search(combined):
        flags.append("stripe_key_pattern")

    # Score / evaluation manipulation
    if _any(combined, _SCORE_KEYWORDS):
        flags.append("score_manipulation")
    elif "graded unfairly" in combined and "increase" in combined:
        flags.append("score_manipulation")

    # Non-owner requesting access restore
    if _any(combined, _NON_OWNER_PHRASES) and _any(combined, _ACCESS_RESTORE_PHRASES):
        flags.append("non_owner_access_restore")

    # Identity theft
    if _any(combined, _IDENTITY_THEFT):
        flags.append("identity_theft")

    # Security vulnerability / disclosure
    if _any(combined, _SECURITY_VULN):
        flags.append("security_vulnerability")

    # Polyglot / non-English prompt injection
    if _has_non_ascii(combined) and _any(combined, _POLYGLOT_TRIGGERS):
        flags.append("polyglot_injection")

    # Billing refund with explicit context
    if "refund" in combined and _any(combined, _REFUND_CONTEXT):
        flags.append("billing_refund")

    # Subscription pause / cancel
    if ("pause" in combined or "cancel" in combined) and "subscription" in combined:
        flags.append("subscription_action")

    # Request to ban a third-party
    if _any(combined, _THIRD_PARTY_BAN):
        flags.append("third_party_ban_request")

    # English prompt injection
    if _any(combined, _PROMPT_INJECTION):
        flags.append("prompt_injection")

    # ── Request-type hint (set regardless of escalation) ─────────────────────

    if _any(combined, _BUG):
        rtype = "bug"
    elif _any(combined, _FEATURE):
        rtype = "feature_request"
    else:
        rtype = "product_issue"

    return _result(bool(flags), flags, rtype)


def _result(escalate: bool, flags: list[str], rtype: str) -> dict:
    return {
        "should_escalate": escalate,
        "risk_flags": flags,
        "request_type_hint": rtype,
    }
