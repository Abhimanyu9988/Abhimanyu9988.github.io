#!/usr/bin/env python3
"""Editorial Intelligence: the Topic Investment Gate, the Evidence Contract and the Editor's review.

The rule behind this file: an agent may propose a claim, an experiment must demonstrate it, and the Editor explains
why the demonstrated result matters to the reader.

  brief(title, details)       Editor writes the reader brief and Evidence Contract before lab money is spent.
  gate1(brief)                code decides whether the topic deserves a lab.
  score(post, context, ...)   Editor scores a draft. Runs only when EDITOR_REVIEW=on.
  decide(card, report, urls)  code turns scores and evidence into pass, revise or stop. A model never decides this.

EDITOR_MODE controls what the gates may affect:
  off      nothing runs.
  shadow   (default) reports only. Labs, drafts and publishing behave exactly as before.
  warn     briefs and contracts reach the lab and writer; problems show at the top of the PR; you can still approve.
  enforce  failed gates stop the topic or draft, with one revision allowed.
"""
import base64, json, os, re

MODES = ("off", "shadow", "warn", "enforce")
MARK = re.compile(r"<!-- hero-brief:v1 ([A-Za-z0-9+/=]+) -->")
ID = re.compile(r"^[a-z0-9_]{1,40}$")
TRIVIAL = re.compile(r"(?i)^\s*(does|do|can|will|is)\b.{0,80}\b(work|works|possible|run|runs|succeed)\??\s*$")
MIN_TECHNICAL, MIN_EDITORIAL = 4, 3
REQUIRED = ("reader", "decision", "takeaway", "adds", "open_question")

def mode():
    v = os.environ.get("EDITOR_MODE", "").strip().lower()
    return v if v in MODES else "shadow"

def active():
    """True when briefs and contracts may influence labs and writers."""
    return mode() in ("warn", "enforce")

def review_on():
    """Enforce always needs the Editor. EDITOR_REVIEW only decides whether it also runs in shadow or warn."""
    return mode() == "enforce" or os.environ.get("EDITOR_REVIEW", "").strip().lower() == "on"

# ---------- Gate 1: Topic Investment Gate and Evidence Contract ----------
def brief(title, details):
    """The Editor's brief for one topic. Raises if the role fails. Page URLs must come from real searches."""
    import llm, roles
    data = roles.data("editor")
    found = llm.web_research(f"Find the best existing pages an engineer would read about: {title}")
    pages = found.get("pages") or []
    prompt = (f"Write the reader brief and Evidence Contract for this proposed post.\n\nTOPIC: {title}\n{details}\n\n"
              f"GOALS:\n{json.dumps(data.get('goals'))[:3000]}\n\nFIELD NOTES:\n{data.get('field_notes')}\n\n"
              f"PAGES FOUND BY SEARCH (use only these URLs in existing_pages):\n{json.dumps(pages)[:6000]}\n\n"
              f"SEARCH SUMMARY:\n{str(found.get('summary'))[:3000]}")
    got = roles.ask("editor", prompt, "brief")
    allowed = {p.get("url") for p in pages}
    got["existing_pages"] = [p for p in got.get("existing_pages") or [] if p.get("url") in allowed]
    got["search_ran"] = bool(pages)
    got["prompt_version"] = roles.prompt_version("editor")
    return got

def gate1(b):
    """(passed, reasons), computed by code from the brief."""
    bad = [f"missing {k}" for k in REQUIRED if not str(b.get(k) or "").strip()]
    if b.get("verdict") != "go":
        bad.append("the Editor voted no: " + "; ".join(map(str, b.get("reasons") or []))[:300])
    if TRIVIAL.match(str(b.get("open_question") or "")):
        bad.append("the open question only asks whether it works")
    if b.get("splunk_fit") == "forced":
        bad.append("Splunk would be forced into this topic")
    ms = b.get("measurements") or []
    if not ms:
        bad.append("no Evidence Contract")
    elif all(m.get("kind") == "count" for m in ms):
        bad.append("the contract only counts things, so it can prove existence but not value")
    bad += [f"bad measurement id {m.get('id')!r}" for m in ms if not ID.match(str(m.get("id") or ""))]
    return not bad, bad

def marker(b):
    return f"<!-- hero-brief:v1 {base64.b64encode(json.dumps(b).encode()).decode()} -->"

def parse_brief(text):
    m = MARK.search(text or "")
    if not m:
        return None
    try:
        return json.loads(base64.b64decode(m[1]).decode())
    except (ValueError, UnicodeDecodeError):
        return None

def brief_md(b, passed=True, reasons=()):
    lines = [f"**Reader:** {b.get('reader')}", f"**Their decision:** {b.get('decision')}",
             f"**Open question:** {b.get('open_question')}", f"**Intended takeaway:** {b.get('takeaway')}",
             f"**What this adds:** {b.get('adds')}", f"**Demand evidence:** {(b.get('demand') or {}).get('level')}"]
    if b.get("tradeoffs"):
        lines.append("**Trade-offs to weigh:** " + "; ".join(map(str, b["tradeoffs"])))
    lines.append("**Evidence Contract:**")
    for m in b.get("measurements") or []:
        a = m.get("accept") or {}
        rule = "" if a.get("op", "present") == "present" else f", must be {a.get('op')} {a.get('value')}"
        lines.append(f"- `{m.get('id')}`: {m.get('what')} ({m.get('unit')}; {m.get('kind')}; "
                     f"{m.get('min_entities', 1)} value(s) needed{rule})")
    lines.append(f"**Topic Investment Gate:** {'pass' if passed else 'fail: ' + '; '.join(map(str, reasons))}")
    return "\n\n".join(lines)

