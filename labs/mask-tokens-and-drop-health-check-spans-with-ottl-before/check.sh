#!/usr/bin/env bash
# The free check, on a GitHub runner with Docker, before AWS is paid for. It runs the lab's real code (the same files the
# manifests carry): both services with the pinned packages and zero-code OpenTelemetry, the gateway Collector image with
# both real configs from k8s/30-gateway.yaml, the real load and the real stats script. Only the gateway's exporter changes:
# it writes what it would send to a file instead of the Splunk agent. A pinger stands in for the kubelet's /healthz checks.
# It proves, before AWS: base.yaml passes every span through with the raw tokens; named.yaml and all.yaml (contrib 0.165.0)
# parse and drop every /healthz span; all.yaml masks every token and keeps every order's 3 spans. What named.yaml leaves
# behind is printed, not asserted: that is the lab's finding.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p c/app c/load c/stats c/conf c/out c/venv
chmod 777 c/out

python3 - <<'PY'
import re
def block(path, key):
    text = open(path).read()
    m = re.search(rf"^  {re.escape(key)}: \|\n((?:    .*\n|\n)+)", text, re.M)
    return "\n".join(line[4:] for line in m[1].splitlines()) + "\n"
for k in ("requirements.txt", "inventory.py", "orders.py"):
    open(f"c/app/{k}", "w").write(block("k8s/00-build.yaml", k))
open("c/load/load.py", "w").write(block("k8s/20-load.yaml", "load.py"))
open("c/stats/gwstats.py", "w").write(block("k8s/30-gateway.yaml", "gwstats.py"))
for k in ("base.yaml", "named.yaml", "all.yaml"):
    conf = block("k8s/30-gateway.yaml", k)
    # The one change from the cluster: write what would go to the Splunk agent into a file, one JSON line per batch.
    conf = conf.replace("otlphttp:\n    endpoint: http://${env:HOST_IP}:4318", "file:\n    path: /out/spans.json")
    conf = conf.replace("exporters: [otlphttp]", "exporters: [file]")
    assert "otlphttp" not in conf and "/out/spans.json" in conf, k
    open(f"c/conf/{k}", "w").write(conf)
PY
sed -i 's/exporter="otlphttp"/exporter="file"/' c/stats/gwstats.py
IMAGE=$(grep -m1 -oE 'python:[^ "]+' k8s/10-app.yaml)
GATEWAY=$(grep -m1 -oE 'otel/opentelemetry-collector-contrib:[0-9.]+' k8s/30-gateway.yaml)
docker network create lab >/dev/null

echo "== One virtualenv with the pinned packages, as the build Job makes it"
docker run --rm -v "$PWD/c/app:/app:ro" -v "$PWD/c/venv:/lab/venv" "$IMAGE" sh -c '
  python -m venv /lab/venv && /lab/venv/bin/pip install -q --no-cache-dir -r /app/requirements.txt &&
  /lab/venv/bin/pip freeze | grep -iE "^(flask|waitress|requests|opentelemetry-(distro|sdk|instrumentation-flask|instrumentation-requests|util-http))=="'
docker pull -q "$GATEWAY" >/dev/null

OTEL="-e OTEL_EXPORTER_OTLP_ENDPOINT=http://gateway:4318 -e OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf -e OTEL_METRICS_EXPORTER=otlp -e OTEL_METRIC_EXPORT_INTERVAL=10000 -e OTEL_SEMCONV_STABILITY_OPT_IN=http/dup -e OTEL_LOGS_EXPORTER=none -e PYTHONUNBUFFERED=1 -e OTEL_RESOURCE_ATTRIBUTES=deployment.environment=check"
svc() {   # service, extra env...
  local s=$1; shift
  docker rm -f "$s" >/dev/null 2>&1 || true
  docker run -d --name "$s" --network lab -v "$PWD/c/app:/lab/app:ro" -v "$PWD/c/venv:/lab/venv:ro" $OTEL -e "OTEL_SERVICE_NAME=$s" "$@" \
    "$IMAGE" /lab/venv/bin/opentelemetry-instrument /lab/venv/bin/python "/lab/app/$s.py" >/dev/null
}
ready() {   # container, port
  for i in $(seq 1 60); do docker exec "$1" /lab/venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:$2/healthz', timeout=2)" 2>/dev/null && return 0; sleep 1; done
  docker logs "$1" 2>&1 | tail -30; echo "CHECK FAILED: $1 didn't start"; exit 1
}

