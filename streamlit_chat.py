import logging
import streamlit as st
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

import agent
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


for m in st.session_state.display:
    show(m)

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

if prompt := st.chat_input("Ask about orders, cancellations, credits, SLAs or tickets…", disabled=bool(pending)):
    st.session_state.display.append({"role": "user", "content": prompt, "trace": []})
    show(st.session_state.display[-1])
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.spinner("Thinking…"):
        handle(agent.run_agent(st.session_state.messages, ctx, SNAPSHOT))
    st.rerun()
