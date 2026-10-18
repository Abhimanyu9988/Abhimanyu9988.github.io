#!/usr/bin/env python3
"""Turn a lab run into a draft post PR, or a comment on the topic issue explaining why not.

  1. Verdict: Claude, ChatGPT and Gemini each read the topic, the lab and its real output and say whether it
     genuinely replicated the problem and showed the fix. 2 of 3 yes is needed to draft.
  2. ChatGPT writes the article (human, story-first, every command) and a LinkedIn post. Claude checks the
     technical accuracy against the evidence; Gemini rules on anything they disagree about.
  3. The bot opens a PR from draft/<slug> showing the post itself at the top, with the lab and raw output.
     Abhimanyu edits and merges; publish.yml then publishes and hands him Medium and LinkedIn links.
REDRAFT=1 redrafts from an earlier lab run's attachment (no new AWS run).
"""
import base64, datetime, json, os, pathlib, re
from radar import HOME_REPO, gh, safe_text, assert_safe, comment, add_label, Unsafe
import llm

OUT = pathlib.Path("out")
SITE_URL = "https://abhimanyu9988.github.io"
REPO_WEB = "https://github.com/" + HOME_REPO          # used only inside post files, never in issue/PR text

VERDICT_Q = """Did this lab GENUINELY replicate the problem described in TOPIC, and does EVIDENCE show the fix working?
Say no if the lab reproduced a different or contrived problem, if the evidence does not show what the scripts claim,
if the fix only "worked" because of a shortcut, or if the result would mislead a reader. Judge only from TOPIC, LAB and
EVIDENCE."""

ARTICLE_RULES = """You are writing as Abhimanyu (Abhi), in the first person. AUTHOR lists true facts about him; he
reproduced this problem himself in a disposable AWS lab. Write the way he'd explain it to a teammate or a student
over coffee: warm, plain, specific. Readers: students learning AWS containers, and working engineers who just hit this.

Make it human:
- Use the chosen TITLE (polish the wording only if it truly reads better) and open with the chosen HOOK: keep its
  substance and its first line. Then the person who hit this and the moment it hurts (from the sources: what they
  were trying to do, what they saw, why it was confusing). Two or three short paragraphs, no heading.
- Add one or two personal lines that connect it to Abhi's own work, using ONLY facts in AUTHOR. Never invent
  anecdotes, numbers, customers or employers.
- STYLE SIGNALS show titles, openings and section headings that are doing well right now. Borrow what makes them
  work (a concrete symptom up front, a clear promise, scannable headings); never copy their wording.
- Then a short **TL;DR**: problem, cause, fix, in three lines.
- Short paragraphs. Plain words. Explain jargon the first time. Use "you" and "I".
- Banned: "In today's...", "delve", "landscape", "robust", "seamless", "game-changer", "leverage", "Let's dive in",
  "unlock", "journey", emojis in headings, exclamation marks.

Sections after the TL;DR (markdown, no front matter, no H1):
  "## What you'll see": the real symptom and error/status, copied from EVIDENCE.
  "## Why it happens": the cause. Say clearly what the sources and the lab confirm, and what is only likely.
  "## Replicate it yourself, step by step": numbered steps covering EVERY command, in the order they run:
     1. get the lab: `git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git` and
        `cd Abhimanyu9988.github.io/labs/LAB_SLUG` (mention providers.tf: set permissions_boundary to null).
     2. `terraform init` and `terraform apply`, and what it creates.
     3. every aws/kubectl/helm/terraform command that reproduce.sh runs, each in its own code block with one line
        saying what it does, followed by the real output from EVIDENCE (loops may be shown once with a note).
     4. the same for fix.sh (and fix.tf if there is one), with the real verification output.
     5. cleanup: every command in cleanup.sh, then `terraform destroy`.
     Readers must be able to follow this section by hand without running the scripts.
  "## What I didn't test": honest limits.
  "## Cost and time": the run's real minutes and resource counts; region eu-central-1.
  "## The takeaway": one or two sentences of practical advice in Abhi's voice.
  "## Sources": each source as a markdown link with a readable title, e.g.
     "- [Stack Overflow: ECS Fargate SQS autoscaling with min capacity 0](https://...)", never a bare URL.
Then a line with only "---", then this exact line, on ONE line:
_I reproduced this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._

Write the steps for a READER, not about the pipeline: plain `terraform init` and `terraform apply` (the lab's
providers.tf already sets the region), never the pipeline's own S3 backend settings, -backend-config, plan files,
-no-color or HERO_RESULT lines. Introduce each command with what it does and why, in plain words, the way you'd walk
a colleague through it; don't narrate script mechanics ("the script loops over...", "formatted output of the
helper commands"). Group closely related commands in one block. Shell variables the scripts set from Terraform
outputs should be shown once, near the start.
Hard rules: every command, output, error message, version or number must appear in LAB or EVIDENCE (trim with
"..."). Never invent output. Keep <account-id> placeholders. 1200 to 2600 words.
Tags: at most 4, lowercase letters and digits only (for example aws, ecs, kubernetes, devops).

LinkedIn post (field "linkedin"), plain text, no markdown, 120 to 220 words:
- First line: a specific, human hook about the problem (no clickbait, no "I'm excited to share").
- Who hits it and why it hurts, what I found in the lab (cause + fix) in two or three short lines.
- One question that invites people to share their experience.
- Then the line: "Full write-up, every command and the lab: {POST_URL}"
- Then 3 to 5 hashtags. At most two emojis in the whole post. No @mentions (Abhi tags people himself).

Source replies (field "source_replies"): for EACH source URL, a short reply Abhi could post on that thread himself,
60 to 120 words, first person, helpful on its own (what I reproduced, what fixed it, one caveat), ending with
"I wrote up the full replication here: {POST_URL}". No greeting fluff, no self-promotion beyond that one link.
Stack Overflow does not allow AI-written answers, so these are drafts Abhi rewrites in his own words."""

