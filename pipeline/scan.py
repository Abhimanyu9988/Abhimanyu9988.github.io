#!/usr/bin/env python3
"""Evidence checks done by code, not by a model. No network and no LLM.

  verify_contract(contract, logs)   did the lab report each promised measurement, and how well is each one supported?
  headline_claims(title, logs)      a heuristic: does each number sit next to its noun on one evidence line?
  scan(logs)                        anomalies a reader or reviewer should not miss: nulls, gaps, weak controls.
  check(logs, title, contract)      all three as one scan/1 report. `passed` covers the title and the contract, which a
                                    revision or a new lab can fix. Flags are reported for people and the Editor.

A lab reports a measurement by printing one line per value:
    HERO_MEASURE: <id>=<value>            or    HERO_MEASURE: <id>[<entity>]=<value>
`logs` is {file name: text}. Findings cite file and line so a person can check them.

Limit of all this: the lab script writes both the raw output and the HERO_MEASURE lines, so nothing here proves a value
is true. It catches lost values, typed-in values and unsupported claims. Only a runner-side re-query could prove more.
"""
import pathlib, re

MEASURE = re.compile(r"^\s*HERO_MEASURE:\s*([a-z0-9_]+)(?:\[([^\]]+)\])?\s*=(.*)$")
VALUE = re.compile(r"^(-?\d[\d,]*(?:\.\d+)?)\s*([A-Za-z%/]*)$")
EMPTY = {"", "null", "none", "n/a", "na", "nan", "unknown", "?", "<no value>", "<nil>"}
BROKEN = re.compile(r"(?i)\b(error|timed? ?out|not found|no value|failed to|exception)\b")
OPS = {"gt": lambda a, b: a > b, "ge": lambda a, b: a >= b, "lt": lambda a, b: a < b, "le": lambda a, b: a <= b,
       "eq": lambda a, b: a == b}
ID_BLOCK = re.compile(r"^\s+([0-9a-f]{16,})\b")
JSON_FIELD = re.compile(r'^\s*"[^"]+"\s*:')      # a null field in raw JSON is normal, not a lost measurement
ANOMALIES = (
    ("null_output", "high", re.compile(r"^\s*null(\s+x\d+)?\s*$"), "output printed null instead of a value"),
    ("missing_value", "high", re.compile(r"(?:=|:\s)(?:null|n/a)\s*$", re.I), "a value is missing"),
    ("zero_datapoints", "medium", re.compile(r"\b(?:0|zero|no) datapoints?\b", re.I), "a query returned no data"),
    ("timed_out", "medium", re.compile(r"\[timed out after \d+s\]|\btimed out\b", re.I), "a step timed out"),
)
CONTROL = re.compile(r"(?:series|results?|found)\b[^\n]*:\s*0\s*$", re.I)
MAX_FLAGS = 30

def read_logs(folder, first=1):
    """{file name: text} for logs numbered `first` or later. A lab passes the start of its final attempt, because
    numbers from a failed attempt prove nothing and a failed attempt's null must not condemn a clean rerun."""
    out = {}
    for p in sorted(pathlib.Path(folder).glob("*.log")):
        m = re.match(r"(\d+)-", p.name)
        if not m or int(m[1]) >= first:
            out[p.name] = p.read_text(errors="replace")
    return out

def _clean(value):
    return value.strip().strip("\"'`").strip()

NOT_RUN = re.compile(r"-(?:init|plan|apply|destroy|cleanup)\.log$")
LITERAL = re.compile(r"HERO_MEASURE:\s*([a-z0-9_]+)(?:\[[^\]]*\])?\s*=\s*[\"']?([^\s\"'`]*)")
ASSIGNED = re.compile(r"^[ \t]*(?:export[ \t]+|readonly[ \t]+|local[ \t]+)?([A-Za-z_]\w*)=[\"']?(-?\d+(?:\.\d+)?)[\"']?[ \t]*(?:#.*)?$", re.M)
WINDOW = 40                                    # how far above a measurement its raw output may sit

def measurements(logs):
    """{id: [{entity, value, file, line, text}]} from HERO_MEASURE lines in the lab's run logs."""
    out = {}
    for name, text in logs.items():
        if NOT_RUN.search(name):
            continue                               # only reproduce and fix output can report a result
        for n, line in enumerate(text.splitlines(), 1):
            m = MEASURE.match(line)
            if m:
                out.setdefault(m[1], []).append({"entity": (m[2] or "").strip().lower(), "value": m[3].strip(),
                                                 "file": name, "line": n})
    return out

TYPED = re.compile(r"[~+\-<v]?\.?\d[\w.,:%/\-]*[;)]*")   # 35, ~35, 1e5, 35/s, 3/3: not $v, [0-9]+, <n2>
PRINTF = re.compile(r"%[-0-9.]*[a-z]")

