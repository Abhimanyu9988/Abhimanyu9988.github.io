#!/usr/bin/env bash
# The free check, on a GitHub runner with Docker, before AWS is paid for. It runs the lab's real code (the same files the
# manifests carry) in python:3.12-slim with the pinned packages and zero-code OpenTelemetry, both retry modes, with the
# same load and slowdown. Kubernetes and Splunk are not tested here. The naive result is printed, not asserted: that is
# the lab's finding. The budget run must recover, because that is the fix the article shows.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p c/app c/load c/venv

python3 - <<'PY'
import re
def block(path, key):
    text = open(path).read()
    m = re.search(rf"^  {re.escape(key)}: \|\n((?:    .*\n|\n)+)", text, re.M)
    return "\n".join(line[4:] for line in m[1].splitlines()) + "\n"
for k in ("requirements.txt", "inventory.py", "orders.py"):
    open(f"c/app/{k}", "w").write(block("k8s/00-build.yaml", k))
open("c/load/load.py", "w").write(block("k8s/20-load.yaml", "load.py"))
PY
IMAGE=$(grep -m1 -oE 'python:[^ "]+' k8s/10-app.yaml)
docker network create lab >/dev/null

echo "== One virtualenv with the pinned packages, as the build Job makes it"
docker run --rm -v "$PWD/c/app:/app:ro" -v "$PWD/c/venv:/lab/venv" "$IMAGE" sh -c '
  python -m venv /lab/venv && /lab/venv/bin/pip install -q --no-cache-dir -r /app/requirements.txt &&
  /lab/venv/bin/pip freeze | grep -iE "^(flask|waitress|requests|opentelemetry-(distro|sdk|instrumentation-flask|instrumentation-requests))=="'

# A tiny OTLP sink stands in for the Collector: it answers 200 and counts the trace exports it receives.
cat > c/sink.py <<'PY'
import http.server, threading
n = {"posts": 0}
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0))); n["posts"] += 1
        self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
    def do_GET(self):
        b = str(n["posts"]).encode(); self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("0.0.0.0", 4318), H).serve_forever()
PY
OTEL="-e OTEL_EXPORTER_OTLP_ENDPOINT=http://sink:4318 -e OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf -e OTEL_METRICS_EXPORTER=otlp -e OTEL_METRIC_EXPORT_INTERVAL=10000 -e OTEL_SEMCONV_STABILITY_OPT_IN=http -e OTEL_LOGS_EXPORTER=none -e PYTHONUNBUFFERED=1"
start() {   # service, extra env...
  local s=$1; shift
  docker rm -f "$s" >/dev/null 2>&1 || true
  docker run -d --name "$s" --network lab -v "$PWD/c/app:/lab/app:ro" -v "$PWD/c/venv:/lab/venv:ro" $OTEL -e "OTEL_SERVICE_NAME=$s" "$@" \
    "$IMAGE" /lab/venv/bin/opentelemetry-instrument /lab/venv/bin/python "/lab/app/$s.py" >/dev/null
}
ready() {   # service, port
  for i in $(seq 1 60); do docker exec "$1" /lab/venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:$2/healthz', timeout=2)" 2>/dev/null && return 0; sleep 1; done
  docker logs "$1" 2>&1 | tail -30; echo "CHECK FAILED: $1 didn't start"; exit 1
}

run() {   # run label (naive-1, budget-1), first order
  echo "== $1"
  docker rm -f sink >/dev/null 2>&1 || true
  docker run -d --name sink --network lab -v "$PWD/c/sink.py:/sink.py:ro" "$IMAGE" python /sink.py >/dev/null
  start inventory -e THREADS=4 -e WORK_MS=40
  start orders -e THREADS=400 -e INVENTORY_URL=http://inventory:8081 -e "RETRY_MODE=${1%-*}"
  ready inventory 8081; ready orders 8080
  docker run --rm --name load --network lab -v "$PWD/c/load:/load:ro" -e RATE=40 -e LOAD_SECONDS=110 -e "FIRST=$2" \
    -e ORDERS_HOST=orders -e INVENTORY_HOST=inventory -e SLOW_AT=20 -e SLOW_SECONDS=20 -e SLOW_MS=2000 "$IMAGE" python /load/load.py > "c/load-$1.txt"
  docker logs orders > "c/orders-$1.log" 2>&1
  docker logs inventory > "c/inventory-$1.log" 2>&1
  sleep 6   # one more export interval
  posts=$(docker exec sink python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:4318/').read().decode())")
  echo "$1: OTLP exports (traces and metrics) the sink received from both services: $posts"
  [ "${posts:-0}" -gt 0 ] || { echo "CHECK FAILED: zero-code OpenTelemetry exported nothing"; exit 1; }
  docker rm -f orders inventory sink >/dev/null
  bash analyze.sh "$1" "c/load-$1.txt" "c/orders-$1.log" "c/inventory-$1.log" | tee "c/report-$1.txt"
  grep -q "orders_success_before_pct\[$1\]=\(9[5-9]\|100\)" "c/report-$1.txt" || { echo "CHECK FAILED: the $1 run started unhealthy"; exit 1; }
}

run naive-1 1
run budget-1 10001
grep -q 'recovery_after_slowdown\[budget-1/recovered\]=1' c/report-budget-1.txt || { echo "CHECK FAILED: the budget run didn't recover within 70 s"; exit 1; }
grep -q 'failed_orders_after_slowdown\[naive-1\]=[1-9]' c/report-naive-1.txt || { echo "CHECK FAILED: the naive run had no failures after the slowdown, so there is nothing to show"; exit 1; }
grep -q 'inventory_late_share_after\[naive-1\]=' c/report-naive-1.txt || { echo "CHECK FAILED: the late-work count is missing"; exit 1; }
echo "CHECK PASSED"