ARTICLE_RUBRIC = """You are the technical checker. Blocking: any personal claim about Abhi that is not in AUTHOR; any
command, output, error text, version or number that is not in EVIDENCE or LAB; a command in the step-by-step section that differs from the lab files, or a command the
scripts run that is missing from it; a claim about the cause stronger than the sources and evidence support; a
missing reproduction or fix verification; anything in the LinkedIn post that the article does not support; anything
that would mislead a beginner. Minor: tone that reads robotic or like an AI, clarity, structure, title, length."""

POST_SCHEMA = {"type": "object", "required": ["title", "description", "tags", "body", "linkedin", "source_replies"], "properties": {
    "title": {"type": "string", "description": "max 90 characters, specific and human, no clickbait"},
    "description": {"type": "string", "description": "one or two sentences for previews"},
    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    "body": {"type": "string", "description": "the article in markdown"},
    "linkedin": {"type": "string", "description": "the LinkedIn post, plain text, with {POST_URL} placeholder"},
    "source_replies": {"type": "array", "items": {"type": "object", "required": ["url", "text"], "properties": {
        "url": {"type": "string"}, "text": {"type": "string"}}}}}}

def review_md(name, log):
    if not log:
        return ""
    pts = log.get("points") or []
    rev = log.get("reviewer", "ChatGPT")
    head = f"**{name}:** {rev} {'approved it as written' if log.get('approved', log.get('chatgpt_approved')) else 'raised points'}"
    if not pts:
        return head + "."
    lines = [f"- _{safe_text(p.get('severity'), 'note')[:12]}_ {safe_text(p.get('issue'))[:240]} \u2192 "
             f"{safe_text(p.get('outcome'))}" for p in pts[:12]]
    return head + f" ({len(pts)}):\n" + "\n".join(lines)

def headline_md(h):
    if not h:
        return ""
    others = "".join(f"\n- {safe_text(o)}" for o in h.get("others", []))
    return (f"**Headline round:** the panel picked {h['by']}'s title and opening ({h['yes']} of 3 votes), shaped by "
            f"what's getting the most reactions on dev.to right now.{others}\n\n")

