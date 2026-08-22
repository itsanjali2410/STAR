"""Smoke tests: access control in the tool layer + one end-to-end agent run.
Run: python test_tools.py   (needs OPENAI_API_KEY in .env)
"""
import logging
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

import agent
import detection
import tools
from db_loader import load_excel_to_memory, get_snapshot_time
from pdf_parser import load_all_documents


def setup():
    load_dotenv()
    sheets, conn = load_excel_to_memory()
    chunks, metas, texts = load_all_documents()
    vectordb = Chroma.from_texts(chunks, OpenAIEmbeddings(), metadatas=metas)
    snap = get_snapshot_time(sheets)
    tools.setup(conn, vectordb, texts, snap)
    return snap


def test_access_control():
    cust = tools.UserContext("customer", "ACCT-001", "Northstar")
    staff = tools.UserContext("support_agent", None, "Rohit")

    assert tools.get_order(cust, "ORD-1001")["account_id"] == "ACCT-001"
    assert "error" in tools.get_order(cust, "ORD-2001")                     # other account's order
    assert "notes" not in tools.get_order(cust, "ORD-1001")                 # internal column hidden
    assert "error" in tools.run_tool(cust, "get_account", {"account_id": "ACCT-002"})
    t = tools.list_tickets(cust)
    assert {x["account_id"] for x in t["tickets"]} == {"ACCT-001"}
    assert all("historical_resolution" not in x for x in t["tickets"])
    assert "error" in tools.run_tool(cust, "update_ticket", {"ticket_id": "TKT-501", "status": "closed"})

    srcs = {r["source"] for r in tools.search_documents(cust, "service credit terms", k=10)["results"]}
    assert "06_LumenWorks_Service_Agreement.pdf" not in srcs             # other customer's contract hidden
    assert not any("DEPRECATED" in s for s in srcs)

    assert len(tools.list_tickets(staff)["tickets"]) == 7
    assert tools.get_customer_agreement(staff, "ACCT-003")["agreement"] is None
    assert tools.time_difference(staff, "2026-08-16 09:00", "now")["minutes"] == 120
    print("access-control tests passed")


def test_agent(snap):
    staff = tools.UserContext("support_agent", None, "Rohit")
    msgs = []
    out = agent.run_agent(msgs, staff, snap)  # no user msg yet -> should just reply
    msgs = [{"role": "user", "content": "Can Northstar cancel ORD-1001 without a cancellation fee? Explain why."}]
    out = agent.run_agent(msgs, staff, snap)
    print("\nTOOLS:", [t["tool"] for t in out["trace"]])
    print("REPLY:", out["reply"])
    assert "get_customer_agreement" in [t["tool"] for t in out["trace"]]
    assert "05_Northstar" in out["reply"] and "fee" in out["reply"].lower()


def test_detection():
    """Detection is deterministic, so assert exact expected findings."""
    board = {r["ticket_id"]: r for r in detection.sla_board()}
    # Northstar contract sets P1=15min (overrides the 30min Enterprise policy default)
    assert board["TKT-501"]["severity"] == "P1"
    assert board["TKT-501"]["target_minutes"] == 15
    assert "Northstar" in board["TKT-501"]["target_source"]
    assert board["TKT-501"]["state"] == "BREACHED"
    # Axis Labs has NO contract -> falls back to the Enterprise policy table
    assert board["TKT-505"]["target_minutes"] == 30
    assert "Support_Policy_v3" in board["TKT-505"]["target_source"]
    assert board["TKT-505"]["severity"] == "P1"          # security incident
    # LumenWorks P2 target comes from their agreement (4 business hours)
    assert board["TKT-502"]["target_minutes"] == 240
    assert board["TKT-502"]["state"] == "OK"

    clusters = {c["known_issue"]: c for c in detection.known_issue_clusters()}
    assert clusters["KI-208"]["ticket_count"] == 2       # TKT-502 + historical TKT-451
    assert "KI-211" in clusters

    credits = detection.credit_exposure()
    assert len(credits) == 1 and credits[0]["order_id"] == "ORD-2002"
    assert credits[0]["threshold_minutes"] == 240        # LumenWorks 4h, not the 2h SOP default
    assert credits[0]["likely_credit"] == "INR 300"
    print("detection tests passed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    snap = setup()
    test_access_control()
    test_detection()
    test_agent(snap)
