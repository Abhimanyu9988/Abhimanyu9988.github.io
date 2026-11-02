#!/usr/bin/env python3
"""CTO: the one place Abhi talks to the team, in Slack once its token is set, and in the CTO desk issue until then.

  python3 pipeline/cto.py   no input: refresh the public team status, and on Mondays post the weekly digest.
                            POLL=true: answer Abhi's new Slack messages. No server is needed, so replies take minutes.
                            COMMENT_BODY: answer Abhi on the CTO desk issue.

What it can do: answer, record a goal he agreed, and assign a tracked task to an active role.
What it cannot do: publish, spend, deploy, change the budget, or hold a credential. Links are checked by code first.
"""
import datetime, ipaddress, json, os, re, socket, urllib.error, urllib.parse, urllib.request
from radar import HOME_REPO, comment, ensure_label, gh, post_issue, safe_text
import analytics, budget, contract, roles, slack, sources

TASKS, STATUS, CURSOR = "analytics/tasks.json", "analytics/status.json", "analytics/slack-cursor.json"
WORKFLOW_OWNER = {"radar": "topic_scout", "replicate": "lab_engineer", "analytics": "analyst", "publish": "publisher"}
URL = re.compile(r"https?://[^\s)\]>]+")
MAX_TASKS = 40
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

def tasks():
    text, sha = analytics.read_file(TASKS)
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
    analytics.write_file(TASKS, json.dumps({"tasks": (keep + [row])[-MAX_TASKS:]}, indent=1), sha, "tasks: add")
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

def answer(text, talk):
    wanted = budget.requested_limit(text)
    if wanted:
        budget.set_limit(wanted)
        post(f"Done. This month's limit is now ${wanted}. It returns to ${budget.LIMIT_USD} next month.", "spend_tracker")
        return
    p, team = progress(), roles.data("cto")
    got = roles.ask("cto", f"ABHI SAID:\n{text[:4000]}\n\nCONVERSATION:\n{json.dumps(talk)[:6000]}\n\nFACTS:\n"
                           f"{json.dumps({**p, 'team': roster(p)})[:4000]}\n\nGOALS AND TEAM:\n{json.dumps(team)[:4000]}", "ctoreply")
    reply = URL.sub("[link removed]", got["reply"])
    good = [u for u in got.get("urls") or [] if link_exists(u)]
    notes = [add_task(got["task"])] if (got.get("task") or {}).get("title") else []
    bad = len(got.get("urls") or []) - len(good)
    notes += [f"{bad} link(s) could not be confirmed, so I left them out."] if bad else []
    links = "\n".join(f"- {u}" for u in good)
    post("\n\n".join(x for x in (reply, links, " ".join(notes)) if x))
    if got["stance"] == "agree" and got.get("goal") and re.search(r"(?i)\bgoals?\b", text):   # his word, not a model's idea
        import assistant
        assistant.comment = lambda n, t: post(t, "executive_assistant")
        assistant.add_goal(got["goal"], 0)

def poll():
    """Slack messages since the last run. The cursor moves first, so one bad message cannot repeat every ten minutes."""
    if not slack.enabled():
        return
    text, sha = analytics.read_file(CURSOR)
    after = json.loads(text)["ts"] if text.strip() else f"{datetime.datetime.now(datetime.timezone.utc).timestamp():.6f}"
    mine, newest = slack.messages(after)
    if newest != after or not sha:
        analytics.write_file(CURSOR, json.dumps({"ts": newest}), sha, "slack: cursor")
    for m in mine[-MAX_REPLIES:]:
        try:
            answer(m["text"], slack.history())
        except Exception as e:
            print(f"::warning title=cto::reply failed: {' '.join(str(e).split())[:200]}")
            post("I could not answer that just now. Please send it again.")

def main():
    if os.environ.get("POLL") == "true":
        poll()
        return
    text = os.environ.get("COMMENT_BODY", "").strip()
    if text:
        answer(text, thread(desk()))
        return
    p = progress()
    _, sha = analytics.read_file(STATUS)
    analytics.write_file(STATUS, json.dumps(public_status(p), indent=1), sha, "status: refresh")
    if datetime.date.today().weekday() == 0 or os.environ.get("DIGEST") == "true":
        post(digest_md(p))

if __name__ == "__main__":
    analytics.ensure_branch()
    main()
