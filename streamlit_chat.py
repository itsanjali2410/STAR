import logging
import streamlit as st
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

import agent
import detection
import tools
from db_loader import load_excel_to_memory, get_snapshot_time
from pdf_parser import load_all_documents

st.set_page_config(page_title="ParcelPilot Support", page_icon="📦", layout="wide")
logging.basicConfig(level=logging.INFO)


@st.cache_resource(show_spinner="Loading documents and data…")
def load_resources():
    """Parse the data pack once per server process (cached across reruns/sessions)."""
    load_dotenv()
    sheets, conn = load_excel_to_memory()
    chunks, metas, texts = load_all_documents()
    vectordb = Chroma.from_texts(chunks, OpenAIEmbeddings(), metadatas=metas)
    return conn, vectordb, texts, get_snapshot_time(sheets)


# Re-inject on EVERY rerun, not just cache misses: Streamlit re-imports modules on
# reload, which resets tools.py's globals while load_resources() stays cached.
conn, vectordb, doc_texts, SNAPSHOT = load_resources()
tools.setup(conn, vectordb, doc_texts, SNAPSHOT)

# Mock authentication: pick who you are. Access control itself lives in tools.py.
USERS = {
    "Customer · Northstar Logistics (ACCT-001)": tools.UserContext("customer", "ACCT-001", "Northstar Logistics"),
    "Customer · LumenWorks (ACCT-002)": tools.UserContext("customer", "ACCT-002", "LumenWorks"),
    "Customer · Beacon Retail (ACCT-003)": tools.UserContext("customer", "ACCT-003", "Beacon Retail"),
    "Customer · Axis Labs (ACCT-004)": tools.UserContext("customer", "ACCT-004", "Axis Labs"),
    "Staff · Support agent": tools.UserContext("support_agent", None, "Rohit"),
    "Staff · Manager": tools.UserContext("manager", None, "Maya"),
}



def reset_chat():
    st.session_state.messages = []   # OpenAI-format history
    st.session_state.display = []    # [{role, content, trace}]
    st.session_state.pending = None


with st.sidebar:
    st.header("📦 ParcelPilot Support")
    user_label = st.selectbox("Signed in as (mock auth)", list(USERS))
    st.caption(f"Reference time: {SNAPSHOT}")
    if st.button("New conversation"):
        reset_chat()
    st.divider()
    st.subheader("Action log")
    if tools.ACTION_LOG:
        for a in tools.ACTION_LOG:
            st.code(a, language="json")
    else:
        st.caption("No actions executed yet.")
    st.divider()
    st.caption("Try: *Can Northstar cancel ORD-1001 without a cancellation fee?*  \n"
               "*A pickup is three hours late because of carrier fault. Should I get a service credit?*")

ctx = USERS[user_label]
if st.session_state.get("user") != user_label:  # first run or switching user resets the chat
    st.session_state.user = user_label
    reset_chat()


def render_ops_dashboard():
    """Problem 1: proactive detection. Staff only - the tab is not rendered for customers."""
    d = detection.summary()
    st.caption(f"Deterministic rules over ticket/order data. Reference time: {d['reference_time']}. "
               "No LLM in the detection path.")

    c1, c2, c3 = st.columns(3)
    c1.metric("SLA breached", len(d["breached"]))
    c2.metric("At risk (>75% of target)", len(d["at_risk"]))
    c3.metric("Known-issue clusters", len(d["clusters"]))

    st.subheader("SLA board")
    st.caption("Severity from the v3 definitions; target from the customer's agreement where one exists, "
               "otherwise the Support Policy v3 table for their plan.")
    for r in d["sla_board"]:
        icon = {"BREACHED": "🔴", "AT RISK": "🟠", "OK": "🟢"}[r["state"]]
        over = f" · over by {r['over_by_minutes']}m" if r["over_by_minutes"] else ""
        with st.expander(f"{icon} {r['ticket_id']} · {r['severity']} · {r['account_name']} · "
                         f"{r['elapsed_minutes']}m elapsed / {r['target_minutes']}m target{over}", expanded=(r["state"] == "BREACHED")):
            st.markdown(f"**{r['subject']}**")
            st.markdown(f"- Severity **{r['severity']}** — {r['severity_reason']}")
            st.markdown(f"- Target **{r['target_minutes']} min** from `{r['target_source']}` ({r['plan']} plan)")
            if r["state"] == "BREACHED":
                st.error(f"First-response target breached by {r['over_by_minutes']} minutes.")

    st.subheader("Repeat issues")
    for c in d["clusters"]:
        st.markdown(f"**{c['known_issue']} — {c['title']}** · _{c['status']}_ · "
                    f"{c['ticket_count']} ticket(s), accounts: {', '.join(c['accounts_affected'])}")
        st.caption(f"Workaround: {c['workaround']}")
        st.dataframe(c["tickets"], hide_index=True, width="stretch")

    st.subheader("Carrier signals")
    if d["carriers"]:
        for c in d["carriers"]:
            flag = " · affects multiple accounts" if c["multi_account"] else ""
            st.markdown(f"**{c['carrier']}** — {c['order_count']} order(s) past the pickup window{flag}")
            st.dataframe(c["stuck_orders"], hide_index=True, width="stretch")
    else:
        st.caption("No orders past their pickup window at the reference time.")

    st.subheader("Unclaimed service-credit exposure")
    st.caption("Orders that appear to qualify for a credit under the account's own rules.")
    if d["credit_exposure"]:
        st.dataframe(d["credit_exposure"], hide_index=True, width="stretch")
    else:
        st.caption("None detected.")


def show(msg):
    with st.chat_message(msg["role"]):
        if msg.get("trace"):
            with st.expander(f"🔧 Tools used ({len(msg['trace'])})"):
                for t in msg["trace"]:
                    st.markdown(f"**{t['tool']}** `{t['args']}`")
                    if isinstance(t["result"], (dict, list)):
                        st.json(t["result"], expanded=False)
                    else:
                        st.caption(str(t["result"]))
        if msg["content"]:
            st.markdown(msg["content"])


def handle(out):
    st.session_state.display.append({"role": "assistant", "content": out["reply"], "trace": out["trace"]})
    st.session_state.pending = out["pending_action"]


def render_chat():
    for m in st.session_state.display:
        show(m)
    _chat_controls()


def _chat_controls():
    pending = st.session_state.pending
    if pending:
        st.warning(f"**Confirm action:** `{pending['name']}`")
        st.json(pending["args"])
        c1, c2 = st.columns(2)
        if c1.button("✅ Confirm", type="primary"):
            handle(agent.resolve_action(st.session_state.messages, ctx, SNAPSHOT, pending, confirmed=True))
            st.rerun()
        if c2.button("❌ Cancel"):
            handle(agent.resolve_action(st.session_state.messages, ctx, SNAPSHOT, pending, confirmed=False))
            st.rerun()


if ctx.is_staff:
    chat_tab, ops_tab = st.tabs(["💬 Chat", "📊 Ops dashboard"])
    with chat_tab:
        render_chat()
    with ops_tab:
        render_ops_dashboard()
else:
    render_chat()

pending = st.session_state.pending
if prompt := st.chat_input("Ask about orders, cancellations, credits, SLAs or tickets…", disabled=bool(pending)):
    st.session_state.display.append({"role": "user", "content": prompt, "trace": []})
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.spinner("Thinking…"):
        handle(agent.run_agent(st.session_state.messages, ctx, SNAPSHOT))
    st.rerun()
