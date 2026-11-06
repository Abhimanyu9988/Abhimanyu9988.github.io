#!/usr/bin/env bash
# The free check, run on a GitHub runner with Docker before AWS is paid for. It uses the lab's own source, images,
# agent and load script, and proves one setting makes the agent emit db.client.operation.duration. Kubernetes and Splunk
# are tested by the rehearsal. No secrets are used here.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p c/src c/out c/init c/load

METRIC=db.client.operation.duration

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

docker run -d --name postgres --network lab -e POSTGRES_PASSWORD=lab -v "$PWD/c/init:/docker-entrypoint-initdb.d:ro" "$PG_IMAGE" -c shared_preload_libraries=pg_stat_statements >/dev/null
for i in $(seq 1 30); do docker exec postgres pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
sleep 3

start() {   # name, then any extra docker arguments. The agent options are the same in both runs.
  local name=$1; shift
  docker run -d --name "$name" --network lab -v "$PWD/c/out:/lab:ro" --entrypoint java "$@" \
    -e SPRING_DATASOURCE_URL=jdbc:postgresql://postgres:5432/postgres -e SPRING_DATASOURCE_USERNAME=postgres \
    -e SPRING_DATASOURCE_PASSWORD=lab -e INVENTORY_URL=http://inventory:8080 \
    -e "JAVA_TOOL_OPTIONS=-javaagent:/lab/opentelemetry-javaagent.jar -Dotel.service.name=$name -Dotel.traces.exporter=console -Dotel.metrics.exporter=console -Dotel.metric.export.interval=5000 -Dotel.logs.exporter=none${PROP:-}" \
    "$IMAGE" -Xms256m -Xmx256m -jar /lab/app.jar >/dev/null
}
ready() {
  for i in $(seq 1 90); do docker logs "$1" 2>&1 | grep -q 'process running for' && break; sleep 1; done
  docker logs "$1" 2>&1 | grep -oE 'process running for [0-9.]+' | tail -1 | sed "s/^/$1 /"
  docker logs "$1" 2>&1 | grep -q 'process running for' || { docker logs "$1" 2>&1 | tail -30; echo "CHECK FAILED: $1 didn't start"; exit 1; }
}
load() { docker run --rm --name load --network lab -v "$PWD/c/load:/load:ro" -e SECONDS_TOTAL="${LOAD_SECONDS:-20}" -e SECONDS_WARM=5 "$PY_IMAGE" python /load/load.py; }
db_names() { grep -oE 'name=db\.[a-z._]+' "$1" | sort -u | sed 's/name=//' || true; }

span_keys() { grep LoggingSpanExporter "$1" | grep -E ' CLIENT .*db\.' | tail -1 | grep -oE '\bdb\.[a-z_.]+=' | tr -d = | sort -u | tr '\n' ' '; echo; }
exports() { grep -c "name=$METRIC," "$1" || true; }

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
  echo "-- $name run: database span attribute keys: $(span_keys "c/$name.log")"
  echo "$METRIC exports in the $name run: $(exports "c/$name.log")"
}

echo "== The agent with no opt-in"
run default
grep -m1 -h 'opentelemetry-javaagent - version' c/default.log || true
echo "== The same agent options, with OTEL_SEMCONV_STABILITY_OPT_IN=database"
# 75 seconds always crosses a minute boundary with calls after it, so the timer in inventory prints at least one line.
LOAD_SECONDS=75 run setting -e OTEL_SEMCONV_STABILITY_OPT_IN=database
echo "== The same agent options, with OTEL_SEMCONV_STABILITY_OPT_IN=database/dup, which emits both sets of names"
run dup -e OTEL_SEMCONV_STABILITY_OPT_IN=database/dup
echo "== The same agent options, with the system property otel.semconv-stability.opt-in=database"
PROP=" -Dotel.semconv-stability.opt-in=database" run sysprop
BEFORE=$(exports c/default.log)
AFTER=$(exports c/setting.log)
DUP=$(exports c/dup.log)
PROPERTY=$(exports c/sysprop.log)

# The last exports hold the running totals, one line for each service, in seconds.
python3 - <<'PY' | tee c/point.txt
import re
lines = {}
for line in open("c/setting.log"):
    if "name=db.client.operation.duration," in line:
        lines[re.search(r'service\.name="(\w+)"', line)[1]] = line
total = 0
for service, text in sorted(lines.items()):
    print("unit", re.search(r"unit=(\w+)", text)[1], "type", re.search(r"unit=\w+, type=(\w+)", text)[1])
    for attributes, summed, count, boundaries, counts in re.findall(r"getAttributes=\w*\{([^}]*)\}, getSum=([^,]+), getCount=(\d+).*?getBoundaries=\[([^\]]*)\], getCounts=\[([^\]]*)\]", text):
        total += int(count)
        print("statement", service, attributes)
        print("  queries", count, "mean_ms", round(float(summed) / int(count) * 1000, 2), "boundaries", boundaries, "counts", counts)
print("total_queries", total)
PY

docker exec postgres psql -U postgres -tAc "select calls, round(mean_exec_time::numeric, 2), left(query, 60) from pg_stat_statements where query ~ '(stock|reserved|audit)' order by calls desc" | tee c/server.txt
for s in 'select stock' 'update stock' 'select reserved' 'select audit'; do
  grep -q "db.query.summary=$s," c/point.txt || { echo "CHECK FAILED: no metric for the statement $s"; exit 1; }
done
grep -E '^KNOWN minute_epoch=[0-9]+ calls=[0-9]+ p95_ms=[0-9.]+ max_ms=[0-9.]+$' c/setting.log | tee c/timer.txt
TIMER=$(grep -c . c/timer.txt || true)
echo "timer lines in inventory: $TIMER"
[ "$TIMER" -ge 1 ] || { echo "CHECK FAILED: the timer in inventory printed no line"; exit 1; }
grep -E '^LOAD minute=[0-9]+ sent=[0-9]+$' c/setting.txt | head -3
grep -qE '^LOAD minute=[0-9]+ sent=[0-9]+$' c/setting.txt || { echo "CHECK FAILED: the load printed no minute lines"; exit 1; }
grep -q pg_sleep c/server.txt || { echo "CHECK FAILED: pg_stat_statements has no known-latency statement"; exit 1; }
[ "$BEFORE" -eq 0 ] || { echo "CHECK FAILED: the run with no opt-in already exports $METRIC"; exit 1; }
[ "$AFTER" -ge 1 ] || { echo "CHECK FAILED: the setting did not export $METRIC"; exit 1; }
[ "$DUP" -ge 1 ] || { echo "CHECK FAILED: database/dup did not export $METRIC"; exit 1; }
[ "$PROPERTY" -ge 1 ] || { echo "CHECK FAILED: the system property did not export $METRIC"; exit 1; }
grep -q 'db.system.name=postgresql' c/point.txt || { echo "CHECK FAILED: the metric has no Postgres attribute"; exit 1; }
QUERIES=$(awk '$1 == "total_queries" {print $2}' c/point.txt)
SENT=$(grep -oE 'requests=[0-9]+' c/setting.txt | cut -d= -f2)
echo "queries in the metric: $QUERIES, requests measured after warm-up: $SENT"
[ "${QUERIES:-0}" -ge "${SENT:-1}" ] || { echo "CHECK FAILED: the metric counted fewer queries than requests"; exit 1; }
echo "CHECK PASSED"
