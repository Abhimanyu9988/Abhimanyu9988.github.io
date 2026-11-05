#!/usr/bin/env python3
"""The leadership desk: Secretary, CEO, CTO, CMO and CFO. Abhi texts them in Slack, and the CTO desk issue works until Slack is on.

A message starting with a title, such as "CMO: ..." or "OSS: ...", goes to that person. Anything else goes to the Secretary,
who asks up to two others and gives him one answer.

  python3 pipeline/cto.py   no input: refresh the public team status, and on Mondays run market research and the digest.
                            POLL=true: answer Abhi's new Slack messages and refresh the live board. The Slack listener
                            starts this as soon as he posts, and a 10-minute check is the fallback.
                            COMMENT_BODY: answer Abhi on the CTO desk issue.

What leaders can do: answer, record a goal he stated, list tasks, close a draft he rejected, and queue a topic he asked for.
Code carries out those last two, and only when his own words ask for them.
New features are built with Abhi in a Claude chat, not by agents or through the API. Tasks owned by the CTO are his build notes,
which code lists when he asks for them. IT Support closes a note only when a merged pull request delivers it.
What it cannot do: publish, spend, deploy, change the budget, or hold a credential. Links are checked by code first.
"""
import base64, datetime, ipaddress, json, os, re, socket, urllib.error, urllib.parse, urllib.request
from radar import HOME_REPO, comment, ensure_label, gh, post_issue, safe_text
import analytics, budget, contract, live_board, roles, slack, sources

TASKS, STATUS, CURSOR = "analytics/tasks.json", "analytics/status.json", "analytics/slack-cursor.json"
INBOX = "analytics/slack-inbox.md"             # a path name only: read_private and write_private keep it in the private repo
CHAT, CHAT_MAX_BYTES = "slack-chat.jsonl", 800_000
ADMIN_REPO = "Abhimanyu9988/team-admin"        # private. This repo is public, so its analytics branch is too
WORKFLOW_OWNER = {"radar": "topic_scout", "replicate": "lab_engineer", "analytics": "analyst", "publish": "publisher"}
URL = re.compile(r"https?://[^\s)\]>]+")
MAX_TASKS = 40
LEADER = re.compile(r"\s*@?(secretary|ceo|cto|cmo|cfo|oss[ -]?scout|oss)\b[\s:,-]*", re.I)
CONSULT = ("ceo", "cto", "cmo", "cfo", "oss_scout")
CLOSE_WORDS = re.compile(r"(?i)\b(reject|close|delete|drop|kill)\b")
NOTES_WORDS = re.compile(r"(?i)\b(feature requests?|build notes|my notes|backlog)\b")
AUDIT_DAYS = 30
TRIAL_WORDS = re.compile(r"(?i)\b(models?|stats?|experiment|trial)\b")
DONE_WORDS = re.compile(r"(?i)\b(done|finished|complete|completed|remove|close|delete|drop)\b")
IDEA_WORDS = re.compile(r"(?i)\b(topic|idea|try|pick|write about|tomorrow)\b")
IDEAS_HEAD, IDEAS_END = "Abhi's own topic ideas. Put these first:", "End of his ideas."
PLAN = "analytics/week-plan.json"
BOARD_ISSUE = "/repos/Abhimanyu9988/oss-scout/issues/1"
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
    row = {"title": str(task.get("title"))[:300], "owner": task["owner"], "status": "open",
           "created": datetime.date.today().isoformat()}
    keep = [t for t in got if t.get("title") != row["title"]]
    if not write_private(TASKS, json.dumps({"tasks": (keep + [row])[-MAX_TASKS:]}, indent=1), sha, "tasks: add"):
        return "Task not saved, because the private admin repo isn't connected yet."
    if row["owner"] == "cto":
        return f"Saved to the CTO's build notes: {row['title']}"
    return f"Added to the list for {row['owner']}: {row['title']}. Agents don't act on this list by themselves yet."

