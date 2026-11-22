#!/usr/bin/env bash
# The free check, on a GitHub runner with Docker, before AWS is paid for. It runs the lab's real code (the files the
# manifests carry): two orders containers with the pinned packages and zero-code OpenTelemetry, the real load, and two
# Collector containers (contrib 0.165.0) that each scrape both orders containers with the Prometheus receiver, the way two
# agents with values-everywhere.yaml do. Their exporter writes to a file instead of Splunk.
# It proves, before AWS: each pod counts every order it took; /scrapes names every scraper; both Collectors scrape both
# pods; the Prometheus counter carries the run label and adds up to twice the orders across two scrapers; OpenTelemetry's
# own POST count, pushed once, adds up to the orders, with the run on its resource. Node scoping needs Kubernetes, so the
# rehearsal and the paid lab test that.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p c/app c/load c/conf c/out c/venv
chmod 777 c/out

python3 - <<'PY'
import re
def block(path, key):
    text = open(path).read()
    m = re.search(rf"^  {re.escape(key)}: \|\n((?:    .*\n|\n)+)", text, re.M)
    return "\n".join(line[4:] for line in m[1].splitlines()) + "\n"
for k in ("requirements.txt", "orders.py"):
    open(f"c/app/{k}", "w").write(block("k8s/00-build.yaml", k))
open("c/load/load.py", "w").write(block("k8s/20-load.yaml", "load.py"))
PY
IMAGE=$(grep -m1 -oE 'python:[^ "]+' k8s/10-app.yaml)
COLLECTOR=otel/opentelemetry-collector-contrib:0.165.0
for n in 1 2; do
  cat > "c/conf/col$n.yaml" <<YAML
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318
  prometheus:
    config:
      scrape_configs:
        - job_name: orders
          scrape_interval: 10s
          static_configs:
            - targets: ["orders-a:8080", "orders-b:8080"]
exporters:
  file:
    path: /out/col$n.json
service:
  pipelines:
    metrics:
      receivers: [otlp, prometheus]
      exporters: [file]
YAML
done
docker network create lab >/dev/null

echo "== One virtualenv with the pinned packages, as the build Jobs make it"
docker run --rm -v "$PWD/c/app:/app:ro" -v "$PWD/c/venv:/lab/venv" "$IMAGE" sh -c '
  python -m venv /lab/venv && /lab/venv/bin/pip install -q --no-cache-dir -r /app/requirements.txt &&
  /lab/venv/bin/pip freeze | grep -iE "^(flask|waitress|prometheus.client|opentelemetry-(distro|sdk|instrumentation-flask))=="'
docker pull -q "$COLLECTOR" >/dev/null

for n in 1 2; do
  docker run -d --name "col$n" --network lab -v "$PWD/c/conf:/conf:ro" -v "$PWD/c/out:/out" "$COLLECTOR" "--config=/conf/col$n.yaml" >/dev/null
done
RUN=check-1
for p in orders-a orders-b; do
  # Both push OTLP to col1 only, as each pod pushes to the one agent on its node.
  docker run -d --name "$p" --network lab -v "$PWD/c/app:/lab/app:ro" -v "$PWD/c/venv:/lab/venv:ro" \
    -e POD_NAME="$p" -e LAB_RUN="$RUN" -e OTEL_SERVICE_NAME=orders -e OTEL_EXPORTER_OTLP_ENDPOINT=http://col1:4318 \
    -e OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf -e "OTEL_RESOURCE_ATTRIBUTES=deployment.environment=check,k8s.pod.name=$p,run=$RUN" \
    -e OTEL_TRACES_EXPORTER=none -e OTEL_METRICS_EXPORTER=otlp -e OTEL_METRIC_EXPORT_INTERVAL=10000 -e OTEL_LOGS_EXPORTER=none \
    -e OTEL_SEMCONV_STABILITY_OPT_IN=http -e OTEL_PYTHON_FLASK_EXCLUDED_URLS=metrics,scrapes,healthz -e PYTHONUNBUFFERED=1 \
    "$IMAGE" /lab/venv/bin/opentelemetry-instrument /lab/venv/bin/python /lab/app/orders.py >/dev/null
done
for p in orders-a orders-b; do
  for i in $(seq 1 60); do docker exec "$p" /lab/venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz', timeout=2)" 2>/dev/null && break; sleep 1; done
