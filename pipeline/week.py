#!/usr/bin/env python3
"""The week's plan: the Editor picks the week's articles at once, and the team checks them before any lab money is spent.

Steps, cheapest first: one Editor call proposes every topic, code applies the rules and headline checks, and one Lab Check call
reviews them all. Claude, ChatGPT and Gemini then vote on each topic and headline, and two of three must agree. Only topics
that survive get a full brief, which is the Editor's searched brief and Evidence Contract, the same as before.
Inputs: new items from radar.collect(), his ideas, existing topics, goals and analytics.
Outputs: one issue per topic, analytics/week-plan.json, a Slack message, and `pick`, the first lab to start.
Every planned topic must answer a problem people raised recently, with a real signal behind it. Code checks that link.
TOPIC set: Abhi asked for an article on this subject. The same checks run, and its lab starts now, ahead of the plan.
cto.py starts one lab a day from the plan until the week's target is met. It must not start a lab for a topic that failed a check.
"""
import datetime, json, os, re
from radar import (DOCS_URLS, HOME_REPO, LAB_LIMITS, LABEL, NO_REFS, SKIP_SOURCES, Unsafe, assert_safe, collect, ensure_label,
                   existing_issues, gh, goals_block, load_insights, market_brief, neutralize, norm, post_issue, safe_text, sources_block, src_marker)
import analytics, roles, slack

PLAN = "analytics/week-plan.json"
DEFAULT_TARGET = 3
WEEK_TARGETS = {"2026-W45": 5}                 # Abhi asked for five in his training week
HOT_REPOS = ["fluent/fluent-bit", "open-telemetry/opentelemetry-collector-contrib", "open-telemetry/opentelemetry-operator",
             "signalfx/splunk-otel-collector-chart", "kedacore/keda", "argoproj/argo-cd",
             "open-telemetry/opentelemetry-python-contrib", "open-telemetry/opentelemetry-java-instrumentation",
             "open-telemetry/opentelemetry-js-contrib", "open-telemetry/semantic-conventions", "traceloop/openllmetry"]
GOALS = "Abhi's goals right now: be known for OpenTelemetry auto-instrumentation, become a Splunk MVP, and earn AWS Hero recognition. Every topic must serve one of them."
LANES = {"otel_auto_instrumentation": "OpenTelemetry auto-instrumentation, the Operator and zero-code agents",
         "ai_monitoring": "AI monitoring with Galileo: LLM and agent tracing, tokens, latency, cost and OpenTelemetry GenAI",
         "kubernetes": "Kubernetes operations and failures", "aws": "AWS services, especially containers",
         "splunk_observability": "Observability on Splunk Observability Cloud"}
MAX_PER_LANE = 2                               # no lane fills a week, so the plan keeps its variety
WISHLIST = re.compile(r"(?i)\b(request|enhancement|feature|proposal|roadmap)\b")   # someone wanting something new isn't someone stuck
MIN_REACTIONS, MIN_VIEWS = 3, 100               # below this, one person's question isn't yet a hot problem
ROUNDS = 2                                     # a second Editor call, told why the first topics were refused
SPARES, EXTRA_IDEAS = 1, 3                     # a spare covers a failed lab. Extra ideas cover topics the checks reject
# Abhi's model trial. Each lab and article uses one, in turn, and the same reviewers judge them all, so the stats compare like with like.
WEAK_TITLE = re.compile(r"(?i)^(how to|a guide|guide to|the complete|complete guide|getting started|introduction|intro to|setting up|set up|"
                        r"deploy(ing)?|install(ing)?|using|understanding|everything you|\d+ (ways|tips|best)|best practices)\b")
