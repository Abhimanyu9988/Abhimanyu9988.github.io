"""Open the LinkedIn draft for a published post in the profile repo, where `/post` publishes it.

Triggered by a comment from you on a publish report:
  /medium https://medium.com/@you/your-story   after importing to Medium: the draft gets dev.to AND Medium links
  /linkedin                                    skip Medium: the draft gets the dev.to link only
Add the post's slug first if a report covers more than one post: /medium <slug> <url>.

The draft is the panel's "problem I solved" post with the links filled in where the writer put {LINKS}, and the
post's cover image, which `/post` attaches to the LinkedIn post. The Medium link is also saved to publish-state.
"""
import json, os, pathlib, re, sys, urllib.parse
import frontmatter, yaml
from radar import PROFILE_REPO, HOME_REPO, gh, safe_text, assert_safe, comment

START, END = "<!-- POST:START -->", "<!-- POST:END -->"
STATE = pathlib.Path("state/publish-state.json")
MEDIUM_URL = re.compile(r"https://(?:[a-z0-9-]+\.)?medium\.com/[A-Za-z0-9@._~/-]+")

def compose_url(text):
    return "https://www.linkedin.com/feed/?shareActive=true&text=" + urllib.parse.quote(text)

def blog():
    site = yaml.safe_load(open("site/_config.yml"))
    return site["url"] + (site.get("baseurl") or "")

def linkedin_text(raw, canonical, devto=None, medium=None):
    """Fill in the links. {LINKS} is where the writer wants them; older posts used {POST_URL}."""
    links = [f"Read it on dev.to: {devto}" if devto else f"Read it here: {canonical}"]
    if medium:
        links.append(f"On Medium: {medium}")
    text = re.sub(r"@(?=\w)", "", str(raw)).replace("{POST_URL}", devto or canonical).strip()
    if "{LINKS}" in text:
        return text.replace("{LINKS}", "\n".join(links))
    if devto and devto in text:                      # an older post: the link is already in the text
        if not medium:
            return text
        i = text.index(devto) + len(devto)
        end = text.find("\n", i)
        end = len(text) if end < 0 else end
        return text[:end] + f"\nOn Medium: {medium}" + text[end:]
    tags = re.search(r"\n((?:#\w+\s*)+)$", text)     # put the links just above the hashtags
    if tags:
        return text[:tags.start()] + "\n\n" + "\n".join(links) + "\n\n" + tags.group(1).strip()
    return text + "\n\n" + "\n".join(links)

def body_for(item):
    text = safe_text(item["linkedin"], "")
    meta = {"kind": "blog", "id": f"hero-{item['slug']}", "title": item["title"], "ref": item["canonical"], "short": ""}
    ticks = "````" if "```" in text else "```"
    picture = (f"\n**Picture:** the post's cover image below goes out with it. To use a different one, attach it in "
               f"a comment; `/post noimage` posts without one.\n\n![{safe_text(item.get('alt') or 'cover image', 'cover image')}]"
               f"({item['image']})\n" if item.get("image") else
               "\n**Picture (optional):** attach one in a comment first; `/post` includes the latest one you attached.\n")
    return "\n".join([
        f"<!-- social:meta {json.dumps(meta)} -->",
        "**AI panel:** written with the blog post (ChatGPT wrote it, Claude checked it against the lab, "
        "Gemini settles disputes), and you reviewed it in the draft PR.",
        "\n**To publish:** edit the text in the box if you like, then comment `/post`. It goes out right away "
        "as a public post from your own profile.",
        picture,
        "Other commands: `/redraft make it warmer` · `/post noimage` · close this issue to drop it.\n",
        START, f"{ticks}text\n{text}\n{ticks}", END,
        "<!-- OPEN:START -->\n**Want to tag people?** [Open this draft in LinkedIn]"
        f"({compose_url(text)}), type @ before the names, and post it there. Then comment `/posted <link to the "
        "post>` here so the panel knows. If you edit the box above first, comment `/open` for a fresh link.\n"
        "<!-- OPEN:END -->",
        "", f"Blog post: {item['canonical']}"])

