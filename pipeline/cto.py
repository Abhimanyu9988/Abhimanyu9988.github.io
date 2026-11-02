#!/usr/bin/env python3
"""The leadership desk: CEO, CTO, CMO and CFO. Abhi texts them in Slack, and the CTO desk issue works until Slack is on.

A message starting with a title, such as "CMO: ...", goes to that leader. Anything else goes to the CEO.

  python3 pipeline/cto.py   no input: refresh the public team status, and on Mondays run market research and the digest.
                            POLL=true: answer Abhi's new Slack messages. No server is needed, so replies take minutes.
                            COMMENT_BODY: answer Abhi on the CTO desk issue.

What leaders can do: answer, record a goal he stated, and assign a tracked task to an active role.
What it cannot do: publish, spend, deploy, change the budget, or hold a credential. Links are checked by code first.
"""
import base64, datetime, ipaddress, json, os, re, socket, urllib.error, urllib.parse, urllib.request
from radar import HOME_REPO, comment, ensure_label, gh, post_issue, safe_text
import analytics, budget, contract, roles, slack, sources

TASKS, STATUS, CURSOR = "analytics/tasks.json", "analytics/status.json", "analytics/slack-cursor.json"
ADMIN_REPO = "Abhimanyu9988/team-admin"        # private. This repo is public, so its analytics branch is too
WORKFLOW_OWNER = {"radar": "topic_scout", "replicate": "lab_engineer", "analytics": "analyst", "publish": "publisher"}
URL = re.compile(r"https?://[^\s)\]>]+")
MAX_TASKS = 40
LEADER = re.compile(r"\s*@?(ceo|cto|cmo|cfo)\b[\s:,-]*", re.I)
STATED_GOAL = re.compile(r"(?i)\b(goals?|targets?|aim|want|objective)\b")
BRIEF = "analytics/market-brief.md"
MAX_REPLIES = 5                                # each poll. CTO replies run past the monthly limit, so this bounds them

def desk():
    number = budget.desk()
    if number:
        return number
    ensure_label("cto-desk")
    return post_issue("CTO desk", "Reply to any email from this thread to talk to the CTO. Budget, progress and work in "
                                  "progress are posted here each Monday.", "cto-desk")["number"]

def post(text, role="cto"):
    if not slack.say(role, text):
        comment(desk(), safe_text(text))

def admin_on():
    return bool(os.environ.get("ADMIN_TOKEN"))

