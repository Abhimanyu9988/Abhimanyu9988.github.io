#!/usr/bin/env python3
"""Live validation with real credentials. Runs in GitHub Actions and reports on a pull request.

Checks, in order: the pinned model IDs exist, the cheap models answer, a real Editor brief, a real Editor scorecard on
the PR 88 draft, and a real Tech Head review of this pull request. Every step records what actually happened, so a step
that did not run is reported as not run. It posts one comment and changes nothing else: no publishing, no lab, no AWS.
"""
import json, os, pathlib, subprocess, time
import editor, llm, roles, scan, tech_head
from radar import comment, safe_text

ROOT = pathlib.Path(__file__).parent.parent
MAX_USD = 3.00
TOPIC = ("Watch ECS Fargate task CPU and memory in Splunk with an OTel sidecar",
         "You get per-task CPU, memory and network metrics for a Fargate service without installing anything on hosts.")
rows, details = [], {}

def step(name, fn):
    """Run one step, measuring what it really used. A step past the budget is skipped and says so."""
    spent = llm.cost_usd()[0]
    if spent >= MAX_USD:
        rows.append({"step": name, "result": f"not run: budget of ${MAX_USD:.2f} reached", "seconds": 0, "in": 0, "out": 0, "usd": 0, "note": ""})
        return None
    start, mark = time.time(), len(llm.LEDGER)
    try:
        value, note, result = fn(), "", "ok"
        if isinstance(value, tuple):
            value, note = value
    except Exception as e:
        value, note, result = None, f"{type(e).__name__}: {' '.join(str(e).split())[:300]}", "failed"
    used = llm.LEDGER[mark:]
    rows.append({"step": name, "result": result, "seconds": round(time.time() - start, 1),
                 "in": sum(e["in"] for e in used), "out": sum(e["out"] for e in used),
                 "usd": llm.cost_usd(used)[0], "note": note, "models": sorted({e["model"] for e in used})})
    return value

def listed():
    """Which pinned IDs each provider actually lists for these keys."""
    wanted = set(llm.PRICES["usd_per_million"]) | {r["model"] for r in roles.registry().values() if r.get("model")}
    have = {m["id"] for m in llm.call("GET", "https://api.anthropic.com/v1/models?limit=100", headers=llm._anthropic_headers())["data"]}
    have |= {m["id"] for m in llm.call("GET", "https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {llm._key('OPENAI_API_KEY')}"})["data"]}
    gem = llm.call("GET", "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000", headers={"x-goog-api-key": llm._key("GEMINI_API_KEY")})
    have |= {m["name"].split("/")[-1] for m in gem.get("models", [])}
    missing = sorted(wanted - have)
    details["models"] = {"checked": sorted(wanted), "missing": missing}
    if missing:
        raise RuntimeError(f"not listed for these keys: {', '.join(missing)}")
    return True, f"all {len(wanted)} listed"

PING = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}

def ping(who, model):
    got = llm.ask(who, "Answer with the requested JSON.", "Set ok to true.", PING, model=model)
    return got, "answered"

def brief():
    b = editor.brief(*TOPIC)
    passed, why = editor.gate1(b)
    details["brief"] = {"gate1_passed": passed, "why": why, "verdict": b.get("verdict"), "search_ran": b.get("search_ran"),
                        "pages_kept": len(b.get("existing_pages") or []), "open_question": b.get("open_question"),
                        "measurements": [m.get("id") for m in b.get("measurements") or []]}
    return b, f"gate 1 {'pass' if passed else 'fail'}; search ran: {b.get('search_ran')}; pages kept: {len(b.get('existing_pages') or [])}"

def score(b):
    text = (ROOT / "tests/fixtures/pr88/post.md").read_text()
    title = text.split('title: "', 1)[1].split('"', 1)[0]
    logs = scan.read_logs(ROOT / "tests/fixtures/pr88")
    report = scan.check(logs, title)
    card = editor.score({"title": title, "body": text.split("---", 2)[2]}, f"EVIDENCE:\n{json.dumps(logs)[:12000]}", report, b)
    verdict, reasons = editor.decide(card, report, require_card=True)
    s = card["scores"]
    details["scorecard"] = {"scores": {k: s[k]["score"] for k in s}, "blocking": [x["problem"] for x in card["blocking"]][:4], "decision": verdict}
    return card, f"technical {s['technical']['score']}, editorial {s['editorial']['score']}, marketing {s['marketing']['score']}; decision {verdict}"

def review():
    diff = subprocess.run(["git", "diff", "--no-color", "--text", f"{os.environ['BASE_SHA']}...{os.environ['HEAD_SHA']}"],
                          capture_output=True, text=True, check=True).stdout
    found = tech_head.rules(diff)
    ai = tech_head.review(diff, {}, roles.data("tech_head").get("agents_md", ""))
    findings, dropped, blocked = tech_head.verdict(found, ai, diff)
    details["tech_head"] = {"diff_chars": len(diff), "cut_at": tech_head.MAX_DIFF, "dropped": dropped, "summary": ai.get("summary"),
                            "findings": [{k: f.get(k) for k in ("severity", "category", "file", "line", "problem", "fix", "rule")} for f in findings[:15]]}
    return ai, f"{len(findings)} finding(s), {dropped} dropped; reviewed {min(len(diff), tech_head.MAX_DIFF):,} of {len(diff):,} characters"

def report():
    lines = ["**Live validation, real API calls.** Tokens are as reported by each API. Costs use the list prices in `pipeline/prices.json`.", "",
             "| Step | Result | Seconds | Tokens in | Tokens out | Cost | Models | Note |", "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |"]
    for r in rows:
        note = str(r["note"]).replace("|", "/")
        lines.append(f"| {r['step']} | {r['result']} | {r['seconds']} | {r['in']:,} | {r['out']:,} | ${r['usd']:.3f} | {', '.join(r.get('models', []))} | {note} |")
    usd, unknown = llm.cost_usd()
    lines += ["", f"**Total:** ${usd:.3f}" + (f", excluding unpriced {', '.join(unknown)}" if unknown else ""), "",
              "<details><summary>Details</summary>", "", "```json", json.dumps(details, indent=1)[:9000], "```", "", "</details>"]
    return "\n".join(lines)

def main():
    step("Model IDs listed", listed)
    pins = llm.PRICES["defaults"]
    step("Ping Haiku pin", lambda: ping("claude", pins["claude_scout"]))
    step("Ping Sonnet pin", lambda: ping("claude", pins["claude_write"]))
    step("Ping OpenAI pin", lambda: ping("chatgpt", pins["openai"]))
    step("Ping Gemini pin", lambda: ping("gemini", pins["gemini"]))
    b = step("Editor brief, with web search", brief)
    step("Editor scorecard on PR 88", lambda: score(b or {}))
    step("Tech Head review of this PR", review)
    comment(int(os.environ["PR_NUMBER"]), safe_text(report()))

if __name__ == "__main__":
    main()
