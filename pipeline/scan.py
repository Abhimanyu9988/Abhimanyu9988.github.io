#!/usr/bin/env python3
"""Evidence checks done by code, not by a model. No network and no LLM.

  verify_contract(contract, logs)   did the lab produce each measurement promised before it ran?
  headline_claims(title, logs)      is every number-and-noun claim in a title backed by one evidence line?
  scan(logs)                        anomalies a reader or reviewer should not miss: nulls, gaps, weak controls.
  check(logs, title, contract)      all three as one scan/1 report, with `passed`.

A lab reports a measurement by printing one line per value:
    HERO_MEASURE: <id>=<value>            or    HERO_MEASURE: <id>[<entity>]=<value>
`logs` is {file name: text}. Findings cite file and line so a person can check them.
"""
import pathlib, re

MEASURE = re.compile(r"^\s*HERO_MEASURE:\s*([a-z0-9_]+)(?:\[([^\]]+)\])?\s*=\s*(.+?)\s*$")
NUMBER = re.compile(r"^-?\d+(?:\.\d+)?")
EMPTY = {"", "null", "none", "n/a", "na", "nan", "unknown", "?"}
OPS = {"gt": lambda a, b: a > b, "ge": lambda a, b: a >= b, "lt": lambda a, b: a < b, "le": lambda a, b: a <= b,
       "eq": lambda a, b: a == b}
ID_BLOCK = re.compile(r"^\s+([0-9a-f]{16,})\b")
ANOMALIES = (
    ("null_output", "high", re.compile(r"^\s*null(\s+x\d+)?\s*$"), "output printed null instead of a value"),
    ("missing_value", "high", re.compile(r"(?:=|:\s)(?:null|n/a)\s*$", re.I), "a value is missing"),
    ("zero_datapoints", "medium", re.compile(r"\b(?:0|zero|no) datapoints?\b", re.I), "a query returned no data"),
    ("timed_out", "medium", re.compile(r"\[timed out after \d+s\]|\btimed out\b", re.I), "a step timed out"),
)
CONTROL = re.compile(r"(?:series|results?|found)\b[^\n]*:\s*0\s*$", re.I)

def read_logs(folder):
    return {p.name: p.read_text(errors="replace") for p in sorted(pathlib.Path(folder).glob("*.log"))}

def measurements(logs):
    """{id: [{entity, value, file, line}]} from HERO_MEASURE lines."""
    out = {}
    for name, text in logs.items():
        for n, line in enumerate(text.splitlines(), 1):
            m = MEASURE.match(line)
            if m:
                out.setdefault(m[1], []).append({"entity": m[2] or "", "value": m[3], "file": name, "line": n})
    return out

def verify_contract(contract, logs):
    """Every measurement must exist, hold a real value, cover enough entities and meet its fixed criterion."""
    seen, results = measurements(logs), []
    for c in contract or []:
        rows, status = seen.get(c["id"], []), "ok"
        real = [r for r in rows if r["value"].strip().lower() not in EMPTY]
        entities = {r["entity"] or r["value"] for r in real}
        accept = c.get("accept") or {"op": "present"}
        if not rows:
            status = "missing"
        elif not real:
            status = "no_value"
        elif len(entities) < int(c.get("min_entities") or 1):
            status = "too_few_entities"
        elif accept.get("op", "present") != "present":
            nums = [NUMBER.match(r["value"]) for r in real]
            if not all(nums):
                status = "not_numeric"
            elif not all(OPS[accept["op"]](float(n[0]), float(accept.get("value", 0))) for n in nums):
                status = "failed_criterion"
        results.append({"id": c["id"], "status": status, "values": len(real), "needed": c.get("min_entities", 1),
                        "where": f"{rows[0]['file']}:{rows[0]['line']}" if rows else ""})
    return {"passed": all(r["status"] == "ok" for r in results), "results": results}

CLAIM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:%|([A-Za-z][A-Za-z-]*(?:\s+[A-Za-z][A-Za-z-]*){0,2}))")
STOP = {"to", "in", "with", "and", "or", "of", "on", "for", "a", "an", "the", "without", "from", "by", "at", "via"}

def _stem(word):
    w = word.lower()
    return w[:-1] if len(w) > 3 and w.endswith("s") else w

def _noun(phrase):
    """The counted noun in "3 Splunk Metric Series" is the last word before any joining word."""
    words = []
    for w in (phrase or "").split():
        if w.lower() in STOP:
            break
        words.append(w)
    return _stem(words[-1]) if words else ""

