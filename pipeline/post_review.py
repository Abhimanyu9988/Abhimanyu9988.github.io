#!/usr/bin/env python3
"""ChatGPT, Gemini and Claude Opus each give an article and its LinkedIn post a pass or a block, judged against the lab's
evidence. For anything else that will be posted (a weekly LinkedIn post, a Reddit draft), ChatGPT's best model and Claude
Opus double-check the text against its sources.

Abhi's rules: a post is ready only when every reviewer approves (ChatGPT and Gemini since 16 Nov 2026, Opus added
25 Nov 2026), and nothing is posted or updated until ChatGPT and Opus have both double-checked it. The draft job runs the same check on every draft
(draft.review_draft). This script runs it on an article written in Claude chat, before it is published, or on a live
ShowMerit article. It writes nothing, not even its spend record (it holds no write token): the result is the job summary, and the job fails unless both approve.

  ARTICLE=<slug>  a ShowMerit article, content/abhimanyu/articles/<slug>.md, with labs/<slug>/evidence
                  (SITE_REF=<branch> reviews the article on an open ShowMerit PR branch instead of main)
  ISSUE_NO=<n>    a chat article, chat-work/<n>/post.json on branch chat/<n>, with the draft branch's evidence if any
  POST_TEXT=...   a standalone post, with POST_KIND (e.g. "LinkedIn post", "Reddit post") and POST_SOURCES (the links
                  and facts it rests on). Checked by ChatGPT and Claude Opus only. The job log ends with
                  "DOUBLE CHECK: PASS" or "DOUBLE CHECK: BLOCK", so a chat session can read the verdict.
"""
import base64, json, os, re, sys
import yaml
import budget, llm
from radar import HOME_REPO, gh

SITE_REPO = "Abhimanyu9988/showmerit"
MAIN_LOG = re.compile(r"-(reproduce|fix|check)\.log$")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,120}")

def text_of(repo, path, ref="main"):
    got = gh("GET", f"/repos/{repo}/contents/{path}?ref={ref}")
    return base64.b64decode(got["content"]).decode("utf-8", "replace")

def evidence(repo, folder, ref="main", budget=80_000):
    """The lab's own output whole and first, then the rest's endings, as the draft panel gets it."""
    try:
        rows = [r for r in gh("GET", f"/repos/{repo}/contents/{folder}?ref={ref}") or [] if r.get("name", "").endswith(".log")]
    except Exception as e:
        print(f"no evidence found: {str(e)[:150]}")
        return {}
    out = {}
    for r in sorted(rows, key=lambda r: (not MAIN_LOG.search(r["name"]), r["name"])):
        t = text_of(repo, f"{folder}/{r['name']}", ref)
        t = t[-13000:] if MAIN_LOG.search(r["name"]) else t[-3000:]
        if budget - len(t) < 0:
            break
        out[r["name"]] = t; budget -= len(t)
    return out

def from_site(slug, ref="main"):
    raw = text_of(SITE_REPO, f"content/abhimanyu/articles/{slug}.md", ref)
    _, front, body = raw.split("---", 2)
    meta = yaml.safe_load(front) or {}
    post = {"title": meta.get("title", ""), "description": meta.get("description", ""), "body": body.strip(),
            "linkedin": meta.get("linkedin", "")}
    try:
        readme = text_of(SITE_REPO, f"labs/{slug}/README.md", ref)
    except Exception:
        readme = ""
    return post, readme, evidence(SITE_REPO, f"labs/{slug}/evidence", ref)

def from_chat(issue):
    post = json.loads(text_of(HOME_REPO, f"chat-work/{issue}/post.json", f"chat/{issue}"))
    lab = json.loads(text_of(HOME_REPO, f"chat-work/{issue}/lab.json", f"chat/{issue}"))
    readme = next((f.get("content", "") for f in lab.get("files") or [] if f.get("path") == "README.md"), "")
    slug = draft_slug(post.get("title", ""))       # the draft step's PR holds the paid run's raw output under this name
    try:
        logs = evidence(HOME_REPO, f"labs/{slug}/evidence", f"draft/{slug}")
    except Exception as e:
        print(f"no draft evidence for {slug}: {str(e)[:120]}")
        logs = {}
    return post, readme, logs

def draft_slug(title):
    """draft.title_slug, repeated here so this script needs none of draft.py's settings."""
    out = ""
    for w in re.sub(r"[^a-z0-9]+", " ", str(title).lower()).split():
        if len(out) + len(w) + 1 > 60:
            break
        out = f"{out}-{w}" if out else w
    return out

def summary_md(name, result):
    lines = [f"## Clean pass for {name}", ""]
    for who, r in result.items():
        lines.append(f"- **{llm.NAMES.get(who, who)}: {'approved' if r['approve'] else 'blocked'}**")
        lines += [f"  - {b.get('problem')} Fix: {b.get('fix')}" for b in r.get("blocking") or []]
    return "\n".join(lines) + "\n"

def text_post():
    """A standalone post: ChatGPT and Opus double-check it against its sources. Exit 1 unless both approve."""
    text, kind = os.environ.get("POST_TEXT", "").strip(), " ".join(os.environ.get("POST_KIND", "post").split())[:60] or "post"
    if len(text) > 20000:
        sys.exit("POST_TEXT is too long for a post")
    sources = os.environ.get("POST_SOURCES", "").strip()[:30000]
    budget.check()                                  # before the first paid call, as every job does
    result = llm.clean_pass({"kind": kind, "text": text}, f"SOURCES:\n{sources or '(none given)'}", who=llm.DOUBLE_CHECK, kind=kind)
    ok = all(r["approve"] for r in result.values()) and set(result) == set(llm.DOUBLE_CHECK)
    md = summary_md(kind, result).replace("## Clean pass", "## Double check") + f"\nDOUBLE CHECK: {'PASS' if ok else 'BLOCK'}\n"
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md)
    if not ok:
        sys.exit(1)

def main():
    if os.environ.get("POST_TEXT", "").strip():
        return text_post()
    slug, issue = os.environ.get("ARTICLE", "").strip(), os.environ.get("ISSUE_NO", "").strip()
    if slug and SLUG.fullmatch(slug):
        post, readme, ev = from_site(slug, os.environ.get("SITE_REF") or "main")
        name = slug
    elif issue.isdigit():
        post, readme, ev = from_chat(int(issue))
        name = f"chat article {issue}"
    else:
        sys.exit("give ARTICLE (a ShowMerit slug), ISSUE_NO (a chat topic number) or POST_TEXT (a standalone post)")
    context = f"LAB README:\n{readme[:12000]}\n\nEVIDENCE:\n{json.dumps(ev)}"
    budget.check()                                  # before the first paid call, as every job does
    result = llm.clean_pass(post, context)
    md = summary_md(name, result)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md)
    if not all(r["approve"] for r in result.values()):
        sys.exit(1)

if __name__ == "__main__":
    main()