def verdict_md(v):
    mark = lambda x: "\u2705" if x == "yes" else "\u274c"
    lines = [f"- {llm.NAMES[w]} {mark(a)} _{safe_text(r)}_" for w, (a, r) in v["votes"].items()]
    return f"**Replication check:** {v['yes']} of 3 say the lab genuinely replicated it\n" + "\n".join(lines)

def put(branch, path, text, msg):
    gh("PUT", f"/repos/{HOME_REPO}/contents/{path}",
       {"message": msg, "branch": branch, "content": base64.b64encode(text.encode()).decode()})

def branch_free(name):
    try:
        gh("GET", f"/repos/{HOME_REPO}/git/ref/heads/{name}")
        return False
    except RuntimeError as e:
        if "HTTP 404" in str(e):
            return True
        raise

def gather(res, topic, slug):
    lab_dir = OUT / "lab"
    lab = {str(p.relative_to(lab_dir)): p.read_text() for p in sorted(lab_dir.rglob("*")) if p.is_file()}
    ev, budget = {}, 70_000
    for p in sorted((OUT / "evidence").glob("*.log")):
        t = p.read_text()[-8000:]
        if budget - len(t) < 0:
            break
        ev[p.name] = t; budget -= len(t)
    context = (f"TOPIC:\n{json.dumps(topic)[:20000]}\n\nRUN: status={res['status']}, attempts={res.get('attempts')}, "
               f"minutes={res.get('minutes')}, summary={res.get('summary')}\n\nLAB_SLUG: {slug}\nLAB_URL: "
               f"{REPO_WEB}/tree/main/labs/{slug}\n\nLAB:\n{json.dumps(lab)[:60000]}\n\nEVIDENCE:\n{json.dumps(ev)}")
    return lab, context

AUTHOR_FILE = pathlib.Path(__file__).with_name("author.md")
STYLE_TAGS = ("aws", "kubernetes", "devops", "docker")

def style_signals():
    """Titles, openings and headings of the most-reacted recent dev.to posts in our tags, plus what has worked on
    Abhi's own posts. Signals only: the writer borrows what works, never the wording."""
    from radar import call, load_insights
    seen, arts = set(), []
    for tag in STYLE_TAGS:
        try:
            for a in call("GET", f"https://dev.to/api/articles?tag={tag}&top=30&per_page=30") or []:
                if a.get("id") not in seen:
                    seen.add(a.get("id")); arts.append(a)
        except Exception as e:
            llm.warn(f"style signals: dev.to tag {tag}: {str(e)[:200]}")
    arts.sort(key=lambda a: -(a.get("public_reactions_count") or a.get("positive_reactions_count") or 0))
    lines = [f"- {a.get('title')} ({a.get('public_reactions_count') or a.get('positive_reactions_count') or 0} "
             f"reactions, {a.get('comments_count', 0)} comments)" for a in arts[:15]]
    shapes = []
    for a in arts[:5]:
        try:
            full = call("GET", f"https://dev.to/api/articles/{a['id']}")
            body = full.get("body_markdown") or ""
            first = next((p.strip() for p in body.split("\n\n") if p.strip() and not p.lstrip().startswith(("#", "!", "{%", "---"))), "")
            heads = re.findall(r"^#{2,3} (.+)$", body, re.M)[:8]
            shapes.append(f"- \"{a.get('title')}\": opens with: {first[:300]!r}; headings: {heads}")
        except Exception as e:
            llm.warn(f"style signals: dev.to article: {str(e)[:200]}")
    own = load_insights()
    return ("Most-reacted recent dev.to posts (aws, kubernetes, devops, docker):\n" + "\n".join(lines) +
            "\n\nHow the top ones open and section themselves:\n" + "\n".join(shapes) +
            f"\n\nWhat has worked on Abhi's own posts:\n{own or 'nothing measured yet'}")

HEADLINE_SCHEMA = {"type": "object", "required": ["title", "hook"], "properties": {
    "title": {"type": "string", "description": "max 90 characters, specific and true to the evidence"},
    "hook": {"type": "string", "description": "the first two or three sentences of the post"}}}

