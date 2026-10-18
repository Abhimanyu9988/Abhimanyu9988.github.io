#!/usr/bin/env python3
"""Build a Medium-ready copy of every post at site/medium/<slug>/index.html.

Medium's importer drops the theme's highlighted code blocks (nested divs and spans), so it lost every command
in the first post. These pages use only plain HTML that Medium understands: h2, p, ol/ul, a, and
<pre><code> for code. They point to the real post as canonical and ask search engines not to index them.
Import https://abhimanyu9988.github.io/medium/<slug>/ into Medium, or open it, select all, copy and paste
into a new Medium story.
"""
import html, json, pathlib, re
import markdown

BLOG = "https://abhimanyu9988.github.io"

def build(posts_dir="site/_posts", out_dir="site/medium"):
    made = []
    for p in sorted(pathlib.Path(posts_dir).glob("*.md")):
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", p.read_text(), re.S)
        if not m:
            continue
        fm, body = m.groups()
        def field(k):
            v = (re.search(rf"^{k}:\s*(.*)$", fm, re.M) or [None, ""])[1].strip()
            try:
                return json.loads(v) if v.startswith('"') else v
            except ValueError:
                return v.strip('"')
        slug = field("slug") or p.stem.split("-", 3)[3]
        url = f"{BLOG}/posts/{slug}/"
        content = markdown.markdown(body, extensions=["fenced_code", "tables", "sane_lists"], output_format="html")
        content = re.sub(r'<code class="language-[^"]*">', "<code>", content)       # Medium ignores languages anyway
        # Steps written as "1. Do X" followed by a code block become one-item lists that Medium would all number "1.";
        # turn each into a "Step N" heading instead.
        n = iter(range(1, 1000))
        content = re.sub(r"<ol(?: start=\"\d+\")?>\s*<li>((?:(?!</li>).){1,200}?)</li>\s*</ol>",
                         lambda m: f"<h3>Step {next(n)}: {m.group(1).rstrip('.')}</h3>", content, flags=re.S)
        title, desc = html.escape(field("title")), html.escape(field("description"))
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{title}</title>
<meta name="robots" content="noindex">
<link rel="canonical" href="{url}">
<meta name="description" content="{desc}">
</head><body><article>
<h1>{title}</h1>
<p><em>{desc}</em></p>
{content}
<hr>
<p><em>Originally published at <a href="{url}">{url}</a>.</em></p>
</article></body></html>
"""
        out = pathlib.Path(out_dir) / slug / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page)
        made.append(slug)
    print(f"Medium-ready pages: {made}")
    return made

if __name__ == "__main__":
    build()