def admin_api(method, path, body=None):
    """The one write outside radar.gh: its own token, this one private repo, and file contents only."""
    req = urllib.request.Request(f"https://api.github.com/repos/{ADMIN_REPO}/contents/{path}", method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {os.environ['ADMIN_TOKEN']}", "User-Agent": "team",
                                          "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)

def admin_read(path):
    try:
        f = admin_api("GET", path)
        return base64.b64decode(f.get("content", "")).decode("utf-8"), f.get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "", None
        raise

def admin_write(path, text, sha, msg):
    body = {"message": msg, "content": base64.b64encode(text.encode()).decode()}
    admin_api("PUT", path, {**body, "sha": sha} if sha else body)

def read_private(path):
    return admin_read(path.split("/")[-1]) if admin_on() else ("", None)

def write_private(path, text, sha, msg):
    """Never falls back to this public repo. Without the private repo, nothing is stored."""
    if not admin_on():
        return False
    admin_write(path.split("/")[-1], text, sha, msg)
    return True

def tasks():
    text, sha = read_private(TASKS)
    got = json.loads(text).get("tasks") if text.strip() else None
    return (got if isinstance(got, list) else []), sha

def add_task(task):
    """Only an active role can own work. Anything else comes back as a named gap."""
    active = sources.roles()
    if task.get("owner") not in active:
        return f"No {safe_text(str(task.get('owner')))} role exists, so this was not assigned."
    got, sha = tasks()
    row = {"title": str(task.get("title"))[:160], "owner": task["owner"], "status": "open",
           "created": datetime.date.today().isoformat()}
    keep = [t for t in got if t.get("title") != row["title"]]
    if not write_private(TASKS, json.dumps({"tasks": (keep + [row])[-MAX_TASKS:]}, indent=1), sha, "tasks: add"):
        return "Task not saved, because the private admin repo isn't connected yet."
    return f"Assigned to {row['owner']}: {row['title']}."

def public_https(url):
    """Model-written links run on a runner that holds keys, so private and internal addresses are refused."""
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.port not in (None, 443):
        return False
    try:
        return all(ipaddress.ip_address(a[4][0]).is_global for a in socket.getaddrinfo(u.hostname, 443))
    except (OSError, ValueError):
        return False

class CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public_https(newurl):
            raise urllib.error.URLError("redirect refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def link_exists(url):
    """True when the page answers. A sign-in page counts: 401 and 403 show it exists, and Abhi is the one signing in."""
    if not public_https(url):
        return False
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.build_opener(CheckedRedirect).open(req, timeout=10):
            return True
    except urllib.error.HTTPError as e:
        return e.code in (401, 403)
    except Exception:
        return False

def progress():
    """Facts from GitHub and the spend record. A failed source is left out by the caller, never guessed."""
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)).isoformat()[:19] + "Z"
    pulls = gh("GET", f"/repos/{HOME_REPO}/pulls?state=all&sort=updated&direction=desc&per_page=50") or []
    drafts = [p for p in pulls if p["head"]["ref"].startswith("draft/")]
    runs = gh("GET", f"/repos/{HOME_REPO}/actions/runs?status=failure&per_page=50&created=%3E{since[:10]}") or {}
    running = gh("GET", f"/repos/{HOME_REPO}/actions/runs?status=in_progress&per_page=30") or {}
    total, jobs = budget.spent()
    return {"waiting": [p["title"] for p in drafts if p["state"] == "open"],
            "shipped": [p["title"] for p in drafts if (p.get("merged_at") or "") >= since],
            "failed_runs": sorted({r.get("name", "?") for r in runs.get("workflow_runs", [])}),
            "running": sorted({r.get("name", "?") for r in running.get("workflow_runs", [])}),
            "spent": total, "limit": budget.limit(), "by_job": jobs, "tasks": [t for t in tasks()[0] if t.get("status") == "open"]}

def roster(p):
    """Everyone, by department, with what each one is doing now."""
    busy = {WORKFLOW_OWNER[w]: "working now" for w in p.get("running", []) if w in WORKFLOW_OWNER}
    rows = []
    for key, r in sorted(roles.registry().items(), key=lambda kv: (kv[1].get("dept", ""), kv[1].get("display", ""))):
        if r.get("status") == "active":
            work = [t["title"] for t in p.get("tasks", []) if t.get("owner") == key] + ([busy[key]] if key in busy else [])
            rows.append(f"- {r.get('dept')}: {r.get('display')}: {'; '.join(work) if work else 'idle'}")
    return rows

def activity():
    """Every workflow run today: (name, result, count). This is the record of what the team did."""
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    runs = gh("GET", f"/repos/{HOME_REPO}/actions/runs?per_page=100&created=%3E%3D{today}") or {}
    seen = {}
    for r in runs.get("workflow_runs", []):
        if r.get("conclusion") != "skipped":
            key = (r.get("name", "?"), r.get("conclusion") or "running")
            seen[key] = seen.get(key, 0) + 1
    return sorted((n, c, k) for (n, c), k in seen.items() if n != "cto")

def results():
    """Latest views per post, from the ledger Analyst keeps. A post with no row is left out, never guessed."""
    rows, _ = analytics.load_ledger()
    latest = {}
    for r in rows:
        if r.get("metric") == "views":
            latest[(r.get("post"), r.get("platform"))] = int(r.get("value") or 0)
    return sorted(((v, p, s) for (p, s), v in latest.items()), reverse=True)[:10]

def facts(p):
    """What the manager judges. The cost per article is arithmetic, so code does it."""
    done = len(p["shipped"])
    return {**p, "cost_per_published_article_this_week": round(p["spent"] / done, 2) if done else None, "views": results()}

def manager_review(p):
    """Advice only. A failed review leaves the page intact and says so."""
    try:
        got = roles.ask("manager", f"FACTS:\n{json.dumps(facts(p))[:6000]}\n\nTEAM:\n{json.dumps(sources.roles())}", "managerreview")
        return (f"**Operations Manager (Office): {got['verdict'].replace('_', ' ')}.** {got['summary']}"
                + "".join(f"\n- {a}" for a in got["actions"]))
    except Exception as e:
        print(f"::warning title=manager::review skipped: {' '.join(str(e).split())[:200]}")
        return "**Operations Manager (Office):** this week's review did not run."

