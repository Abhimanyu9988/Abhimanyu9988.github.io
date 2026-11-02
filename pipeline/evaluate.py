#!/usr/bin/env python3
"""Shadow-mode evaluation of the gates against a saved, labelled set. Touches no AWS and publishes nothing.

  python3 pipeline/evaluate.py           offline: runs the code checks on tests/eval/cases.json
  python3 pipeline/evaluate.py --live    also asks the Editor for briefs (needs ANTHROPIC_API_KEY; costs a few dollars)

Two different things live here, and they must never be merged:
  regression checks    synthetic, evidence-derived or provisional labels. They catch regressions in the gates.
  human calibration    Abhi's ratings in tests/eval/human-ratings.json. Empty until he rates, and too small to show
                       that editorial quality improved even then.
"""
import json, pathlib, re, sys
import editor, scan

ROOT = pathlib.Path(__file__).parent.parent
EVAL = ROOT / "tests" / "eval"
TARGETS = {"headline_detection": 1.0, "trivial_rejection": 0.8, "strong_false_rejection": 0.1, "evidence_bypass": 0}

def logs_for(case):
    if case.get("logs_dir"):
        return scan.read_logs(ROOT / case["logs_dir"])
    return {"case.log": "\n".join(case.get("log_lines") or [])}

def rate(hits, total):
    return round(hits / total, 3) if total else None

def run(cases, live=False):
    bad_titles = caught = strong = strong_rejected = trivial = trivial_rejected = evidence = bypassed = 0
    misses = []
    for c in cases:
        if c["type"] == "headline":
            report = scan.check(logs_for(c), c["title"], c.get("contract"))
            flagged = any(not h["supported"] for h in report["headline"])
            if c["unsupported"]:
                bad_titles += 1; caught += flagged
            if flagged != c["unsupported"]:
                misses.append(c["id"])
        elif c["type"] == "topic":
            if live:
                import roles
                roles._calls.clear()              # the budget is per topic here, not per run
            b = editor.brief(c["title"], c["details"]) if live else c["brief"]
            passed = editor.gate1(b)[0]
            if c["label"] == "trivial":
                trivial += 1; trivial_rejected += not passed
            else:
                strong += 1; strong_rejected += not passed
            if passed != (c["label"] == "strong"):
                misses.append(c["id"])
        elif c["type"] == "evidence":
            evidence += 1
            result = scan.verify_contract(c["contract"], logs_for(c))
            if result["passed"] != c["should_pass"]:
                bypassed += 1; misses.append(c["id"])
    return {"headline_detection": rate(caught, bad_titles), "headline_cases": bad_titles,
            "trivial_rejection": rate(trivial_rejected, trivial), "trivial_cases": trivial,
            "strong_false_rejection": rate(strong_rejected, strong), "strong_cases": strong,
            "evidence_bypass": bypassed, "evidence_cases": evidence, "misses": misses}

def human_comparison():
    """Code checks against Abhi's ratings, piece by piece. A missing rating stays None and is never guessed."""
    data, rows = json.loads((EVAL / "human-ratings.json").read_text()), []
    for piece, rating in data["ratings"].items():
        post = next(iter((ROOT / "site" / "_posts").glob(f"*{piece}.md")), None)
        folder = ROOT / "labs" / piece / "evidence"
        if post:
            title = (re.search(r'^title:\s*"?(.*?)"?\s*$', post.read_text(), re.M) or [None, piece])[1]
        else:
            folder, title = ROOT / "tests" / "fixtures" / "pr88", data["unpublished_titles"].get(piece, piece)
        if not folder.exists():
            continue                              # the monthly post has no lab to check
        passed = scan.check(scan.read_logs(folder), title)["passed"]
        rows.append({"piece": piece, "rating": rating, "checks_passed": passed,
                     "agrees": None if rating is None else passed == (rating >= 4)})
    return rows

def evidence_basis(cases):
    """How many cases each kind of label rests on, so nobody mistakes these for human judgement."""
    out = {}
    for c in cases:
        key = c.get("labelled_by", "unknown")
        out[key] = out.get(key, 0) + 1
    return out

def human_calibration(rows):
    rated = [x for x in rows if x["rating"] is not None]
    return {"rated": len(rated), "of": len(rows), "calibrated": False if not rated else None,
            "agreement": rate(sum(1 for x in rated if x["agrees"]), len(rated)),
            "note": "Agreement on a handful of pieces is a sanity check. It is not evidence that quality improved."}

def meets_targets(r):
    return (r["headline_detection"] == TARGETS["headline_detection"]
            and (r["trivial_rejection"] or 0) >= TARGETS["trivial_rejection"]
            and (r["strong_false_rejection"] or 0) < TARGETS["strong_false_rejection"]
            and r["evidence_bypass"] == TARGETS["evidence_bypass"]
            and not r["misses"])

def cost_per_worthwhile(total_usd, ratings, worthwhile_from=4):
    """All spend, including rejected topics and failed labs, divided by accepted articles rated worthwhile.
    Model fees per article hide what weak topics cost; this does not."""
    good = sum(1 for r in ratings if isinstance(r, (int, float)) and r >= worthwhile_from)
    return round(total_usd / good, 2) if good else None

def main():
    cases = json.loads((EVAL / "cases.json").read_text())
    r = run(cases, "--live" in sys.argv)
    rows = human_comparison()
    r["human_comparison"], r["human_calibration"], r["label_basis"] = rows, human_calibration(rows), evidence_basis(cases)
    print(json.dumps(r, indent=1))
    ok = meets_targets(r)
    print("Regression checks pass." if ok else "Regression checks fail.")
    print("These are mostly synthetic regression checks. They do not show that editorial quality improved.")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
