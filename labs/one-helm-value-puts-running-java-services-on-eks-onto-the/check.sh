#!/usr/bin/env bash
# The free check, run on a GitHub runner with Docker before AWS is paid for. It renders the real chart, then runs the real OBI
# and Collector images against the lab's own running services. Kubernetes and Splunk don't run here, and the rehearsal covers those.
set -euo pipefail
cd "$(dirname "$0")"
source ./lib.sh
mkdir -p c/src c/out c/init c/load

python3 - <<'PY'
import re
def block(path, key):
    text = open(path).read()
    m = re.search(rf"^  {re.escape(key)}: \|\n((?:    .*\n|\n)+)", text, re.M)
    return "\n".join(line[4:] for line in m[1].splitlines()) + "\n"
open("c/src/pom.xml", "w").write(block("k8s/00-build.yaml", "pom.xml"))
open("c/src/App.java", "w").write(block("k8s/00-build.yaml", "App.java"))
open("c/init/init.sql", "w").write(block("k8s/10-app.yaml", "init.sql"))
open("c/load/load.py", "w").write(block("k8s/20-load.yaml", "load.py"))
PY

echo "== What the setting changes, rendered from the chart"
CHART_VERSION=$(grep -m1 -oE -- '--version [0-9.]+' reproduce.sh | cut -d' ' -f2)
helm repo add splunk-otel-collector-chart https://signalfx.github.io/splunk-otel-collector-chart >/dev/null
helm repo update >/dev/null
render() {
  helm template lab splunk-otel-collector-chart/splunk-otel-collector --version "$CHART_VERSION" --namespace splunk \
    --set splunkObservability.realm=us0 --set splunkObservability.accessToken=not-a-token --set clusterName=check \
    --set environment=check --set cloudProvider=aws --set distribution=eks -f k8s/collector-tap.yaml "$@"
}
render > c/off.yaml
render --set obi.enabled=true > c/on.yaml
render --set obi.enabled=true --set-string obi.env.OTEL_EBPF_BPF_CONTEXT_PROPAGATION=headers > c/headers.yaml
[ "$(grep -c 'ebpf-instrument:' c/off.yaml || true)" -eq 0 ] || { echo "CHECK FAILED: OBI renders while the setting is off"; exit 1; }
OBI_IMAGE=$(grep -m1 -oE 'ghcr\.io/[^ "]*ebpf-instrument:[^ "]+' c/on.yaml || true)
[ -n "$OBI_IMAGE" ] || { echo "CHECK FAILED: obi.enabled=true rendered no OBI image"; exit 1; }
grep -q 'OTEL_EBPF_BPF_CONTEXT_PROPAGATION' c/headers.yaml || { echo "CHECK FAILED: the propagation value did not render"; exit 1; }
grep -q 'context_propagation\|OTEL_EBPF_BPF_CONTEXT_PROPAGATION' c/on.yaml && echo "propagation set by the one setting: yes" || echo "propagation set by the one setting: no"
grep -q 'traces/tap:' c/on.yaml && grep -q 'otlp_http' c/on.yaml || { echo "CHECK FAILED: the tap or the Splunk export is missing from the agent config"; exit 1; }
COL_IMAGE=$(grep -m1 -oE 'quay\.io/signalfx/splunk-otel-collector:[^ "]+' c/on.yaml || true)
echo "chart $CHART_VERSION, OBI image $OBI_IMAGE, Collector image $COL_IMAGE"
grep -E '^ +(hostPID|hostNetwork|privileged): ' c/on.yaml | sort -u
echo "objects the setting adds:"
diff <(grep -E '^kind: ' c/off.yaml | sort) <(grep -E '^kind: ' c/on.yaml | sort) | grep '^>' || true

IMAGE=$(grep -m1 -oE 'maven:[^ ]+' k8s/10-app.yaml)
PY_IMAGE=$(grep -m1 -oE 'python:[^ ]+' k8s/20-load.yaml)
PG_IMAGE=$(grep -m1 -oE 'postgres:[^ ]+' k8s/10-app.yaml)
docker network create lab >/dev/null

