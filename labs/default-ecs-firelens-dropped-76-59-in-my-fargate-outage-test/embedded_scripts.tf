# Helper programs live here as Terraform locals (no separate .py files).

locals {
  # HTTP sink: port 8080 = ingest (can be stopped = listener closed), port 8081 = admin/report.
  sink_py = <<PY
import array, json, re, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

TOKEN = sys.argv[1]
MAXSEQ = 400000
RX = re.compile(rb"seq=(\d+) run=")
LOCK = threading.Lock()
RUNS = {}
G = {"phase": "steady", "stop_ts": 0.0, "start_ts": 0.0}
INGEST = [None]


def new_run():
    return {"arr": array.array("d", [0.0]) * MAXSEQ, "n_records": 0, "n_dups": 0,
            "last_arrival": 0.0, "stop_seq": None, "start_seq": None,
            "seq": 0, "total": None, "stats": []}


def get_run(name):
    if name not in RUNS:
        RUNS[name] = new_run()
    return RUNS[name]


def read_body(h):
    te = h.headers.get("Transfer-Encoding", "")
    if "chunked" in te.lower():
        out = b""
        while True:
            line = h.rfile.readline().strip()
            n = int(line.split(b";")[0] or b"0", 16)
            if n == 0:
                h.rfile.readline()
                break
            out += h.rfile.read(n)
            h.rfile.readline()
        return out
    n = int(h.headers.get("Content-Length", "0") or 0)
    return h.rfile.read(n) if n else b""


class Ingest(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = read_body(self)
        run = self.path.split("?")[0].rstrip("/").split("/")[-1]
        now = time.time()
        seqs = [int(x) for x in RX.findall(body)]
        with LOCK:
            r = get_run(run)
            arr = r["arr"]
            for s in seqs:
                if s < MAXSEQ:
                    r["n_records"] += 1
                    if arr[s] == 0.0:
                        arr[s] = now
                    else:
                        r["n_dups"] += 1
            if seqs:
                r["last_arrival"] = now
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    do_PUT = do_POST


def start_ingest():
    srv = ThreadingHTTPServer(("0.0.0.0", 8080), Ingest)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    INGEST[0] = srv


def stop_ingest():
    srv = INGEST[0]
    if srv is not None:
        srv.shutdown()
        srv.server_close()
        INGEST[0] = None


def mib(b):
    return None if b is None else round(b / 1048576.0, 1)


def peak(stats, phase, idx):
    v = [s[idx] for s in stats if s[0] == phase and s[idx] is not None]
    return max(v) if v else None


def report(name):
    with LOCK:
        r = RUNS.get(name)
        if r is None:
            return {"run": name, "error": "unknown run"}
        arr = r["arr"]
        top = r["total"] if r["total"] is not None else r["seq"]
        top = min(top, MAXSEQ - 1)
        seqs = arr[1:top + 1]
        delivered = sum(1 for x in seqs if x > 0.0)
        out = {"run": name, "phase": G["phase"], "generator_done": r["total"] is not None,
               "generated": top, "delivered": delivered, "lost": top - delivered,
               "lost_pct": round(100.0 * (top - delivered) / top, 2) if top else None,
               "n_records": r["n_records"], "n_dups": r["n_dups"]}
        ss = r["stop_seq"]
        st = G["start_ts"]
        if ss is not None:
            ss = min(ss, top)
            pre = arr[1:ss + 1]
            win = arr[ss + 1:top + 1]
            out["stop_seq"] = ss
            out["generated_pre_outage"] = len(pre)
            out["lost_pre_outage"] = sum(1 for x in pre if x == 0.0)
            out["generated_in_outage"] = len(win)
            out["lost_in_outage"] = sum(1 for x in win if x == 0.0)
            out["delivered_after_recovery"] = sum(1 for x in win if st > 0 and x >= st)
            out["delivered_before_recovery"] = sum(1 for x in win if x > 0.0 and not (st > 0 and x >= st))
        out["drain_time_s"] = round(max(0.0, r["last_arrival"] - st), 1) if st > 0 else None
        stats = r["stats"]
        out["mem_baseline_mib"] = mib(peak(stats, "steady", 1))
        out["mem_peak_outage_mib"] = mib(peak(stats, "outage", 1))
        out["mem_peak_drain_mib"] = mib(peak(stats, "recovered", 1))
        out["mem_raw_peak_outage_mib"] = mib(peak(stats, "outage", 2))
        out["max_write_ms_steady"] = peak(stats, "steady", 3)
        out["max_write_ms_outage"] = peak(stats, "outage", 3)
        out["max_write_ms_recovered"] = peak(stats, "recovered", 3)
        return out


def jdump(h, obj, code=200):
    b = json.dumps(obj).encode()
    h.send_response(code)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(b)))
    h.end_headers()
    h.wfile.write(b)