def build_notes():
    """Written by code from the saved list, so a note can't be dropped or reworded on its way to him."""
    rows = [t["title"] for t in tasks()[0] if t.get("status") == "open" and t.get("owner") == "cto"]
    if not rows:
        return ["The CTO's build notes are empty."]
    parts, part = [], "CTO's build notes for your next session:"
    for n, title in enumerate(rows, 1):
        if len(part) + len(title) > 3000:      # Slack cuts a message at 3,500 characters here
            parts, part = parts + [part], ""
        part += f"\n{n}. {title}"
    return parts + [part]

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

def read_plan():
    text, sha = analytics.read_file(PLAN)
    return (json.loads(text) if text.strip() else {}), sha

def lab_drafts(plan):
    """Labs that produced an article since the plan started. A redraft shares its lab's run id, so it isn't counted twice."""
    pulls = gh("GET", f"/repos/{HOME_REPO}/pulls?state=all&sort=created&direction=desc&per_page=50") or []
    return len({run for p in pulls if p["head"]["ref"].startswith("draft/") and p.get("created_at", "") >= plan.get("created", "9")
                for run in re.findall(r"lab-run: (\d+)", str(p.get("body")))})

def trial_rows():
    """Each lab's record, with the Editor's average score when it reached review."""
    names = [f["name"] for f in gh("GET", f"/repos/{HOME_REPO}/contents/analytics?ref=analytics") or []]
    rows = []
    for name in (n for n in names if re.fullmatch(r"trial-\d+\.json", n)):
        row = json.loads(analytics.read_file(f"analytics/{name}")[0])
        review = next((n for n in names if n.startswith("review-") and n.endswith(f"-{row['run']}.json")), None)
        card = (json.loads(analytics.read_file(f"analytics/{review}")[0]).get("scorecard") or {}) if review else {}
        marks = [v.get("score") if isinstance(v, dict) else v for v in (card.get("scores") or {}).values()]
        marks = [m for m in marks if isinstance(m, (int, float))]
        rows.append({**row, "editor_score": sum(marks) / len(marks) if marks else None})
    return rows

def trial_stats(rows):
    """Computed by code, per model, so a leader can't round a result up. Averages leave out labs with nothing to measure."""
    out = {}
    for model in sorted({r.get("model") for r in rows}):
        mine = [r for r in rows if r.get("model") == model]
        mean = lambda key: (lambda got: round(sum(got) / len(got), 2) if got else None)([r[key] for r in mine if r.get(key) is not None])
        out[model] = {"labs": len(mine), "finished": sum(r.get("status") == "reproduced_and_fixed" for r in mine), "avg_editor_score_out_of_5": mean("editor_score"),
                      "avg_attempts": mean("attempts"), "avg_minutes": mean("minutes"), "avg_lab_model_usd": mean("lab_usd")}
    return {"models": out, "note": "A small sample, with a different topic for each lab. Don't call a winner from a few labs."}

def lab_active():
    """Every state a run can wait in, so a lab that is only queued still blocks the next one."""
    return any(r.get("name") == "replicate" for state in ("in_progress", "queued", "waiting", "requested")
               for r in (gh("GET", f"/repos/{HOME_REPO}/actions/runs?status={state}&per_page=30") or {}).get("workflow_runs", []))

def requeue_lost(plan, today):
    """A lab always comments on its issue. No comment a day later means the lab never started, so the topic goes back in the queue."""
    for t in plan.get("topics") or []:
        if t.get("state") == "started" and t.get("started_on", today) < today and not (gh("GET", f"/repos/{HOME_REPO}/issues/{t['issue']}") or {}).get("comments", 1):
            t["state"] = "queued"

def next_lab(today=None):
    """One lab a day from the plan, until the week's target is met. A spare only runs if a lab failed."""
    today = today or datetime.date.today().isoformat()
    plan, sha = read_plan()
    if not plan.get("topics") or lab_active() or lab_drafts(plan) >= plan.get("target", 0):
        return None
    requeue_lost(plan, today)
    queued = [t for t in plan["topics"] if t.get("state") == "queued" and t.get("confirmed") and t.get("picked") and not t.get("chat")]   # Marketing and the Editor confirm, Abhi picks, and a chat topic starts from chat
    if not queued:
        return None
    queued[0].update(state="started", started_on=today)
    analytics.write_file(PLAN, json.dumps(plan, indent=1), sha, "week: next lab")
    return queued[0]