QUESTION = ("Should this become a post on Abhimanyu's blog, under exactly this headline? Readers are working engineers on AWS containers and "
            "observability who find posts through a symptom they hit: an error, a stuck deployment, a wrong number or a surprise bill. Vote yes only "
            "if a busy ECS or EKS engineer would stop at the headline, the headline names a concrete symptom, surprise or number, the hook teaches "
            "something the docs don't say, and the proof can deliver everything the headline promises. Vote no for setup walkthroughs, headlines "
            "that describe steps, vague or clickbait headlines, topics most engineers would scroll past, and anything that repeats EXISTING.")
TRIAL_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5")

def week_id(day=None):
    y, w, _ = (day or datetime.date.today()).isocalendar()
    return f"{y}-W{w:02d}"

def target(day=None):
    return WEEK_TARGETS.get(week_id(day), DEFAULT_TARGET)

def galileo_ready():
    return os.environ.get("GALILEO_READY") == "true"

def hot_items():
    """{url: signal} and a short list for the Editor. Free API calls, and real counts, so demand is shown instead of guessed."""
    import problems
    problems.WATCH = problems.WATCH + [r for r in HOT_REPOS if r not in problems.WATCH]
    hot, view = {}, []
    for i in problems.collect():
        heat = i.get("reactions", 0) + len(i.get("comments") or [])
        if WISHLIST.search(f"{i['title']} {' '.join(map(str, i.get('labels') or []))} {i['url']}"):
            continue
        if i["src"] == "github" and heat >= MIN_REACTIONS:
            signal = f"{i.get('reactions', 0)} reactions and {len(i.get('comments') or [])} comments on a GitHub issue"
        elif i["src"] == "stackoverflow" and i.get("views", 0) >= MIN_VIEWS:
            signal = f"{i.get('views')} views and a score of {i.get('score')} on Stack Overflow"
        else:
            continue
        hot[i["url"]] = signal
        view.append({"title": i["title"], "url": i["url"], "signal": signal, "says": str(i.get("reporter_says") or "")[:300]})
    return hot, view

def propose(items, existing, want, refused=(), hot=()):
    tried = f"\n\nALREADY REFUSED THIS WEEK. Learn from these reasons, and don't offer the same idea again:\n{json.dumps(list(refused))[:4000]}" if refused else ""
    return roles.ask("editor", f"Plan this week's articles. Give {want} topics, best first. {NO_REFS}\n\n"
                               f"Every topic must solve one problem from HOT PROBLEMS, which are real complaints from the last 30 days. Put its url in demand_url, "
                               f"and use the reader's own words for the symptom. "
                               f"{GOALS} Every topic must sit in one lane, given in lane: {json.dumps(LANES)}. "
                               f"{'Include at least one ai_monitoring topic if a real complaint supports it. It must use Galileo, with a small open model on EC2, because the lab has no paid AI API. ' if galileo_ready() else 'Offer no ai_monitoring topics yet, because the lab has no Galileo key. '}"
                               f"Vary the week: no two topics on the same service or failure. Abhi has rejected repeated ALB posts, so offer at most one ALB topic, "
                               f"and cover different ground, such as ECS, logs, OpenTelemetry and Splunk Observability. "
                               f"The topic and headline matter most, so spend your effort there. Write three headlines for each topic and keep the strongest. "
                               f"A headline names the technology and a concrete symptom, surprise or number a busy engineer recognises, in 30 to 75 characters. "
                               f"It never describes steps, never says how to, and never promises more than the proof can show. "
                               f"Each topic teaches something the docs don't, "
                               f"with proof a lab can measure in minutes. If WHAT MARKETING FOUND opens with Abhi's own ideas, put those first unless "
                               f"a lab can't test them. Don't repeat EXISTING. Use only URLs from ITEMS as sources.\n\n"
                               f"GOALS:\n{goals_block()}\n\nLAB LIMITS:\n{LAB_LIMITS}\n\nWHAT HAS WORKED SO FAR:\n{load_insights() or 'no posts measured yet'}\n\n"
                               f"WHAT MARKETING FOUND:\n{market_brief() or 'no research yet'}\n\n"
                               f"HOT PROBLEMS:\n{json.dumps(list(hot))[:14000]}\n\nEXISTING:\n{json.dumps(existing[:200])}\n\nITEMS:\n{json.dumps(items)[:16000]}{tried}", "weekplan")["topics"]

