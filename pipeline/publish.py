"""Publish merged posts: the site is deployed by the workflow; this cross-posts and hands out the rest.

  dev.to     published automatically (canonical = your site).
  Hashnode   published automatically once HASHNODE_TOKEN is saved.
  Medium     with MEDIUM_TOKEN: created as a Medium DRAFT (markdown, real code blocks, canonical = your site),
             ready for you to press Publish. Without it: the one-tap import link.
  LinkedIn   share.json is written for share.py, which opens a `linkedin-draft` issue in your profile repo;
             comment `/post` there and it goes live. The report also has a link that opens the LinkedIn post
             box with the text filled in.
  Sources    a ready-to-post reply for each source thread (you post it yourself, in your own words).
"""
import hashlib, json, os, pathlib, subprocess, sys, time, urllib.parse, urllib.request
import frontmatter, yaml
os.environ.setdefault("REPO", "Abhimanyu9988/Abhimanyu9988.github.io")
from radar import safe_text          # the report is a GitHub issue: same link/mention safety as the bot

def safe_code(text):
    """For text inside a ```text fence: same link/mention neutralizing, but keep < and > so it copies cleanly
    (GitHub doesn't render HTML inside fences)."""
    return safe_text(text, "[left out: failed the link-safety check]").replace("\u2039", "<").replace("\u203a", ">")

SITE = yaml.safe_load(open("site/_config.yml"))
BLOG = SITE["url"] + (SITE.get("baseurl") or "")
STATE = pathlib.Path("state/publish-state.json")
SHARE = pathlib.Path("share.json")

def changed_posts():
    """Read changed post paths from git (NUL-delimited) so filenames never reach a shell."""
    before, after = os.environ.get("BEFORE_SHA", ""), os.environ["GITHUB_SHA"]
    spec = ["--", "site/_posts/*.md"]
    if not before or set(before) == {"0"}:
        cmd = ["git", "ls-files", "-z", *spec]
    else:
        cmd = ["git", "diff", "-z", "--name-only", "--diff-filter=AM", before, after, *spec]
    out = subprocess.run(cmd, check=True, capture_output=True).stdout.decode()
    return [p for p in out.split("\0") if p]

def slug_of(path, meta):
    return meta.get("slug") or pathlib.Path(path).stem.split("-", 3)[3]

def canonical_url(path, meta):
    if SITE.get("permalink") != "/posts/:slug/":
        sys.exit("permalink changed in _config.yml: update canonical_url()")
    return f"{BLOG}/posts/{slug_of(path, meta)}/"

def wait_live(url, tries=10):
    for _ in range(tries):
        try:
            with urllib.request.urlopen(url) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(30)
    raise RuntimeError(f"canonical page not live: {url}")

def call(method, url, payload, headers):
    req = urllib.request.Request(url, json.dumps(payload).encode() if payload is not None else None, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          "User-Agent": "hero-pipeline/1.0", **headers})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

def devto(meta, body, canonical, prev_id):
    art = {"article": {"title": meta["title"], "body_markdown": body, "published": True,
                       "tags": meta.get("tags", [])[:4], "canonical_url": canonical,
                       "description": (meta.get("description") or "")[:250]}}
    h = {"api-key": os.environ["DEVTO_API_KEY"]}
    r = (call("PUT", f"https://dev.to/api/articles/{prev_id}", art, h) if prev_id
         else call("POST", "https://dev.to/api/articles", art, h))
    return r["id"], r["url"]

def hashnode(meta, body, canonical, prev_id):
    h = {"Authorization": "Bearer " + os.environ["HASHNODE_TOKEN"]}
    f = {"title": meta["title"], "contentMarkdown": body, "originalArticleURL": canonical}
    if prev_id:
        q, key = "mutation($i: UpdatePostInput!){ updatePost(input:$i){ post{ id url } } }", "updatePost"
        i = {"id": prev_id, **f}
    else:
        q, key = "mutation($i: PublishPostInput!){ publishPost(input:$i){ post{ id url } } }", "publishPost"
        i = {**f, "publicationId": os.environ["HASHNODE_PUB_ID"],
             "tags": [{"slug": t, "name": t} for t in meta.get("tags", [])[:5]]}
    api = os.environ.get("HASHNODE_API_URL") or "https://gql-beta.hashnode.com/"
    r = call("POST", api, {"query": q, "variables": {"i": i}}, h)
    if r.get("errors"):
        raise RuntimeError(r["errors"])
    p = r["data"][key]["post"]
    return p["id"], p["url"]