def next_run():
    """What is really planned, read from the week's plan. If it can't be read, this is left out rather than guessed."""
    try:
        plan, _ = read_plan()
        queued = [t["title"] for t in plan.get("topics") or [] if t.get("state") == "queued" and t.get("confirmed") and t.get("picked") and not t.get("chat")]
        return {"week": plan.get("week"), "articles_this_week": plan.get("target"), "next_lab": queued[0] if queued else "nothing queued",
                "next_lab_starts": "07:12 UTC tomorrow" if queued else "the next plan, on Monday at 06:00 UTC", "queued_after_that": queued[1:],
                "note": "One lab a day. A lab can still fail, and an article needs your approval."}
    except Exception as e:
        print(f"::warning title=cto::plan not read: {' '.join(str(e).split())[:200]}")
        return {}

def recent_labs(limit=4):
    """What the last labs really did, so a leader never has to guess which lab ran or what happened to it."""
    try:
        names = [f["name"] for f in gh("GET", f"/repos/{HOME_REPO}/contents/analytics?ref=analytics") or []]
        runs = sorted((n for n in names if re.fullmatch(r"trial-\d+\.json", n)), key=lambda n: int(n[6:-5]), reverse=True)[:limit]
        rows = [json.loads(analytics.read_file(f"analytics/{n}")[0]) for n in runs]
        return [{k: r.get(k) for k in ("title", "status", "attempts", "model")} for r in rows]
    except Exception as e:
        print(f"::warning title=cto::labs not read: {' '.join(str(e).split())[:200]}")
        return []

