#!/usr/bin/env bash
# Reads one phase's watcher output (Flink's REST API once a second, plus Flink's own state-change timestamps) and the
# TaskManager deletions, and prints counts: raw lines first, then HERO_MEASURE lines. Code only, no model. The lab
# runner has no Python, so lib.sh runs this in a pod. For each deletion:
#   not running: from Flink's RESTARTING timestamp to its next RUNNING timestamp (Flink's own clock, so under a second counts)
#   RUNNING but not back: seconds the job already said RUNNING before every task was RUNNING and a new checkpoint completed
# Usage: bash analyze.sh <phase> <watch log> <kills file> <window start s> <window end s>
#    or: bash analyze.sh k8s <phase> <kubectl samples> <kills file> <Splunk k8s.deployment.available file>
#        For each deletion: the old and new TaskManager pod, when the new pod turned Ready (its Ready condition's
#        lastTransitionTime), the gap, and the lowest available replicas in that gap from kubectl and from Splunk.
exec python3 - "$@" <<'PY'
import re, sys
from datetime import datetime, timezone
if sys.argv[1] == "k8s":
    label, kube_f, kills_f, sf_f = sys.argv[2:6]
    iso = lambda v: datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() if v else None
    samples = []                                   # (t, available, {pod: (ready, ready_since, restarts)})
    for m in re.finditer(r"^KUBE t=(\d+) available=(\d*) pods=(.*)$", open(kube_f).read(), re.M):
        pods = {}
        for x in m[3].split():
            f = (x.split("|") + ["", "", "", ""])[:4]
            pods[f[0]] = (f[1] == "True", iso(f[2]), int(f[3]) if f[3].isdigit() else None)
        samples.append((int(m[1]), int(m[2] or 0), pods))
    kills = [(int(m[1]), m[2]) for m in re.finditer(rf"^KILL t=(\d+) {re.escape(label)} (\S+)", open(kills_f).read(), re.M)]
    sf = [tuple(map(float, l.split())) for l in open(sf_f) if len(l.split()) == 2]
    na = lambda v: "null" if v is None else v
    print(f"k8s {label}: {len(samples)} kubectl samples, {len(sf)} Splunk points")
    out = []
    for k, (tk, old) in enumerate(kills, 1):
        before = set(next((p for t, _, p in reversed(samples) if t < tk), {}))
        new = next((n for t, _, p in samples if t >= tk for n in p if n not in before and n != old), None)
        ready = next((p[new][1] for t, _, p in samples if t >= tk and new in p and p[new][0]), None)
        gap = round(ready - tk, 1) if ready is not None else None
        kmin = min((a for t, a, _ in samples if ready is not None and tk <= t <= ready), default=None)
        smin = min((v for ts, v in sf if ready is not None and ts + 10 > tk and ts <= ready), default=None)
        pts = " ".join(f"{int(ts)}={v:g}" for ts, v in sf if ready is not None and ts + 10 > tk and ts <= ready)
        print(f"{label}-{k}: deleted {old} at {tk}; new pod {na(new)} Ready at {na(ready)} ({na(gap)} s); "
              f"kubectl available min {na(kmin)}; Splunk points overlapping the gap: {pts or 'none'}")
        out += [f"HERO_MEASURE: splunk_k8s_view[ready_gap_s-{k}]={na(gap)}",
                f"HERO_MEASURE: splunk_k8s_view[kubectl_available_min-{k}]={na(kmin)}",
                f"HERO_MEASURE: splunk_k8s_view[available_min-{k}]={na(smin if smin is None else int(smin))}"]
    rs = [r for _, _, p in samples for _, _, r in p.values() if r is not None]
    print(f"TaskManager pods seen by kubectl: {len({n for _, _, p in samples for n in p})}; highest container restart count {max(rs, default='null')}")
    out.append(f"HERO_MEASURE: splunk_k8s_view[kubectl_container_restarts_max]={max(rs) if rs else 'null'}")
    print("\n".join(out))
    sys.exit(0)
label, watch_f, kills_f, w0, w1 = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
text = open(watch_f).read()
num = lambda v: int(float(v)) if v not in (None, "na") else None
rows = [(int(m[1]), m[2], num(m[3]), num(m[4]), num(m[5]), num(m[6]), num(m[7]), num(m[8])) for m in re.finditer(
    r"^WATCH t=(\d+) state=(\w+)(?: vertices_running=(\d+)/(\d+) restarts=(\S+) ckpt_ok=(\S+) ckpt_fail=(\S+) ckpt_id=(\S+))?", text, re.M)
    if w0 <= int(m[1]) < w1]
moves = [(m[1], float(m[2])) for m in re.finditer(r"^STATE (\w+) at=([\d.]+)", text, re.M)]
kills = [int(m[1]) for m in re.finditer(rf"^KILL t=(\d+) {re.escape(label)}\b", open(kills_f).read(), re.M) if w0 <= int(m[1]) < w1]
def first_last(i):
    vals = [r[i] for r in rows if r[i] is not None]
    return (vals[0], vals[-1]) if vals else (None, None)
r0, r1 = first_last(4); c0, c1 = first_last(5); f0, f1 = first_last(6)
states = {}
for r in rows:
    states[r[1]] = states.get(r[1], 0) + 1
print(f"{label}: {len(rows)} one-second samples from {w0} to {w1}; states " + ", ".join(f"{k} {v}" for k, v in sorted(states.items())) +
      f"; Flink's restart count {r0} -> {r1}; checkpoints completed {c0} -> {c1}, failed {f0} -> {f1}")
bumps, prev = [], r0                               # the second each new restart first shows in the REST API
for r in rows:
    if r[4] is not None and prev is not None and r[4] > prev:
        bumps += [r[0]] * (r[4] - prev)
    if r[4] is not None:
        prev = r[4]
na = lambda v: "null" if v is None else v
out = []
for k, tk in enumerate(kills, 1):
    bound = kills[k] if k < len(kills) else w1
    down = next((at for s, at in moves if s == "RESTARTING" and tk - 1 <= at < bound), None)
    up = next((at for s, at in moves if s == "RUNNING" and down is not None and at > down), None)
    not_running = round(up - down, 1) if down is not None and up is not None else None
    ck0 = next((r[7] for r in reversed(rows) if r[0] <= tk and r[7] is not None), None)
    back = next((r[0] for r in rows if r[0] >= tk and r[1] == "RUNNING" and r[2] and r[2] == r[3] and r[7] is not None
                 and ck0 is not None and r[7] > ck0), None)
    unrecovered = sum(1 for r in rows if up is not None and back is not None and up <= r[0] < back and r[1] == "RUNNING")
    lead = round(down - tk, 1) if down is not None else None
    print(f"{label}-{k}: TaskManager deleted at {tk}; RESTARTING at {na(down)} ({na(lead)} s after the deletion), RUNNING again at {na(up)} ({na(not_running)} s not RUNNING); "
          f"every task RUNNING and a new checkpoint at {na(back)}, {unrecovered} s after the job already said RUNNING")
    out += [f"HERO_MEASURE: deletion_not_running_s[{label}-{k}]={na(not_running)}",
            f"HERO_MEASURE: deletion_running_unrecovered_s[{label}-{k}]={unrecovered if back is not None else 'null'}"]
print(f"RESTARTS {label} " + " ".join(map(str, bumps)))
print(f"HERO_MEASURE: restarts_injected[{label}]={len(kills)}")
print(f"HERO_MEASURE: flink_rest_restarts[{label}]={na(r1 - r0 if r0 is not None and r1 is not None else None)}")
print("\n".join(out))
PY