def lab_check(topics):
    """{index: reason} for topics a lab can't do. One call covers every topic, so nothing is paid for twice."""
    view = [{"id": n, "title": t["title"], "lab": t["lab"], "proof": t["proof"]} for n, t in enumerate(topics)]
    got = roles.ask("lab_check", f"LAB LIMITS:\n{LAB_LIMITS}\n\nTOPICS:\n{json.dumps(view)}", "labcheck")["verdicts"]
    said = {}
    for v in got:
        said.setdefault(v["id"], []).append(v)
    out = {}
    for n in range(len(topics)):                # no clear yes means no, so a skipped topic can't reach a lab unchecked
        rows = said.get(n, [])
        if len(rows) != 1:
            out[n] = "Lab Check gave no clear answer on this topic"
        elif not rows[0]["doable"]:
            out[n] = rows[0]["reason"]
    return out

def title_problem(title):
    """A plain reason, or "". These are cheap checks, and the panel judges the rest."""
    if not 30 <= len(title) <= 80:
        return f"the headline is {len(title)} characters, outside 30 to 80"
    return "the headline describes steps instead of a symptom or surprise" if WEAK_TITLE.match(title) else ""

def panel(topics, rejected, existing):
    """{index: yes votes} for the topics still in. Every topic in one call per model, so the vote stays cheap."""
    import llm
    view = [{"id": f"t{n}", "headline": t["title"], "hook": t["hook"], "payoff": t["payoff"], "proof": t["proof"]} for n, t in enumerate(topics) if n not in rejected]
    votes = llm.panel_votes(view, QUESTION, f"LAB LIMITS:\n{LAB_LIMITS}\n\nEXISTING:\n{json.dumps(existing[:150])}", tier="write")
    return {int(k[1:]): v for k, v in votes.items()}

def usable(t, allowed, have):
    """The topic's title and sources, or None. Code decides this, so a made-up source can't reach a lab."""
    title, srcs = neutralize(t.get("title")).strip()[:100], [u for u in t.get("sources") or [] if u in allowed]
    return (title, srcs) if title and srcs and norm(title) not in have else None

