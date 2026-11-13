#!/usr/bin/env bash
# The free check, run on a GitHub runner with Docker before AWS is paid for. It uses the lab's own source, images, agent
# and load script, and proves that OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true alone moves the agent's database span
# keys and connection pool metrics to the new names, with pool timings in seconds. Kubernetes and Splunk are tested by
# the rehearsal. No secrets are used here.
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
for i in 1 2 3; do
  docker run --rm -v "$PWD/c/out:/lab" "$PY_IMAGE" python -c "import urllib.request; urllib.request.urlretrieve('$AGENT_URL', '/lab/opentelemetry-javaagent.jar')" && break
  sleep 10
done
docker run --rm -v "$PWD/c/src:/src:ro" -v "$PWD/c/out:/lab" "$IMAGE" sh -c '
  set -e; mkdir -p /w/src/main/java/lab; cp /src/pom.xml /w/; cp /src/App.java /w/src/main/java/lab/; cd /w
  mvn -q -B -DskipTests package; cp target/app.jar /lab/app.jar'
ls -l c/out

docker run -d --name postgres --network lab -e POSTGRES_PASSWORD=lab -v "$PWD/c/init:/docker-entrypoint-initdb.d:ro" "$PG_IMAGE" -c shared_preload_libraries=pg_stat_statements >/dev/null
for i in $(seq 1 30); do docker exec postgres pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
sleep 3

start() {   # name, then any extra docker arguments. The agent options are the same in both runs.
  local name=$1; shift
  docker run -d --name "$name" --network lab -v "$PWD/c/out:/lab:ro" --entrypoint java "$@" \
    -e SPRING_DATASOURCE_URL=jdbc:postgresql://postgres:5432/postgres -e SPRING_DATASOURCE_USERNAME=postgres \
    -e SPRING_DATASOURCE_PASSWORD=lab -e INVENTORY_URL=http://inventory:8080 \
    -e "JAVA_TOOL_OPTIONS=-javaagent:/lab/opentelemetry-javaagent.jar -Dotel.service.name=$name -Dotel.traces.exporter=console -Dotel.metrics.exporter=console -Dotel.metric.export.interval=5000 -Dotel.logs.exporter=none" \
    "$IMAGE" -Xms256m -Xmx256m -jar /lab/app.jar >/dev/null
}
ready() {
  for i in $(seq 1 90); do docker logs "$1" 2>&1 | grep -q 'process running for' && break; sleep 1; done
  docker logs "$1" 2>&1 | grep -oE 'process running for [0-9.]+' | tail -1 | sed "s/^/$1 /"
  docker logs "$1" 2>&1 | grep -q 'process running for' || { docker logs "$1" 2>&1 | tail -30; echo "CHECK FAILED: $1 didn't start"; exit 1; }
}
load() { docker run --rm --name load --network lab -v "$PWD/c/load:/load:ro" -e SECONDS_TOTAL=20 -e SECONDS_WARM=5 "$PY_IMAGE" python /load/load.py; }
db_names() { grep -oE 'name=db\.[a-z._]+' "$1" | sed 's/name=//' | sort -u || true; }
pool_names() { db_names "$1" | grep -E "^$2\." || true; }
unit_of() { grep -oE "name=$2, description=[^,]*, unit=[a-z]+" "$1" | head -1 | grep -oE 'unit=[a-z]+$' | cut -d= -f2 || true; }
span_keys() { grep LoggingSpanExporter "$1" | grep -E ' CLIENT .*db\.' | tail -1 | grep -oE '\bdb\.[a-z_.]+=' | tr -d = | sort -u | tr '\n' ' '; echo; }

run() {   # name, then any extra docker arguments for both services
  local name=$1; shift
  docker rm -f orders inventory >/dev/null 2>&1 || true
  start inventory "$@"
  start orders "$@"
  ready inventory
  ready orders
  load | tee "c/$name.txt"
  grep -q 'errors=0' "c/$name.txt" || { docker logs orders 2>&1 | tail -30; echo "CHECK FAILED: requests failed in the $name run"; exit 1; }
  sleep 8
  { docker logs orders 2>&1; docker logs inventory 2>&1; } > "c/$name.log"
  echo "-- $name run: database metric names"
  db_names "c/$name.log" | sed 's/^/  /'
  echo "-- $name run: database span keys: $(span_keys "c/$name.log")"
}

echo "== The agent with its defaults"
run default
grep -m1 -h 'opentelemetry-javaagent - version' c/default.log || true
echo "== The same agent options, with OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true"
run preview -e OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true

D_OLD=$(pool_names c/default.log db.client.connections | grep -c . || true)
D_NEW=$(pool_names c/default.log db.client.connection | grep -c . || true)
P_OLD=$(pool_names c/preview.log db.client.connections | grep -c . || true)
P_NEW=$(pool_names c/preview.log db.client.connection | grep -c . || true)
D_UNIT=$(unit_of c/default.log db.client.connections.use_time)
P_UNIT=$(unit_of c/preview.log db.client.connection.use_time)
D_KEYS=$(span_keys c/default.log)
P_KEYS=$(span_keys c/preview.log)
echo "pool names: default old $D_OLD new $D_NEW, preview old $P_OLD new $P_NEW"
echo "use_time unit: default ${D_UNIT:-none}, preview ${P_UNIT:-none}"
echo "span keys: default [$D_KEYS] preview [$P_KEYS]"
[ "$D_OLD" -ge 1 ] || { echo "CHECK FAILED: the default run has no pool metric under db.client.connections"; exit 1; }
[ "$D_NEW" -eq 0 ] || { echo "CHECK FAILED: the default run already uses the new pool names"; exit 1; }
[ "$P_NEW" -ge 1 ] || { echo "CHECK FAILED: the preview run has no pool metric under db.client.connection"; exit 1; }
[ "$P_OLD" -eq 0 ] || { echo "CHECK FAILED: the preview run still sends old pool names"; exit 1; }
[ "$D_UNIT" = ms ] || { echo "CHECK FAILED: the default use_time unit is not ms"; exit 1; }
[ "$P_UNIT" = s ] || { echo "CHECK FAILED: the preview use_time unit is not s"; exit 1; }
[[ " $D_KEYS " == *" db.system "* ]] || { echo "CHECK FAILED: the default span has no db.system"; exit 1; }
[[ " $P_KEYS " == *" db.system.name "* ]] || { echo "CHECK FAILED: the preview span has no db.system.name"; exit 1; }
echo "CHECK PASSED"
