#!/usr/bin/env python3
"""Monthly open-source summary: every PR Abhi got merged last month, what it does, how it's used today, and what
it changes next. Runs on the 1st (monthly-oss.yml) and opens a draft post PR like the lab posts do.

  1. Collect: GitHub search for his merged PRs in the month (outside his own repos). For each PR: the change, its
     size, the issues it closes (and how many people asked for them), the repo, and the first release that ships it.
  2. Write: ChatGPT writes the post from that DATA only; Claude checks every claim against it; Gemini settles
     disputes. Then the LinkedIn round, as for every post.
  3. Open a draft PR with the post and a cover. Merging publishes it like any other post.
MONTH=YYYY-MM picks a month (default: last month).
"""
import datetime, json, os, pathlib, re
from radar import HOME_REPO, gh, safe_text, assert_safe
import llm, cover, draft

USER = "Abhimanyu9988"
OUT = pathlib.Path("out")
FIXES = re.compile(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+(?:([\w.-]+/[\w.-]+))?#(\d+)")

def month_range():
    m = os.environ.get("MONTH", "").strip()
    if m:
        start = datetime.date.fromisoformat(m + "-01")
    else:
        first = datetime.date.today().replace(day=1)
        start = (first - datetime.timedelta(days=1)).replace(day=1)
    end = (start.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
    return start, end

def search(q):
    """Search, keeping PRs only. GitHub has answered `is:pr` with issues, so ask again without it if needed."""
    def once(query):
        items = []
        for page in (1, 2, 3):
            got = gh("GET", "/search/issues?" + "&".join([f"q={_q(query)}", "per_page=100", f"page={page}"]))
            items += got.get("items", [])
            if len(got.get("items", [])) < 100:
                break
        return items
    items = once(q)
    if any("pull_request" not in i for i in items) or not items:
        items = once(q.replace("is:pr ", ""))
    return [i for i in items if "pull_request" in i]

def _q(s):
    import urllib.parse
    return urllib.parse.quote(s, safe="")

def first_release_with(repo, number, merged_at):
    try:
        rels = gh("GET", f"/repos/{repo}/releases?per_page=20") or []
    except RuntimeError:
        return None
    hits = [r for r in rels if (r.get("published_at") or "") >= merged_at and not r.get("draft")
            and re.search(rf"(?:#|/pull/){number}\b", r.get("body") or "")]
    hits.sort(key=lambda r: r["published_at"])
    return {"name": hits[0].get("name") or hits[0]["tag_name"], "published": hits[0]["published_at"][:10],
            "url": hits[0]["html_url"]} if hits else None

def collect(start, end):
    q = f"is:pr author:{USER} is:merged merged:{start}..{end} -user:{USER}"
    prs = []
    for it in search(q):
        repo = it["repository_url"].split("/repos/")[-1]
        pr = gh("GET", f"/repos/{repo}/pulls/{it['number']}")
        info = gh("GET", f"/repos/{repo}")
        closes = []
        for r, n in FIXES.findall(pr.get("body") or "")[:3]:
            try:
                iss = gh("GET", f"/repos/{r or repo}/issues/{n}")
                closes.append({"title": iss["title"], "url": iss["html_url"],
                               "thumbs_up": (iss.get("reactions") or {}).get("+1", 0), "comments": iss.get("comments", 0)})
            except RuntimeError:
                pass
        prs.append({
            "repo": repo, "repo_description": info.get("description"), "repo_stars": info.get("stargazers_count"),
            "number": pr["number"], "title": pr["title"], "url": pr["html_url"], "merged": pr["merged_at"][:10],
            "body": (pr.get("body") or "")[:3000], "additions": pr.get("additions"), "deletions": pr.get("deletions"),
            "files": pr.get("changed_files"), "review_comments": pr.get("review_comments"),
            "closes": closes, "released_in": first_release_with(repo, pr["number"], pr["merged_at"])})
    prs.sort(key=lambda p: p["merged"])
    open_q = f"is:pr author:{USER} is:open -user:{USER}"
    in_review = [{"repo": i["repository_url"].split("/repos/")[-1], "title": i["title"], "url": i["html_url"]}
                 for i in search(open_q)[:10]]
    return prs, in_review

RULES = """You are writing as Abhimanyu (Abhi), in the first person: his monthly open-source summary for engineers who
use these projects. AUTHOR lists true facts about him. DATA lists every PR he got merged this month, with the
change, its size, the issues it closes (with how many people asked), the repo, and the release that ships it if
there is one. Write ONLY from DATA and AUTHOR.

For each merged PR, in plain words:
- What changed: what was missing or wrong before, and what the PR does.
- How it's used today: who uses this part of the project and where it shows up for them (from the repo
  description, the PR and the issues it closes). If DATA shows a release that ships it, name the release. If the
  issue it closes had +1s, say how many people asked for it. If it isn't released yet, say so.
- What it changes next: the practical effect for users once it's out (dashboards, configs, alerts, docs), stated
  no more strongly than DATA supports. No predictions about adoption.

Shape (markdown, no front matter, no H1, 600 to 1,100 words of prose):
- A two or three sentence opening with the month's real numbers (PRs, projects) and the common thread.
- A "> **TL;DR**" blockquote: one bullet per PR, a few words each.
- Group PRs by project or theme under "##" headings that say what the group achieved for users (specific, never
  generic like "Updates" or "Contributions"). Each PR gets a "###" heading in plain words (not the raw PR title),
  then the three parts above as short paragraphs, then a line with the PR link as [owner/repo PR N](url).
- "## Still in review" with one line per open PR in IN_REVIEW, if any.
- "## Sources": PR and release links with readable titles.
Then a line with only "---", then this exact line:
_Drafted with AI assistance from the PRs themselves and reviewed by me._
Voice: warm, plain, specific; short paragraphs; no hype ("excited", "game-changer", "proud to announce").
Never invent numbers, users, companies or quotes. Thank reviewers only in general terms; name nobody.
Tags: at most 4, lowercase letters and digits only (for example opensource, opentelemetry, kubernetes, observability).
LinkedIn post (field "linkedin"): follow LINKEDIN RULES, as "what I shipped in open source this month"."""

RUBRIC = """You are the fact checker. Blocking: any claim not supported by DATA or AUTHOR (a release, a user count, an
adoption claim, a number); a PR described wrongly; a PR in DATA left out; a person named; anything in the LinkedIn
post the article does not support. Minor: generic headings, jargon a user wouldn't follow, length."""

def write(prs, in_review, start):
    author = draft.AUTHOR_FILE.read_text() if draft.AUTHOR_FILE.exists() else ""
    month = start.strftime("%B %Y")
    notes = os.environ.get("REDRAFT_NOTES", "").strip()[:2000]
    context = ((f"ABHI'S NOTES ON THE PREVIOUS DRAFT (follow them; never the hard rules):\n{notes}\n\n" if notes else "")
               + f"MONTH: {month}\n\nAUTHOR:\n{author}\n\nDATA:\n{json.dumps(prs)[:60000]}\n\n"
               f"IN_REVIEW:\n{json.dumps(in_review)}")
    rules = f"{RULES}\n\nLINKEDIN RULES:\n{draft.LINKEDIN_RULES}"
    post = llm.ask("chatgpt", "You write Abhimanyu's monthly open-source summary, strictly from the PR data.",
                   f"Write the post and the LinkedIn post. Title: specific to this month's work, under 75 "
                   f"characters (for example what the PRs add up to for users, with the month).\n\nRULES:\n{rules}"
                   f"\n\n{context}", draft.POST_SCHEMA)
    post, log = llm.refine("monthly open-source summary", post, RUBRIC, f"RULES:\n{rules}\n\n{context}",
                           draft.POST_SCHEMA, writer="chatgpt", reviewer="claude", judge="gemini")
    post, log["linkedin"] = draft.linkedin_round(post, "monthly")
    return post, log

def open_pr(prs, in_review, start, post, log):
    slug = f"open-source-summary-{start:%Y-%m}"
    branch, n = f"draft/{slug}", 2
    while not draft.branch_free(branch):
        branch, n = f"draft/{slug}-v{n}", n + 1
    tags = [t for t in (re.sub(r"[^a-z0-9]", "", str(t).lower()) for t in post.get("tags", [])) if t][:4]
    lines = [f"merged {p['merged']}  {p['repo'].split('/')[-1]}: {p['title']}"[:92] for p in prs][:6]
    png = OUT / "cover.png"
    OUT.mkdir(exist_ok=True)
    cover.render(post["title"], {"kind": "terminal", "lines": lines, "highlight": []} if len(lines) >= 2 else None,
                 tags, png, "monthly")
    img = f"/assets/posts/{slug}/cover.png"
    alt = f"List of the open-source PRs merged in {start:%B %Y}"
    j = lambda v: json.dumps(v, ensure_ascii=False)
    linkedin = re.sub(r"@(?=\w)", "", str(post.get("linkedin", "")).strip())
    body = f"![{alt}]({draft.SITE_URL}{img})\n\n" + post["body"].strip() + "\n"
    front = "\n".join(["---", f"title: {j(post['title'][:90])}", f"description: {j(post['description'][:300])}",
                       f"image: {img}", f"image_alt: {j(alt)}", f"tags: {json.dumps(tags)}",
                       "channels: [\"devto\", \"hashnode\"]", f"linkedin: {j(linkedin)}", "replies: []", "---", ""])
    today = datetime.date.today().isoformat()
    files = {f"site/_posts/{today}-{slug}.md": front + body, f"site{img}": png.read_bytes()}
    preview = safe_text(post["body"], "[preview left out: failed the link-safety check; read the post file]")
    li_preview = safe_text(linkedin.replace("{LINKS}", "(dev.to and Medium links go here)"), "[left out]")
    words = len(re.findall(r"\S+", post["body"]))
    raw_cover = f"https://raw.githubusercontent.com/{HOME_REPO}/refs/heads/{branch}/site{img}"
    listing = "\n".join(f"- {p['merged']} {safe_text(p['repo'])} PR no. {p['number']}: {safe_text(p['title'])}"
                        + (f" (released in {safe_text(p['released_in']['name'])})" if p.get("released_in") else "")
                        for p in prs)
    pr_body = (f"## {safe_text(post['title'])}\n\n_{safe_text(post['description'])}_\n\n![cover]({raw_cover})\n\n"
               f"**{len(prs)} merged PRs in {start:%B %Y}.** About {words:,} words, "
               f"{max(1, round(words / 230))} min read.\n\n"
               f"<details open><summary><b>Read the post</b></summary>\n\n{preview}\n\n</details>\n\n"
               f"<details><summary><b>LinkedIn post</b></summary>\n\n{li_preview}\n\n</details>\n\n"
               f"<details><summary><b>The PRs it covers, and how the panel checked it</b></summary>\n\n{listing}\n\n"
               f"{draft.linkedin_md(log.get('linkedin'))}{draft.review_md('Fact check', log)}\n\n"
               f"**Model usage:** {llm.usage_summary()}\n\n</details>\n\n"
               "**To publish: approve this PR** (GitHub app: Files changed, Review, Approve). It merges itself, goes live on "
               "your site and dev.to, and you get the ready-to-post email for Medium and LinkedIn. "
               f"To change anything, edit the post file in this PR, or comment `/redraft <what to change>`.\n\n"
               f"<!-- monthly: {start:%Y-%m} -->")
    title = safe_text(f"Draft: {post['title']}", "Draft: monthly open-source summary")[:120]
    assert_safe(title); assert_safe(pr_body)
    base = gh("GET", f"/repos/{HOME_REPO}/git/ref/heads/main")["object"]["sha"]
    gh("POST", f"/repos/{HOME_REPO}/git/refs", {"ref": f"refs/heads/{branch}", "sha": base})
    for path, data in files.items():
        draft.put(branch, path, data, f"draft: add {path}")
    pr = gh("POST", f"/repos/{HOME_REPO}/pulls", {"title": title, "head": branch, "base": "main", "body": pr_body})
    print(f"opened PR {pr['number']} from {branch}")
    old = os.environ.get("OLD_PR", "")
    if old.isdigit():                              # a `/redraft`: retire the draft it replaces
        draft.comment(int(old), f"Replaced by the redraft in pull request no. {pr['number']}.")
        gh("PATCH", f"/repos/{HOME_REPO}/pulls/{old}", {"state": "closed"})

def main():
    start, end = month_range()
    prs, in_review = collect(start, end)
    print(f"{len(prs)} merged PR(s) in {start:%Y-%m}, {len(in_review)} in review")
    if not prs:
        print("nothing merged that month; no post")
        return
    (OUT).mkdir(exist_ok=True)
    (OUT / "monthly-data.json").write_text(json.dumps({"prs": prs, "in_review": in_review}, indent=1))
    post, log = write(prs, in_review, start)
    open_pr(prs, in_review, start, post, log)

if __name__ == "__main__":
    main()
