import hashlib, json, os, pathlib, subprocess, sys, time, urllib.request
import frontmatter, yaml

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

def canonical_url(path, meta):
    if SITE.get("permalink") != "/posts/:slug/":
        sys.exit("permalink changed in _config.yml: update canonical_url()")
    slug = meta.get("slug") or pathlib.Path(path).stem.split("-", 3)[3]
    return f"{BLOG}/posts/{slug}/"

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
    req = urllib.request.Request(url, json.dumps(payload).encode(), method=method,
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

def devto(meta, body, canonical, prev_id):
    art = {"article": {"title": meta["title"], "body_markdown": body, "published": True,
                       "tags": meta.get("tags", [])[:4], "canonical_url": canonical}}
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

CHANNELS = {"devto": devto, "hashnode": hashnode}

def main(paths):
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    report, failed = [], False
    for path in paths:
        post = frontmatter.load(path)
        canonical = canonical_url(path, post.metadata)
        try:
            wait_live(canonical)
        except RuntimeError as e:
            report.append(f"- {path}: SKIPPED, {e}"); failed = True; continue
        digest = hashlib.sha256(frontmatter.dumps(post).encode()).hexdigest()
        done = state.setdefault(canonical, {})
        for ch in post.get("channels", []):
            prev = done.get(ch, {})
            if prev.get("hash") == digest:
                continue
            if ch not in CHANNELS:
                report.append(f"- {ch}: not automated yet"); continue
            try:
                pid, url = CHANNELS[ch](post.metadata, post.content, canonical, prev.get("id"))
                done[ch] = {"id": pid, "url": url, "hash": digest}
                report.append(f"- {ch}: {url}")
            except Exception as e:
                report.append(f"- {ch}: FAILED {e}"); failed = True
        if not post.get("canonical_url"):
            report.append(f"- medium: import -> https://medium.com/p/import?url={canonical}")
        STATE.write_text(json.dumps(state, indent=2))
    pathlib.Path("publish-report.md").write_text("\n".join(report) or "Nothing to publish.")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main(sys.argv[1:] or changed_posts())
