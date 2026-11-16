#!/usr/bin/env python3
"""ChatGPT and Gemini each give an article and its LinkedIn post a pass or a block, judged against the lab's evidence.

Abhi's rule (16 Nov 2026): a post is ready only when both approve. The draft job runs the same check on every draft
(draft.review_draft). This script runs it on an article written in Claude chat, before it is published, or on a live
ShowMerit article. It writes nothing, not even its spend record (it holds no write token): the result is the job summary, and the job fails unless both approve.

  ARTICLE=<slug>  a ShowMerit article, content/abhimanyu/articles/<slug>.md, with labs/<slug>/evidence
  ISSUE_NO=<n>    a chat article, chat-work/<n>/post.json on branch chat/<n>, with the draft branch's evidence if any
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

def from_site(slug):
    raw = text_of(SITE_REPO, f"content/abhimanyu/articles/{slug}.md")
    _, front, body = raw.split("---", 2)
    meta = yaml.safe_load(front) or {}
    post = {"title": meta.get("title", ""), "description": meta.get("description", ""), "body": body.strip(),
            "linkedin": meta.get("linkedin", "")}
    try:
        readme = text_of(SITE_REPO, f"labs/{slug}/README.md")
    except Exception:
        readme = ""
    return post, readme, evidence(SITE_REPO, f"labs/{slug}/evidence")

def from_chat(issue):
    post = json.loads(text_of(HOME_REPO, f"chat-work/{issue}/post.json", f"chat/{issue}"))
    lab = json.loads(text_of(HOME_REPO, f"chat-work/{issue}/lab.json", f"chat/{issue}"))
    readme = next((f.get("content", "") for f in lab.get("files") or [] if f.get("path") == "README.md"), "")
    return post, readme, {}

def summary_md(name, result):
    lines = [f"## Clean pass for {name}", ""]
    for who, r in result.items():
        lines.append(f"- **{llm.NAMES.get(who, who)}: {'approved' if r['approve'] else 'blocked'}**")
        lines += [f"  - {b.get('problem')} Fix: {b.get('fix')}" for b in r.get("blocking") or []]
    return "\n".join(lines) + "\n"

def main():
    slug, issue = os.environ.get("ARTICLE", "").strip(), os.environ.get("ISSUE_NO", "").strip()
    if slug and SLUG.fullmatch(slug):
        post, readme, ev = from_site(slug)
        name = slug
    elif issue.isdigit():
        post, readme, ev = from_chat(int(issue))
        name = f"chat article {issue}"
    else:
        sys.exit("give ARTICLE (a ShowMerit slug) or ISSUE_NO (a chat topic number)")
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