def body_for(t, srcs, gate_md):
    vote = f"**Panel vote:** {t['yes']} of 3\n\n" if t.get("yes") else ""
    vote += f"**Why it's hot:** {t['signal']}\n\n" if t.get("signal") else ""
    return (f"{vote}**Why read it:** {neutralize(t.get('hook'))}\n\n**What you'll build:** {neutralize(t.get('payoff'))}\n\n"
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
        if sum(p.get("lane") == t.get("lane") for p in plan) >= MAX_PER_LANE:
            dropped.append((t.get("title"), f"this week already has {MAX_PER_LANE} {t.get('lane')} topics"))
        elif not ok:
            dropped.append((t.get("title"), "repeats a topic, or has no real source"))
        elif n in rejected:
            dropped.append((ok[0], rejected[n]))
        else:
            title, srcs = ok
            try:
                b = editor.brief(title, f"Real demand: {t.get('signal')} ({t.get('demand_url')})\n{t.get('hook')}\n{t.get('proof')}\n{t.get('lab')}")
                passed, why = editor.gate1(b)
                if not passed:
                    dropped.append((title, "; ".join(map(str, why))[:200]))
                    continue
                b["gate1"] = {"passed": True, "reasons": []}
                body = body_for(t, srcs, f"{safe_text(editor.brief_md(b, True, []))}\n\n{editor.marker(b)}")
                assert_safe(title); assert_safe(body)
                plan.append({"title": title, "body": body, "kind": t.get("kind") or "build", "hook": t.get("hook"), "yes": t.get("yes"), "signal": t.get("signal"), "lane": t.get("lane")})
            except (Unsafe, RuntimeError, ValueError) as e:
                dropped.append((title, str(e)[:200]))
    return plan, dropped

def plan_message(topics, goal, dropped):
    rows = "\n".join(f"{n}. {t['title']}" + (f" ({t['signal']})" if t.get("signal") else "") + (" (spare)" if n > goal else "") for n, t in enumerate(topics, 1))
    left = "\n".join(f"- {str(title)[:90]}: {str(why)[:140]}" for title, why in dropped[:5])
    return (f"This week's plan: {goal} articles, one lab a day.\n{rows}" + (f"\n\nDropped before any lab ran:\n{left}" if left else "")
            + ("" if len(topics) >= goal else f"\n\nOnly {len(topics)} of {goal} passed the checks."))

def vet(topics, existing, allowed, have, need, hot):
    """(plan, dropped) for one round of topics, cheapest check first."""
    for t in topics:
        t["signal"] = hot.get(t.get("demand_url"), "")
        t["sources"] = [*(t.get("sources") or []), *([t["demand_url"]] if t["signal"] else [])]
    rejected = {n: "no real recent complaint behind it" for n, t in enumerate(topics) if not t["signal"]}
    rejected.update({n: "AI monitoring needs the Galileo key" for n, t in enumerate(topics) if t.get("lane") == "ai_monitoring" and not galileo_ready() and n not in rejected})
    rejected.update({n: title_problem(t["title"]) for n, t in enumerate(topics) if title_problem(t["title"]) and n not in rejected})
    rejected.update({n: why for n, why in lab_check(topics).items() if n not in rejected})
    votes = panel(topics, rejected, existing)
    rejected.update({n: "the panel didn't vote on this topic" for n in range(len(topics)) if n not in rejected and n not in votes})
    for n, v in votes.items():
        topics[n]["yes"] = v["yes"]
        if not v["accepted"]:
            no = next((why for vote, why in v["votes"].values() if vote == "no"), "")
            rejected[n] = f"the panel voted {v['yes']} of 3: {no}"[:200]
    ranked = sorted(topics, key=lambda t: t.get("yes", 0), reverse=True)     # sorted keeps the Editor's order on a tie
    return build_plan(ranked, {ranked.index(topics[n]): why for n, why in rejected.items()}, allowed, have, need)

def requested(subject):
    """His subject, three angles, the same checks. The vote only ranks angles, because he chose the subject. Returns (topic, why not)."""
    import editor, llm
    topics = roles.ask("editor", f"Abhi asked for an article on this subject. Stay on his subject. Give three angles, best first, each with its strongest "
                                 f"headline. A headline names the technology and a concrete symptom, surprise or number, in 30 to 75 characters. It never describes "
                                 f"steps. {NO_REFS}\n\nHIS SUBJECT:\n{subject[:800]}\n\nGOALS:\n{goals_block()}\n\nLAB LIMITS:\n{LAB_LIMITS}", "weekplan")["topics"][:3]
    rejected = {n: title_problem(t["title"]) for n, t in enumerate(topics) if title_problem(t["title"])}
    rejected.update({n: why for n, why in lab_check(topics).items() if n not in rejected})
    votes = panel(topics, rejected, [])
    order = sorted((n for n in range(len(topics)) if n not in rejected), key=lambda n: votes.get(n, {}).get("yes", 0), reverse=True)
    for n in order:
        t = topics[n]
        title = neutralize(t.get("title")).strip()[:100]
        b = editor.brief(title, f"Requested by Abhi: {subject[:400]}\n{t.get('hook')}\n{t.get('proof')}\n{t.get('lab')}")
        passed, why = editor.gate1(b)
        if not passed:
            rejected[n] = "; ".join(map(str, why))[:200]
            continue
        b["gate1"] = {"passed": True, "reasons": []}
        srcs = [p["url"] for p in b.get("existing_pages") or [] if p.get("url")][:5]
        body = body_for(t, srcs, f"{safe_text(editor.brief_md(b, True, []))}\n\n{editor.marker(b)}")
        assert_safe(title); assert_safe(body)
        return {"title": title, "body": body, "kind": t.get("kind") or "build"}, ""
    return None, "; ".join(str(w)[:160] for w in list(rejected.values())[:3]) or "the team couldn't form a topic from it"

def start_requested(subject):
    t, why = requested(subject)
    if not t:
        slack.say("editor", f"I can't run this one yet: {subject[:200]}\n\nWhy: {why}\n\nNo lab ran. Tell me another angle, or say what you'd like changed.")
        return
    ensure_label(LABEL); ensure_label(t["kind"])
    number = post_issue(t["title"], t["body"], LABEL, (t["kind"],), assign=False)["number"]
    text, sha = analytics.read_file(PLAN)
    plan = json.loads(text) if text.strip() else {"week": week_id(), "target": 0, "created": datetime.date.today().isoformat(), "topics": []}
    plan["target"] = plan.get("target", 0) + 1     # it comes on top of the week's target, so a planned article isn't dropped for it
    plan["topics"].insert(0, {"issue": number, "title": t["title"], "state": "started", "started_on": datetime.date.today().isoformat(), "requested": True})
    analytics.write_file(PLAN, json.dumps(plan, indent=1), sha, "week: Abhi's article")
    slack.say("editor", f"On it. Headline: {t['title']}\n\nThe lab is starting now. It takes up to an hour, and you'll get a PR to approve if it works.")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"pick={int(number)}\npick_model=\n")

