#!/usr/bin/env python3
"""Reads real time series for one lab environment from Splunk and prints them, so a chart can show what Splunk recorded.

No lab code runs here, nothing is written, and only a read token is used. Printed lines start with SERIES, as JSON.
"""
import json, os, re, sys, urllib.request

PROGRAMS = {
    "requests": "data('service.request.count', filter=filter('sf_environment', '{env}')).sum(by=['sf_service']).publish()",
    "latency_p50": "data('service.request.duration.ns.median', filter=filter('sf_environment', '{env}')).mean(by=['sf_service']).publish()",
    "latency_p90": "data('service.request.duration.ns.p90', filter=filter('sf_environment', '{env}')).mean(by=['sf_service']).publish()",
    "latency_p99": "data('service.request.duration.ns.p99', filter=filter('sf_environment', '{env}')).mean(by=['sf_service']).publish()",
    "jvm_memory": "data('jvm.memory.used', filter=filter('deployment.environment', '{env}')).sum(by=['service.name']).publish()",
    "jvm_threads": "data('jvm.thread.count', filter=filter('deployment.environment', '{env}')).sum(by=['service.name']).publish()",
    "jvm_cpu": "data('jvm.cpu.recent_utilization', filter=filter('deployment.environment', '{env}')).mean(by=['service.name']).publish()",
}

def parse(stream):
    """{series name: [[time ms, value], ...]} from a SignalFlow stream of event and data lines."""
    names, out, event, lines = {}, {}, "", []
    def flush():
        if not lines:
            return
        try:
            body = json.loads("".join(lines))
        except ValueError:
            return
        if event == "metadata":
            p = body.get("properties") or {}
            names[body.get("tsId")] = p.get("sf_service") or p.get("service.name") or p.get("sf_originatingMetric") or "all"
        elif event == "data":
            for row in body.get("data") or []:
                if row.get("value") is not None:
                    out.setdefault(names.get(row.get("tsId"), "unknown"), []).append([body.get("logicalTimestampMs"), row["value"]])
    for raw in stream.splitlines():
        if raw.startswith("event:"):
            flush()
            event, lines = raw[6:].strip(), []
        elif raw.startswith("data:"):
            lines.append(raw[5:].strip())
    flush()
    return out

def run(realm, token, program, start, stop, resolution):
    url = f"https://stream.{realm}.signalfx.com/v2/signalflow/execute?start={start}&stop={stop}&resolution={resolution}&immediate=true"
    req = urllib.request.Request(url, data=program.encode(), method="POST", headers={"X-SF-Token": token, "Content-Type": "text/plain"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode(errors="replace")

def main():
    env, start, stop = (os.environ.get(k, "") for k in ("LAB_ENV", "START_MS", "STOP_MS"))
    if not (re.fullmatch(r"[a-z0-9-]{1,63}", env) and start.isdigit() and stop.isdigit()):
        print("SERIES FAILED: bad input")
        return 1
    found = 0
    for name, program in PROGRAMS.items():
        try:
            got = parse(run(os.environ["SPLUNK_REALM"], os.environ["SPLUNK_API_TOKEN"], program.format(env=env), start, stop, os.environ.get("RESOLUTION_MS", "10000")))
        except Exception as e:
            print(f"SERIES {name} failed: {' '.join(str(e).split())[:160]}")
            continue
        for who, points in got.items():
            found += 1
            print("SERIES " + json.dumps({"metric": name, "service": who, "points": points}))
        print(f"{name}: {len(got)} series, {sum(len(p) for p in got.values())} points")
    return 0 if found else 1

if __name__ == "__main__":
    sys.exit(main())
