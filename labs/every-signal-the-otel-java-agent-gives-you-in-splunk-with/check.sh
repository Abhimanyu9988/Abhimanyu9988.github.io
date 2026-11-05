#!/usr/bin/env bash
# The free check, run on a GitHub runner with Docker before AWS is paid for. It uses the lab's own source, images,
# agent and load script, so a build error, a bad image tag or a missing span shows up here. Kubernetes and Splunk don't.
set -euo pipefail
cd "$(dirname "$0")"
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

IMAGE=$(grep -m1 -oE 'maven:[^ ]+' k8s/10-app.yaml)
PY_IMAGE=$(grep -m1 -oE 'python:[^ ]+' k8s/20-load.yaml)
PG_IMAGE=$(grep -m1 -oE 'postgres:[^ ]+' k8s/10-app.yaml)
AGENT_URL=$(grep -m1 -oE 'https://github.com/[^"]+\.jar' k8s/00-build.yaml)
docker network create lab >/dev/null

echo "== Fetching the agent and building the jar"
docker run --rm -v "$PWD/c/out:/lab" "$PY_IMAGE" python -c "import urllib.request; urllib.request.urlretrieve('$AGENT_URL', '/lab/opentelemetry-javaagent.jar')"
docker run --rm -v "$PWD/c/src:/src:ro" -v "$PWD/c/out:/lab" "$IMAGE" sh -c '
  set -e; mkdir -p /w/src/main/java/lab; cp /src/pom.xml /w/; cp /src/App.java /w/src/main/java/lab/; cd /w
  mvn -q -B -DskipTests package; cp target/app.jar /lab/app.jar'
ls -l c/out

docker run -d --name postgres --network lab -e POSTGRES_PASSWORD=lab -v "$PWD/c/init:/docker-entrypoint-initdb.d:ro" "$PG_IMAGE" >/dev/null
for i in $(seq 1 30); do docker exec postgres pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
sleep 3

start() {   # name, JAVA_TOOL_OPTIONS
  docker run -d --name "$1" --network lab -v "$PWD/c/out:/lab:ro" --entrypoint java \
    -e SPRING_DATASOURCE_URL=jdbc:postgresql://postgres:5432/postgres -e SPRING_DATASOURCE_USERNAME=postgres \
    -e SPRING_DATASOURCE_PASSWORD=lab -e INVENTORY_URL=http://inventory:8080 -e "JAVA_TOOL_OPTIONS=$2" \
    "$IMAGE" -Xms256m -Xmx256m -jar /lab/app.jar >/dev/null
}
ready() {
  for i in $(seq 1 60); do docker logs "$1" 2>&1 | grep -q 'process running for' && break; sleep 1; done
  docker logs "$1" 2>&1 | grep -oE 'process running for [0-9.]+' | tail -1 | sed "s/^/$1 /"
  docker logs "$1" 2>&1 | grep -q 'process running for' || { docker logs "$1" 2>&1 | tail -30; echo "CHECK FAILED: $1 didn't start"; exit 1; }
}
load() { docker run --rm --name load --network lab -v "$PWD/c/load:/load:ro" -e SECONDS_TOTAL=20 -e SECONDS_WARM=5 "$PY_IMAGE" python /load/load.py; }

echo "== No agent"
start inventory ""
start orders ""
ready inventory
ready orders
load | tee c/before.txt
grep -q 'errors=0' c/before.txt || { docker logs orders 2>&1 | tail -30; echo "CHECK FAILED: requests failed with no agent"; exit 1; }
docker exec orders sha256sum /lab/app.jar
docker exec orders grep VmRSS /proc/1/status
docker rm -f orders inventory >/dev/null

echo "== With the agent, printing what it exports"
for s in inventory orders; do
  start "$s" "-javaagent:/lab/opentelemetry-javaagent.jar -Dotel.service.name=$s -Dotel.traces.exporter=console -Dotel.metrics.exporter=console -Dotel.metric.export.interval=5000 -Dotel.logs.exporter=none"
done
ready inventory
ready orders
load | tee c/after.txt
grep -q 'errors=0' c/after.txt || { docker logs orders 2>&1 | tail -30; echo "CHECK FAILED: requests failed with the agent"; exit 1; }
docker logs orders 2>&1 | grep -m1 'opentelemetry-javaagent - version' || true
docker exec orders grep VmRSS /proc/1/status
sleep 8

TRACE=$(grep -oE 'last_trace_id=[0-9a-f]{32}' c/after.txt | cut -d= -f2)
{ docker logs orders 2>&1; docker logs inventory 2>&1; } > c/agent.log
grep LoggingSpanExporter c/agent.log | grep "$TRACE" | sed -E 's/^.*LoggingSpanExporter - //' | cut -c1-100 | tee c/spans.txt
SPANS=$(grep -c . c/spans.txt || true)
grep -oE 'name=(jvm|http|db|hikaricp|tomcat|executor)\.[a-z._]+' c/agent.log | sort -u | sed 's/name=//' | tee c/metrics.txt
NAMES=$(grep -c . c/metrics.txt || true)
echo "spans in one request: $SPANS"
echo "agent metric names: $NAMES"
[ "$SPANS" -ge 3 ] && [ "$NAMES" -ge 5 ] || { echo "CHECK FAILED: too little telemetry"; exit 1; }
echo "CHECK PASSED"
