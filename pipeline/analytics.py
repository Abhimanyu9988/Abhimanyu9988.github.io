#!/usr/bin/env python3
"""Impact log: how the published posts are doing, kept as dated evidence and fed back into topic choice.

  MODE=collect (weekly): pull dev.to numbers for every published post and append them to
      analytics/ledger.csv on the `analytics` branch. Then Claude, ChatGPT and Gemini each read the numbers
      and say what is working; Claude merges their views into analytics/insights.md (the topic finders read
      it) and the summary is posted on the "Impact log" issue.
  MODE=stats (a `/stats` comment by Abhimanyu): record numbers the platforms don't expose through an API,
      e.g. `/stats linkedin impressions=1200 reactions=40` on a post's publish report issue.

The ledger is append-only and every row says where its number came from (api or manual), so it can be
cited as-is. Keep screenshots for the manual rows.
"""
import base64, csv, datetime, io, json, os, re
from radar import HOME_REPO, gh, call, safe_text, comment, ensure_label, post_issue, Blocked

BRANCH, LEDGER, INSIGHTS = "analytics", "analytics/ledger.csv", "analytics/insights.md"
FIELDS = ["date_utc", "post", "platform", "metric", "value", "source", "url"]
PLATFORMS = {"linkedin", "medium", "devto", "hashnode", "site", "youtube", "twitter", "talk", "teaching", "repost",
             "stackoverflow", "other"}
TODAY = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

# ---------- storage on the analytics branch ----------
def read_file(path, ref=BRANCH):
    try:
        f = gh("GET", f"/repos/{HOME_REPO}/contents/{path}?ref={ref}")
        return base64.b64decode(f.get("content", "")).decode("utf-8"), f.get("sha")
    except RuntimeError as e:
        if "HTTP 404" in str(e):
            return "", None
        raise

def ensure_branch():
    try:
        gh("GET", f"/repos/{HOME_REPO}/git/ref/heads/{BRANCH}")
    except RuntimeError as e:
        if "HTTP 404" not in str(e):
            raise
        base = gh("GET", f"/repos/{HOME_REPO}/git/ref/heads/main")["object"]["sha"]
        gh("POST", f"/repos/{HOME_REPO}/git/refs", {"ref": f"refs/heads/{BRANCH}", "sha": base})

def write_file(path, text, sha, msg):
    body = {"message": msg, "branch": BRANCH, "content": base64.b64encode(text.encode()).decode()}
    if sha:
        body["sha"] = sha
    gh("PUT", f"/repos/{HOME_REPO}/contents/{path}", body)

def load_ledger():
    text, sha = read_file(LEDGER)
    rows = list(csv.DictReader(io.StringIO(text))) if text else []
    return rows, sha

def append_rows(new, msg):
    if not new:
        return
    ensure_branch()
    rows, sha = load_ledger()
    buf = io.StringIO()
    w = csv.DictWriter(buf, FIELDS)
    w.writeheader()
    w.writerows(rows + new)
    write_file(LEDGER, buf.getvalue(), sha, msg)

# ---------- posts and automatic numbers ----------
def published_posts():
    """{slug: {"site": url, "devto": {id, url}}} from the publisher's state."""
    text, _ = read_file("publish-state.json", ref="publish-state")
    state = json.loads(text) if text.strip() else {}
    out = {}
    for canonical, ch in state.items():
        m = re.search(r"/posts/([^/]+)/?$", canonical)
        if m:
            out[m[1]] = {"site": canonical, **ch}
    return out

def devto_rows(posts):
    key = os.environ.get("DEVTO_API_KEY", "")
    want = {str(v["devto"]["id"]): (slug, v["devto"].get("url", "")) for slug, v in posts.items() if v.get("devto")}
    if not key or not want:
        return []
    arts = call("GET", "https://dev.to/api/articles/me/all?per_page=1000", headers={"api-key": key}) or []
    rows = []
    for a in arts:
        if str(a.get("id")) in want:
            slug, url = want[str(a["id"])]
            for metric, field in (("views", "page_views_count"), ("reactions", "public_reactions_count"),
                                  ("comments", "comments_count")):
                if a.get(field) is not None:
                    rows.append({"date_utc": TODAY, "post": slug, "platform": "devto", "metric": metric,
                                 "value": int(a[field]), "source": "api", "url": url})
    return rows

# ---------- what the three think ----------
def latest(rows):
    """Latest value per (post, platform, metric), plus the change since the previous snapshot."""
    seen = {}
    for r in sorted(rows, key=lambda r: r["date_utc"]):
        k = (r["post"], r["platform"], r["metric"])
        prev = seen.get(k)
        seen[k] = {"value": int(r["value"]), "date": r["date_utc"],
                   "change": int(r["value"]) - prev["value"] if prev else None}
    return seen

def table(seen):
    lines = ["| Post | Platform | Metric | Latest | Change | As of |", "|---|---|---|---|---|---|"]
    for (post, plat, metric), v in sorted(seen.items()):
        ch = "" if v["change"] is None else f"{v['change']:+d}"
        lines.append(f"| {post} | {plat} | {metric} | {v['value']:,} | {ch} | {v['date']} |")
    return "\n".join(lines)

INSIGHT_SCHEMA = {"type": "object", "required": ["observations", "suggestions"], "properties": {
    "observations": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    "suggestions": {"type": "array", "items": {"type": "string"}, "maxItems": 4}}}
