#!/usr/bin/env bash
# Reads one run's three logs and prints the counts, raw first, then HERO_MEASURE lines. Code only, no model.
# Usage: bash analyze.sh <run label> <load output> <orders log> <inventory log>
exec python3 - "$@" <<'PY'
import sys, re
label, load_f, orders_f, inv_f = sys.argv[1:5]
WINDOW, CONTROL = 70, 20
text = open(load_f).read()
st = re.search(r"LOAD start=(\d+) rate=(\d+)", text); start, rate = int(st[1]), int(st[2])
sl = re.search(r"SLOWDOWN start=(\d+) end=(\d+) second=(\d+) seconds=(\d+)", text)
slow_at, slow_s = int(sl[3]), int(sl[4]); end_s = slow_at + slow_s
per = {int(m[1]): (int(m[2]), int(m[3])) for m in re.finditer(r"LOAD second=(\d+) ok=(\d+) failed=(\d+)", text)}
sent = int(re.search(r"LOAD end=\d+ sent=(\d+)", text)[1])
total = lambda rows: (sum(o for o, f in rows), sum(f for o, f in rows))
b_ok, b_fail = total([per.get(s, (0, 0)) for s in range(slow_at - CONTROL, slow_at)])
d_ok, d_fail = total([per.get(s, (0, 0)) for s in range(slow_at, end_s)])
after = [(s, *per.get(s, (0, 0))) for s in range(end_s, end_s + WINDOW)]
a_ok, a_fail = sum(x[1] for x in after), sum(x[2] for x in after)
rec = next((after[i][0] - end_s for i in range(len(after) - 4)
            if all(after[j][1] + after[j][2] > 0 and after[j][1] / (after[j][1] + after[j][2]) >= 0.95 for j in range(i, i + 5))), None)
print(f"{label}: load started {start} at {rate} orders a second ({sent} sent), slowdown from second {slow_at} for {slow_s} s, watched seconds {end_s} to {end_s + WINDOW - 1}")
print(f"{label}: success per 10 s after the slowdown (ok/sent): " + " ".join(f"{sum(x[1] for x in after[i:i+10])}/{sum(x[1]+x[2] for x in after[i:i+10])}" for i in range(0, WINDOW, 10)))
print(f"{label}: before the slowdown {b_ok} of {b_ok + b_fail} orders succeeded; during it {d_fail} of {d_ok + d_fail} failed; after it {a_fail} of {a_ok + a_fail} failed")
print(f"{label}: back to 95% success for 5 s in a row: " + (f"{rec} s after the slowdown ended" if rec is not None else f"not within {WINDOW} s"))
w0, w1 = start + end_s, start + end_s + WINDOW
rows = [tuple(map(int, m.groups())) for m in re.finditer(r"ORDER \d+ ok=(\d) attempts=(\d+) denied=(\d) ms=(\d+) t=(\d+)", open(orders_f).read())]
win = [r for r in rows if w0 <= r[4] < w1]
calls, denied = sum(r[1] for r in win), sum(r[2] for r in win)
cpo = calls / len(win) if win else 0
lat = sorted(r[3] for r in win if r[0] == 1)
pre = sorted(r[3] for r in rows if r[0] == 1 and start + slow_at - CONTROL <= r[4] < start + slow_at)
pq = lambda xs, q: xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None
p = lambda q: pq(lat, q)
print(f"{label}: orders log, {len(win)} orders in the window made {calls} inventory calls ({cpo:.2f} per order); retries skipped because the budget was spent: {denied}")
print(f"{label}: successful orders before the slowdown: n={len(pre)}, latency p50 {pq(pre, 0.5)} ms, p99 {pq(pre, 0.99)} ms")
print(f"{label}: successful orders in the window after it: n={len(lat)}, latency p50 {p(0.5)} ms, p99 {p(0.99)} ms")
inv = [tuple(m.groups()) for m in re.finditer(r"INVENTORY t=(\d+) handled=(\d+) late=(\d+) queued=(-?\d+) cpu_usec=(\S+) node_busy=(\d+) node_total=(\d+)", open(inv_f).read())]
iw = [r for r in inv if w0 <= int(r[0]) < w1]
handled, late = sum(int(r[1]) for r in iw), sum(int(r[2]) for r in iw)
def cores_of(samples):   # (t, usec) pairs inside the window -> average cores used
    s = [(int(t), int(u)) for t, u in samples if u.isdigit()]
    return (s[-1][1] - s[0][1]) / 1e6 / (s[-1][0] - s[0][0]) if len(s) > 1 and s[-1][0] > s[0][0] else None
def peak_of(samples):    # (t, usec) pairs -> the busiest single second, in cores
    s = [(int(t), int(u)) for t, u in samples if u.isdigit()]
    return max(((b[1] - a[1]) / 1e6 / (b[0] - a[0]) for a, b in zip(s, s[1:]) if b[0] > a[0]), default=None)
