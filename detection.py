"""Proactive issue detection for the internal ops view (assessment Problem 1).

Deliberately NO LLM in the detection path: every signal below is a deterministic rule
over the ticket/order data. An LLM that "notices patterns" cannot be tested or trusted;
these rules can. The agent is only used (optionally, in the UI) to summarise what the
rules found.

SLA targets follow the same precedence as the chat agent: a signed customer agreement
overrides the default Support Policy v3 table.
"""
import re
from datetime import datetime

import tools

# --- Default first-response targets, Support Policy v3 (minutes) ---
# Business hours are not modelled; see MINUTES_PER_BUSINESS_* below.
POLICY_V3 = {
    "Enterprise": {"P1": 30, "P2": 120, "P3": 8 * 60},
    "Growth": {"P1": 2 * 60, "P2": 4 * 60, "P3": 2 * 8 * 60},
    "Standard": {"P1": 4 * 60, "P2": 8 * 60, "P3": 2 * 8 * 60},
}

# Contract overrides, parsed from the agreement PDFs at load time (see _contract_targets).
# Falls back to POLICY_V3 when an account has no agreement.

# Keyword -> severity, from the v3 severity definitions.
P1_PATTERNS = [
    (r"\bapi key\b|\bcredential\b|\bsecurity\b|\bexposed?\b|\bleak", "security incident / possible credential exposure"),
    (r"all .*(failing|down)|every user|complete outage|cannot create any", "complete outage: no shipment creation"),
]
P2_PATTERNS = [
    (r"bulk upload|csv", "bulk upload degraded (workaround exists)"),
    (r"still shows booked|not updated|webhook", "status sync degraded"),
]

# Known issues from 04_Product_Operations_Guide_and_Known_Issues.pdf
KNOWN_ISSUES = {
    "KI-208": {"title": "Bulk Upload failures on large CSVs", "status": "Investigating",
               "match": r"bulk upload|csv", "workaround": "Split uploads into files below 3,000 rows."},
    "KI-211": {"title": "SwiftShip pickup webhook delay", "status": "Monitoring",
               "match": r"still shows booked|webhook|pickup.*(not|isn't) (shown|updated)",
               "workaround": "Verify carrier status; webhooks can lag up to 20 minutes."},
}


def _dt(s):
    return datetime.strptime(str(s).split(" Asia")[0].strip()[:16], "%Y-%m-%d %H:%M")


def classify_severity(ticket) -> tuple:
    """Return (severity, reason) from the ticket text using the v3 severity definitions."""
    text = f"{ticket.get('subject', '')} {ticket.get('description', '')}".lower()
    for pattern, reason in P1_PATTERNS:
        if re.search(pattern, text):
            return "P1", reason
    for pattern, reason in P2_PATTERNS:
        if re.search(pattern, text):
            return "P2", reason
    return "P3", "no critical or major-degradation indicators"


def _contract_targets(account_id):
    """Parse first-response targets out of an account's agreement, if it has one.

    Returns ({severity: minutes}, source_filename) or (None, None).
    """
    ctx = tools.UserContext("manager", None, "detection")
    agreement = tools.get_customer_agreement(ctx, account_id)
    text = agreement.get("text")
    if not text:
        return None, None
    targets = {}
    # Matches "P1: 15 minutes", "P2: 1 hour", "P3: 8 business hours", "P1: 2 business hours"
    for sev, num, unit in re.findall(r"(P[123])\s*:?\s*(\d+)\s*(?:business\s+)?(minute|hour|day)", text, re.I):
        n = int(num)
        mins = n if unit.lower() == "minute" else n * 60 if unit.lower() == "hour" else n * 8 * 60
        targets.setdefault(sev.upper(), mins)
    return (targets, agreement.get("source")) if targets else (None, None)