MERGE_SCHEMA = {"type": "object", "required": ["insights_md"], "properties": {
    "insights_md": {"type": "string", "description": "markdown, at most 300 words"}}}

def insights(tbl, posts):
    import llm
    q = ("You help Abhimanyu grow a hands-on AWS containers blog (replicate a real problem in a lab, then fix it) "
         "toward an AWS Container Hero nomination. From the NUMBERS, what is working and what should the next "
         "posts try (topics, titles, angles, platforms, timing)? Be concrete. Numbers are small early on: say "
         "when something is too early to call, and don't overfit.")
    ctx = f"NUMBERS:\n{tbl}\n\nPOSTS:\n{json.dumps(sorted(posts))}"
    views = {}
    for who in llm.PANEL:
        try:
            views[who] = llm.ask(who, "You are one of three AI analysts (Claude, ChatGPT, Gemini).", f"{q}\n\n{ctx}",
                                 INSIGHT_SCHEMA, tool="submit_insights")
        except Exception as e:
            llm.warn(f"{who} gave no insights: {str(e)[:400]}")
    if not views:
        return ""
    merged = llm.ask("claude", "You merge three analysts' views into one short brief.",
                     "Merge these views into a short markdown brief: '### What's working', '### Try next', and "
                     "'### Where they disagree' (omit if they agree). Name who said what only where they "
                     f"disagree. At most 300 words.\n\nVIEWS:\n{json.dumps(views, indent=1)}\n\n{ctx}",
                     MERGE_SCHEMA, tool="submit_brief")
    return str(merged.get("insights_md", "")).strip()

def impact_issue():
    got = gh("GET", f"/repos/{HOME_REPO}/issues?labels=analytics&state=open&per_page=5")
    got = [i for i in got if "pull_request" not in i]
    if got:
        return got[0]["number"]
    ensure_label("analytics")
    return post_issue("\U0001f4c8 Impact log", "Weekly numbers for every published post, and what the three AIs "
                      "make of them. The full dated record is analytics/ledger.csv on the analytics branch.\n\n"
                      "Record numbers the platforms don't share through an API by commenting on a post's publish "
                      "report issue, for example `/stats linkedin impressions=1200 reactions=40`.", "analytics")["number"]

def collect():
    import llm
    posts = published_posts()
    print(f"{len(posts)} published post(s)")
    new = devto_rows(posts)
    append_rows(new, f"analytics: {len(new)} api row(s) for {TODAY}")
    rows, _ = load_ledger()
    if not rows:
        print("no numbers yet; nothing to summarise"); return
    tbl = table(latest(rows))
    brief = insights(tbl, posts)
    if brief:
        _, sha = read_file(INSIGHTS)
        write_file(INSIGHTS, f"# What's working (as of {TODAY})\n\nWritten by Claude, ChatGPT and Gemini from "
                             f"analytics/ledger.csv. The topic finders read this.\n\n{brief}\n", sha,
                   f"analytics: insights for {TODAY}")
    comment(impact_issue(), f"**Week of {TODAY}**\n\n{safe_text(tbl)}\n\n{safe_text(brief) if brief else ''}\n\n"
                            f"_Model usage: {llm.usage_summary()}_")

# ---------- /stats ----------
STAT_RX = re.compile(r"^([a-z_]{1,30})=([0-9][0-9,]{0,12})$")

def parse_stats(text, issue_body):
    """'/stats [post] <platform> metric=value ...' -> (post, platform, {metric: value}) or raises ValueError."""
    words = text.strip().splitlines()[0].split()
    if not words or words[0] != "/stats":
        raise ValueError("not a /stats command")
    words = words[1:]
    post = None
    if words and words[0] not in PLATFORMS and "=" not in words[0]:
        post = words.pop(0)
    if not words or words[0] not in PLATFORMS:
        raise ValueError(f"name a platform first: {', '.join(sorted(PLATFORMS))}")
    platform, metrics = words.pop(0), {}
    for w in words:
        m = STAT_RX.match(w)
        if not m:
            raise ValueError(f"could not read {w!r}; use metric=number, e.g. impressions=1200")
        metrics[m[1]] = int(m[2].replace(",", ""))
    if not metrics:
        raise ValueError("no numbers given, e.g. impressions=1200 reactions=40")
    if not post:
        marks = re.findall(r"<!-- post: ([a-z0-9-]+) -->", issue_body or "")
        if len(set(marks)) != 1:
            raise ValueError("say which post, e.g. `/stats my-post-slug linkedin impressions=1200`")
        post = marks[0]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,80}", post):
        raise ValueError(f"post slug {post!r} doesn't look right")
    return post, platform, metrics

def stats():
    issue = int(os.environ["ISSUE"])
    try:
        post, platform, metrics = parse_stats(os.environ.get("COMMENT_BODY", ""), os.environ.get("ISSUE_BODY", ""))
    except ValueError as e:
        comment(issue, f"Couldn't record that: {safe_text(str(e))}"); return
    rows = [{"date_utc": TODAY, "post": post, "platform": platform, "metric": k, "value": v, "source": "manual",
             "url": ""} for k, v in metrics.items()]
    append_rows(rows, f"analytics: manual {platform} numbers for {post}")
    got = ", ".join(f"{k} {v:,}" for k, v in metrics.items())
    comment(issue, f"Recorded for **{post}** on {platform}, {TODAY}: {got}. Keep a screenshot of these; the ledger "
                   "marks them as entered by hand.")

if __name__ == "__main__":
    stats() if os.environ.get("MODE") == "stats" else collect()