done
sleep 25
echo "== The load: 20 s at 5 orders a second to each pod"
docker run -d --name load-a --network lab -v "$PWD/c/load:/load:ro" -e RATE=5 -e LOAD_SECONDS=20 -e RUN=$RUN -e FIRST=1 -e ORDERS_HOST=orders-a "$IMAGE" python /load/load.py >/dev/null
docker run --rm --network lab -v "$PWD/c/load:/load:ro" -e RATE=5 -e LOAD_SECONDS=20 -e RUN=$RUN -e FIRST=1001 -e ORDERS_HOST=orders-b "$IMAGE" python /load/load.py > c/load-b.txt
docker wait load-a >/dev/null; docker logs load-a > c/load-a.txt
sleep 22
for p in orders-a orders-b; do
  docker exec "$p" /lab/venv/bin/python -c 'import json, urllib.request
d = json.load(urllib.request.urlopen("http://localhost:8080/scrapes", timeout=5))
print(d["pod"], d["orders"], " ".join(f"{ip}:{n}" for ip, n in sorted(d["scrapers"].items())))' | tee -a c/pods.txt
done
docker rm -f col1 col2 >/dev/null; docker rm -f orders-a orders-b load-a >/dev/null   # Collectors first: no staleness marker after the pods go

python3 - "$RUN" <<'PY'
import json, re, sys
run = sys.argv[1]
fail = lambda m: (print(f"CHECK FAILED: {m}"), sys.exit(1))
ok = sum(int(re.search(r"ok=(\d+)", open(f"c/load-{x}.txt").read())[1]) for x in "ab")
pods = [l.split() for l in open("c/pods.txt")]
pod_orders = sum(int(p[1]) for p in pods)
print(f"orders ok {ok}; the pods counted {pod_orders}; scrapers: {[(p[0], p[2:]) for p in pods]}")
pod_orders == ok or fail("the pods didn't count every order")
all(len([s for s in p[2:] if int(s.split(':')[1]) >= 2]) == 2 for p in pods) or fail("each pod should show two scrapers")
def points(path):
    out = []
    for line in open(path):
        for rm in json.loads(line).get("resourceMetrics", []):
            res = {a["key"]: list(a["value"].values())[0] for a in rm["resource"].get("attributes", [])}
            for sm in rm.get("scopeMetrics", []):
                for m in sm.get("metrics", []):
                    body = m.get("sum") or m.get("histogram") or m.get("gauge") or {}
                    for dp in body.get("dataPoints", []):
                        if int(dp.get("flags", 0)) & 1 or not any(k in dp for k in ("asDouble", "asInt", "count")):
                            continue      # a staleness marker carries no value
                        attrs = {a["key"]: list(a["value"].values())[0] for a in dp.get("attributes", [])}
                        val = dp.get("asDouble", dp.get("asInt", dp.get("count", 0)))
                        out.append((m["name"], res, attrs, float(val), int(dp.get("timeUnixNano", 0))))
    return out
prom_total, otel_total, names = 0, 0, set()
for col in ("col1", "col2"):
    pts = points(f"c/out/{col}.json")
    last = {}
    for name, res, attrs, val, t in pts:
        if name.startswith("orders_created") and attrs.get("run") == run:
            names.add(name)
            key = (name, res.get("service.instance.id"), res.get("server.address"), json.dumps(attrs, sort_keys=True))
            if t >= last.get(key, (0, 0))[0]: last[key] = (t, val)
        if name == "http.server.request.duration" and res.get("run") == run:
            key = ("otel", res.get("k8s.pod.name"), json.dumps(attrs, sort_keys=True))
            if t >= last.get(key, (0, 0))[0]: last[key] = (t, val)
    prom = sum(v for k, (t, v) in last.items() if k[0] != "otel"); otel = sum(v for k, (t, v) in last.items() if k[0] == "otel")
    print(f"{col}: Prometheus counter {prom:.0f} over {sum(1 for k in last if k[0] != 'otel')} series; OpenTelemetry POST count {otel:.0f}")
    prom_total += prom; otel_total += otel
print(f"counter name the Prometheus receiver gave: {sorted(names)}; across both Collectors: Prometheus {prom_total:.0f}, OpenTelemetry {otel_total:.0f}")
names or fail("no orders_created series carried the run label")
prom_total == 2 * ok or fail(f"two scrapers should give twice the orders, got {prom_total:.0f} for {ok}")
otel_total == ok or fail(f"OpenTelemetry should count each order once, got {otel_total:.0f} for {ok}")
print("CHECK PASSED")
PY
