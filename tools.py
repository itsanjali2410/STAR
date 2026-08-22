"""Tools for the ParcelPilot support agent.

Access control is enforced HERE, in the tool/data layer, not in the prompt:
- role="customer" is pinned to ctx.account_id. Every structured lookup is filtered to
  that account, internal-only columns are dropped, and document search hides other
  customers' agreements.
- Staff roles (support_agent, manager) can see every account.
State-changing tools (ACTION_TOOLS) are never run by the agent loop directly; the UI
must confirm them first (see agent.py).
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

STAFF_ROLES = {"support_agent", "manager"}
ACTION_TOOLS = {"create_escalation", "update_ticket"}

# Columns customers must never see.
CUSTOMER_HIDDEN = {
    "accounts": {"notes"},
    "orders": {"notes"},
    "tickets": {"assigned_to", "historical_resolution"},
}


@dataclass
class UserContext:
    role: str
    account_id: Optional[str] = None
    name: str = ""

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES


class AccessDenied(Exception):
    pass


# Set once by setup().
_CONN = None
_VECTORDB = None
_DOC_TEXTS: dict = {}
_SNAPSHOT = "unknown"
ACTION_LOG: list = []  # executed state-changing actions (mock persistence)


def setup(conn, vectordb, doc_texts, snapshot_time):
    global _CONN, _VECTORDB, _DOC_TEXTS, _SNAPSHOT
    _CONN, _VECTORDB, _DOC_TEXTS, _SNAPSHOT = conn, vectordb, doc_texts, snapshot_time


# ---------- helpers ----------

def _rows(sql, params=()):
    if _CONN is None:
        raise RuntimeError("tools.setup() has not been called - no data loaded.")
    cur = _CONN.execute(sql, params)
    cols = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        row = {}
        for c, v in zip(cols, r):
            if v is None or (isinstance(v, float) and v != v):  # NaN
                v = None
            row[c] = str(v) if isinstance(v, (datetime,)) else v
        out.append(row)
    return out


def _strip(ctx, table, row):
    if not ctx.is_staff:
        for col in CUSTOMER_HIDDEN.get(table, ()):
            row.pop(col, None)
    return row


def _scope_account(ctx, account_id):
    """Customers may only ever act on their own account."""
    if ctx.is_staff:
        return account_id
    if account_id and account_id != ctx.account_id:
        raise AccessDenied("You can only access data for your own account.")
    return ctx.account_id


def _agreement_files():
    return {r["contract_file"] for r in _rows("SELECT contract_file FROM accounts WHERE contract_file IS NOT NULL")}


def _authority(source):
    if source in _agreement_files():
        return "tier 1: signed customer agreement (overrides policy/SOP)"
    if "Policy" in source or "SOP" in source:
        return "tier 2: current policy / SOP"
    return "tier 3: product documentation"


def _parse_dt(s):
    s = s.strip()
    if s.lower() in ("now", "snapshot"):
        s = _SNAPSHOT
    s = s.split(" Asia")[0]  # "2026-08-16 11:00 Asia/Kolkata"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"Unrecognised datetime: {s!r} (use YYYY-MM-DD HH:MM or 'now')")


# ---------- document tools ----------

def search_documents(ctx, query: str, k: int = 4):
    allowed = [f for f in _DOC_TEXTS if "DEPRECATED" not in f.upper()]
    if not ctx.is_staff:
        own = _rows("SELECT contract_file FROM accounts WHERE account_id=?", (ctx.account_id,))
        own_file = own[0]["contract_file"] if own else None
        allowed = [f for f in allowed if f not in _agreement_files() or f == own_file]
    docs = _VECTORDB.similarity_search(query, k=k, filter={"source": {"$in": allowed}})
    return {"results": [{"source": d.metadata["source"], "authority": _authority(d.metadata["source"]),
                         "text": d.page_content} for d in docs]}


def get_customer_agreement(ctx, account_id: str = None):
    account_id = _scope_account(ctx, account_id)
    acct = _rows("SELECT account_id, account_name, plan, contract_file FROM accounts WHERE account_id=?", (account_id,))
    if not acct:
        return {"error": f"No account {account_id}"}
    f = acct[0]["contract_file"]
    if not f or f not in _DOC_TEXTS:
        return {"account_id": account_id, "agreement": None,
                "note": f"No custom agreement on file; standard {acct[0]['plan']} policy and current SOP apply."}
    return {"account_id": account_id, "source": f, "authority": _authority(f), "text": _DOC_TEXTS[f]}


# ---------- structured-data tools ----------

def get_account(ctx, account_id: str = None):
    account_id = _scope_account(ctx, account_id)
    rows = _rows("SELECT * FROM accounts WHERE account_id=?", (account_id,))
    return _strip(ctx, "accounts", rows[0]) if rows else {"error": f"No account {account_id}"}


def get_order(ctx, order_id: str):
    rows = _rows("SELECT * FROM orders WHERE order_id=?", (order_id,))
    if not rows or (not ctx.is_staff and rows[0]["account_id"] != ctx.account_id):
        return {"error": f"Order {order_id} not found" + ("" if ctx.is_staff else " for your account")}
    return _strip(ctx, "orders", rows[0])


def list_tickets(ctx, account_id: str = None, ticket_id: str = None, status: str = None):
    account_id = _scope_account(ctx, account_id)
    sql, params = "SELECT * FROM tickets WHERE 1=1", []
    if account_id:
        sql += " AND account_id=?"; params.append(account_id)
    if ticket_id:
        sql += " AND ticket_id=?"; params.append(ticket_id)
    if status:
        sql += " AND status=?"; params.append(status)
    rows = [_strip(ctx, "tickets", r) for r in _rows(sql + " ORDER BY created_at", params)]
    out = {"tickets": rows}
    if ctx.is_staff:
        out["warning"] = "historical_resolution is what a past agent said; it may be wrong. Verify against current policy/agreement."
    return out


def time_difference(ctx, start: str, end: str = "now"):
    """Deterministic time arithmetic. 'now' = dataset snapshot time."""
    s, e = _parse_dt(start), _parse_dt(end)
    mins = int((e - s).total_seconds() // 60)
    return {"start": str(s), "end": str(e), "minutes": mins, "hours": round(mins / 60, 2),
            "reference_time": _SNAPSHOT}


# ---------- state-changing tools (confirmation required) ----------

def create_escalation(ctx, summary: str, priority: str, account_id: str = None,
                      ticket_id: str = None, order_id: str = None):
    account_id = _scope_account(ctx, account_id)
    rec = {"id": f"ESC-{len(ACTION_LOG) + 1:03d}", "type": "escalation", "priority": priority,
           "account_id": account_id, "ticket_id": ticket_id, "order_id": order_id,
           "summary": summary, "requested_by": f"{ctx.role}:{ctx.name}", "status": "open"}
    ACTION_LOG.append(rec)
    logging.info(f"Escalation created: {rec}")
    return rec


def update_ticket(ctx, ticket_id: str, status: str = None, note: str = None):
    if not ctx.is_staff:
        raise AccessDenied("Only ParcelPilot staff can update tickets.")
    if not _rows("SELECT 1 FROM tickets WHERE ticket_id=?", (ticket_id,)):
        return {"error": f"No ticket {ticket_id}"}
    if status:
        _CONN.execute("UPDATE tickets SET status=? WHERE ticket_id=?", (status, ticket_id))
    rec = {"id": f"ACT-{len(ACTION_LOG) + 1:03d}", "type": "ticket_update", "ticket_id": ticket_id,
           "status": status, "note": note, "by": f"{ctx.role}:{ctx.name}"}
    ACTION_LOG.append(rec)
    return rec


# ---------- registry + OpenAI schemas ----------

REGISTRY = {f.__name__: f for f in (search_documents, get_customer_agreement, get_account, get_order,
                                    list_tickets, time_difference, create_escalation, update_ticket)}


def run_tool(ctx, name, args: dict):
    try:
        return REGISTRY[name](ctx, **args)
    except AccessDenied as e:
        return {"error": f"Access denied: {e}"}
    except Exception as e:
        logging.exception(f"Tool {name} failed")
        return {"error": f"{type(e).__name__}: {e}"}


def _tool(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": list(required)}}}

S = {"type": "string"}
TOOL_SCHEMAS = [
    _tool("search_documents", "Semantic search over current policies, SOPs and product docs (deprecated docs excluded). "
          "Returns chunks with source and authority tier.", {"query": S}, ["query"]),
    _tool("get_customer_agreement", "Return the full signed agreement for an account (tier-1 authority), or a note that none exists. "
          "Always call this before answering cancellation, credit or SLA questions.",
          {"account_id": S}),
    _tool("get_account", "Account details: plan, status, CSM, premium_support.", {"account_id": S}),
    _tool("get_order", "Order details: status, booking/pickup times, fee, fault flags, cancellation request time.",
          {"order_id": S}, ["order_id"]),
    _tool("list_tickets", "List support tickets, optionally filtered by account, ticket_id or status (open/closed).",
          {"account_id": S, "ticket_id": S, "status": S}),
    _tool("time_difference", "Minutes/hours between two timestamps (YYYY-MM-DD HH:MM). Use end='now' for the dataset snapshot time. "
          "Use this instead of doing time arithmetic yourself.", {"start": S, "end": S}, ["start"]),
    _tool("create_escalation", "STATE-CHANGING: create an escalation to the human support team. Requires user confirmation.",
          {"summary": S, "priority": {"type": "string", "enum": ["P1", "P2", "P3"]},
           "account_id": S, "ticket_id": S, "order_id": S}, ["summary", "priority"]),
    _tool("update_ticket", "STATE-CHANGING (staff only): update a ticket's status and/or add a note. Requires user confirmation.",
          {"ticket_id": S, "status": {"type": "string", "enum": ["open", "closed", "pending"]}, "note": S}, ["ticket_id"]),
]