otext = open(orders_f).read()
orows = [m.groups() for m in re.finditer(r"ORDERS t=(\d+) cpu_usec=(\S+) timeouts=(\d+) conn_errors=(\d+) http_errors=(\d+)", otext) if w0 <= int(m[1]) < w1]
cores, ocores = cores_of((r[0], r[4]) for r in iw), cores_of((r[0], r[1]) for r in orows)
ipeak, opeak = peak_of((r[0], r[4]) for r in iw), peak_of((r[0], r[1]) for r in orows)
nodes = [(int(r[5]), int(r[6])) for r in iw]
npeak = max(((b[0] - a[0]) / (b[1] - a[1]) for a, b in zip(nodes, nodes[1:]) if b[1] > a[1]), default=None)   # busy share of the node
qpeak = max((int(r[3]) for r in iw), default=None)
timeouts, conns, https = (sum(int(r[k]) for r in orows) for k in (2, 3, 4))
srv = re.search(r"INVENTORY server threads=(\d+) backlog=(\d+) connection_limit=(\d+)", open(inv_f).read())
late_pct = 100 * late / handled if handled else 0
print(f"{label}: inventory finished {handled} requests in {len(iw)} s of the window ({handled / max(1, len(iw)):.1f} a second); {late} of them started after the caller's timeout had passed ({late_pct:.1f}%)")
fmt = lambda c: f"{c:.2f}" if c is not None else "null"
print(f"{label}: inventory as it ran: " + (f"{srv[1]} threads, socket backlog {srv[2]}, connection limit {srv[3]}" if srv else "server line missing"))
print(f"{label}: inventory queue, accepted but waiting for a thread: peak {qpeak} in the window")
print(f"{label}: CPU in the window from each container's cgroup: inventory average {fmt(cores)}, busiest second {fmt(ipeak)} cores; orders average {fmt(ocores)}, busiest second {fmt(opeak)} cores; node busiest second {fmt(npeak * 100 if npeak is not None else None)}% busy")
print(f"{label}: failed inventory calls in the window by kind: timeouts {timeouts}, refused or reset {conns}, HTTP errors {https}")
pct = lambda a, b: f"{100 * a / b:.1f}" if b else "null"
mode = label.split("-")[0]
nz = lambda v: "null" if v is None else v
print(f"HERO_MEASURE: orders_success_before_pct[{label}]={pct(b_ok, b_ok + b_fail)}")
print(f"HERO_MEASURE: failed_orders_during_slowdown[{label}]={d_fail}")
print(f"HERO_MEASURE: failed_orders_after_slowdown[{label}]={a_fail}")
print(f"HERO_MEASURE: recovery_after_slowdown[{label}/recovered]={1 if rec is not None else 0}")
if rec is not None:
    print(f"HERO_MEASURE: recovery_after_slowdown[{label}/seconds]={rec}")
print(f"HERO_MEASURE: calls_after_slowdown[{label}/calls-per-order]={cpo:.2f}")
print(f"HERO_MEASURE: calls_after_slowdown[{label}/timeouts]={timeouts}")
print(f"HERO_MEASURE: calls_after_slowdown[{label}/refused-or-reset]={conns}")
if mode == "budget":
    print(f"HERO_MEASURE: calls_after_slowdown[{label}/retries-skipped-by-budget]={denied}")
print(f"HERO_MEASURE: inventory_late_share_after[{label}]={late_pct:.1f}")
print(f"HERO_MEASURE: inventory_queue_peak_after[{label}]={nz(qpeak)}")
print(f"HERO_MEASURE: order_latency_ms[{label}/before-p50]={nz(pq(pre, 0.5))}")
print(f"HERO_MEASURE: order_latency_ms[{label}/before-p99]={nz(pq(pre, 0.99))}")
print(f"HERO_MEASURE: order_latency_ms[{label}/after-p50]={nz(p(0.5))}")
print(f"HERO_MEASURE: order_latency_ms[{label}/after-p99]={nz(p(0.99))}")
print(f"HERO_MEASURE: cpu_cores_after[{label}/inventory-avg]={fmt(cores)}")
print(f"HERO_MEASURE: cpu_cores_after[{label}/inventory-peak]={fmt(ipeak)}")
print(f"HERO_MEASURE: cpu_cores_after[{label}/orders-avg]={fmt(ocores)}")
print(f"HERO_MEASURE: cpu_cores_after[{label}/orders-peak]={fmt(opeak)}")
print(f"HERO_MEASURE: cpu_cores_after[{label}/node-peak-pct]={fmt(npeak * 100 if npeak is not None else None)}")
# The same window on 10-second boundaries, which is how Splunk APM buckets its counts, with the logs' counts inside it.
a0, a1 = -(-w0 // 10) * 10, w1 // 10 * 10
sent_a = sum(sum(per.get(s - start, (0, 0))) for s in range(a0, a1))
handled_a = sum(int(r[1]) for r in inv if a0 <= int(r[0]) < a1)
print(f"{label}: on 10 s boundaries, {a0} to {a1}: orders sent {sent_a}, inventory finished {handled_a}")
print(f"WINDOW {label} {a0} {a1} {sent_a} {handled_a} {1 if rec is not None else 0}")
PY
