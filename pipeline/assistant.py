#!/usr/bin/env python3
"""Executive Assistant: Abhi states a goal, and the team's priorities shift to serve it.

Commands, as comments by Abhi on any issue in this repo:
  /goal <what you want>    record a goal and answer with a plan for the team
  /goal drop <id>          stop working toward a goal
  /goals                   list active goals and the current plan

What it can change: analytics/goals.json and analytics/plan.json, on the analytics branch. Topic selection reads both.
What it cannot do: publish, spend, start a lab, deploy, or create a role. New capabilities come back as proposals.
"""
import datetime, json, os, re
from radar import comment, safe_text
import analytics, contract, roles, sources

GOALS, PLAN = sources.GOALS_PATH, sources.PLAN_PATH

def slug(title, taken):
    base = re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")[:40] or "goal"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name

def clean_plan(plan):
    """Only real, active roles receive work. Anything else becomes a proposal for Abhi to decide."""
    active = sources.roles()
    kept, needs = [], list(plan.get("needs") or [])
    for a in plan.get("assignments") or []:
        (kept if a.get("role") in active else needs).append(
            a if a.get("role") in active else f"No {a.get('role')} role exists: {a.get('task')}")
    return {"focus": plan.get("focus", ""), "assignments": kept, "needs": needs[:4]}

def load():
    text, sha = analytics.read_file(GOALS)
    return (json.loads(text).get("goals", []) if text.strip() else []), sha

def save(path, data, sha, msg):
    analytics.ensure_branch()
    analytics.write_file(path, json.dumps(data, indent=1) + "\n", sha, msg)

def plan_md(plan):
    lines = [f"**This period's focus:** {plan.get('focus')}", ""]
    lines += [f"- **{a['role']}:** {a['task']} ({a['why']})" for a in plan.get("assignments") or []]
    if plan.get("needs"):
        lines += ["", "**Not covered by the team yet (proposals, nothing was created):**"]
        lines += [f"- {n}" for n in plan["needs"]]
    return "\n".join(lines)

def add_goal(text, issue):
    goals, sha = load()
    goal = roles.ask("executive_assistant", f"Turn GOAL TEXT into one structured goal.\n\nGOAL TEXT:\n{text[:2000]}\n\n"
                                            f"EXISTING GOALS:\n{json.dumps(goals)[:3000]}", "goal")
    goal.update(id=slug(goal["title"], {g.get("id") for g in goals}), status="active", stated=text[:2000],
                created=datetime.date.today().isoformat())
    goals.append(goal)
    save(GOALS, {"goals": goals}, sha, f"goals: add {goal['id']}")
    plan = make_plan([g for g in goals if g.get("status") == "active"])
    comment(issue, safe_text(f"Goal saved as `{goal['id']}`, weight {goal['weight']} of 10.\n\n{plan_md(plan)}\n\nTopic "
                             "selection follows this from its next run. Drop it with `/goal drop` and the id."))

def make_plan(active):
    plan = clean_plan(roles.ask("executive_assistant", "Write a short plan for the team that serves these goals.\n\n"
                                f"GOALS:\n{json.dumps(active)[:5000]}\n\nROLES:\n{json.dumps(sources.roles())}\n\n"
                                f"RECENT POST MIX:\n{json.dumps(sources.post_mix())}", "plan"))
    _, sha = analytics.read_file(PLAN)
    save(PLAN, plan, sha, "goals: update plan")
    return plan

def drop_goal(gid, issue):
    goals, sha = load()
    hit = [g for g in goals if g.get("id") == gid]
    if not hit:
        comment(issue, safe_text(f"No goal called {gid}. Use `/goals` to see them.")); return
    hit[0]["status"] = "dropped"
    save(GOALS, {"goals": goals}, sha, f"goals: drop {gid}")
    comment(issue, safe_text(f"Dropped `{gid}`. Topic selection stops favouring it from its next run."))

def list_goals(issue):
    active = sources.goals()
    lines = [f"- `{g.get('id')}` (weight {g.get('weight')}): {g.get('outcome')}" for g in active] or ["No active goals."]
    plan = sources.plan()
    comment(issue, safe_text("\n".join(lines) + (f"\n\n{plan_md(plan)}" if plan.get("focus") else "")))

def parse(text):
    """('add', text) | ('drop', id) | ('list', '') | ('help', '')."""
    t = (text or "").strip()
    if re.match(r"/goals\b", t):
        return "list", ""
    m = re.match(r"/goal\s+drop\s+([a-z0-9-]+)\s*$", t)
    if m:
        return "drop", m[1]
    m = re.match(r"/goal\s+(\S[\s\S]*)$", t)
    return ("add", m[1].strip()) if m else ("help", "")

def main():
    issue, (cmd, arg) = int(os.environ["ISSUE"]), parse(os.environ.get("COMMENT_BODY", ""))
    if cmd == "add":
        add_goal(arg, issue)
    elif cmd == "drop":
        drop_goal(arg, issue)
    elif cmd == "list":
        list_goals(issue)
    else:
        comment(issue, "Use `/goal <what you want>`, `/goal drop <id>` or `/goals`.")

if __name__ == "__main__":
    main()