def headline_round(context, signals, author):
    """Each of the three proposes a title and opening; all three vote; 2 of 3 or the most votes wins."""
    props = []
    for who in llm.PANEL:
        try:
            h = llm.ask(who, "You write headlines and openings for Abhimanyu's hands-on AWS container posts.",
                        "Propose ONE title and ONE opening (two or three sentences) for this post. It must make an "
                        "engineer who has this problem stop scrolling, and it must be true to the evidence: no "
                        "clickbait, no promises the lab doesn't back. Use STYLE SIGNALS for what works now and AUTHOR "
                        f"for any personal angle.\n\nSTYLE SIGNALS:\n{signals}\n\nAUTHOR:\n{author}\n\n"
                        f"{context[:40000]}", HEADLINE_SCHEMA, tool="submit_headline")
            if h.get("title") and h.get("hook"):
                props.append({"id": f"h{len(props) + 1}", "by": llm.NAMES[who], "title": str(h["title"])[:120],
                              "hook": str(h["hook"])[:800]})
        except Exception as e:
            llm.warn(f"{who} proposed no headline: {str(e)[:200]}")
    if not props:
        return None
    votes = llm.panel_votes(props, "Which title and opening would make an AWS or Kubernetes engineer click AND keep "
                            "reading, while staying accurate to the lab? Vote yes only for ones you would publish.",
                            f"STYLE SIGNALS:\n{signals}", tier="write")
    best = max(props, key=lambda p: votes[p["id"]]["yes"])
    best["yes"] = votes[best["id"]]["yes"]
    best["others"] = [f"{p['by']}: {p['title']} ({votes[p['id']]['yes']}/3)" for p in props if p is not best]
    return best

def write_post(context):
    author = AUTHOR_FILE.read_text() if AUTHOR_FILE.exists() else ""
    signals = style_signals()
    head = headline_round(context, signals, author)
    chosen = (f"TITLE: {head['title']}\nHOOK: {head['hook']}" if head else "TITLE/HOOK: write your own")
    context = f"{chosen}\n\nAUTHOR:\n{author}\n\nSTYLE SIGNALS:\n{signals}\n\n{context}"
    post = llm.ask("chatgpt", "You write hands-on AWS container articles for Abhimanyu's blog, strictly from real lab "
                              "output, in a warm human voice.", f"Write the article and the LinkedIn post.\n\n"
                   f"RULES:\n{ARTICLE_RULES}\n\n{context}", POST_SCHEMA)
    post, log = llm.refine("blog article and LinkedIn post", post, ARTICLE_RUBRIC, f"RULES:\n{ARTICLE_RULES}\n\n{context}",
                           POST_SCHEMA, writer="chatgpt", reviewer="claude", judge="gemini")
    log["headline"] = head
    return post, log

def on_main(path):
    try:
        gh("GET", f"/repos/{HOME_REPO}/contents/{path}?ref=main")
        return True
    except RuntimeError as e:
        if "HTTP 404" in str(e):
            return False
        raise

