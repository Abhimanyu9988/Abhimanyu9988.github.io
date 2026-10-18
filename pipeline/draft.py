#!/usr/bin/env python3
"""Turn a lab run into a draft post PR, or a comment on the topic issue explaining why not.

Claude writes the post from the lab's REAL output only. ChatGPT checks it against that output, and
Gemini rules on disagreements. The bot opens a PR from draft/<slug> with the post, the lab and its
raw output. Abhimanyu reviews, edits and merges; publish.yml then publishes it everywhere.
"""
import base64, datetime, json, os, pathlib, re
from radar import HOME_REPO, gh, safe_text, assert_safe, comment, add_label, Unsafe
import llm

OUT = pathlib.Path("out")
SITE_URL = "https://abhimanyu9988.github.io"
REPO_WEB = "https://github.com/" + HOME_REPO          # used only inside post files, never in issue/PR text

ARTICLE_RULES = """Write as Abhimanyu, in the first person: an engineer who replicated this himself. Plain and direct,
no hype, no filler openings. Readers: students learning AWS containers, and working engineers.
Structure (markdown, no front matter, no H1; start with a short paragraph):
  1. The symptom people hit, in two or three sentences.
  2. "## What you'll see": the real error or status, copied from EVIDENCE.
  3. "## Why it happens": the cause. Say clearly what the sources and the lab confirm, and what is only likely.
  4. "## Reproduce it": what the lab builds and how to run it. Link the lab folder LAB_URL. Show the key
     Terraform or script snippets, copied from the LAB files.
  5. "## The fix": the change, then the verification output copied from EVIDENCE.
  6. "## Clean up" and "## Cost and time": use the run's real minutes; say it ran in eu-central-1.
  7. "## Sources": the source URLs as a list.
Hard rules: every command output, error message, version or number you show must appear in EVIDENCE or LAB
(you may trim with "..."). Never invent output. Keep <account-id> placeholders as they are. 900 to 1600 words.
Finish with this exact line: _I reproduced this in a disposable AWS lab. The lab code and raw output are in the
repo. Drafted with AI assistance and reviewed by me._
Tags: at most 4, lowercase letters and digits only (for example aws, eks, kubernetes, devops)."""

ARTICLE_RUBRIC = """Blocking: any output, error text, version or number that is not in EVIDENCE or LAB; a claim about
the cause stronger than the sources and evidence support; steps that do not match the lab code; a missing
reproduction or fix verification; anything that would mislead a beginner. Minor: clarity, structure, title, length."""

POST_SCHEMA = {"type": "object", "required": ["title", "description", "tags", "body"], "properties": {
    "title": {"type": "string", "description": "max 90 characters, specific, no clickbait"},
    "description": {"type": "string", "description": "one or two sentences for previews"},
    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    "body": {"type": "string", "description": "the article in markdown"}}}

def review_md(name, log):
    if not log:
        return ""
    pts = log.get("points") or []
    head = f"**{name}:** ChatGPT {'approved it as written' if log.get('chatgpt_approved') else 'raised points'}"
    if not pts:
        return head + "."
    lines = [f"- _{safe_text(p.get('severity'), 'note')[:12]}_ {safe_text(p.get('issue'))[:240]} → "
             f"{safe_text(p.get('outcome'))}" for p in pts[:12]]
    return head + f" ({len(pts)}):\n" + "\n".join(lines)

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

def write_post(res, topic, slug):
    lab_dir = OUT / "lab"
    lab = {str(p.relative_to(lab_dir)): p.read_text() for p in sorted(lab_dir.rglob("*")) if p.is_file()}
    ev, budget = {}, 70_000
    for p in sorted((OUT / "evidence").glob("*.log")):
        t = p.read_text()[-8000:]
        if budget - len(t) < 0:
            break
        ev[p.name] = t; budget -= len(t)
    context = (f"TOPIC:\n{json.dumps(topic)[:20000]}\n\nRUN: status={res['status']}, attempts={res.get('attempts')}, "
               f"minutes={res.get('minutes')}, summary={res.get('summary')}\n\nLAB_URL: {REPO_WEB}/tree/main/labs/{slug}"
               f"\n\nLAB:\n{json.dumps(lab)[:60000]}\n\nEVIDENCE:\n{json.dumps(ev)}")
    post = llm.claude_json("You write hands-on AWS container articles for Abhimanyu's blog, strictly from real lab output.",
                           f"Write the article.\n\nRULES:\n{ARTICLE_RULES}\n\n{context}", POST_SCHEMA, tool="submit_post")
    post, log = llm.refine("blog article", post, ARTICLE_RUBRIC, f"RULES:\n{ARTICLE_RULES}\n\n{context}", POST_SCHEMA)
    return post, log, lab

def open_pr(issue_no, res, topic):
    slug = res["slug"][:60].strip("-")
    if not branch_free(f"draft/{slug}"):
        slug = f"{slug[:50].strip('-')}-{res['run_id'][-6:]}"      # branch, post and lab folder all move
    branch = f"draft/{slug}"
    post, log, lab = write_post(res, topic, slug)
    tags = [t for t in (re.sub(r"[^a-z0-9]", "", str(t).lower()) for t in post.get("tags", [])) if t][:4]
    today = datetime.date.today().isoformat()
    j = lambda v: json.dumps(v, ensure_ascii=False)
    front = "\n".join(["---", f"title: {j(post['title'][:90])}", f"description: {j(post['description'][:300])}",
                       f"tags: {json.dumps(tags)}", "channels: [\"devto\", \"hashnode\"]", f"lab: labs/{slug}", "---", ""])
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

    warn = "" if res.get("destroyed") else ("\n\n⚠️ **The lab may not have been fully destroyed.** The next lab run "
                                             "retries automatically; check the AWS console if you're worried.")
    body = (f"Draft post for topic issue no. {issue_no}, replicated on the AWS lab account.\n\n"
            f"**Lab:** {safe_text(res.get('summary'))}\n\n"
            f"**Run:** reproduced and fixed in attempt {res.get('attempts')}, {res.get('minutes')} min total "
            f"(workflow run {res['run_id']}).{warn}\n\n"
            f"{review_md('Lab review', res.get('review'))}\n\n{review_md('Article review', log)}\n\n"
            f"**Model usage:** lab job: {res.get('usage')}; draft job: {llm.usage_summary()}\n\n"
            "**Before you merge** (merging publishes to the site, dev.to and Hashnode):\n"
            "- [ ] Every output in the post matches a file in the lab's evidence folder\n"
            "- [ ] The cause is stated no more strongly than the sources support\n"
            "- [ ] Add your own intro or story, and make the title yours\n\n"
            )
    title = safe_text("Draft: " + post["title"], "Draft post")[:120]
    assert_safe(title); assert_safe(body)          # check before anything is created, so a failure leaves no mess
    base = gh("GET", f"/repos/{HOME_REPO}/git/ref/heads/main")["object"]["sha"]
    gh("POST", f"/repos/{HOME_REPO}/git/refs", {"ref": f"refs/heads/{branch}", "sha": base})
    for path, text in files.items():
        put(branch, path, text, f"draft: add {path}")
    pr = gh("POST", f"/repos/{HOME_REPO}/pulls", {"title": title, "head": branch, "base": "main",
                                                  "body": f"{body}\n\nCloses #{issue_no}"})
    print(f"opened PR {pr['number']} from {branch}")
    comment(issue_no, f"✅ Replicated and fixed. The draft post is ready for your review in pull request no. "
                      f"{pr['number']} (branch {branch}).{warn}")
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
