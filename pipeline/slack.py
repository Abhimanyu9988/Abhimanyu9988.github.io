#!/usr/bin/env python3
"""Slack: where Abhi and the team talk. It uses the workspace's #general channel, and nothing here needs a server.

A bot token in SLACK_BOT_TOKEN turns this on. Without it, say() returns False and callers use the CTO desk issue.
Each agent posts under its own name from roles/registry.json. Only the workspace's primary owner can direct the team.
"""
import json, os, urllib.parse, urllib.request

API = "https://slack.com/api/"

def enabled():
    return bool(os.environ.get("SLACK_BOT_TOKEN"))

def api(method, **params):
    data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
    req = urllib.request.Request(API + method, data=data, headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        got = json.load(r)
    if not got.get("ok"):
        raise RuntimeError(f"slack {method}: {got.get('error')}")
    return got

def channel():
    return next(c["id"] for c in api("conversations.list", types="public_channel", limit=200)["channels"] if c.get("is_general"))

def owner():
    return next(u["id"] for u in api("users.list", limit=200)["members"] if u.get("is_primary_owner"))

def clean(text):
    """Slack control codes start with <, so escaping stops pasted or model text pinging the channel."""
    text = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("**", "*")   # Slack bold is one star
    return text[:3500]

def display(role):
    import roles
    r = roles.get(role)
    return f"{r.get('display', role)} ({r.get('dept', 'Team')})"

def say(role, text):
    """True when posted. A Slack outage must never stop the work it reports on."""
    if not enabled():
        return False
    try:
        cid = channel()
        api("conversations.join", channel=cid)
        api("chat.postMessage", channel=cid, text=clean(text), username=display(role), unfurl_links="false")
        return True
    except Exception as e:
        print(f"::warning title=slack::message not sent: {' '.join(str(e).split())[:200]}")
        return False

def messages(after):
    """(Abhi's messages newer than `after`, oldest first, newest timestamp seen). Bots and other people are ignored."""
    cid, me = channel(), owner()
    api("conversations.join", channel=cid)
    rows = api("conversations.history", channel=cid, oldest=after, limit=50)["messages"]
    mine = [m for m in rows if m.get("user") == me and not m.get("bot_id") and not m.get("subtype") and m.get("text")]
    return sorted(mine, key=lambda m: float(m["ts"])), max([m["ts"] for m in rows] + [after], key=float)

def history():
    """Abhi and the team only, so anyone else in the workspace stays out of the model's context."""
    me, rows = owner(), api("conversations.history", channel=channel(), limit=20)["messages"]
    kept = [m for m in rows if m.get("user") == me or m.get("bot_id")][:8]
    return [{"from": m.get("username") or "Abhi", "text": m.get("text", "")[:1500]} for m in reversed(kept)]