def admin_md(p, day, review=""):
    """The private admin page. Code writes every number, so nothing here depends on a model."""
    lines = [f"# Team admin: {day}", "", review, "", "## Budget", "", f"${p['spent']:.2f} of ${p['limit']:.2f} this month, at list prices "
             "plus AWS's figure or lab estimates. These are not invoices.", ""]
    lines += [f"- {k}: ${v:.2f}" for k, v in sorted(p["by_job"].items(), key=lambda kv: -kv[1])] or ["- No spending recorded."]
    lines += ["", "## Today", ""] + ([f"- {n}: {c} ({k})" for n, c, k in activity()] or ["- Nothing ran."])
    lines += ["", "## Articles", "", f"Waiting for you: {', '.join(p['waiting']) or 'none'}", "", f"Published this week: {', '.join(p['shipped']) or 'none'}"]
    lines += ["", "## Readers", ""] + ([f"- {v} views: {post} ({s})" for v, post, s in results()] or ["- No view numbers yet."])
    lines += ["", "## Team", ""] + roster(p)
    return "\n".join(lines) + "\n"

def save_admin(p, review=""):
    if not admin_on():
        return
    day = datetime.date.today().isoformat()
    try:
        page = admin_md(p, day, review)
        path = f"log-{day}.md"
        admin_write(path, page, admin_read(path)[1], f"log: {day}")
        admin_write("README.md", page, admin_read("README.md")[1], f"admin: {day}")
    except Exception as e:
        print(f"::warning title=admin::page not saved: {' '.join(str(e).split())[:200]}")

def digest_md(p):
    lines = [f"**Budget:** ${p['spent']:.2f} of ${p['limit']:.2f} this month, at list prices plus lab estimates.", ""]
    for head, rows in (("Waiting for your decision", p["waiting"]), ("Published this week", p["shipped"]),
                       ("Workflows that failed this week", p["failed_runs"])):
        lines += [f"**{head}:** " + (", ".join(safe_text(r) for r in rows) if rows else "none"), ""]
    lines += ["**The team:**"] + [safe_text(r) for r in roster(p)]
    return "\n".join(lines + ["", "Reply here to talk to me."])

def public_status(p):
    """For the public team page. No spending, tasks or failures: Abhi's requests stay private."""
    team = [{"role": r.get("display"), "dept": r.get("dept"), "mission": r.get("mission")} for r in roles.registry().values()
            if r.get("status") == "active"]
    return {"updated": datetime.date.today().isoformat(), "team": team, "in_review": p["waiting"], "published": p["shipped"]}

def thread(number):
    rows = gh("GET", f"/repos/{HOME_REPO}/issues/{number}/comments?per_page=100") or []
    return [{"from": "Abhi" if c["user"]["login"] == "Abhimanyu9988" else "CTO", "text": c["body"][:1500]} for c in rows[-8:]]

def route(text):
    """(leader, message). The CEO takes anything not addressed to a leader."""
    hit = LEADER.match(text)
    return (hit[1].lower(), text[hit.end():].strip() or text) if hit else ("ceo", text)

def steps_md(steps):
    return "*What I need from you:*\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)) if steps else ""

def research():
    """How real people reached the top goal. Only real pages count, and it respects the monthly limit."""
    active = sources.goals()
    if not active or budget.spent()[0] >= budget.limit():
        return ""
    import llm
    goal = active[0]
    found = llm.web_research(f"How did people achieve this, and what did they publish to get there: {goal.get('outcome') or goal.get('title')}. "
                             "Find real examples, their content and how they became visible.", max_uses=4) or {}
    pages = found.get("pages") or {}
    if not pages:
        return ""
    got = roles.ask("market_research", f"GOAL:\n{json.dumps(goal)[:1500]}\n\nSUMMARY:\n{str(found.get('summary'))[:5000]}\n\n"
                                       f"PAGES:\n{json.dumps(pages)[:5000]}", "marketbrief")
    real = [u for u in got.get("sources") or [] if u in pages]
    text = "\n".join(["What worked for others:"] + [f"- {x}" for x in got["what_worked"]] + ["", "Topics to try:"] +
                      [f"- {x}" for x in got["topics_to_try"]] + ["", "Avoid:"] + [f"- {x}" for x in got["avoid"]] + ["", "Sources:"] + [f"- {u}" for u in real])
    _, sha = analytics.read_file(BRIEF)
    analytics.write_file(BRIEF, text + "\n", sha, "market: weekly brief")
    return text

