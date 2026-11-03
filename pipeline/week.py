#!/usr/bin/env python3
"""The week's plan: the Editor picks the week's articles at once, and the team checks them before any lab money is spent.

Steps, cheapest first: one Editor call proposes every topic, one Lab Check call reviews them all, and code applies the rules. Only
topics that survive get a full brief, which is the Editor's searched brief and Evidence Contract, the same as before.
Inputs: new items from radar.collect(), his ideas, existing topics, goals and analytics.
Outputs: one issue per topic, analytics/week-plan.json, a Slack message, and `pick`, the first lab to start.
cto.py starts one lab a day from the plan until the week's target is met. It must not start a lab for a topic that failed a check.
"""
import datetime, json, os
from radar import (DOCS_URLS, HOME_REPO, LAB_LIMITS, LABEL, NO_REFS, SKIP_SOURCES, Unsafe, assert_safe, collect, comment, ensure_label,
                   existing_issues, goals_block, load_insights, market_brief, neutralize, norm, post_issue, safe_text, sources_block, src_marker)
import analytics, roles, slack

PLAN = "analytics/week-plan.json"
DEFAULT_TARGET = 3
WEEK_TARGETS = {"2026-W45": 5}                 # Abhi asked for five in his training week
SPARES, EXTRA_IDEAS = 1, 3                     # a spare covers a failed lab. Extra ideas cover topics the checks reject
# Abhi's model trial. Each lab and article uses one, in turn, and the same reviewers judge them all, so the stats compare like with like.
TRIAL_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5")

def week_id(day=None):
    y, w, _ = (day or datetime.date.today()).isocalendar()
    return f"{y}-W{w:02d}"

def target(day=None):
    return WEEK_TARGETS.get(week_id(day), DEFAULT_TARGET)

def propose(items, existing, want):
    return roles.ask("editor", f"Plan this week's articles. Give {want} topics, best first. {NO_REFS}\n\n"
                               f"Each title states the symptom, surprise or question, never the steps. Each topic teaches something the docs don't, "
                               f"with proof a lab can measure in minutes. If WHAT MARKETING FOUND opens with Abhi's own ideas, put those first unless "
                               f"a lab can't test them. Don't repeat EXISTING. Use only URLs from ITEMS as sources.\n\n"
                               f"GOALS:\n{goals_block()}\n\nLAB LIMITS:\n{LAB_LIMITS}\n\nWHAT HAS WORKED SO FAR:\n{load_insights() or 'no posts measured yet'}\n\n"
                               f"WHAT MARKETING FOUND:\n{market_brief() or 'no research yet'}\n\n"
                               f"EXISTING:\n{json.dumps(existing[:200])}\n\nITEMS:\n{json.dumps(items)[:30000]}", "weekplan")["topics"]

def lab_check(topics):
    """{index: reason} for topics a lab can't do. One call covers every topic, so nothing is paid for twice."""
    view = [{"id": n, "title": t["title"], "lab": t["lab"], "proof": t["proof"]} for n, t in enumerate(topics)]
    got = roles.ask("lab_check", f"LAB LIMITS:\n{LAB_LIMITS}\n\nTOPICS:\n{json.dumps(view)}", "labcheck")["verdicts"]
    return {v["id"]: v["reason"] for v in got if not v["doable"]}

def usable(t, allowed, have):
    """The topic's title and sources, or None. Code decides this, so a made-up source can't reach a lab."""
    title, srcs = neutralize(t.get("title")).strip()[:100], [u for u in t.get("sources") or [] if u in allowed]
    return (title, srcs) if title and srcs and norm(title) not in have else None

def body_for(t, srcs, gate_md):
    return (f"**Why read it:** {neutralize(t.get('hook'))}\n\n**What you'll build:** {neutralize(t.get('payoff'))}\n\n"
            f"**Proof it works:** {neutralize(t.get('proof'))}\n\n**Lab:** {neutralize(t.get('lab'))}\n\n{gate_md}\n\n{sources_block(srcs)}\n\n"
            f"**To run this lab yourself:** comment `approve` here. Close the issue if you're not interested.\n\n{src_marker(srcs)}")

def build_plan(topics, rejected, allowed, have, goal):
    """Briefs only the topics that passed, and stops at the target plus a spare."""
    import editor
    plan, dropped = [], []
    for n, t in enumerate(topics):
        if len(plan) >= goal + SPARES:
            break
        ok = usable(t, allowed, have)
        if not ok:
            dropped.append((t.get("title"), "repeats a topic, or has no real source"))
        elif n in rejected:
            dropped.append((ok[0], rejected[n]))
        else:
            title, srcs = ok
            try:
                b = editor.brief(title, f"{t.get('hook')}\n{t.get('proof')}\n{t.get('lab')}")
                passed, why = editor.gate1(b)
                if not passed:
                    dropped.append((title, "; ".join(map(str, why))[:200]))
                    continue
                b["gate1"] = {"passed": True, "reasons": []}
                body = body_for(t, srcs, f"{safe_text(editor.brief_md(b, True, []))}\n\n{editor.marker(b)}")
                assert_safe(title); assert_safe(body)
                plan.append({"title": title, "body": body, "kind": t.get("kind") or "build"})
            except (Unsafe, RuntimeError, ValueError) as e:
                dropped.append((title, str(e)[:200]))
    return plan, dropped

def plan_message(topics, goal, dropped):
    rows = "\n".join(f"{n}. {t['title']}" + (" (spare)" if n > goal else "") for n, t in enumerate(topics, 1))
    left = "\n".join(f"- {str(title)[:90]}: {str(why)[:140]}" for title, why in dropped[:5])
    return (f"This week's plan: {goal} articles, one lab a day.\n{rows}" + (f"\n\nDropped before any lab ran:\n{left}" if left else "")
            + ("" if len(topics) >= goal else f"\n\nOnly {len(topics)} of {goal} passed the checks."))

def main():
    existing, used = existing_issues()
    items = [i for i in collect() if (i["url"] not in used or i["url"] in DOCS_URLS) and i["url"] not in SKIP_SOURCES]
    goal = target()
    topics = propose(items, existing, goal + SPARES + EXTRA_IDEAS)
    rejected = lab_check(topics)
    plan, dropped = build_plan(topics, rejected, {i["url"] for i in items}, {norm(t) for t in existing}, goal)
    if os.environ.get("DRY_RUN") == "true":
        print(plan_message(plan, goal, dropped))
        return
    rows = []
    for l in (LABEL, "build", "problem"):
        ensure_label(l)
    for t in plan:
        iss = post_issue(t["title"], t["body"], LABEL, (t["kind"],), assign=False)
        rows.append({"issue": iss["number"], "title": t["title"], "state": "queued", "model": TRIAL_MODELS[len(rows) % len(TRIAL_MODELS)]})
    if rows:
        rows[0]["state"] = "started"
    _, sha = analytics.read_file(PLAN)
    analytics.write_file(PLAN, json.dumps({"week": week_id(), "target": goal, "created": datetime.date.today().isoformat(), "topics": rows}, indent=1), sha, "week: new plan")
    slack.say("editor", plan_message(rows, goal, dropped))
    if rows and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"pick={int(rows[0]['issue'])}\npick_model={rows[0]['model']}\n")

if __name__ == "__main__":
    analytics.ensure_branch()
    main()