def open_pr(issue_no, res, topic):
    slug = res["slug"][:60].strip("-")
    if on_main(f"labs/{slug}"):                    # already published: the new post needs its own name
        slug = f"{slug[:50].strip('-')}-{res['run_id'][-6:]}"
    branch, n = f"draft/{slug}", 2
    while not branch_free(branch):                 # a redraft: new branch, same post and lab names
        branch, n = f"draft/{slug[:70]}-v{n}", n + 1
    lab, context = gather(res, topic, slug)
    v = llm.verdict(VERDICT_Q, context)
    if not v["passed"]:
        add_label(issue_no, "replication-disputed")
        comment(issue_no, "\u26a0\ufe0f The lab reported reproduced-and-fixed, but the panel did not confirm the "
                          f"replication, so nothing was drafted.\n\n{verdict_md(v)}\n\nThe lab and raw output are in the "
                          f"`lab` attachment of workflow run {res['run_id']}.")
        return
    post, log = write_post(context)
    tags = [t for t in (re.sub(r"[^a-z0-9]", "", str(t).lower()) for t in post.get("tags", [])) if t][:4]
    today = datetime.date.today().isoformat()
    j = lambda v: json.dumps(v, ensure_ascii=False)
    linkedin = re.sub(r"@(?=\w)", "", str(post.get("linkedin", "")).strip())
    srcs = {x.get("url") for x in topic.get("sources") or [] if isinstance(x, dict)}
    replies = [{"url": str(r["url"]), "text": re.sub(r"@(?=\w)", "", str(r.get("text", "")).strip())}
               for r in post.get("source_replies") or []
               if isinstance(r, dict) and r.get("text") and (not srcs or r.get("url") in srcs)]
    front = "\n".join(["---", f"title: {j(post['title'][:90])}", f"description: {j(post['description'][:300])}",
                       f"tags: {json.dumps(tags)}", "channels: [\"devto\", \"hashnode\"]", f"lab: labs/{slug}",
                       f"linkedin: {j(linkedin)}", f"replies: {j(replies)}", "---", ""])
    files = {f"site/_posts/{today}-{slug}.md": front + post["body"].strip() + "\n"}
    for name, text in lab.items():
        files[f"labs/{slug}/{name}"] = text
    files[f"labs/{slug}/providers.tf"] = (
        "# Use this instead of the runner's backend file: terraform init && terraform apply\n"
        'variable "region" {\n  type    = string\n  default = "eu-central-1"\n}\n'
        f'variable "lab_name" {{\n  type    = string\n  default = "{slug[:24].strip("-")}"\n}}\n'
        'variable "permissions_boundary" {\n  type    = string\n  default = null\n}\n'
        'provider "aws" {\n  region = var.region\n}\n')
    for p in sorted((OUT / "evidence").glob("*.log")):
        files[f"labs/{slug}/evidence/{p.name}"] = p.read_text()

    warn = "" if res.get("destroyed") else ("\n\n\u26a0\ufe0f **The lab may not have been fully destroyed.** The next lab run "
                                             "retries automatically; check the AWS console if you're worried.")
    preview = safe_text(post["body"], "[preview left out: failed the link-safety check; read the post file]")
    li_preview = safe_text(linkedin.replace("{POST_URL}", "(link to the post)"), "[left out]")
    body = (f"## {safe_text(post['title'])}\n\n_{safe_text(post['description'])}_\n\n"
            "This is the post as it will appear (GitHub links show as plain text in this preview only). Edit the post "
            f"file `site/_posts/{today}-{slug}.md` in this PR to change it.\n\n"
            f"<details open><summary><b>Read the post</b></summary>\n\n{preview}\n\n</details>\n\n"
            f"<details><summary><b>LinkedIn draft</b> (you'll get a one-tap link after publishing)</summary>\n\n"
            f"{li_preview}\n\n</details>\n\n"
            + "".join(f"<details><summary><b>Reply for the source thread</b> ({safe_text(r['url'], 'a source')})"
                      "</summary>\n\n" + safe_text(r["text"].replace("{POST_URL}", "(link to the post)"), "[left out]")
                      + "\n\n</details>\n\n" for r in replies)
            + "---\n\n"
            f"**Topic:** issue no. {issue_no}. **Lab:** {safe_text(res.get('summary'))}\n\n"
            f"**Run:** reproduced and fixed in attempt {res.get('attempts')}, {res.get('minutes')} min total "
            f"(workflow run {res['run_id']}).{warn}\n\n{verdict_md(v)}\n\n"
            f"{headline_md(log.get('headline'))}"
            f"{review_md('Lab review', res.get('review'))}\n\n{review_md('Article review', log)}\n\n"
            f"**Model usage:** lab job: {res.get('usage')}; draft job: {llm.usage_summary()}\n\n"
            "**Before you merge** (merging publishes to your site and dev.to; you then get Medium and LinkedIn links):\n"
            "- [ ] It reads like you (edit `pipeline/author.md` to change what the panel knows about you)\n"
            "- [ ] Every output in the post matches a file in the lab's evidence folder\n"
            "- [ ] The cause is stated no more strongly than the sources support\n")
    title = safe_text("Draft: " + post["title"], "Draft post")[:120]
    if len(body) > 60000:
        body = body.replace(preview, "[preview too long for the PR description; read the post file]")
    assert_safe(title); assert_safe(body)          # check before anything is created, so a failure leaves no mess
    base = gh("GET", f"/repos/{HOME_REPO}/git/ref/heads/main")["object"]["sha"]
    gh("POST", f"/repos/{HOME_REPO}/git/refs", {"ref": f"refs/heads/{branch}", "sha": base})
    for path, text in files.items():
        put(branch, path, text, f"draft: add {path}")
    pr = gh("POST", f"/repos/{HOME_REPO}/pulls", {"title": title, "head": branch, "base": "main",
                                                  "body": f"{body}\n\nCloses #{issue_no}"})
    print(f"opened PR {pr['number']} from {branch}")
    comment(issue_no, f"\u2705 Replicated and fixed, and the panel confirmed it ({v['yes']} of 3). The draft post is "
                      f"ready for your review in pull request no. {pr['number']}.{warn}")
    add_label(issue_no, "drafted")