def progress():
    """Facts from GitHub and the spend record. A failed source is left out by the caller, never guessed."""
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)).isoformat()[:19] + "Z"
    pulls = gh("GET", f"/repos/{HOME_REPO}/pulls?state=all&sort=updated&direction=desc&per_page=50") or []
    drafts = [p for p in pulls if p["head"]["ref"].startswith("draft/")]
    runs = gh("GET", f"/repos/{HOME_REPO}/actions/runs?status=failure&per_page=50&created=%3E{since[:10]}") or {}
    running = gh("GET", f"/repos/{HOME_REPO}/actions/runs?status=in_progress&per_page=30") or {}
    picks = gh("GET", f"/repos/{HOME_REPO}/issues?labels=radar&state=open&sort=created&direction=desc&per_page=8") or []
    total, jobs = budget.spent()
    pipeline = [p for p in pulls if not p["head"]["ref"].startswith("draft/")]
    return {"tomorrow": next_run(), "recent_labs_newest_first": recent_labs(), "board_url": live_board.PAGE,
            "open_article_prs": [{"title": p["title"], "url": p.get("html_url", "")} for p in drafts if p["state"] == "open"],
            "open_pipeline_prs": [{"title": p["title"], "url": p.get("html_url", "")} for p in pipeline if p["state"] == "open"],
            "recently_shipped": [p["title"] for p in pipeline if (p.get("merged_at") or "") >= since][:12],
            "newest_topics_first": [{"title": i["title"], "panel_pick": any(l["name"] == "replicate" for l in i["labels"]), "opened": i["created_at"][:10]}
                                    for i in picks if "pull_request" not in i],
            "waiting": [p["title"] for p in drafts if p["state"] == "open"],
            "shipped": [p["title"] for p in drafts if (p.get("merged_at") or "") >= since],
            "failed_runs": sorted({r.get("name", "?") for r in runs.get("workflow_runs", [])}),
            "running": sorted({r.get("name", "?") for r in running.get("workflow_runs", [])}),
            "lab_running_now": any(r.get("name") == "replicate" for r in running.get("workflow_runs", [])),
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
    """(leader, message). The Secretary takes anything not addressed to a leader."""
    hit = LEADER.match(text)
    if not hit:
        return "secretary", text
    role = "oss_scout" if hit[1].lower().startswith("oss") else hit[1].lower()
    return role, text[hit.end():].strip() or text

def split_leaders(text):
    """One message can address several leaders, one per line. Each leader gets only its own lines."""
    parts = []
    for line in text.splitlines():
        if LEADER.match(line) or not parts:
            parts.append(line)
        elif line.strip():
            parts[-1] += "\n" + line
    return [route(p) for p in parts] or [route(text)]

def recent_talk(talk, limit=6000):
    """The newest messages win. Long old digests must never push out what was just said."""
    kept, size = [], 0
    for m in reversed(talk):
        row = {**m, "text": str(m.get("text"))[:700]}
        size += len(json.dumps(row))
        if size > limit:
            break
        kept.append(row)
    return list(reversed(kept))

def steps_md(steps):
    """Steps may link to a page, but only to one code has confirmed."""
    clean = [URL.sub(lambda m: m[0] if link_exists(m[0]) else "[link removed]", str(s)) for s in steps or []]
    return "*What I need from you:*\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(clean, 1)) if clean else ""

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
    urls = {p.get("url") for p in pages if isinstance(p, dict)}
    real = [u for u in got.get("sources") or [] if u in urls]
    text = "\n".join(["What worked for others:"] + [f"- {x}" for x in got["what_worked"]] + ["", "Topics to try:"] +
                      [f"- {x}" for x in got["topics_to_try"]] + ["", "Avoid:"] + [f"- {x}" for x in got["avoid"]] + ["", "Sources:"] + [f"- {u}" for u in real])
    old, sha = analytics.read_file(BRIEF)
    analytics.write_file(BRIEF, ideas_block(old) + text + "\n", sha, "market: weekly brief")
    return text

def finish_tasks(titles):
    """Only exact titles, and only when his own words ask. A task stays in the file, marked done."""
    got, sha = tasks()
    hit = [t for t in got if t.get("status") == "open" and t.get("title") in titles]
    for t in hit:
        t["status"] = "done"
    if hit and write_private(TASKS, json.dumps({"tasks": got}, indent=1), sha, "tasks: done"):
        return [t["title"] for t in hit]
    return []

def merged_prs():
    """Pipeline PRs merged lately. These are the only evidence that can close a note, so a read failure closes nothing."""
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=AUDIT_DAYS)).isoformat()[:19] + "Z"
    pulls = gh("GET", f"/repos/{HOME_REPO}/pulls?state=closed&sort=updated&direction=desc&per_page=50") or []
    return [{"title": p["title"], "url": p["html_url"], "about": str(p.get("body") or "")[:400]}
            for p in pulls if (p.get("merged_at") or "") >= since and not p["head"]["ref"].startswith("draft/")]

def change_log():
    try:
        with open("docs/decisions.md") as f:
            return f.read()[-3000:]
    except OSError:
        return ""

def audit_notes():
    """[(note, PR address)] that IT Support closed. Code checks each one, so a model can't close a note without a real merge."""
    got, sha = tasks()
    notes = [t for t in got if t.get("status") == "open" and t.get("owner") == "cto"]
    merged = merged_prs() if notes else []
    if not merged:
        return []
    found = roles.ask("it", f"NOTES:\n{json.dumps([t['title'] for t in notes])[:5000]}\n\nMERGED:\n{json.dumps(merged)[:9000]}"
                            f"\n\nCHANGE LOG:\n{change_log()}", "noteaudit")["done"]
    real, closed = {m["url"] for m in merged}, []
    for row in found:
        for t in notes:
            if t["title"] == row["note"] and row["pr"] in real and t["status"] == "open":
                t.update(status="done", done_by=row["pr"])
                closed.append((t["title"], row["pr"]))
    if closed and not write_private(TASKS, json.dumps({"tasks": got}, indent=1), sha, "tasks: checked by IT Support"):
        return []
    return closed

def closed_message(closed):
    return "Checked against merged pull requests. These are finished:\n" + "\n".join(f"- {note} ({pr})" for note, pr in closed)