def answer(text, talk, role="ceo"):
    wanted = budget.requested_limit(text)
    if wanted:
        budget.set_limit(wanted)
        post(f"Done. This month's limit is now ${wanted}. It returns to ${budget.LIMIT_USD} next month.", "cfo")
        return
    p, team = progress(), roles.data("cto")
    brief, _ = analytics.read_file(BRIEF)
    got = roles.ask(role, f"ABHI SAID:\n{text[:4000]}\n\nCONVERSATION:\n{json.dumps(talk)[:6000]}\n\nFACTS:\n"
                           f"{json.dumps({**p, 'team': roster(p)})[:4000]}\n\nGOALS AND TEAM:\n{json.dumps(team)[:4000]}"
                          f"\n\nMARKET BRIEF:\n{brief[:2500]}", "ctoreply")
    reply = URL.sub("[link removed]", got["reply"])
    good = [u for u in got.get("urls") or [] if link_exists(u)]
    notes = [add_task(got["task"])] if (got.get("task") or {}).get("title") else []
    bad = len(got.get("urls") or []) - len(good)
    notes += [f"{bad} link(s) could not be confirmed, so I left them out."] if bad else []
    links = "\n".join(f"- {u}" for u in good)
    post("\n\n".join(x for x in (reply, links, " ".join(notes), steps_md(got.get("needs_abhi"))) if x), role)
    if got.get("goal") and STATED_GOAL.search(text):   # his words are recorded, and a model's idea never becomes a goal
        import assistant
        assistant.comment = lambda n, t: post(t, "ceo")
        assistant.add_goal(text, 0)
        try:
            brief = research()
            if brief:
                post(f"Here is how others reached this goal. The Topic Scout now works from it.\n\n{brief[:3000]}", "cmo")
        except Exception as e:
            print(f"::warning title=cmo::research skipped: {' '.join(str(e).split())[:200]}")

def routed(text):
    role, message = route(text)
    return message, slack.history(), role

def poll():
    """Slack messages since the last run. The cursor moves first, so one bad message cannot repeat every ten minutes."""
    if not slack.enabled():
        return
    text, sha = analytics.read_file(CURSOR)
    after = json.loads(text)["ts"] if text.strip() else f"{datetime.datetime.now(datetime.timezone.utc).timestamp():.6f}"
    try:
        mine, newest = slack.messages(after)
    except Exception as e:
        print(f"::warning title=slack::messages not read: {' '.join(str(e).split())[:200]}")
        return
    if newest != after or not sha:
        analytics.write_file(CURSOR, json.dumps({"ts": newest}), sha, "slack: cursor")
    for m in mine[-MAX_REPLIES:]:
        try:
            answer(*routed(m["text"]))
        except Exception as e:
            print(f"::warning title=cto::reply failed: {' '.join(str(e).split())[:200]}")
            post("I could not answer that just now. Please send it again.", "ceo")

def main():
    if os.environ.get("POLL") == "true":
        poll()
        return
    text = os.environ.get("COMMENT_BODY", "").strip()
    if text:
        role, message = route(text)
        answer(message, thread(desk()), role)
        return
    p = progress()
    _, sha = analytics.read_file(STATUS)
    analytics.write_file(STATUS, json.dumps(public_status(p), indent=1), sha, "status: refresh")
    weekly = datetime.date.today().weekday() == 0 or os.environ.get("DIGEST") == "true"
    review = manager_review(p) if weekly else ""
    if datetime.date.today().weekday() == 0:
        try:
            brief = research()
            if brief:
                post(f"This week's market brief. The Topic Scout works from it.\n\n{brief[:3000]}", "cmo")
        except Exception as e:
            print(f"::warning title=cmo::research skipped: {' '.join(str(e).split())[:200]}")
    save_admin(p, review)
    if weekly:
        post(f"{digest_md(p)}\n\n{review}")

if __name__ == "__main__":
    analytics.ensure_branch()
    main()
