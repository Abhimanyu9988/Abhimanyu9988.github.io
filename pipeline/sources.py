#!/usr/bin/env python3
"""Data-source adapters. A role names sources in roles/registry.json; each name maps to one function here.

To change where a role gets data, change or add one adapter. Roles and prompts stay the same.
Every adapter returns plain text or JSON-ready data, and returns an empty value when the source is unavailable,
so a missing source degrades a role instead of crashing it.
"""
import base64, datetime, json, pathlib, re

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
GOALS_PATH, PLAN_PATH = "analytics/goals.json", "analytics/plan.json"

def _analytics_json(path):
    from radar import HOME_REPO, gh
    try:
        f = gh("GET", f"/repos/{HOME_REPO}/contents/{path}?ref=analytics")
        return json.loads(base64.b64decode(f.get("content", "")).decode("utf-8", "replace") or "{}")
    except Exception as e:
        print(f"note: {path} not read: {str(e)[:160]}")
        return {}

def goals():
    """Active goals, highest weight first. Empty until Abhi sets one with `/goal`."""
    got, today = _analytics_json(GOALS_PATH).get("goals"), datetime.date.today().isoformat()
    return sorted((g for g in (got if isinstance(got, list) else []) if _usable(g, today)), key=lambda g: -g["weight"])

def _usable(g, today):
    """One badly edited goal is skipped. It must not empty the list or stop a run."""
    if not isinstance(g, dict) or g.get("status", "active") != "active" or not isinstance(g.get("weight"), int):
        return False
    until = g.get("until") or ""
    return not until or (isinstance(until, str) and until >= today)

def plan():
    return _analytics_json(PLAN_PATH)

def field_notes():
    p = HERE / "field-notes.md"
    if not p.exists():
        return ""
    return "\n".join(l for l in p.read_text().split("## Notes", 1)[-1].splitlines() if l.startswith("- "))[:4000] \
        if "## Notes" in p.read_text() else ""

def post_mix(last=10):
    """How many recent lab posts feature Splunk. Deterministic: read from the post files."""
    posts = sorted((ROOT / "site" / "_posts").glob("*.md"), reverse=True)
    posts = [p for p in posts if "open-source-summary" not in p.name][:last]
    splunk = sum(bool(re.search(r"(?i)splunk", p.read_text(errors="replace")[:600])) for p in posts)
    return {"recent_posts": len(posts), "featuring_splunk": splunk}

def roles():
    return {k: v.get("mission", "") for k, v in json.loads((HERE / "roles" / "registry.json").read_text())["roles"].items()
            if v.get("status") == "active"}

def agents_md():
    p = ROOT / "AGENTS.md"
    return p.read_text()[:8000] if p.exists() else ""

ADAPTERS = {"goals": goals, "plan": plan, "field_notes": field_notes, "post_mix": post_mix, "roles": roles,
            "agents_md": agents_md}
# Supplied by the caller at run time, not read here: the role's task provides them.
RUNTIME = {"web_search", "pr_diff", "scanner_reports", "insights"}

def known(name):
    return name in ADAPTERS or name in RUNTIME