def constants_in(text):
    """Names given one literal value and never changed. A counter starts at 0 and is computed, not typed.
    When unsure, a name is not constant: a false "typed" fails a paid lab, and no grade proves a value anyway."""
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    code = re.sub(r"HERO_MEASURE:[^\n\"'`]*", "", code)   # the text of a measure is not an assignment, the rest of its line may be
    out = set()
    for n in {m[1] for m in ASSIGNED.finditer(code)}:
        assigned = re.findall(rf"(?<![\w$-]){n}\+?=(?!=)", code)
        changed = re.search(rf"\(\([^)\n]*(?:(?:\+\+|--)\s*{n}\b|\b{n}\s*(?:\+\+|--|[-+*/]?=(?!=)))"
                            rf"|\bread\b[^\n;|<]*\b{n}\b|\bprintf\s+-v\s+{n}\b|\blet\b[^\n]*\b{n}\b|(?:^|[;&|])\s*for\s+{n}\b", code, re.M)
        if len(assigned) == 1 and not changed:
            out.add(n)
    return out

def typed_values(scripts):
    """{measure id: True} for measurements a script types in instead of computing."""
    typed = {}
    for text in (scripts or {}).values():
        constants = constants_in(text)
        for m in LITERAL.finditer(text):
            value = m[2].strip()
            if re.match(r"[\"']?[$`]", text[m.end():]):
                continue                           # the value carries on past the quote, so part of it is computed
            name = re.fullmatch(r"\$\{?([A-Za-z_]\w*)\}?", value)
            if (TYPED.fullmatch(value) and not PRINTF.search(value)) or (name and name[1] in constants):
                typed[m[1]] = True
    return typed

def _printed_by_script(line, flat):
    """True when a log line is text a script typed, so it cannot corroborate anything."""
    wanted = re.sub(r"\s+", " ", line).strip()
    return bool(wanted) and any(wanted in t for t in flat)

def _raw_support(row, cid, logs, flat):
    """True when command output above the measurement holds its value next to its entity or measure name.
    Scripts control stdout, so this raises the cost of a false claim. It does not prove the claim."""
    lines = logs[row["file"]].splitlines()[max(0, row["line"] - 1 - WINDOW):row["line"] - 1]
    number = VALUE.fullmatch(_clean(row["value"]))
    value = _plain(number[1]) if number else _clean(row["value"])
    names = {row["entity"]} if row["entity"] else {w for w in cid.lower().split("_") if len(w) > 2}
    for line in reversed(lines):
        low = _plain(line).lower()
        if "HERO_" in line or line.startswith(("$ ", "# ")) or _printed_by_script(line, flat):
            continue                               # the line itself, xtrace echoes and typed text prove nothing
        if re.search(rf"(?<![\d.\w]){re.escape(value.lower())}(?![\d.\w])", low) and names and all(n in low for n in names):
            return True
    return False

def bad_measure(c):
    """Why a contract entry cannot be evaluated, or "". A malformed contract must fail, never crash a draft."""
    if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not c["id"]:
        return "no id"
    accept, needed = c.get("accept") or {"op": "present"}, c.get("min_entities", 1)
    if not isinstance(accept, dict) or isinstance(needed, bool) or not isinstance(needed, int):
        return "bad accept or min_entities"
    op, value = accept.get("op", "present"), accept.get("value")
    if op == "present":
        return ""
    if op not in OPS:
        return f"unknown op {op}"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return f"op {op} needs a numeric value"
    return ""