echo "== Building the jar"
docker run --rm -v "$PWD/c/src:/src:ro" -v "$PWD/c/out:/lab" "$IMAGE" sh -c '
  set -e; mkdir -p /w/src/main/java/lab; cp /src/pom.xml /w/; cp /src/App.java /w/src/main/java/lab/; cd /w
  mvn -q -B -DskipTests package; cp target/app.jar /lab/app.jar'
ls -l c/out

docker run -d --name postgres --network lab -e POSTGRES_PASSWORD=lab -v "$PWD/c/init:/docker-entrypoint-initdb.d:ro" "$PG_IMAGE" >/dev/null
for i in $(seq 1 30); do docker exec postgres pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
sleep 3

start() {
  docker run -d --name "$1" --network lab -v "$PWD/c/out:/lab:ro" --entrypoint java -e "OTEL_SERVICE_NAME=$1" \
    -e SPRING_DATASOURCE_URL=jdbc:postgresql://postgres:5432/postgres -e SPRING_DATASOURCE_USERNAME=postgres \
    -e SPRING_DATASOURCE_PASSWORD=lab -e INVENTORY_URL=http://inventory:8080 \
    "$IMAGE" -Xms256m -Xmx256m -jar /lab/app.jar >/dev/null
}
ready() {
  for i in $(seq 1 60); do docker logs "$1" 2>&1 | grep -q 'process running for' && break; sleep 1; done
  docker logs "$1" 2>&1 | grep -q 'process running for' || { docker logs "$1" 2>&1 | tail -30; echo "CHECK FAILED: $1 didn't start"; exit 1; }
}
load() { docker run --rm --name load --network lab -v "$PWD/c/load:/load:ro" -e SECONDS_TOTAL=15 -e SECONDS_WARM=2 "$PY_IMAGE" python /load/load.py; }
state() { docker inspect -f '{{.Name}} {{.Id}} {{.State.StartedAt}} {{.RestartCount}}' orders inventory postgres; }
agents() { docker exec "$1" grep -c libinstrument /proc/1/maps || true; }

echo "== The services start with OBI off, and they keep running from here on"
start inventory
start orders
ready inventory
ready orders
load | tee c/off.txt
grep -q 'errors=0' c/off.txt || { docker logs orders 2>&1 | tail -30; echo "CHECK FAILED: requests failed with OBI off"; exit 1; }
state > c/state-before.txt
echo "Java agents loaded before OBI: orders $(agents orders), inventory $(agents inventory)"

cat > c/tap.yaml <<'YAML'
receivers:
  otlp:
    protocols:
      http:
        endpoint: 127.0.0.1:4318
exporters:
  debug/tap:
    verbosity: normal
    use_internal_logger: false
service:
  pipelines:
    traces/tap:
      receivers: [otlp]
      exporters: [debug/tap]
YAML
chmod 644 c/tap.yaml
KNOWN=4bf92f3577b34da6a3ce929d0e0e4736
known() { docker run --rm --network lab "$PY_IMAGE" python -c "import urllib.request as u; u.urlopen(u.Request('http://orders:8080/orders/1', headers={'traceparent': '00-$KNOWN-00f067aa0ba902b7-01'}), timeout=5).read()" || true; }