LAB_RULES = """THIS TOPIC HAS AN EVIDENCE CONTRACT (in the topic body). It was fixed before this lab was written.
- Measure every item and print one line per value: `HERO_MEASURE: <id>=<value>`, or, when the contract needs several
  entities, `HERO_MEASURE: <id>[<entity>]=<value>`, one line per task, pod or service.
- Print the real result even when it is unexpected or unflattering. Print the line with value null if you could not
  measure it; never estimate.
- Directly above each HERO_MEASURE line, print the unedited command output the value came from, including the id or
  entity name. Never type a measured value into a script.
- The before-check must be able to fail. A zero from a brand-new lab name proves nothing."""

WRITER_RULES = """READER BRIEF AND CLAIM RULES (these override the section structure above; sections are roles you may
merge or reorder):
- Write for the READER in the brief and lead with what the experiment found. The title comes from the result.
- Every lab result, command, output and number must come from LAB or EVIDENCE. Numbers in the title must measure what
  the title says they measure.
- Facts from outside the lab are welcome when they help the reader decide. Each needs an inline link to a page in the
  brief or sources, and must read as outside information, not as your result. Mark estimates as estimates.
- Weigh at least one trade-off or alternative from the brief.
- Address every item in EVIDENCE FLAGS. Do not hide a failed or missing measurement; state it plainly.
- FIELD NOTES are true, first-hand patterns you may use. Nothing else about Abhi's work may be invented."""

def lab_refusal(body):
    """In enforce, a topic without a passing brief may not spend money, however its lab was started."""
    if mode() != "enforce":
        return ""
    b = parse_brief(body)
    if not b or not (b.get("gate1") or {}).get("passed"):
        return "this topic has no passing Topic Investment Gate brief, and EDITOR_MODE is enforce"
    return ""

def lab_rules(body):
    return "\n\n" + LAB_RULES if active() and parse_brief(body) else ""

def writer_context(b, report):
    if not active():
        return ""
    import roles, scan
    return (f"\n\n{WRITER_RULES}\n\nREADER BRIEF:\n{json.dumps(b or {})[:5000]}\n\nFIELD NOTES:\n{roles.data('writer').get('field_notes')}"
            f"\n\nEVIDENCE FLAGS:\n{scan.markdown(report)}")

# ---------- Gate 2: the Editor's review ----------
def score(post, context, report, b):
    """A scorecard for a finished draft. The prompt carries the evidence report so the Editor must answer each flag."""
    import roles
    prompt = (f"Score this draft.\n\nBRIEF:\n{json.dumps(b or {})[:6000]}\n\nEVIDENCE REPORT:\n{json.dumps(report)[:6000]}"
              f"\n\nTITLE: {post.get('title')}\n\nPOST:\n{str(post.get('body'))[:30000]}\n\n{context[:60000]}")
    card = roles.ask("editor", prompt, "scorecard")
    card["prompt_version"] = roles.prompt_version("editor")
    return card

def decide(card, report, known_urls=(), require_card=False):
    """pass or revise, with reasons, computed by code. Dimensions never average. High flags need the Editor's answer
    when it runs; they stay visible either way, because logs themselves cannot be revised."""
    out = ["no successful Editor scorecard exists for this draft"] if require_card and not card else []
    if report and not report.get("passed", True):
        out.append("the evidence checks did not pass: " + "; ".join(report.get("failures") or [])[:400])
    if card:
        s = card["scores"]
        if s["technical"]["score"] < MIN_TECHNICAL:
            out.append(f"technical score {s['technical']['score']} is below {MIN_TECHNICAL}")
        if s["editorial"]["score"] < MIN_EDITORIAL:
            out.append(f"editorial score {s['editorial']['score']} is below {MIN_EDITORIAL}")
        out += [f"{b['dimension']}: {b['problem']} Fix: {b['fix']}" for b in card.get("blocking") or []]
        done = {f["id"] for f in card.get("flags_addressed") or [] if f.get("addressed")}
        out += [f"flag not addressed: {f['why']}" for f in (report or {}).get("flags", [])
                if f["severity"] == "high" and f["id"] not in done]
        out += [f"outside claim without a known source: {c['text']}" for c in card.get("claims") or []
                if c.get("class") == "sourced" and c.get("source_ref") not in set(known_urls)]
    return ("revise" if out else "pass"), out

def card_md(card):
    if not card:
        return ""
    s = card["scores"]
    return "\n".join(f"- **{k.title()}: {s[k]['score']} of 5.** " + "; ".join(map(str, s[k]["reasons"]))
                     for k in ("technical", "editorial", "marketing"))