def medium(meta, body, canonical, prev_id):
    """A Medium DRAFT from the markdown (Medium's API can't edit posts, so this runs once per post)."""
    if prev_id:
        return prev_id, None
    h = {"Authorization": "Bearer " + os.environ["MEDIUM_TOKEN"]}
    me = call("GET", "https://api.medium.com/v1/me", None, h)["data"]["id"]
    content = (f"# {meta['title']}\n\n*{meta.get('description', '')}*\n\n{body}\n\n"
               f"*Originally published at [{canonical}]({canonical}).*")
    r = call("POST", f"https://api.medium.com/v1/users/{me}/posts",
             {"title": meta["title"], "contentFormat": "markdown", "content": content, "canonicalUrl": canonical,
              "tags": meta.get("tags", [])[:3], "publishStatus": "draft"}, h)["data"]
    return r["id"], r["url"]

CHANNELS = {"devto": devto, "hashnode": hashnode, "medium": medium}
NEEDS = {"devto": "DEVTO_API_KEY", "hashnode": "HASHNODE_TOKEN", "medium": "MEDIUM_TOKEN"}

def share_block(post, canonical, done, slug):
    """What you do by hand: Medium (if no token), LinkedIn, and replies on the source threads."""
    lines = [f"\n### {safe_text(post.get('title'))}", f"- Your site (the original): {canonical}"]
    if done.get("devto", {}).get("url"):
        lines.append(f"- dev.to (live): {done['devto']['url']}")
    if done.get("hashnode", {}).get("url"):
        lines.append(f"- Hashnode (live): {done['hashnode']['url']}")
    if done.get("medium", {}).get("url"):
        lines.append(f"- **Medium:** your draft is ready, with code blocks and your site as the original: "
                     f"{done['medium']['url']}. Open it, check it, press Publish.")
    elif not post.get("canonical_url"):
        lines.append("- **Medium:** open https://medium.com/p/import and paste your site link above (Medium keeps "
                     "your site as the original). Save a MEDIUM_TOKEN to get a ready draft instead.")
    li = str(post.get("linkedin") or "").replace("{POST_URL}", canonical).strip()
    if li:
        share = "https://www.linkedin.com/feed/?shareActive=true&text=" + urllib.parse.quote(li, safe="")
        lines += [f"- **LinkedIn:** [open the LinkedIn post box with this text filled in]({share}), check it, press "
                  "Post. If the hero bot is installed on your profile repo, a `linkedin-draft` issue also opens there "
                  "and `/post` publishes it for you.",
                  "", "<details><summary>LinkedIn text</summary>", "", "```text", safe_code(li), "```", "", "</details>"]
    for r in post.get("replies") or []:
        text = str(r.get("text", "")).replace("{POST_URL}", canonical)
        where = str(r.get("url", ""))
        so = "stackoverflow.com" in where
        lines += ["", f"**Reply for the source thread:** {safe_text(where, 'the source thread')}",
                  ("_Stack Overflow doesn't allow AI-written answers: rewrite this in your own words before you "
                   "post it._" if so else "_Post it yourself if it helps the thread; the bot never does._"),
                  "", "```text", safe_code(text), "```"]
    lines += ["", "After it's been up a few days, record numbers by commenting here, for example:",
              "`/stats linkedin impressions=1200 reactions=40 comments=6 reposts=2` or "
              "`/stats medium views=300 reads=120 claps=50`", f"<!-- post: {slug} -->"]
    return lines

def main(paths):
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    report, failed, share = [], False, []
    for path in paths:
        post = frontmatter.load(path)
        canonical = canonical_url(path, post.metadata)
        try:
            wait_live(canonical)
        except RuntimeError as e:
            report.append(f"- {path}: SKIPPED, {e}"); failed = True; continue
        digest = hashlib.sha256(frontmatter.dumps(post).encode()).hexdigest()
        done = state.setdefault(canonical, {})
        for ch in list(post.get("channels", [])) + ["medium"]:
            prev = done.get(ch, {})
            if prev.get("hash") == digest or (ch == "medium" and prev.get("id")):
                continue
            if ch not in CHANNELS:
                report.append(f"- {ch}: not automated yet"); continue
            if not os.environ.get(NEEDS[ch]):
                if ch != "medium":
                    report.append(f"- {ch}: skipped (no {NEEDS[ch]} saved)")
                continue
            try:
                pid, url = CHANNELS[ch](post.metadata, post.content, canonical, prev.get("id"))
                done[ch] = {"id": pid, "url": url or prev.get("url"), "hash": digest}
                report.append(f"- {ch}: {url}")
            except Exception as e:
                report.append(f"- {ch}: FAILED {safe_text(str(e))[:300]}"); failed = True
        slug = slug_of(path, post.metadata)
        report += share_block(post, canonical, done, slug)
        if post.get("linkedin") and not done.get("linkedin_draft"):
            share.append({"slug": slug, "title": post.get("title"), "canonical": canonical,
                          "linkedin": str(post["linkedin"]).replace("{POST_URL}", canonical)})
            done["linkedin_draft"] = True
        STATE.write_text(json.dumps(state, indent=2))
    SHARE.write_text(json.dumps(share))
    pathlib.Path("publish-report.md").write_text("\n".join(report) or "Nothing to publish.")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main(sys.argv[1:] or changed_posts())