class Admin(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def handle_any(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        n = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(n) if n else b""
        if p == "/health":
            return jdump(self, {"ok": True, "ingest_up": INGEST[0] is not None})
        if q.get("t", [""])[0] != TOKEN:
            return jdump(self, {"error": "forbidden"}, 403)
        run = q.get("run", [""])[0]
        if p == "/reset":
            with LOCK:
                RUNS[run] = new_run()
            return jdump(self, {"reset": run})
        if p == "/phase":
            with LOCK:
                G["phase"] = q.get("set", ["steady"])[0]
                G["stop_ts"] = 0.0
                G["start_ts"] = 0.0
            return jdump(self, {"phase": G["phase"]})
        if p == "/stop":
            stop_ingest()
            with LOCK:
                G["phase"] = "outage"
                G["stop_ts"] = time.time()
                for r in RUNS.values():
                    r["stop_seq"] = r["seq"]
            return jdump(self, {"ingest_listener": "closed", "stop_ts": G["stop_ts"]})
        if p == "/start":
            with LOCK:
                G["start_ts"] = time.time()
                G["phase"] = "recovered"
                for r in RUNS.values():
                    r["start_seq"] = r["seq"]
            start_ingest()
            return jdump(self, {"ingest_listener": "open", "start_ts": G["start_ts"]})
        if p == "/stat":
            d = json.loads(body.decode() or "{}")
            with LOCK:
                r = get_run(d.get("run", run))
                r["seq"] = max(r["seq"], int(d.get("seq", 0)))
                if d.get("done"):
                    r["total"] = int(d.get("seq", 0))
                r["stats"].append((G["phase"], d.get("mem"), d.get("memraw"), d.get("max_write_ms")))
                ph = G["phase"]
            return jdump(self, {"phase": ph})
        if p == "/report":
            return jdump(self, report(run))
        return jdump(self, {"error": "unknown path"}, 404)

    do_GET = handle_any
    do_POST = handle_any


if __name__ == "__main__":
    start_ingest()
    ThreadingHTTPServer(("0.0.0.0", 8081), Admin).serve_forever()
PY

  # Log generator: fixed rate to stdout (awsfirelens driver), measures its own write latency,
  # reports progress + router memory to the sink admin port (NOT through Fluent Bit).
  # Stops generating when the sink reports that the endpoint is back.
  gen_py = <<PY
import json, os, sys, threading, time, urllib.request

RUN = os.environ["RUN"]
ADMIN = os.environ["SINK_ADMIN"]
TOKEN = os.environ["SINK_TOKEN"]
RATE = int(os.environ.get("RATE", "200"))
MAX_SECONDS = int(os.environ.get("MAX_SECONDS", "900"))
META = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
PAD = "x" * 120
LOCK = threading.Lock()
ST = {"seq": 0, "max": 0.0, "inflight": None, "phase": "steady", "done": False}
ROUTER = [None]


def http_json(url, data=None, timeout=1.5):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def find_router():
    try:
        t = http_json(META + "/task")
        for c in t["Containers"]:
            if c["Name"] == "log_router":
                return c["DockerId"]
    except Exception:
        pass
    return None


def mem():
    if not META:
        return None, None
    if ROUTER[0] is None:
        ROUTER[0] = find_router()
    if ROUTER[0] is None:
        return None, None
    try:
        s = http_json(META + "/task/stats", timeout=2.5)[ROUTER[0]]["memory_stats"]
        usage = s["usage"]
        stt = s.get("stats", {})
        inactive = stt.get("total_inactive_file", stt.get("inactive_file", 0))
        return usage - inactive, usage
    except Exception:
        return None, None


def reporter():
    while True:
        time.sleep(1)
        now = time.perf_counter()
        with LOCK:
            m = ST["max"]
            if ST["inflight"] is not None:
                m = max(m, (now - ST["inflight"]) * 1000.0)
            ST["max"] = 0.0
            seq = ST["seq"]
            done = ST["done"]
        ws, raw = mem()
        body = {"run": RUN, "seq": seq, "max_write_ms": round(m, 2), "mem": ws, "memraw": raw, "done": done}
        try:
            r = http_json(ADMIN + "/stat?t=" + TOKEN, body)
            ST["phase"] = r.get("phase", "steady")
        except Exception:
            pass


def writer():
    interval = 1.0 / RATE
    t0 = time.perf_counter()
    nxt = t0
    deadline = t0 + MAX_SECONDS
    seq = 0
    while time.perf_counter() < deadline and ST["phase"] != "recovered":
        now = time.perf_counter()
        if now < nxt:
            time.sleep(nxt - now)
        elif now - nxt > 0.5:
            nxt = now
        seq += 1
        line = "seq=%d run=%s ts=%.3f %s\n" % (seq, RUN, time.time(), PAD)
        a = time.perf_counter()
        with LOCK:
            ST["inflight"] = a
        sys.stdout.write(line)
        sys.stdout.flush()
        b = time.perf_counter()
        with LOCK:
            ST["inflight"] = None
            ST["seq"] = seq
            d = (b - a) * 1000.0
            if d > ST["max"]:
                ST["max"] = d
        nxt += interval
    with LOCK:
        ST["done"] = True


threading.Thread(target=reporter, daemon=True).start()
writer()
while True:
    time.sleep(60)
PY
}
