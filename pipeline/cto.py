#!/usr/bin/env python3
"""CTO: the one place Abhi talks to the team, in a single issue. A reply to its email becomes a comment here.

  python3 pipeline/cto.py   no COMMENT_BODY: refresh the public team status, and on Mondays post the weekly digest.
                            with COMMENT_BODY: answer Abhi, agreeing or pushing back on the evidence.

What it can do: answer, record a goal he agreed, and assign a tracked task to an active role.
What it cannot do: publish, spend, deploy, change the budget, or hold a credential. Links are checked by code first.
"""
import datetime, json, os, re, urllib.error, urllib.request
from radar import HOME_REPO, comment, ensure_label, gh, post_issue, safe_text
import analytics, budget, contract, roles, sources

TASKS, STATUS = "analytics/tasks.json", "analytics/status.json"
URL = re.compile(r"https?://[^\s)\]>]+")
MAX_TASKS = 40

def desk():
    number = budget.desk()
    if number:
        return number
    ensure_label("cto-desk")
    return post_issue("CTO desk", "Reply to any email from this thread to talk to the CTO. Budget, progress and work in "
                                  "progress are posted here each Monday.", "cto-desk")["number"]

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

def link_exists(url):
    """True when the page answers. A sign-in page counts: 401 and 403 show it exists, and Abhi is the one signing in."""
    if not url.startswith("https://"):
        return False
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10):
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
    total, jobs = budget.spent()
    return {"waiting": [p["title"] for p in drafts if p["state"] == "open"],
            "shipped": [p["title"] for p in drafts if (p.get("merged_at") or "") >= since],
            "failed_runs": sorted({r.get("name", "?") for r in runs.get("workflow_runs", [])}),
            "spent": total, "limit": budget.limit(), "by_job": jobs, "tasks": [t for t in tasks()[0] if t.get("status") == "open"]}

def digest_md(p):
    lines = [f"**Budget:** ${p['spent']:.2f} of ${p['limit']:.2f} this month, at list prices plus lab estimates.", ""]
    for head, rows in (("Waiting for your decision", p["waiting"]), ("Published this week", p["shipped"]),
                       ("Workflows that failed this week", p["failed_runs"])):
        lines += [f"**{head}:** " + (", ".join(safe_text(r) for r in rows) if rows else "none"), ""]
    lines += ["**Work in progress:**"] + ([f"- {t['owner']}: {safe_text(t['title'])}" for t in p["tasks"]] or ["- No tasks are open."])
    return "\n".join(lines + ["", "Reply to this email to talk to me."])

def public_status(p):
    """For the public team page. No spending, tasks or failures: Abhi's requests stay private."""
    team = [{"role": k.replace("_", " "), "mission": v} for k, v in sources.roles().items()]
    return {"updated": datetime.date.today().isoformat(), "team": team, "in_review": p["waiting"], "published": p["shipped"]}

def thread(number):
    rows = gh("GET", f"/repos/{HOME_REPO}/issues/{number}/comments?per_page=100") or []
    return [{"from": "Abhi" if c["user"]["login"] == "Abhimanyu9988" else "CTO", "text": c["body"][:1500]} for c in rows[-8:]]

def answer(text, number):
    wanted = budget.requested_limit(text)
    if wanted:
        budget.set_limit(wanted)
        comment(number, f"Done. This month's limit is now ${wanted}. It returns to ${budget.LIMIT_USD} next month.")
        return
    p, team = progress(), roles.data("cto")
    got = roles.ask("cto", f"ABHI SAID:\n{text[:4000]}\n\nCONVERSATION:\n{json.dumps(thread(number))[:6000]}\n\nFACTS:\n"
                           f"{json.dumps(p)[:4000]}\n\nGOALS AND TEAM:\n{json.dumps(team)[:4000]}", "ctoreply")
    reply = URL.sub("[link removed]", got["reply"])
    good = [u for u in got.get("urls") or [] if link_exists(u)]
    notes = [add_task(got["task"])] if (got.get("task") or {}).get("title") else []
    bad = len(got.get("urls") or []) - len(good)
    notes += [f"{bad} link(s) could not be confirmed, so I left them out."] if bad else []
    links = "\n".join(f"- {u}" for u in good)
    comment(number, safe_text("\n\n".join(x for x in (reply, links, " ".join(notes)) if x)))
    if got["stance"] == "agree" and got.get("goal"):
        import assistant
        assistant.add_goal(got["goal"], number)

def main():
    number, text = desk(), os.environ.get("COMMENT_BODY", "").strip()
    if text:
        answer(text, number)
        return
    p = progress()
    _, sha = analytics.read_file(STATUS)
    analytics.write_file(STATUS, json.dumps(public_status(p), indent=1), sha, "status: refresh")
    if datetime.date.today().weekday() == 0 or os.environ.get("DIGEST") == "true":
        comment(number, digest_md(p))

if __name__ == "__main__":
    analytics.ensure_branch()
    main()