def verify_contract(contract, logs, scripts=None, strict=False):
    """Check the contract and grade each measurement's provenance.

    status      ok, or why the measurement fails. A typed-in value always fails.
    provenance  corroborated: command output above it holds the value. self_reported: only the HERO_MEASURE line.
                Neither means proven: the lab script prints both. In strict mode, self_reported fails.
    """
    seen, typed, results = measurements(logs), typed_values(scripts), []
    flat = [re.sub(r"\s+", " ", t) for t in (scripts or {}).values()]
    for c in contract or []:
        problem = bad_measure(c)
        if problem:
            results.append({"id": str(c.get("id") if isinstance(c, dict) else c), "status": "bad_contract", "values": 0,
                            "needed": 0, "provenance": "none", "where": problem})
            continue
        rows, status = seen.get(c["id"], []), "ok"
        real = [r for r in rows if _clean(r["value"]).lower() not in EMPTY and not BROKEN.search(r["value"])]
        needed = int(c.get("min_entities") or 1)
        count = len({r["entity"] for r in real if r["entity"]}) if needed > 1 else len(real)
        accept = c.get("accept") or {"op": "present"}
        values = {}
        for r in real:
            values.setdefault(r["entity"], set()).add(_clean(r["value"]))
        backed = [r for r in real if _raw_support(r, c["id"], logs, flat)]
        provenance = "corroborated" if real and len(backed) == len(real) else "self_reported"
        if not rows:
            status = "missing"
        elif c["id"] in typed:
            status, provenance = "hardcoded", "typed_in_script"
        elif not real:
            status = "no_value"
        elif len(real) < len(rows):
            status = "bad_value"                  # one lost value cannot hide behind the good ones
        elif any(len(v) > 1 for k, v in values.items() if k or needed == 1):
            status = "conflicting"                # one thing, two answers
        elif count < needed:
            status = "too_few_entities"
        elif accept.get("op", "present") == "present":
            if not all(re.search(r"\d", r["value"]) for r in real):
                status = "no_value"               # a measurement is a number, not a word that fills the slot
        else:
            parsed = [VALUE.fullmatch(_clean(r["value"])) for r in real]
            unit = str(c.get("unit") or "").strip()
            if not all(parsed):
                status = "not_numeric"
            elif any(p[2] and p[2] != unit for p in parsed):
                status = "wrong_unit"
            elif not all(OPS[accept["op"]](float(p[1].replace(",", "")), float(accept.get("value", 0))) for p in parsed):
                status = "failed_criterion"
        if status == "ok" and strict and provenance != "corroborated":
            status = "uncorroborated"
        results.append({"id": c["id"], "status": status, "values": len(real), "needed": needed, "provenance": provenance,
                        "where": f"{rows[0]['file']}:{rows[0]['line']}" if rows else ""})
    return {"passed": all(r["status"] == "ok" for r in results), "results": results}

WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
         "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
         "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000}
CLAIM = re.compile(r"(?:(\$)|(?<![\w.,$]))(\d(?:[\d,]*\d)?(?:\.\d+)?(?![\w,]*\d)|(?:" + "|".join(sorted(WORDS, key=len, reverse=True)) +
                   r")\b)\s*(%|[-\s]*[A-Za-z][A-Za-z-]*(?:\s+[A-Za-z][A-Za-z-]*){0,2})?", re.I)
STOP = {"to", "in", "with", "and", "or", "of", "on", "for", "a", "an", "the", "without", "from", "by", "at", "via"}
NOT_A_COUNT = re.compile(r"(?i)\b(step|attempt|phase|version|line|stage|try)\s*#?$")
NEAR = 5                                       # the number and noun must sit this close, in tokens, to count together

def _stem(word):
    w = word.lower()
    return w[:-1] if len(w) > 3 and w.endswith("s") else w

def _noun(phrase, attached=False):
    """The counted noun in "3 Splunk Metric Series" is the last word before any joining word.
    In "30-second Cold Start" the hyphen ties the number to its unit, so the noun is the first word."""
    if phrase and (attached or phrase.lstrip(" ").startswith("-")):
        first = re.findall(r"[A-Za-z]+", phrase)     # "5GB Memory": the unit is attached, so it is what is counted
        return _stem(first[0]) if first else ""
    words = []
    for w in (phrase or "").replace("-", " ").split():
        if w.lower() in STOP:
            break
        words.append(w)
    return _stem(words[-1]) if words else ""

def _plain(text):
    return re.sub(r"(?<=\d),(?=\d)", "", text)

def _numbers(raw):
    """The forms a number may take in a log: "1,000" and "1000", or "five" and "5"."""
    low = raw.lower()
    if low in WORDS:
        return {low, str(WORDS[low])}
    return {_plain(raw)}

def _pairs(line, numbers, nouns):
    """True when a number and noun sit close together, and the number is not a step label."""
    tokens = re.findall(r"\$?\d+(?:\.\d+)?[A-Za-z%]*|[A-Za-z][A-Za-z-]*", _plain(line))
    for i, t in enumerate(tokens):
        lead = re.match(r"\$?(\d+(?:\.\d+)?)([A-Za-z%]*)", t)
        value = lead[1] if lead else t.lower()
        if value not in numbers or NOT_A_COUNT.search(" ".join(tokens[max(0, i - 1):i])):
            continue
        near = {_stem(w) for w in tokens[max(0, i - NEAR):i + NEAR + 1] if w[0].isalpha()}
        if near & nouns or (lead and lead[2] and _stem(lead[2]) in nouns):
            return True
    return False