def show_notes(role):
    try:
        closed = audit_notes()
        if closed:
            post(closed_message(closed), "it")
    except Exception as e:
        print(f"::warning title=it::notes not checked: {' '.join(str(e).split())[:200]}")
    for part in build_notes():
        post(part, role)

def ideas_block(text):
    """His own ideas survive new research until a Topic Scout run has used them."""
    return text[:text.index(IDEAS_END) + len(IDEAS_END)] + "\n\n" if text.startswith(IDEAS_HEAD) and IDEAS_END in text else ""

def save_ideas(ideas):
    """Queues his topics at the top of the brief the Editor reads when it plans the week."""
    old, sha = analytics.read_file(BRIEF)
    kept = ideas_block(old)
    rows = list(dict.fromkeys([l for l in kept.splitlines() if l.startswith("- ")] + [f"- {i}" for i in ideas]))
    head = "\n".join([IDEAS_HEAD, *rows, IDEAS_END]) + "\n\n"
    analytics.write_file(BRIEF, head + old[len(kept):], sha, "market: Abhi's topic ideas")

def close_drafts(titles):
    """Only drafts open right now, by exact title. Closing can be undone, and GitHub keeps the branch."""
    closed = []
    for p in gh("GET", f"/repos/{HOME_REPO}/pulls?state=open&per_page=50") or []:
        name = p["title"].removeprefix("Draft: ")
        if p["head"]["ref"].startswith("draft/") and (p["title"] in titles or name in titles):
            gh("PATCH", f"/repos/{HOME_REPO}/pulls/{p['number']}", {"state": "closed"})
            closed.append(name)
    return closed

ME = "Abhimanyu9988"
NEEDED_QUERIES = {"review_requested_from_him": f"is:open is:pr user-review-requested:{ME}",
                  "code_owner_team_reviews": f"is:open is:pr review-requested:{ME} -user-review-requested:{ME}",
                  "prs_assigned_to_him": f"is:open is:pr assignee:{ME} -user:{ME}",
                  "assigned_to_him": f"is:open is:issue assignee:{ME} -user:{ME}",   # his own repos hold the team's topics and drafts
                  "changes_requested_on_his_prs": f"is:open is:pr author:{ME} review:changes_requested",
                  "pr_mentions": f"is:open is:pr mentions:{ME} -author:{ME} -user:{ME}",
                  "mentions": f"is:open is:issue mentions:{ME} -author:{ME} -user:{ME}"}

def needed():
    """GitHub's own answer on why he's involved. A failed search is reported, never replaced by a guess."""
    out = {}
    for name, query in NEEDED_QUERIES.items():
        q = urllib.parse.quote(f"{query} archived:false")
        try:
            rows = (gh("GET", f"/search/issues?q={q}&sort=updated&order=desc&per_page=12") or {}).get("items", [])
            out[name] = [{"title": i["title"], "url": i["html_url"], "updated": i["updated_at"][:10]} for i in rows]
        except Exception as e:
            print(f"::warning title=oss::search failed: {' '.join(str(e).split())[:200]}")
            out[name] = "the search failed"
    return out

def own_prs(today=None):
    """His pull requests in other people's projects. They are his work, so they stay out of what OSS Scout says needs him."""
    since = ((today or datetime.date.today()) - datetime.timedelta(days=7)).isoformat()
    out = {}
    for name, query in (("open", f"is:open is:pr author:{ME} -user:{ME} archived:false"),
                        ("merged", f"is:pr is:merged author:{ME} -user:{ME} merged:>={since}")):
        try:
            rows = (gh("GET", f"/search/issues?q={urllib.parse.quote(query)}&sort=updated&order=desc&per_page=12") or {}).get("items", [])
            out[name] = [{"title": i["title"], "url": i["html_url"], "closed": i.get("closed_at") or ""} for i in rows]
        except Exception as e:
            print(f"::warning title=board::search failed: {' '.join(str(e).split())[:200]}")
            out[name] = "the search failed"
    return out