run() {   # run label, config
  echo "== $1 with $2"
  rm -f c/out/spans.json
  docker rm -f gateway pinger >/dev/null 2>&1 || true
  docker run -d --name gateway --network lab -v "$PWD/c/conf:/conf:ro" -v "$PWD/c/out:/out" "$GATEWAY" "--config=/conf/$2" >/dev/null
  for i in $(seq 1 30); do docker logs gateway 2>&1 | grep -q 'Everything is ready' && break; sleep 1; done
  docker logs gateway 2>&1 | grep -q 'Everything is ready' || { docker logs gateway 2>&1 | tail -30; echo "CHECK FAILED: the gateway didn't start with $2"; exit 1; }
  svc inventory -e WORK_MS=20
  svc orders -e INVENTORY_URL=http://inventory:8081
  ready inventory 8081; ready orders 8080
  # The kubelet's checks: /healthz on both services every 2 s, plus every 10 s.
  docker run -d --name pinger --network lab "$IMAGE" python -c '
import time, urllib.request
for n in range(400):
    for h in ("orders:8080", "inventory:8081"):
        try: urllib.request.urlopen(f"http://{h}/healthz", timeout=2).read()
        except Exception: pass
        if n % 5 == 0:
            try: urllib.request.urlopen(f"http://{h}/healthz", timeout=2).read()
            except Exception: pass
    time.sleep(2)' >/dev/null
  docker run --rm --network lab -v "$PWD/c/load:/load:ro" -e RATE=10 -e LOAD_SECONDS=25 -e "RUN=$1" -e "FIRST=$3" -e ORDERS_HOST=orders \
    "$IMAGE" python /load/load.py > "c/load-$1.txt"
  sleep 14   # one more export from each service and the gateway's batch
  docker run --rm --network lab -v "$PWD/c/stats:/stats:ro" "$IMAGE" python /stats/gwstats.py | tee "c/gw-$1.txt"
  docker rm -f pinger orders inventory >/dev/null
  sleep 3
  docker rm -f gateway >/dev/null
  cp c/out/spans.json "c/spans-$1.json"
  python3 - "$1" <<'PY'
import json, re, sys
run = sys.argv[1]
spans = []
for line in open(f"c/spans-{run}.json"):
    for rs in json.loads(line).get("resourceSpans", []):
        svc = next((a["value"].get("stringValue") for a in rs["resource"]["attributes"] if a["key"] == "service.name"), "?")
        for ss in rs.get("scopeSpans", []):
            for s in ss.get("spans", []):
                spans.append((svc, s["traceId"], s["name"], json.dumps(s)))
samples = [dict(kv.split("=", 1) for kv in l.split()[1:]) for l in open(f"c/load-{run}.txt") if l.startswith("SAMPLE ")]
health = sum(1 for _, _, name, _ in spans if name == "GET /healthz")
raw = sum(1 for _, _, _, body in spans if re.search(r"token=tok_", body))
masked = sum(1 for _, _, _, body in spans if "token=REDACTED" in body)
per_trace = [sum(1 for _, t, _, _ in spans if t == s["trace"]) for s in samples]
sent = re.search(r"sent=(\d+) ok=(\d+)", open(f"c/load-{run}.txt").read())
print(f"CHECK {run}: spans exported {len(spans)}; /healthz spans {health}; spans with a raw token {raw}; with token=REDACTED {masked}; "
      f"sample traces {len(samples)}, spans each {min(per_trace)}-{max(per_trace)}; orders sent {sent[1]}, ok {sent[2]}")
open(f"c/result-{run}.txt", "w").write(f"{len(spans)} {health} {raw} {masked} {min(per_trace)} {sent[1]} {sent[2]}\n")
PY
}

run before-1 base.yaml 1
run named-1 named.yaml 20001
run all-1 all.yaml 40001
fail() { echo "CHECK FAILED: $*"; exit 1; }
for r in before-1 named-1 all-1; do
  read -r spans health raw masked min sent ok < "c/result-$r.txt"
  [ "$ok" = "$sent" ] || fail "$r: orders failed"
  [ "$min" -ge 3 ] || fail "$r: a sample trace lost spans"
  eval "health_${r%-1}=$health raw_${r%-1}=$raw masked_${r%-1}=$masked sent_${r%-1}=$sent"
done
[ "$health_before" -gt 0 ] || fail "base.yaml exported no /healthz span, so the filter would have nothing to drop"
[ "$raw_before" -gt 0 ] || fail "base.yaml exported no raw token, so there is nothing to mask"
[ "$health_named" = 0 ] && [ "$health_all" = 0 ] || fail "an OTTL config still exported /healthz spans"
[ "$raw_all" = 0 ] || fail "all.yaml still exported $raw_all spans with a raw token"
[ "$masked_all" -ge $(( sent_all * 3 )) ] || fail "all.yaml masked $masked_all spans for $sent_all orders, expected 3 an order"
echo "named.yaml left the raw token in $raw_named spans (printed, not asserted)"
grep -q '^GW filter_in=[1-9]' c/gw-all-1.txt || fail "the stats script read no filter counts"
grep -q '^GW spans_received=[1-9]' c/gw-before-1.txt || fail "the stats script read no gateway counts"
echo "CHECK PASSED"