def sla_board():
    """Every open ticket with its severity, applicable target, elapsed time and status.

    Precedence: signed agreement > Support Policy v3 default for the account's plan.
    """
    ctx = tools.UserContext("manager", None, "detection")
    now = _dt(tools._SNAPSHOT)
    rows = []
    for t in tools.list_tickets(ctx, status="open")["tickets"]:
        sev, reason = classify_severity(t)
        acct = tools.get_account(ctx, t["account_id"])
        plan = acct.get("plan", "Standard")

        contract, src = _contract_targets(t["account_id"])
        if contract and sev in contract:
            target, source = contract[sev], src
        else:
            target, source = POLICY_V3.get(plan, POLICY_V3["Standard"])[sev], "01_Support_Policy_v3_CURRENT.pdf"

        elapsed = int((now - _dt(t["created_at"])).total_seconds() // 60)
        pct = elapsed / target if target else 0
        state = "BREACHED" if elapsed > target else ("AT RISK" if pct >= 0.75 else "OK")
        rows.append({
            "ticket_id": t["ticket_id"], "account_id": t["account_id"],
            "account_name": acct.get("account_name", ""), "plan": plan,
            "subject": t["subject"], "severity": sev, "severity_reason": reason,
            "target_minutes": target, "elapsed_minutes": elapsed,
            "over_by_minutes": max(0, elapsed - target), "state": state, "target_source": source,
        })
    order = {"BREACHED": 0, "AT RISK": 1, "OK": 2}
    return sorted(rows, key=lambda r: (order[r["state"]], r["severity"], -r["elapsed_minutes"]))


def known_issue_clusters():
    """Group tickets (open and historical) against documented known issues."""
    ctx = tools.UserContext("manager", None, "detection")
    all_tickets = tools.list_tickets(ctx)["tickets"]
    clusters = []
    for ki, meta in KNOWN_ISSUES.items():
        hits = [t for t in all_tickets
                if re.search(meta["match"], f"{t.get('subject','')} {t.get('description','')}".lower())]
        if hits:
            clusters.append({
                "known_issue": ki, "title": meta["title"], "status": meta["status"],
                "workaround": meta["workaround"], "ticket_count": len(hits),
                "accounts_affected": sorted({t["account_id"] for t in hits}),
                "tickets": [{"ticket_id": t["ticket_id"], "account_id": t["account_id"],
                             "status": t["status"], "subject": t["subject"]} for t in hits],
            })
    return sorted(clusters, key=lambda c: -c["ticket_count"])


def carrier_signals():
    """Carriers with stuck pickups: BOOKED past the end of the pickup window."""
    ctx = tools.UserContext("manager", None, "detection")
    now = _dt(tools._SNAPSHOT)
    by_carrier = {}
    for o in tools._rows("SELECT * FROM orders WHERE status='BOOKED'"):
        if not o.get("pickup_window_end"):
            continue
        overdue = int((now - _dt(o["pickup_window_end"])).total_seconds() // 60)
        if overdue <= 0:
            continue
        c = by_carrier.setdefault(o["carrier"], {"carrier": o["carrier"], "stuck_orders": [],
                                                 "accounts": set(), "carrier_fault_count": 0})
        c["stuck_orders"].append({"order_id": o["order_id"], "account_id": o["account_id"],
                                  "overdue_minutes": overdue, "carrier_fault": bool(o.get("carrier_fault"))})
        c["accounts"].add(o["account_id"])
        c["carrier_fault_count"] += 1 if o.get("carrier_fault") else 0
    out = []
    for c in by_carrier.values():
        c["accounts"] = sorted(c["accounts"])
        c["order_count"] = len(c["stuck_orders"])
        c["multi_account"] = len(c["accounts"]) > 1
        out.append(c)
    return sorted(out, key=lambda c: -c["order_count"])


def credit_exposure():
    """Orders that look eligible for a service credit but have no ticket - unclaimed risk.

    Uses each account's contract threshold where one exists, else the SOP default (2h / lower
    of INR 500 or 10%).
    """
    ctx = tools.UserContext("manager", None, "detection")
    now = _dt(tools._SNAPSHOT)
    ticketed = {t["account_id"] for t in tools.list_tickets(ctx, status="open")["tickets"]}
    out = []
    for o in tools._rows("SELECT * FROM orders WHERE status='BOOKED' AND carrier_fault=1"):
        if not o.get("pickup_window_end") or o.get("customer_fault"):
            continue
        late = int((now - _dt(o["pickup_window_end"])).total_seconds() // 60)
        agreement = tools.get_customer_agreement(ctx, o["account_id"])
        text = (agreement.get("text") or "")
        m = re.search(r"more than (\d+) hours past the end of the scheduled pickup window", text, re.I)
        fixed = re.search(r"fixed INR ([\d,]+) service credit", text, re.I)
        if m:
            threshold, rule = int(m.group(1)) * 60, f"contract ({agreement.get('source')})"
            amount = f"INR {fixed.group(1)}" if fixed else "per contract"
        else:
            threshold, rule = 120, "SOP default (03_Cancellation_and_Service_Credit_SOP_v4.pdf)"
            amount = f"INR {min(500, round(o['shipment_fee_inr'] * 0.10))}"
        if late > threshold:
            out.append({"order_id": o["order_id"], "account_id": o["account_id"],
                        "late_minutes": late, "threshold_minutes": threshold, "rule": rule,
                        "likely_credit": amount, "has_open_ticket": o["account_id"] in ticketed})
    return out


def summary():
    board = sla_board()
    return {
        "reference_time": tools._SNAPSHOT,
        "breached": [r for r in board if r["state"] == "BREACHED"],
        "at_risk": [r for r in board if r["state"] == "AT RISK"],
        "sla_board": board,
        "clusters": known_issue_clusters(),
        "carriers": carrier_signals(),
        "credit_exposure": credit_exposure(),
    }