def board():
    """His contribution board. It's public, and a missing board only weakens the answer."""
    try:
        return str((gh("GET", BOARD_ISSUE) or {}).get("body", ""))[:9000]
    except Exception as e:
        print(f"::warning title=oss::board not read: {' '.join(str(e).split())[:200]}")
        return ""

def ask_role(role, text, talk, p, heard=None):
    team, (brief, _) = roles.data("cto"), analytics.read_file(BRIEF)
    extra = f"\n\nGITHUB:\n{json.dumps(needed())[:9000]}\n\nBOARD:\n{board()[:3000]}" if role == "oss_scout" else ""
    extra += f"\n\nTEAM ANSWERS:\n{json.dumps(heard)[:5000]}" if heard else ""
    if TRIAL_WORDS.search(text):
        try:
            extra += f"\n\nMODEL TRIAL:\n{json.dumps(trial_stats(trial_rows()))[:3000]}"
        except Exception as e:
            print(f"::warning title=cto::trial not read: {' '.join(str(e).split())[:200]}")
    return roles.ask(role, f"ABHI SAID:\n{text[:4000]}\n\nCONVERSATION, newest last:\n{json.dumps(recent_talk(talk))}\n\nFACTS:\n"
                           f"{json.dumps({**p, 'team': roster(p)})[:6000]}\n\nGOALS AND TEAM:\n{json.dumps(team)[:4000]}"
                           f"\n\nMARKET BRIEF:\n{brief[:2500]}{extra}", "ctoreply")

def answer(text, talk, role="secretary"):
    wanted = budget.requested_limit(text)
    if wanted:
        budget.set_limit(wanted)
        post(f"Done. This month's limit is now ${wanted}. It returns to ${budget.LIMIT_USD} next month.", "cfo")
        return
    p = progress()
    got = ask_role(role, text, talk, p)
    if role == "secretary" and got.get("consult"):         # only her final answer can act or reach him
        heard = {who: ask_role(who, text, talk, p).get("reply", "") for who in got["consult"][:2] if who in CONSULT}
        if heard:
            got = ask_role(role, text, talk, p, heard)
    reply = URL.sub("[link removed]", got["reply"])
    good = [u for u in got.get("urls") or [] if link_exists(u)]
    notes = [add_task(got["task"])] if (got.get("task") or {}).get("title") else []
    if got.get("close_prs") and CLOSE_WORDS.search(text):   # his words decide, never a model's alone
        closed = close_drafts(got["close_prs"])
        notes.append(f"Closed: {', '.join(closed)}." if closed else "I found no open draft with that title.")
    if got.get("done_tasks") and DONE_WORDS.search(text):
        finished = finish_tasks(got["done_tasks"])
        notes.append(f"Marked done: {', '.join(finished)}." if finished else "I found no open task with that title.")
    if got.get("article"):                              # labs and articles are written in Claude chat, so Slack never starts one
        notes.append("Saved for Claude chat. No lab starts from Slack, and the Editor's shortlist comes first.")
    elif got.get("ideas") and IDEA_WORDS.search(text):
        save_ideas(got["ideas"])
        notes.append("Saved for the Editor's plan on Monday, ahead of other topics.")
    bad = len(got.get("urls") or []) - len(good)
    notes += [f"{bad} link(s) could not be confirmed, so I left them out."] if bad else []
    links = "\n".join(f"- {u}" for u in good)
    post("\n\n".join(x for x in (reply, links, " ".join(notes), steps_md(got.get("needs_abhi"))) if x), role)
    if got.get("show_notes") or NOTES_WORDS.search(text):
        show_notes(role)
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

def save_inbox(force=False):
    """His own messages, in the private repo, so a Claude chat can read Slack without a Slack key. Never goes to the public repo."""
    text, sha = read_private(INBOX)
    if (force or not text.strip()) and admin_on():
        rows = "\n\n".join(f"**{datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc):%Y-%m-%d %H:%M} UTC:** {body}" for ts, body in slack.his_messages())
        write_private(INBOX, f"# What Abhi texted the team\n\n{rows}\n", sha, "inbox: his Slack messages")

