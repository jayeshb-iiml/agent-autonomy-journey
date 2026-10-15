"""Deterministic autonomy policy: tier state, promotion/demotion, the Tier 2
safety gate, and the dashboard metrics. No LLM calls happen anywhere in this
file — classification and drafting are the model's job; everything here is
plain, auditable Python.
"""

CATEGORY_CAPS = {
    "password_reset": 2,
    "billing": 1,
    "feature_howto": 2,
    "bug_report": 2,
    "cancellation_risk": 1,
}

CATEGORY_LABELS = {
    "password_reset": "Password reset / account access",
    "billing": "Billing question",
    "feature_howto": "Feature request / how-to",
    "bug_report": "Bug report",
    "cancellation_risk": "Cancellation or downgrade risk",
}

PROMOTION_STREAK = 3

# Illustrative only, not exhaustive or production-grade — forces human review
# regardless of earned tier whenever any of these terms appear.
SAFETY_DENYLIST = [
    "fraud", "hacked", "unauthorized", "stolen", "lawyer", "legal", "sue",
    "refund", "chargeback", "someone else", "compromised",
    "delete my account", "gdpr", "data request",
]

TIER2_CONFIDENCE_THRESHOLD = 0.85
TIER2_MAX_WORDS = 40
TIER2_MAX_QUESTION_MARKS = 1


def init_state():
    return {cat: {"tier": 0, "streak": 0} for cat in CATEGORY_CAPS}


def is_tier2_eligible(category, state, confidence, ticket_text):
    if state[category]["tier"] != 2:
        return False
    if confidence < TIER2_CONFIDENCE_THRESHOLD:
        return False
    lower = ticket_text.lower()
    if any(term in lower for term in SAFETY_DENYLIST):
        return False
    if len(ticket_text.split()) > TIER2_MAX_WORDS:
        return False
    if ticket_text.count("?") > TIER2_MAX_QUESTION_MARKS:
        return False
    return True


def record_outcome(state, audit_log, ticket_id, category, outcome, auto_resolved=False):
    """outcome is one of 'approved_clean', 'edited', 'rejected'.
    Idempotent: a ticket_id already in audit_log is a no-op, so a Streamlit
    rerun can never double-apply the same decision.
    """
    if ticket_id in audit_log:
        return state

    tier_before = state[category]["tier"]

    if auto_resolved:
        tier_after = tier_before  # no human judged it, so no streak change
    elif outcome == "approved_clean":
        state[category]["streak"] += 1
        if state[category]["streak"] >= PROMOTION_STREAK:
            cap = CATEGORY_CAPS[category]
            state[category]["tier"] = min(tier_before + 1, cap)
            state[category]["streak"] = 0
        tier_after = state[category]["tier"]
    elif outcome in ("edited", "rejected"):
        state[category]["tier"] = max(tier_before - 1, 0)
        state[category]["streak"] = 0
        tier_after = state[category]["tier"]
    else:
        raise ValueError(f"unknown outcome: {outcome}")

    audit_log[ticket_id] = {
        "category": category,
        "outcome": outcome,
        "auto_resolved": auto_resolved,
        "tier_before": tier_before,
        "tier_after": tier_after,
    }
    return state


def compute_metrics(audit_log):
    total = len(audit_log)
    if total == 0:
        return {
            "total": 0, "auto_resolved": 0, "clean": 0, "corrections": 0,
            "autonomy_ratio": None, "human_agreement_rate": None,
        }

    auto_resolved = sum(1 for e in audit_log.values() if e["auto_resolved"])
    clean = sum(1 for e in audit_log.values() if not e["auto_resolved"] and e["outcome"] == "approved_clean")
    corrections = sum(1 for e in audit_log.values() if not e["auto_resolved"] and e["outcome"] in ("edited", "rejected"))

    autonomy_ratio = (clean + auto_resolved) / total
    human_reviewed = clean + corrections
    human_agreement_rate = (clean / human_reviewed) if human_reviewed > 0 else None

    return {
        "total": total,
        "auto_resolved": auto_resolved,
        "clean": clean,
        "corrections": corrections,
        "autonomy_ratio": autonomy_ratio,
        "human_agreement_rate": human_agreement_rate,
    }