def open_draft(item):
    """Opens the issue in the profile repo. Returns its number, or the number of one already open for this post."""
    mark = f'"id": "hero-{item["slug"]}"'
    for iss in gh("GET", f"/repos/{PROFILE_REPO}/issues?labels=linkedin-draft&state=open&per_page=100") or []:
        if mark in (iss.get("body") or ""):
            return iss["number"], False
    title = safe_text(f"LinkedIn draft: {item['title']}", "LinkedIn draft: new blog post")[:240]
    body = body_for(item)
    assert_safe(title); assert_safe(body)
    iss = gh("POST", f"/repos/{PROFILE_REPO}/issues", {"title": title, "body": body, "labels": ["linkedin-draft"]})
    return iss["number"], True

def parse(cmd):
    """'/medium [slug] <url>' or '/linkedin [slug]' -> (slug or None, medium url or None). Raises ValueError."""
    words = cmd.strip().split()
    if not words or words[0] not in ("/medium", "/linkedin"):
        raise ValueError("start the comment with /medium or /linkedin")
    rest = words[1:]
    url = None
    if words[0] == "/medium":
        cand = [w for w in rest if w.startswith("http")]
        if len(cand) != 1:
            raise ValueError("add your Medium story's link: `/medium https://medium.com/...`")
        url = cand[0].split("?")[0].split("#")[0]
        if not MEDIUM_URL.fullmatch(url):
            raise ValueError("that doesn't look like a Medium story link (https://medium.com/...)")
        pid = re.search(r"(?:/p/|-)([0-9a-f]{10,16})/?$", url)
        if pid:                                      # short form: no @handle (it would read as a mention)
            url = f"https://medium.com/p/{pid.group(1)}"
        elif "@" in url:
            raise ValueError("use the story's own link (it ends in a code like -dc183f2d922f)")
        rest = [w for w in rest if w != cand[0]]
    slug = rest[0] if rest else None
    if slug and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,90}", slug):
        raise ValueError(f"{slug!r} isn't a post name")
    return slug, url

def find_post(slug):
    for p in sorted(pathlib.Path("site/_posts").glob("*.md")):
        post = frontmatter.load(p)
        s = post.get("slug") or p.stem.split("-", 3)[3]
        if s == slug:
            return post
    return None

def main():
    issue = int(os.environ["ISSUE"])
    try:
        slug, medium = parse(os.environ["COMMENT_BODY"].splitlines()[0])
        slugs = re.findall(r"<!-- post: ([a-z0-9-]+) -->", os.environ.get("ISSUE_BODY", ""))
        if not slugs:
            raise ValueError("this issue isn't a publish report")
        if slug and slug not in slugs:
            raise ValueError(f"this report has no post called {slug!r}; it has: {', '.join(slugs)}")
        if not slug and len(slugs) > 1:
            raise ValueError(f"this report has {len(slugs)} posts; add the name first, e.g. `/medium {slugs[0]} <link>`")
        slug = slug or slugs[0]
        post = find_post(slug)
        if not post or not post.get("linkedin"):
            raise ValueError(f"no LinkedIn text found for {slug}")
    except ValueError as e:
        comment(issue, f"Nothing opened: {safe_text(str(e))}.")
        return
    base = blog()
    canonical = f"{base}/posts/{slug}/"
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    done = state.setdefault(canonical, {})
    devto = (done.get("devto") or {}).get("url")
    if medium:
        done["medium"] = {**done.get("medium", {}), "url": medium, "manual": True}
        STATE.write_text(json.dumps(state, indent=2))
    item = {"slug": slug, "title": post.get("title"), "canonical": canonical,
            "linkedin": linkedin_text(post["linkedin"], canonical, devto, medium),
            "image": f"{base}{post['image']}" if str(post.get("image") or "").startswith("/") else None,
            "alt": post.get("image_alt")}
    try:
        number, new = open_draft(item)
    except RuntimeError as e:
        comment(issue, "Couldn't open the LinkedIn draft in your profile repo. Is the hero bot app installed there? "
                       f"({safe_text(str(e))[:200]})")
        sys.exit(1)
    links = "dev.to and Medium links" if medium and devto else ("Medium link" if medium else "dev.to link")
    url = f"https://github.com/{PROFILE_REPO}/issues/{int(number)}"
    if new:
        comment(issue, f"✅ Your LinkedIn draft is ready, with the {links}"
                       f"{' and the cover image' if item['image'] else ''}.\n\n**[Open your LinkedIn draft]({url})**, "
                       "check it, and comment `/post` there.", own_links=True)
    else:
        comment(issue, f"A LinkedIn draft for this post is already open: **[open it]({url})**. Close it and comment "
                       "here again if you want a fresh one with new links.", own_links=True)

if __name__ == "__main__":
    main()