def save_chat():
    """The whole team conversation, in the private repo, so a Claude chat can read it and use it later. Never goes to the public repo.
    Old lines roll off before the file reaches GitHub's read limit, because a file too big to read would lose everything."""
    if not admin_on():
        return 0
    text, sha = read_private(CHAT)
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    new = [{"ts": ts, "time": f"{datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc):%Y-%m-%d %H:%M} UTC", "from": who, "text": body}
           for ts, who, body in slack.team_messages(rows[-1]["ts"] if rows else "0")]
    if new:
        rows += new
        while len(json.dumps(rows)) > CHAT_MAX_BYTES:
            rows = rows[50:]
        write_private(CHAT, "".join(json.dumps(r) + "\n" for r in rows), sha, "chat: Slack messages")
    return len(new)

def poll():
    """Slack messages since the last run. The cursor moves first, so one bad message cannot repeat every ten minutes."""
    if not slack.enabled():
        return
    try:                                       # a GitHub or Slack blip waits for the next run, and the cursor keeps his messages safe
        text, sha = analytics.read_file(CURSOR)
        after = json.loads(text)["ts"] if text.strip() else f"{datetime.datetime.now(datetime.timezone.utc).timestamp():.6f}"
        mine, newest = slack.messages(after)
    except Exception as e:
        print(f"::warning title=slack::messages not read: {' '.join(str(e).split())[:200]}")
        return
    if newest != after or not sha:
        analytics.write_file(CURSOR, json.dumps({"ts": newest}), sha, "slack: cursor")
    try:
        save_inbox(bool(mine))
        save_chat()
    except Exception as e:
        print(f"::warning title=slack::inbox not saved: {' '.join(str(e).split())[:200]}")
    for m in mine[-MAX_REPLIES:]:
        for role, message in split_leaders(m["text"]):
            try:
                answer(message, slack.history(), role)
            except Exception as e:
                print(f"::warning title=cto::reply failed: {' '.join(str(e).split())[:200]}")
                post("I could not answer that just now. Please send it again.", role)

def refresh_board():
    """Never stops the work around it. If this fails, the board keeps its old time, which shows it's out of date."""
    try:
        brief, _ = analytics.read_file(BRIEF)
        ideas = [l[2:] for l in ideas_block(brief).splitlines() if l.startswith("- ")]
        planned = [t["title"] for t in read_plan()[0].get("topics") or [] if t.get("state") == "queued" and t.get("confirmed") and t.get("picked") and not t.get("chat")]
        live_board.refresh(needed(), own_prs(), planned + ideas, budget.spent()[0], budget.limit())
    except Exception as e:
        print(f"::warning title=board::not refreshed: {' '.join(str(e).split())[:200]}")

def main():
    if os.environ.get("POLL") == "true":
        poll()
        refresh_board()
        return
    text = os.environ.get("COMMENT_BODY", "").strip()
    if text:
        for role, message in split_leaders(text):
            answer(message, thread(desk()), role)
        return
    p = progress()
    _, sha = analytics.read_file(STATUS)
    analytics.write_file(STATUS, json.dumps(public_status(p), indent=1), sha, "status: refresh")
    scheduled = os.environ.get("SCHEDULE") == "12 7 * * *"   # a manual run refreshes the page and never repeats the digest
    weekly = (scheduled and datetime.date.today().weekday() == 0) or os.environ.get("DIGEST") == "true"
    if scheduled:
        try:
            lab = next_lab()
            if lab and os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                    f.write(f"run_lab={int(lab['issue'])}\nrun_model={lab.get('model', '')}\n")
        except Exception as e:
            print(f"::warning title=cto::next lab not started: {' '.join(str(e).split())[:200]}")
    if scheduled:
        try:
            closed = audit_notes()
            if closed:
                post(closed_message(closed), "it")
        except Exception as e:
            print(f"::warning title=it::notes not checked: {' '.join(str(e).split())[:200]}")
    review = manager_review(p) if weekly else ""
    if weekly:
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