def headline_claims(title, logs):
    """Each number in the title must sit next to its noun on one evidence line. Finds claims nobody measured."""
    out, lines, seen = [], [(n, i, l) for n, t in logs.items() for i, l in enumerate(t.splitlines(), 1)], set()
    for m in CLAIM.finditer(title or ""):
        seen.add(m.start(2))
        money, raw, rest = m[1], m[2], m[3] or ""
        noun = "%" if rest.strip() == "%" else _noun(rest, title[m.end(2):m.end(2) + 1].isalpha())
        numbers = _numbers(raw)
        if noun == "%" or money:
            sign = r"\s*%" if noun == "%" else ""
            rx = "|".join((rf"\${re.escape(n)}" if money else rf"(?<![\d.]){re.escape(n)}{sign}") for n in numbers)
            hit = next(((n, i) for n, i, l in lines if re.search(rx, _plain(l))), None)
        elif noun:
            units = {noun} | ({"s", "sec"} if noun == "second" else {"m", "min"} if noun == "minute" else set())
            hit = next(((n, i) for n, i, l in lines if _pairs(l, numbers, units)), None)
        else:                                  # a bare number must at least appear in the evidence
            hit = next(((n, i) for n, i, l in lines if _pairs(l, numbers, {w for w in ()}) or
                        any(re.search(rf"(?<![\d.]){re.escape(x)}(?![\d.])", _plain(l)) for x in numbers)), None)
        out.append({"claim": m[0].strip(), "number": _plain(raw), "noun": noun, "supported": bool(hit),
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
                if rx.search(line) and not (kind == "missing_value" and JSON_FIELD.match(line)):
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

def _validate_title(heads, contract, result, logs):
    """A title number is validated only by a corroborated contract measurement that shares its noun and value.
    A text match is a heuristic: it shows the words appear in a log, not that the claim is true."""
    ok = {r["id"] for r in (result or {}).get("results", []) if r["status"] == "ok" and r["provenance"] == "corroborated"}
    seen = measurements(logs)
    for h in heads:
        h["basis"], h["validated"] = "heuristic", False
        for c in contract or []:
            if bad_measure(c):
                continue
            about = f"{c.get('id')} {c.get('what', '')} {c.get('unit', '')}".lower().replace("_", " ")
            if c.get("id") in ok and h["noun"] and re.search(rf"(?<!\w){re.escape(h['noun'])}s?(?!\w)", about):
                got = {_plain(VALUE.fullmatch(_clean(r["value"]))[1]) for r in seen.get(c["id"], [])
                       if VALUE.fullmatch(_clean(r["value"]))}
                numbers = _numbers(h["number"])
                if numbers & got:
                    h["basis"], h["validated"] = "measurement", True
    return heads

def check(logs, title="", contract=None, scripts=None, strict=False):
    """One report. `passed` is false on a broken contract or a title claim with no match in the evidence.
    strict (enforce) also requires corroborated measurements and measured title numbers."""
    flags = scan(logs)
    result = verify_contract(contract, logs, scripts, strict) if contract else None
    heads = _validate_title(headline_claims(title, logs), contract, result, logs)
    failures = [f"title claim not found in evidence: {h['claim']}" for h in heads if not h["supported"]]
    failures += [f"measurement {r['id']}: {r['status']}" for r in (result or {}).get("results", []) if r["status"] != "ok"]
    if strict:
        failures += [f"title claim has no measurement behind it: {h['claim']}" for h in heads
                     if h["supported"] and not h["validated"]]
    rank = {"high": 0, "medium": 1, "low": 2}       # a cap must never hide the worst flags
    flags = sorted(flags, key=lambda f: rank.get(f["severity"], 3))
    return {"schema": "scan/1", "flags": flags[:MAX_FLAGS], "headline": heads, "contract": result, "failures": failures,
            "passed": not failures}

def markdown(report):
    lines = []
    for h in report.get("headline") or []:
        if not h["supported"]:
            lines.append(f"- **Title claim not found in evidence:** {h['claim']}")
        elif h.get("validated"):
            lines.append(f"- Title claim backed by a corroborated measurement: {h['claim']}")
        else:
            lines.append(f"- Title claim matched by text search only, which is not proof: {h['claim']}")
    c = report.get("contract")
    for r in (c or {}).get("results", []):
        if r["status"] != "ok":
            lines.append(f"- **Contract measurement `{r['id']}`:** {r['status']} ({r['values']} of {r['needed']} values)")
        else:
            lines.append(f"- Measurement `{r['id']}`: {r['provenance']}, reported by the lab script, not independently re-run")
    for f in report.get("flags") or []:
        lines.append(f"- {f['severity']}: {f['why']} ({f['file']}:{f['line']}): `{f['text']}`")
    return "\n".join(lines) or "- No evidence problems found."

if __name__ == "__main__":
    import json, sys
    print(json.dumps(check(read_logs(sys.argv[1]), " ".join(sys.argv[2:])), indent=1))
