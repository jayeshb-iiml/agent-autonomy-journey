import json
import os

import streamlit as st
from dotenv import load_dotenv

from agent import build_graph, run_ticket
from autonomy import (
    CATEGORY_CAPS, CATEGORY_LABELS, init_state, record_outcome,
    is_tier2_eligible, compute_metrics,
)

load_dotenv()

st.set_page_config(page_title="Agent Autonomy Journey", layout="wide")


def get_groq_api_key():
    key = os.getenv("GROQ_API_KEY")
    if key:
        return key
    try:
        return st.secrets["GROQ_API_KEY"]
    except Exception:
        return None


@st.cache_resource
def load_graph(groq_api_key):
    return build_graph(groq_api_key)


@st.cache_data
def load_tickets():
    with open("data/tickets.json") as f:
        return json.load(f)


def init_session():
    if "autonomy_state" not in st.session_state:
        st.session_state.autonomy_state = init_state()
        st.session_state.audit_log = {}
        st.session_state.ticket_index = 0
        st.session_state.drafts = {}  # ticket_id -> {category, confidence, draft}
        st.session_state.auto_resolved_shown = set()


def reset_demo():
    st.session_state.autonomy_state = init_state()
    st.session_state.audit_log = {}
    st.session_state.ticket_index = 0
    st.session_state.drafts = {}
    st.session_state.auto_resolved_shown = set()


st.title("Agent Autonomy Journey")
st.caption("A support-ticket agent that starts under full human oversight and earns autonomy as its calls prove correct.")

groq_api_key = get_groq_api_key()
if not groq_api_key:
    st.error("GROQ_API_KEY is not set. Add it to a .env file locally, or to Streamlit secrets when deployed.")
    st.stop()

init_session()
tickets = load_tickets()
graph = load_graph(groq_api_key)

# ---------- Sidebar: dashboard ----------
with st.sidebar:
    st.header("Dashboard")
    metrics = compute_metrics(st.session_state.audit_log)
    if metrics["total"] == 0:
        st.caption("No tickets processed yet.")
    else:
        c1, c2 = st.columns(2)
        c1.metric("Autonomy ratio", f"{metrics['autonomy_ratio']:.0%}")
        c2.metric("Human agreement", f"{metrics['human_agreement_rate']:.0%}" if metrics["human_agreement_rate"] is not None else "n/a")
        st.caption(f"{metrics['total']} processed · {metrics['auto_resolved']} auto-resolved · {metrics['corrections']} corrections")

    st.subheader("Category tiers")
    for cat in CATEGORY_CAPS:
        s = st.session_state.autonomy_state[cat]
        cap = CATEGORY_CAPS[cat]
        st.write(f"**{CATEGORY_LABELS[cat]}**")
        st.progress((s["tier"]) / max(cap, 1) if cap > 0 else 0, text=f"Tier {s['tier']} / cap {cap} · streak {s['streak']}")

    st.divider()
    if st.button("Reset demo", use_container_width=True):
        reset_demo()
        st.rerun()

# ---------- Main: ticket queue ----------
idx = st.session_state.ticket_index

if idx >= len(tickets):
    st.success(f"All {len(tickets)} tickets processed. Use Reset demo in the sidebar to replay.")
    st.stop()

ticket = tickets[idx]
st.subheader(f"Ticket {idx + 1} of {len(tickets)} — {ticket['customer']}")
st.write(ticket["text"])

if ticket["id"] not in st.session_state.drafts:
    with st.spinner("Classifying and drafting..."):
        result = run_ticket(graph, ticket["text"])
    st.session_state.drafts[ticket["id"]] = {
        "category": result["category"],
        "confidence": result["confidence"],
        "draft": result["draft"],
    }

info = st.session_state.drafts[ticket["id"]]
category = info["category"]
confidence = info["confidence"]
draft = info["draft"]
tier = st.session_state.autonomy_state[category]["tier"]

st.caption(f"Classified as **{CATEGORY_LABELS[category]}** · confidence {confidence:.2f} · category currently at Tier {tier}")

eligible = is_tier2_eligible(category, st.session_state.autonomy_state, confidence, ticket["text"])

if eligible:
    if ticket["id"] not in st.session_state.auto_resolved_shown:
        record_outcome(st.session_state.autonomy_state, st.session_state.audit_log, ticket["id"], category, "approved_clean", auto_resolved=True)
        st.session_state.auto_resolved_shown.add(ticket["id"])
    st.success("Auto-resolved — sent without waiting for review.")
    st.write(draft)
    if st.button("Next ticket"):
        st.session_state.ticket_index += 1
        st.rerun()
else:
    st.write(draft)
    col1, col2, col3 = st.columns(3)
    if col1.button("Approve", type="primary"):
        record_outcome(st.session_state.autonomy_state, st.session_state.audit_log, ticket["id"], category, "approved_clean")
        st.session_state.ticket_index += 1
        st.rerun()
    if col2.button("Reject"):
        record_outcome(st.session_state.autonomy_state, st.session_state.audit_log, ticket["id"], category, "rejected")
        st.session_state.ticket_index += 1
        st.rerun()
    with col3.popover("Edit"):
        edited = st.text_area("Edit the reply before sending", value=draft, key=f"edit_{ticket['id']}")
        if st.button("Submit edited reply", key=f"submit_{ticket['id']}"):
            record_outcome(st.session_state.autonomy_state, st.session_state.audit_log, ticket["id"], category, "edited")
            st.session_state.ticket_index += 1
            st.rerun()

with st.expander("Audit log"):
    for tid, entry in st.session_state.audit_log.items():
        st.text(f"{tid}: {entry['category']} · {entry['outcome']} · auto_resolved={entry['auto_resolved']} · tier {entry['tier_before']}→{entry['tier_after']}")
