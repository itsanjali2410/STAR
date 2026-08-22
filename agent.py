"""Minimal tool-calling agent loop over the OpenAI Chat Completions API.

run_agent() executes read-only tools inline. When the model calls a state-changing
tool it STOPS and returns a pending_action; the UI asks the user, then calls
resolve_action() to execute (or decline) it and let the model finish its reply.
"""
import json
import os
from openai import OpenAI

import tools

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
MAX_STEPS = 10
_client = None


def client():
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def system_prompt(ctx: tools.UserContext, snapshot_time: str) -> str:
    tmpl = open(os.path.join(os.path.dirname(__file__), "system_prompt.txt")).read()
    if ctx.is_staff:
        who = f"ParcelPilot staff member ({ctx.role}: {ctx.name}). They may access every account."
    else:
        who = (f"a CUSTOMER from account {ctx.account_id} ({ctx.name}). The tools already restrict them to "
               f"their own account; never speculate about other customers or internal notes.")
    return tmpl.format(reference_time=snapshot_time, user_description=who)


def run_agent(messages: list, ctx: tools.UserContext, snapshot_time: str) -> dict:
    """messages: OpenAI-format history WITHOUT the system message (mutated in place)."""
    trace = []
    for _ in range(MAX_STEPS):
        resp = client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": system_prompt(ctx, snapshot_time)}] + messages,
            tools=tools.TOOL_SCHEMAS,
            parallel_tool_calls=False,  # one tool per step keeps confirmation handling simple
            temperature=0,
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return {"reply": msg.content or "", "trace": trace, "pending_action": None}

        tc = msg.tool_calls[0]
        name, args = tc.function.name, json.loads(tc.function.arguments or "{}")
        if name in tools.ACTION_TOOLS:
            trace.append({"tool": name, "args": args, "result": "awaiting user confirmation"})
            return {"reply": msg.content or "", "trace": trace,
                    "pending_action": {"tool_call_id": tc.id, "name": name, "args": args}}

        result = tools.run_tool(ctx, name, args)
        trace.append({"tool": name, "args": args, "result": result})
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)})
    return {"reply": "I couldn't finish within the step limit. Please escalate to a human agent.",
            "trace": trace, "pending_action": None}


def resolve_action(messages, ctx, snapshot_time, pending: dict, confirmed: bool) -> dict:
    if confirmed:
        result = tools.run_tool(ctx, pending["name"], pending["args"])
    else:
        result = {"status": "declined", "message": "The user declined this action. Do not perform it."}
    messages.append({"role": "tool", "tool_call_id": pending["tool_call_id"], "content": json.dumps(result, default=str)})
    out = run_agent(messages, ctx, snapshot_time)
    out["trace"].insert(0, {"tool": pending["name"], "args": pending["args"], "result": result})
    return out
