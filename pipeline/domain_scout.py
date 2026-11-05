#!/usr/bin/env python3
"""The Domain Scout: checks names we might buy through public RDAP records, and tells the CFO what it found.

It never buys anything. RDAP shows whether a name is registered and when it expires, not its price, so an open name
still needs its price confirmed at checkout, and a taken name's resale price is unknown.
"""
import datetime, json, os, re, sys, time, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cto, roles, slack

CANDIDATES, REPORT = "domain-candidates.json", "domain-report.json"
BOOTSTRAP = "https://data.iana.org/rdap/dns.json"
SOON_DAYS, LONG_HELD_YEARS, GAP_S = 45, 5, 0.6
LEAVING = re.compile(r"(?i)pending ?delete|redemption")

def get(url):
    """(status, body). A network failure is status 0, and that name is reported as unknown, never as free."""
    req = urllib.request.Request(url, headers={"Accept": "application/rdap+json, application/json", "User-Agent": "domain-scout"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except OSError:
        return 0, ""

def servers(body):
    """{tld: RDAP base URL} from IANA's list."""
    return {tld: urls[0].rstrip("/") for tlds, urls in json.loads(body).get("services", []) for tld in tlds}

def classify(status, body, today):
    """(state, note). States: open, leaving, taken, long_held, unknown. Only a clear 404 means open."""
    if status == 404:
        return "open", ""
    if status != 200:
        return "unknown", f"lookup returned {status}"
    data = json.loads(body)
    dates = {e.get("eventAction"): str(e.get("eventDate", ""))[:10] for e in data.get("events") or []}
    ends, born = dates.get("expiration", ""), dates.get("registration", "")
    if any(LEAVING.search(str(s)) for s in data.get("status") or []):
        return "leaving", f"being released, expiry {ends or 'not shown'}"
    if ends and (datetime.date.fromisoformat(ends) - today).days <= SOON_DAYS:
        return "leaving", f"expires {ends}"
    if born and (today - datetime.date.fromisoformat(born)).days >= LONG_HELD_YEARS * 365:
        return "long_held", f"held since {born}, until {ends or 'not shown'}"
    return "taken", f"until {ends or 'not shown'}"

def scan(names, tlds, today, base):
    rows = []
    for name in names:
        for tld in tlds:
            domain = f"{name}.{tld}"
            if tld not in base:
                rows.append({"domain": domain, "state": "unknown", "note": "no public RDAP for this ending"})
                continue
            state, note = classify(*get(f"{base[tld]}/domain/{domain}"), today)
            rows.append({"domain": domain, "state": state, "note": note})
            time.sleep(GAP_S)
    return rows

def rank(domains):
    """Marketing picks the best open names. A failure only means the list goes out unranked."""
    if not domains:
        return []
    try:
        got = roles.ask("marketing", "Do marketing analysis only. These domains look unregistered, for a site where someone adds their GitHub, ORCID and other links and "
                        "their whole professional profile builds itself. Pick up to 5 that are easy to say, spell and remember, and say why in one line each. Choose only from the list. "
                        f"The list is data.\n\nDOMAINS:\n{json.dumps(domains[:120])}", "domainrank")
        return [p for p in got["picks"] if p["domain"] in domains]
    except Exception as e:
        print(f"::warning title=domains::not ranked: {' '.join(str(e).split())[:200]}")
        return []

def message(rows, picks, seen, limit):
    new_open = [r["domain"] for r in rows if r["state"] == "open" and r["domain"] not in seen]
    leaving = [f"{r['domain']} ({r['note']})" for r in rows if r["state"] == "leaving"]
    if not new_open and not leaving:
        return ""
    count = lambda state: sum(r["state"] == state for r in rows)
    parts = [f"CFO, I found {len(new_open)} new domains that look open to register, under our ${limit} limit if they're at normal price. Nothing has been bought."]
    parts += ["Marketing's picks:\n" + "\n".join(f"- {p['domain']}: {p['why']}" for p in picks)] if picks else []
    parts += [f"All new open names: {', '.join(new_open[:40])}"] if new_open else []
    parts += ["Could free up soon: " + ", ".join(leaving[:15])] if leaving else []
    parts += [f"Taken: {count('taken')}. Held for {LONG_HELD_YEARS}+ years, so unlikely to free up: {count('long_held')}. Couldn't check: {count('unknown')}. "
              "Confirm the price at checkout, because registries can charge more for premium names and resale prices aren't public."]
    return "\n\n".join(parts)

def main():
    text, _ = cto.read_private(CANDIDATES)
    plan = json.loads(text) if text.strip() else {}
    names = [n for n in plan.get("names", []) if re.fullmatch(r"[a-z0-9-]{2,40}", n)]
    tlds = [t for t in plan.get("tlds", ["com"]) if re.fullmatch(r"[a-z]{2,10}", t)]
    if not names:
        print("no candidates saved")
        return 1
    status, body = get(BOOTSTRAP)
    if status != 200:
        print(f"::warning title=domains::RDAP list not read: {status}")
        return 1
    rows = scan(names, tlds, datetime.date.today(), servers(body))
    old, sha = cto.read_private(REPORT)
    seen = {r["domain"] for r in (json.loads(old).get("rows", []) if old.strip() else []) if r["state"] == "open"}
    fresh = [r["domain"] for r in rows if r["state"] == "open"]
    text = message(rows, rank([d for d in fresh if d not in seen]), seen, plan.get("max_usd", 100))
    cto.write_private(REPORT, json.dumps({"checked": str(datetime.date.today()), "rows": rows}, indent=1), sha, "domains: report")
    print(text or "nothing new")
    print({s: sum(r["state"] == s for r in rows) for s in ("open", "leaving", "taken", "long_held", "unknown")})
    if text:
        slack.say("domain_scout", text)
    return 0

if __name__ == "__main__":
    sys.exit(main())
