"""Publish merged posts: the site is deployed by the workflow; this cross-posts and hands out the rest.

  dev.to     published automatically (canonical = your site).
  Hashnode   published automatically once HASHNODE_TOKEN is saved.
  Medium     with MEDIUM_TOKEN: created as a Medium DRAFT (markdown, real code blocks, canonical = your site),
             ready for you to press Publish. Without it: the one-tap import link.
  LinkedIn   comment `/medium <story link>` (or `/linkedin`) on the report: share.py opens a `linkedin-draft`
             issue in your profile repo with the dev.to and Medium links and the cover image; `/post` there
             publishes it.
  Sources    a ready-to-post reply for each source thread (you post it yourself, in your own words).
"""
import hashlib, json, os, pathlib, re, subprocess, sys, time, urllib.request
import frontmatter, yaml
os.environ.setdefault("REPO", "Abhimanyu9988/Abhimanyu9988.github.io")
from radar import safe_text          # the report is a GitHub issue: same link/mention safety as the bot

SITE = yaml.safe_load(open("site/_config.yml"))
BLOG = SITE["url"] + (SITE.get("baseurl") or "")
STATE = pathlib.Path("state/publish-state.json")

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

def cover_url(meta):
    img = str(meta.get("image") or "")
    return BLOG + img if img.startswith("/") else None

def without_cover(body, meta):
    """dev.to shows the cover above the post, so drop the copy at the top of the body."""
    url = cover_url(meta)
    m = re.match(r"\s*!\[[^\]]*\]\(([^)\s]+)\)\s*\n", body)
    return body[m.end():] if url and m and m.group(1) == url else body

def devto(meta, body, canonical, prev_id):
    art = {"article": {"title": meta["title"], "body_markdown": without_cover(body, meta), "published": True,
                       "tags": meta.get("tags", [])[:4], "canonical_url": canonical,
                       "description": (meta.get("description") or "")[:250]}}
    if cover_url(meta):
        art["article"]["main_image"] = cover_url(meta)
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

def safe_code(text):
    """For text inside a ```text fence: link/mention neutralizing (links to Abhi's own repos kept), with < and >
    kept so it copies cleanly (GitHub doesn't render HTML inside fences)."""
    return (safe_text(text, "[left out: failed the link-safety check]", own_links=True)
            .replace("\u2039", "<").replace("\u203a", ">"))

def share_block(post, canonical, done, slug):
    """The "ready to post" email: everything for Medium and LinkedIn, ready to copy on a phone."""
    devto = done.get("devto", {}).get("url")
    lines = [f"## {safe_text(post.get('title'))}", "",
             f"**Live now:** [your site]({canonical})" + (f" · [dev.to]({devto})" if devto else ""), ""]
    med = done.get("medium", {})
    if med.get("url"):
        lines += [f"**Medium:** {'live' if med.get('manual') else 'draft ready'}: {med['url']}", ""]
    else:
        ready = f"{BLOG}/medium/{slug}/"
        lines += ["### 1. Medium (2 minutes)",
                  f"- **Fastest:** open https://medium.com/p/import, paste `{ready}` and tap Import.",
                  f"- **Or copy and paste:** open {ready}, select all, copy, and paste into a new Medium story "
                  "(code blocks stay code blocks). The full article is also at the bottom of this email.",
                  f"- **Before you publish:** story settings, Advanced settings, canonical link: `{canonical}`", ""]
    li = str(post.get("linkedin") or "").strip()
    if li:
        from share import linkedin_text
        text = linkedin_text(li, canonical, devto, med.get("url") if med.get("manual") else None)
        lines += ["### 2. LinkedIn (1 minute)",
                  "Copy the text, paste it into a new LinkedIn post, add the picture below (press and hold to save "
                  "it), and post.", "", "```text", safe_code(text), "```", ""]
        if post.get("image"):
            lines += [f"![picture for the LinkedIn post]({BLOG}{post['image']})", ""]
        lines += ["_Rather have it posted for you? Comment `/medium <your Medium story link>` (or `/linkedin`) here "
                  "and use the LinkedIn draft link it replies with._", ""]
    for r in post.get("replies") or []:
        text = str(r.get("text", "")).replace("{POST_URL}", devto or canonical)
        where = str(r.get("url", ""))
        so = "stackoverflow.com" in where
        lines += [f"### 3. Reply on the source thread ({safe_text(where, 'the source thread')})",
                  ("_Stack Overflow doesn't allow AI-written answers: rewrite this in your own words before you "
                   "post it._" if so else "_Post it yourself if it helps the thread; the bot never does._"),
                  "", "```text", safe_code(text), "```", ""]
    lines += ["**Later:** a few days after posting, reply with numbers, e.g. `/stats linkedin impressions=1200 "
              "reactions=40 comments=6` or `/stats medium views=300 reads=120 claps=50`.", ""]
    body = safe_code(getattr(post, "content", None) or post.get("content", ""))[:40000]
    if body.strip() and not med.get("url"):
        lines += ["<details><summary><b>Full article for Medium copy-paste</b></summary>", "",
                  f"# {safe_text(post.get('title'))}", "", body, "", "</details>"]
    lines += [f"<!-- post: {slug} -->", ""]
    return lines

def main(paths):
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    report, failed, titles = [], False, []
    for path in paths:
        post = frontmatter.load(path)
        canonical = canonical_url(path, post.metadata)
        try:
            wait_live(canonical)
        except RuntimeError as e:
            report.append(f"- {path}: SKIPPED, {e}"); failed = True; continue
        digest = hashlib.sha256(frontmatter.dumps(post).encode()).hexdigest()
        done = state.setdefault(canonical, {})
        if done.get("devto", {}).get("url"):       # already out before this run: an update, not a new post
            done.setdefault("announced", True)
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
        if not done.get("announced"):              # the "ready to post" email goes out once per post
            report += share_block(post, canonical, done, slug)
            titles.append(str(post.get("title") or slug))
            done["announced"] = True
        STATE.write_text(json.dumps(state, indent=2))
    pathlib.Path("publish-report.md").write_text("\n".join(report) if titles or failed else "")
    pathlib.Path("publish-title.txt").write_text(
        safe_text("Ready to post: " + "; ".join(titles), "Ready to post")[:200] if titles else "Publish problems")
    sys.exit(1 if failed else 0)

def preview(slug):
    """The ready-to-post email for a post that is already out, without publishing anything (to see the format)."""
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    path = next((p for p in sorted(pathlib.Path("site/_posts").glob("*.md")) if p.stem.split("-", 3)[3] == slug), None)
    if not path:
        sys.exit(f"no post called {slug}")
    post = frontmatter.load(path)
    canonical = canonical_url(str(path), post.metadata)
    lines = ["_Preview: this post is already out; nothing was published again._", ""]
    lines += share_block(post, canonical, state.get(canonical, {}), slug)
    pathlib.Path("publish-report.md").write_text("\n".join(lines))
    pathlib.Path("publish-title.txt").write_text(safe_text("Ready to post (preview): " + str(post.get("title")),
                                                           "Ready to post (preview)")[:200])

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--preview":
        preview(sys.argv[2])
    else:
        main(sys.argv[1:] or changed_posts())