obi_run() {   # label, then any -e settings. OBI runs as the chart runs it, exporting to the same tap the lab reads on EKS
  local label=$1 i n
  shift
  docker run -d --name tap --network=host -v "$PWD/c/tap.yaml:/etc/tap.yaml:ro" "$COL_IMAGE" --config=/etc/tap.yaml >/dev/null || true
  sleep 3
  docker run -d --name obi --privileged --pid=host --network=host -v /sys/fs/cgroup:/sys/fs/cgroup:ro \
    -e OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 -e OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
    -v /sys/kernel/tracing:/sys/kernel/tracing -v /sys/kernel/security:/sys/kernel/security:ro \
    -e OTEL_EBPF_TRACE_PRINTER=text -e OTEL_EBPF_OPEN_PORT=8080 -e OTEL_EBPF_LOG_LEVEL=info "$@" "$OBI_IMAGE" >/dev/null
  n=0
  for i in $(seq 1 45); do
    n=$(docker logs obi 2>&1 | grep -c 'instrumenting process' || true)
    [ "$n" -ge 2 ] && break
    sleep 2
  done
  echo "JVMs OBI reports instrumenting in the $label run: $n"
  sleep 15
  load | tee "c/$label.txt" || echo "the load stopped early in the $label run"
  known
  sleep 8
  docker logs obi > "c/$label.log" 2>&1
  docker rm -f obi >/dev/null
  sleep 2
  docker logs tap 2>/dev/null | tap_parse > "c/tap-$label.txt" || true
  docker logs tap 2>&1 >/dev/null | grep -iE 'error|fatal' | cut -c1-190 | tail -3 || true
  docker rm -f tap >/dev/null 2>&1 || true
  (cd c && tap_ids "tap-$label.txt" "$label" 2>&1) || true
  echo "$label: traced under the ID the caller sent: $(grep -c "$KNOWN" "c/tap-$label.txt" || true)"
  grep -E 'instrumenting process|java agent|java injection|capabilit|level=ERROR' "c/$label.log" | cut -c1-190 | tail -6 || true
  python3 - "$label" <<'PY'
import re, sys
label = sys.argv[1]
span = re.compile(r"\) (HTTP|HTTPClient|SQLClient)\(subType=\d+\) (\S*) (\S*) (\S*).*?traceparent=\[00-([0-9a-f]{32})-")
kinds = {"orders": set(), "inventory": set(), "client": 0, "sql": 0}
for line in open(f"c/{label}.log", errors="replace"):
    m = span.search(line)
    if not m:
        continue
    kind, _, _, path, trace = m.groups()
    if kind == "HTTP" and path.startswith("/orders/"):
        kinds["orders"].add(trace)
    elif kind == "HTTP" and path.startswith("/stock/"):
        kinds["inventory"].add(trace)
    elif kind == "HTTPClient" and path.startswith("/stock/"):
        kinds["client"] += 1
    elif kind == "SQLClient":
        kinds["sql"] += 1
joined = len(kinds["orders"] & kinds["inventory"])
print(f"{label}: orders traces {len(kinds['orders'])}, inventory traces {len(kinds['inventory'])}, client spans {kinds['client']}, SQL spans {kinds['sql']}, joined {joined}")
open(f"c/{label}.counts", "w").write(f"{len(kinds['orders'])} {len(kinds['inventory'])}\n")
PY
}

echo "== OBI starts against the running services, with the chart's own settings"
obi_run default
echo "Java agents loaded after OBI: orders $(agents orders), inventory $(agents inventory)"
read -r seen_orders seen_inventory < c/default.counts
[ "$seen_orders" -gt 0 ] && [ "$seen_inventory" -gt 0 ] || { tail -30 c/default.log | cut -c1-200; echo "CHECK FAILED: OBI saw no spans from a running service"; exit 1; }

echo "== Two more runs, with the same-host shortcut off, as for services on different nodes"
obi_run crossnode -e OTEL_EBPF_BPF_DISABLE_BLACK_BOX_CP=true
obi_run headers -e OTEL_EBPF_BPF_DISABLE_BLACK_BOX_CP=true -e OTEL_EBPF_BPF_CONTEXT_PROPAGATION=headers
grep -H -o 'errors=[0-9]*' c/default.txt c/crossnode.txt c/headers.txt || true

echo "== The same containers, with no restart"
state > c/state-after.txt
cat c/state-after.txt
cmp c/state-before.txt c/state-after.txt || { echo "CHECK FAILED: a container restarted"; exit 1; }
echo "CHECK PASSED"
