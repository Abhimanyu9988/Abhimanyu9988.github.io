#!/usr/bin/env bash
# The free check, on a GitHub runner with Docker, before AWS is paid for. The lab's real Flink settings, job, watcher and
# Collector config (its Flink receiver, with a file exporter instead of the Splunk agent) run in containers. Two
# TaskManager restarts must show in Flink's REST API and in the receiver's flink.job.restart.count, and the receiver must
# send JVM GC metrics for the JobManager and the TaskManager. Kubernetes and Splunk are not tested here.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p c/out && chmod 777 c/out
python3 - <<'PY'
import re
flink = open("k8s/10-app.yaml").read()
props = re.findall(r"name: FLINK_PROPERTIES\n\s+value: \|\n((?:\s{16}.*\n)+)", flink)
open("c/jm.props", "w").write("\n".join(l.strip() for l in props[0].splitlines()).replace("flink-jobmanager", "jobmanager") + "\n")
open("c/tm.props", "w").write("\n".join(l.strip() for l in props[1].splitlines()).replace("flink-jobmanager", "jobmanager") + "\n")
watch = open("k8s/20-load.yaml").read()
m = re.search(r"  watch.py: \|\n((?:    .*\n|\n)+)", watch)
open("c/watch.py", "w").write("\n".join(l[4:] for l in m[1].splitlines()) + "\n")
conf = open("k8s/30-collector.yaml").read()
m = re.search(r"  config.yaml: \|\n((?:    .*\n|\n)+)", conf)
c = "\n".join(l[4:] for l in m[1].splitlines()) + "\n"
c = c.replace("http://flink-jobmanager:8081", "http://jobmanager:8081").replace("LAB_ENV", "free-check")
c = re.sub(r"  otlphttp:\n    endpoint: .*\n", "  file:\n    path: /out/metrics.json\n", c).replace("exporters: [otlphttp]", "exporters: [file]")
open("c/otel.yaml", "w").write(c)
PY
FLINK=$(grep -m1 -oE 'flink:[^ "]+' k8s/10-app.yaml); OTEL=$(grep -m1 -oE 'otel/opentelemetry-collector-contrib:[^ "]+' k8s/30-collector.yaml)
docker network create lab >/dev/null
tm() { docker run -d --name taskmanager --network lab -e "FLINK_PROPERTIES=$(cat c/tm.props)" "$FLINK" taskmanager >/dev/null; }
docker run -d --name jobmanager --network lab -e "FLINK_PROPERTIES=$(cat c/jm.props)" "$FLINK" jobmanager >/dev/null
tm
docker run -d --name watch --network lab -v "$PWD/c/watch.py:/watch.py:ro" -e FLINK=http://jobmanager:8081 -e WATCH_SECONDS=400 python:3.12-slim python -u /watch.py >/dev/null
state() { docker logs watch 2>/dev/null | grep '^WATCH t=' | tail -1; }
for i in $(seq 1 90); do docker exec jobmanager flink list -r >/dev/null 2>&1 && break; sleep 2; done
docker exec jobmanager flink run -d -p 2 /opt/flink/examples/streaming/StateMachineExample.jar | grep -m1 -i jobid
wait_running() { for i in $(seq 1 60); do state | grep -qE "state=RUNNING .*restarts=$1 " && return 0; sleep 2; done; state; echo "CHECK FAILED: the job did not reach RUNNING with $1 restarts"; exit 1; }
wait_running 0
docker run -d --name otel --network lab -v "$PWD/c/otel.yaml:/conf/otel.yaml:ro" -v "$PWD/c/out:/out" "$OTEL" --config=/conf/otel.yaml >/dev/null
sleep 25
: > c/kills.txt
w0=$(date +%s)
for n in 1 2; do
  echo "KILL t=$(date +%s) check taskmanager" >> c/kills.txt
  docker stop -t 10 taskmanager >/dev/null; docker rm taskmanager >/dev/null; tm
  wait_running "$n"; sleep 20
done
w1=$(date +%s)
sleep 15
docker logs watch > c/watch.log 2>&1
docker logs otel 2>&1 | grep -iE "error|warn" | head -5 || true
echo "$FLINK, $OTEL, $(head -1 c/watch.log)"
bash analyze.sh check c/watch.log c/kills.txt "$w0" "$w1" | tee c/report.txt
python3 - <<'PY'
import json, sys
restarts, gc = [], set()
for line in open("c/out/metrics.json"):
    for rm in json.loads(line).get("resourceMetrics", []):
        res = {a["key"]: list(a["value"].values())[0] for a in rm.get("resource", {}).get("attributes", [])}
        for sm in rm.get("scopeMetrics", []):
            for m in sm.get("metrics", []):
                points = (m.get("sum") or m.get("gauge") or {}).get("dataPoints", [])
                if m["name"] == "flink.job.restart.count":
                    restarts += [int(p.get("asInt", 0)) for p in points]
                if m["name"] == "flink.jvm.gc.collections.count" and points:
                    gc.add(res.get("flink.resource.type"))
print(f"receiver: flink.job.restart.count values {sorted(set(restarts))}; JVM GC metrics from {sorted(x for x in gc if x)}")
if max(restarts or [0]) < 2:
    sys.exit("CHECK FAILED: the receiver did not report the two restarts")
if not {"jobmanager", "taskmanager"} <= gc:
    sys.exit("CHECK FAILED: the receiver did not send GC metrics for both the JobManager and the TaskManager")
PY
grep -q 'flink_rest_restarts\[check\]=2' c/report.txt || { echo "CHECK FAILED: Flink's REST API didn't count 2 restarts"; exit 1; }
echo "CHECK PASSED"
