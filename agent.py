"""The only place an LLM is called: classify a ticket, then draft a reply.
One clean graph invoke per ticket, no interrupt, no checkpointing — see
README for why. Every decision about what happens with the output is made
elsewhere, in autonomy.py, by plain deterministic code.
"""

import json
import re
from typing import TypedDict

from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, START, END

from autonomy import CATEGORY_CAPS

CATEGORIES = list(CATEGORY_CAPS.keys())
MODEL = "openai/gpt-oss-20b"

CLASSIFY_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You classify customer support tickets for a SaaS product. Choose exactly one category "
        f"from this list: {', '.join(CATEGORIES)}. Return ONLY valid JSON, no markdown fences, "
        'with exactly these fields: {{"category": "<one of the list>", "confidence": <float 0-1, '
        'how sure you are this is the right single category>}}',
    ),
    ("user", "Ticket: {ticket_text}"),
])

DRAFT_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You write short, friendly customer support replies for a SaaS product. The ticket has "
        "been classified as '{category}'. Write a 2-4 sentence reply directly addressing it. "
        "Plain language, no apologies for things that aren't your fault, no filler.",
    ),
    ("user", "Ticket: {ticket_text}"),
])


class TicketState(TypedDict):
    ticket_text: str
    category: str
    confidence: float
    draft: str


def _parse_json(raw):
    cleaned = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


def build_graph(groq_api_key):
    llm = ChatGroq(model=MODEL, api_key=groq_api_key, temperature=0)
    classify_chain = CLASSIFY_PROMPT | llm
    draft_chain = DRAFT_PROMPT | llm

    def classify_node(state: TicketState) -> TicketState:
        raw = classify_chain.invoke({"ticket_text": state["ticket_text"]}).content
        try:
            parsed = _parse_json(raw)
            category = parsed["category"]
            confidence = float(parsed["confidence"])
        except Exception:
            category, confidence = "bug_report", 0.0  # safe fallback: low confidence, always-ask category
        if category not in CATEGORIES:
            category, confidence = "bug_report", 0.0
        return {**state, "category": category, "confidence": confidence}

    def draft_node(state: TicketState) -> TicketState:
        draft = draft_chain.invoke({
            "ticket_text": state["ticket_text"],
            "category": state["category"],
        }).content
        return {**state, "draft": draft.strip()}

    graph = StateGraph(TicketState)
    graph.add_node("classify", classify_node)
    graph.add_node("draft", draft_node)
    graph.add_edge(START, "classify")
    graph.add_edge("classify", "draft")
    graph.add_edge("draft", END)
    return graph.compile()


def run_ticket(graph, ticket_text):
    result = graph.invoke({"ticket_text": ticket_text, "category": "", "confidence": 0.0, "draft": ""})
    return result