def main():
    issue_no = int(os.environ["ISSUE"])
    rf = OUT / "result.json"
    if not rf.exists():
        comment(issue_no, f"The lab job (workflow run {os.environ.get('RUN_ID', '?')}) ended without a result: it "
                          "was not approved, was cancelled, or crashed before writing output. Nothing was drafted.")
        return
    res = json.loads(rf.read_text())
    topic = json.loads((OUT / "topic.json").read_text()) if (OUT / "topic.json").exists() else {}
    if res["status"] == "reproduced_and_fixed":
        try:
            open_pr(issue_no, res, topic)
        except Unsafe:
            comment(issue_no, "Replicated and fixed, but the draft's PR text failed the link-safety check, so no PR "
                              f"was opened. The lab and its output are in the `lab` attachment of workflow run {res['run_id']}.")
        return
    warn = "" if res.get("destroyed") in (True, None) else "\n\n⚠️ **The lab may not have been fully destroyed.** The next run retries."
    if res["status"] == "dry_run":
        msg = (f"Dry run: the panel wrote and reviewed a lab; AWS was not touched.\n\n**Lab:** {safe_text(res.get('summary'))}"
               f"\n\n{review_md('Lab review', res.get('review'))}\n\n**Model usage:** {res.get('usage')}")
    else:
        lf = res.get("last_failure") or {}
        out = safe_text(lf.get("output") or res.get("error") or "", "[output left out: failed the link-safety check]")[-1500:]
        out = out.replace("```", "'''")
        nice = {"not_reproduced": "\U0001f50e The lab ran but could not reproduce the problem.",
                "reproduced_fix_failed": "\U0001f9ea The lab reproduced the problem, but the fix did not work.",
                "failed": "❌ The lab did not get to a result."}.get(res["status"], res["status"])
        msg = (f"{nice} {res.get('attempts')} attempt(s), {res.get('minutes')} min, workflow run {res['run_id']}."
               f"{warn}\n\n**Last failure:** {safe_text(lf.get('step') or 'before the lab ran')}\n```\n{out}\n```\n\n"
               f"{_cleanup_md(res)}**Lab:** {safe_text(res.get('summary') or 'not written')}\n\nThe lab files and raw output are in the run's `lab` "
               f"attachment. Remove and re-add the `replicate` label to retry.\n\n**Model usage:** {res.get('usage')}")
        if res["status"] == "not_reproduced":
            add_label(issue_no, "not-reproduced")
    try:
        comment(issue_no, msg)
    except Unsafe:
        comment(issue_no, f"The lab run ended with status {res['status']}. Details failed the link-safety check; see the "
                          f"`lab` attachment of workflow run {res['run_id']}.")

def _cleanup_md(res):
    acts = (res.get("janitor_before") or []) + (res.get("janitor_after") or [])
    swept = [s for s in res.get("swept") or [] if s.get("destroyed")]
    if not acts and not swept:
        return ""
    lines = [f"- destroyed leftover lab {safe_text(s['lab'])}" for s in swept] + [f"- {safe_text(a)}" for a in acts[:15]]
    return "**Cleanup of leftovers:**\n" + "\n".join(lines) + "\n\n"

if __name__ == "__main__":
    main()