def main():
    existing, used = existing_issues()
    items = [i for i in collect() if (i["url"] not in used or i["url"] in DOCS_URLS) and i["url"] not in SKIP_SOURCES]
    hot, hot_view = hot_items()
    goal, allowed, have = target(), {i["url"] for i in items} | set(hot), {norm(t) for t in existing}
    want = goal + SPARES
    plan, dropped = [], []
    for _ in range(ROUNDS):
        if len(plan) >= want:
            break
        new, out = vet(propose(items, existing, want - len(plan) + EXTRA_IDEAS, dropped, hot_view), existing, allowed, have, want - len(plan) - SPARES, hot)
        plan, dropped = plan + new, dropped + out
        have |= {norm(t["title"]) for t in new}
    if os.environ.get("DRY_RUN") == "true":
        print(plan_message(plan, goal, dropped))
        for t in plan:
            print(f"\nTOPIC {t.get('yes')}/3: {t['title']}\nHOT: {t.get('signal')}\nHOOK: {t.get('hook')}")
        for title, why in dropped:
            print(f"\nDROPPED: {title}\nWHY: {why}")
        return
    rows = []
    for l in (LABEL, "build", "problem"):
        ensure_label(l)
    for t in plan:
        number = t.get("issue") or post_issue(t["title"], t["body"], LABEL, (t["kind"],), assign=False)["number"]
        rows.append({"issue": number, "title": t["title"], "state": "queued", "signal": t.get("signal"), "model": TRIAL_MODELS[len(rows) % len(TRIAL_MODELS)]})
    if rows:
        rows[0].update(state="started", started_on=datetime.date.today().isoformat())
    _, sha = analytics.read_file(PLAN)
    analytics.write_file(PLAN, json.dumps({"week": week_id(), "target": goal, "created": datetime.date.today().isoformat(), "topics": rows}, indent=1), sha, "week: new plan")
    slack.say("editor", plan_message(rows, goal, dropped))
    if rows and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"pick={int(rows[0]['issue'])}\npick_model={rows[0]['model']}\n")

if __name__ == "__main__":
    analytics.ensure_branch()
    start_requested(os.environ["TOPIC"]) if os.environ.get("TOPIC", "").strip() else main()
