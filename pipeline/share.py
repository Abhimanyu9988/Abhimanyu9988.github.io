#!/usr/bin/env python3
"""Hand each newly published post's LinkedIn text to the posting panel in your profile repo.

It opens a `linkedin-draft` issue in Abhimanyu9988/Abhimanyu9988 in the exact format that repo's
social panel reads, so commenting `/post` there publishes it to LinkedIn (and `/redraft`, pictures and
token renewal all work as they already do). Needs the hero bot app installed on that repo too.
"""
import json, os, pathlib, re, urllib.parse
from radar import PROFILE_REPO, gh, safe_text, assert_safe

START, END = "<!-- POST:START -->", "<!-- POST:END -->"

def compose_url(text):
    return "https://www.linkedin.com/feed/?shareActive=true&text=" + urllib.parse.quote(text)

def body_for(item):
    text = safe_text(re.sub(r"@(?=\w)", "", item["linkedin"]), "")       # no pings; tag people on LinkedIn
    meta = {"kind": "blog", "id": f"hero-{item['slug']}", "title": item["title"], "ref": item["canonical"], "short": ""}
    ticks = "````" if "```" in text else "```"
    return "\n".join([
        f"<!-- social:meta {json.dumps(meta)} -->",
        "**AI panel:** written with the blog post (ChatGPT wrote it, Claude checked it against the lab, "
        "Gemini settles disputes), and you reviewed it in the draft PR.",
        "\n**To publish:** edit the text in the box if you like, then comment `/post`. It goes out right away "
        "as a public post from your own profile.\n**Picture (optional):** attach one in a comment first; `/post` "
        "includes the latest one you attached.\nOther commands: `/redraft make it warmer` · `/post noimage` · "
        "close this issue to drop it.\n",
        START, f"{ticks}text\n{text}\n{ticks}", END,
        "<!-- OPEN:START -->\n**Want to tag people?** [Open this draft in LinkedIn]"
        f"({compose_url(text)}), type @ before the names, and post it there. Then comment `/posted <link to the "
        "post>` here so the panel knows. If you edit the box above first, comment `/open` for a fresh link.\n"
        "<!-- OPEN:END -->",
        "", f"Blog post: {item['canonical']}"])

def main():
    items = json.loads(pathlib.Path("share.json").read_text()) if pathlib.Path("share.json").exists() else []
    for item in items:
        title = safe_text(f"LinkedIn draft: {item['title']}", "LinkedIn draft: new blog post")[:240]
        body = body_for(item)
        assert_safe(title); assert_safe(body)
        try:
            iss = gh("POST", f"/repos/{PROFILE_REPO}/issues", {"title": title, "body": body, "labels": ["linkedin-draft"]})
            print(f"LinkedIn draft opened in {PROFILE_REPO}: issue {iss['number']}")
        except RuntimeError as e:
            print(f"::warning title=LinkedIn draft::could not open it in {PROFILE_REPO} ({str(e)[:300]}). Is the "
                  "hero bot app installed on that repo? The publish report still has the LinkedIn link.")

if __name__ == "__main__":
    main()