def headline_claims(title, logs):
    """Each number and noun in the title must sit together on one evidence line. Finds claims nobody measured."""
    out, lines = [], [(n, i, l) for n, t in logs.items() for i, l in enumerate(t.splitlines(), 1)]
    for m in CLAIM.finditer(title or ""):
        number, noun = m[1], _noun(m[2])
        if m[0].rstrip().endswith("%"):
            hit = next(((n, i) for n, i, l in lines if re.search(rf"(?<![\d.]){re.escape(number)}\s*%", l)), None)
            out.append({"claim": m[0].strip(), "number": number, "noun": "%", "supported": bool(hit),
                        "where": f"{hit[0]}:{hit[1]}" if hit else ""})
            continue
        if not noun:
            continue
        units = {noun} | ({"sec", "s", "second"} if noun == "second" else set())
        hit = next(((n, i) for n, i, l in lines if re.search(rf"(?<![\d.]){re.escape(number)}(?![\d.])", l)
                    and any(u in {_stem(w) for w in re.findall(r"[A-Za-z]+", l)} or re.search(rf"\b{number}{u}\b", l)
                            for u in units)), None)
        out.append({"claim": m[0].strip(), "number": number, "noun": noun, "supported": bool(hit),
                    "where": f"{hit[0]}:{hit[1]}" if hit else ""})
    return out

def scan(logs):
    """Anomalies in the raw output. Terraform logs are skipped: this looks at what the lab scripts printed."""
    flags, n = [], 0
    for name, text in logs.items():
        if re.search(r"-(?:init|plan|apply|destroy)\.log$", name):
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            for kind, sev, rx, why in ANOMALIES:
                if rx.search(line):
                    n += 1
                    flags.append({"id": f"f{n}", "kind": kind, "severity": sev, "file": name, "line": i,
                                  "text": line.strip()[:160], "why": why})
            if "reproduce" in name and CONTROL.search(line):
                n += 1
                flags.append({"id": f"f{n}", "kind": "weak_control", "severity": "low", "file": name, "line": i,
                              "text": line.strip()[:160],
                              "why": "a zero before anything sends data may be unable to fail; check it can"})
        flags += _coverage(name, lines, n)
        n = len(flags)
    return flags

def _coverage(name, lines, start):
    """Blocks of IDs under a heading: a later block with fewer of the same IDs means values went missing."""
    blocks, cur = [], None
    for i, line in enumerate(lines, 1):
        m = ID_BLOCK.match(line)
        if m:
            if cur is None:
                cur = {"head": lines[i - 2].strip() if i > 1 else "", "line": i, "ids": set()}
                blocks.append(cur)
            cur["ids"].add(m[1])
        else:
            cur = None
    if not blocks:
        return []
    ref = max(blocks, key=lambda b: len(b["ids"]))
    out = []
    for b in blocks:
        if b["ids"] < ref["ids"]:
            out.append({"id": f"f{start + len(out) + 1}", "kind": "coverage_gap", "severity": "high", "file": name,
                        "line": b["line"], "text": b["head"][:160],
                        "why": f"values for {len(b['ids'])} of {len(ref['ids'])} entities"})
    return out

def check(logs, title="", contract=None):
    """One report. `passed` is false on a broken contract, an unsupported title, or a high-severity flag."""
    flags, heads = scan(logs), headline_claims(title, logs)
    result = verify_contract(contract, logs) if contract else None
    bad_heads = [h for h in heads if not h["supported"]]
    highs = [f for f in flags if f["severity"] == "high"]
    return {"schema": "scan/1", "flags": flags, "headline": heads, "contract": result,
            "passed": not highs and not bad_heads and (result is None or result["passed"])}

def markdown(report):
    lines = []
    for h in report.get("headline") or []:
        if not h["supported"]:
            lines.append(f"- **Title claim not found in evidence:** {h['claim']}")
    c = report.get("contract")
    for r in (c or {}).get("results", []):
        if r["status"] != "ok":
            lines.append(f"- **Contract measurement `{r['id']}`:** {r['status']} ({r['values']} of {r['needed']} values)")
    for f in report.get("flags") or []:
        lines.append(f"- {f['severity']}: {f['why']} ({f['file']}:{f['line']}): `{f['text']}`")
    return "\n".join(lines) or "- No evidence problems found."

if __name__ == "__main__":
    import json, sys
    print(json.dumps(check(read_logs(sys.argv[1]), " ".join(sys.argv[2:])), indent=1))
