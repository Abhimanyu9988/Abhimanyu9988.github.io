#!/usr/bin/env python3
"""Proves, for free, that a test trace reaches Splunk APM and can be read back through the APIs labs use.

A lab that depends on APM is only worth paying for after this passes. It prints which request format worked, and never a token.
"""
import json, os, secrets, sys, time, urllib.error, urllib.request

WAIT_S, GAP_S = 360, 20

def call(method, url, token, body=None):
    """(status, text). A network failure is status 0, so one bad moment doesn't end the check."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"X-SF-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except OSError as e:
        return 0, str(e)

def test_spans(env, now_us):
    """One request across two services, as Zipkin JSON, the format Splunk's /v2/trace route takes."""
    trace, a, b, c = secrets.token_hex(16), secrets.token_hex(8), secrets.token_hex(8), secrets.token_hex(8)
    tags = {"environment": env, "deployment.environment": env}
    def span(sid, parent, kind, service):
        s = {"traceId": trace, "id": sid, "name": "GET /check", "kind": kind, "timestamp": now_us, "duration": 20000,
             "localEndpoint": {"serviceName": service}, "tags": tags}
        return {**s, "parentId": parent} if parent else s
    return trace, [span(a, None, "SERVER", "lab-check-a"), span(b, a, "CLIENT", "lab-check-a"), span(c, b, "SERVER", "lab-check-b")]

def ranges(now):
    """Splunk's Go client uses epoch milliseconds. ISO times are the other format seen in use."""
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    start = now - 1500
    return {"epoch milliseconds": f"{start * 1000}/{now * 1000}", "ISO times": f"{iso(start)}/{iso(now)}"}

def read_topology(api, token, env, now):
    """(format that worked, node names), and the last error text when none did."""
    last = ""
    for name, value in ranges(now).items():
        status, text = call("POST", f"{api}/v2/apm/topology", token, {"timeRange": value, "tagFilters": [
            {"name": "sf_environment", "operator": "equals", "scope": "GLOBAL", "value": env}]})
        if status == 200:
            nodes = (json.loads(text).get("data") or {}).get("nodes") or []
            names = sorted(str(n.get("serviceName")) for n in nodes)
            if names:
                return name, names, ""
            last = f"{name}: HTTP 200 with no services yet"
        else:
            last = f"{name}: HTTP {status} {text[:120]}"
    return "", [], last

def read_trace(api, token, trace):
    """(route that worked, span count), and the last status."""
    status, text = call("GET", f"{api}/v2/apm/trace/{trace}/latest", token)
    if status == 200 and text.strip().startswith("[") and json.loads(text):
        return "latest", len(json.loads(text)), status
    status, text = call("GET", f"{api}/v2/apm/trace/{trace}/segments", token)
    if status == 200 and text.strip().startswith("[") and json.loads(text):
        return "segments", len(json.loads(text)), status
    return "", 0, status

def main():
    realm, ingest, read = (os.environ.get(k, "") for k in ("SPLUNK_REALM", "SPLUNK_ACCESS_TOKEN", "SPLUNK_API_TOKEN"))
    if not (realm and ingest and read):
        print("CHECK FAILED: the Splunk settings are missing")
        return 1
    api, env = f"https://api.{realm}.signalfx.com", f"lab-check-{os.environ.get('GITHUB_RUN_ID', 'local')}"
    url, first, map_ok, trace_ok = f"https://ingest.{realm}.signalfx.com/v2/trace", None, False, False
    start = time.time()
    while time.time() - start < WAIT_S:
        trace, spans = test_spans(env, int(time.time() * 1000000))
        status, text = call("POST", url, ingest, spans)
        print(f"sent a test trace: HTTP {status}", "" if status in (200, 202) else text[:200])
        first = first or trace
        name, nodes, why = read_topology(api, read, env, int(time.time()))
        if name:
            map_ok = True
            print(f"service map: {len(nodes)} services {nodes}, using {name}")
        else:
            print(f"service map: not yet ({why})")
        route, count, code = read_trace(api, read, first)
        if route:
            trace_ok = True
            print(f"trace read back: {count} items through /{route}")
        else:
            print(f"trace: not yet (HTTP {code})")
        if map_ok and trace_ok:
            print("CHECK PASSED: Splunk APM takes a trace and both read APIs work")
            return 0
        time.sleep(GAP_S)
    print(f"CHECK FAILED: service map {'worked' if map_ok else 'failed'}, trace read {'worked' if trace_ok else 'failed'}")
    return 1

if __name__ == "__main__":
    sys.exit(main())
