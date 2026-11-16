# Shared by reproduce.sh and fix.sh: the clock, the watcher, TaskManager deletions, the per-phase report and the
# Splunk reads. Sourced.
# A phase = a WATCH pod reading Flink's REST API once a second while TaskManager pods are deleted, one at a time, the
# way a node drain, an eviction or an OOM kill would take one. The Deployment brings a new TaskManager each time.
export LC_ALL=C

start_clock() {   # seconds allowed, the result to print on failure. Every wait is capped so a result always prints.
  START=$(date +%s); LIMIT=$1; FAILED=$2; HERO_DONE=0
  trap '[ "$HERO_DONE" = 1 ] || { HERO_DONE=1; echo "HERO_RESULT: $FAILED"; }' EXIT
}
remaining() { echo $(( START + LIMIT - $(date +%s) )); }
cap() { local want=$1 reserve=$2 r; r=$(( $(remaining) - reserve )); [ "$r" -lt 5 ] && r=5; [ "$want" -lt "$r" ] && echo "$want" || echo "$r"; }
emit() { HERO_DONE=1; echo "HERO_RESULT: $1"; }
fail() { echo "FAIL: $*"; [ "$HERO_DONE" = 1 ] || emit "$FAILED"; exit 0; }
why() { kubectl -n demo get pods -o wide | cut -c1-150 | tail -8; kubectl -n demo get events --sort-by=.lastTimestamp | tail -8 | cut -c1-180; kubectl -n demo logs deploy/flink-jobmanager --tail=15 | cut -c1-200; }

start_watch() {   # pod name, seconds it runs
  WATCH=$1
  kubectl -n demo delete pod "$WATCH" --ignore-not-found --wait=true >/dev/null 2>&1
  sed -e "s/WATCH_NAME/$1/" -e "s/LOAD_NODE/$LOAD_NODE/" -e "s/WATCH_SECONDS_VALUE/$2/" k8s/20-load.yaml | kubectl apply -f - >/dev/null || fail "could not start the watcher"
  kubectl -n demo wait pod/"$WATCH" --for=condition=Ready --timeout=120s >/dev/null || { why; fail "the watcher did not start"; }
}
last_watch() { kubectl -n demo logs "$WATCH" --tail=8 2>/dev/null | grep '^WATCH t=' | tail -1; }
rest_at() {   # watcher log, field (restarts, ckpt_ok, ckpt_fail), second: Flink's value at the last sample at or before it
  awk -v f="$2" -v at="$3" '/^WATCH t=/ { split($2, a, "="); if (a[2] > at) exit; for (i = 3; i <= NF; i++) { split($i, b, "="); if (b[1] == f && b[2] != "na") v = b[2] } } END { print (v == "" ? "null" : v) }' "$1"
}
job_state() { last_watch | sed -n 's/.* state=\([A-Z_]*\).*/\1/p'; }
job_restarts() { last_watch | sed -n 's/.* restarts=\([0-9]*\).*/\1/p'; }
wait_running() {   # seconds, the restart count to reach
  local t0=$(date +%s) r
  while [ $(( $(date +%s) - t0 )) -lt "$1" ]; do
    r=$(job_restarts)
    [ "$(job_state)" = RUNNING ] && [ "${r:-0}" -ge "${2:-0}" ] && return 0
    sleep 2
  done
  return 1
}

kill_tm() {   # phase, seconds allowed for the job to come back
  local before p
  before=$(job_restarts); p=$(kubectl -n demo get pods -l app=flink-taskmanager -o name | head -1)
  [ -n "$p" ] || fail "no TaskManager pod to delete"
  echo "KILL t=$(date +%s) $1 ${p#pod/}" >> kills.txt
  kubectl -n demo delete "$p" --wait=false >/dev/null || fail "could not delete $p"
  wait_running "$2" $(( ${before:-0} + 1 )) || { why; fail "the job did not come back to RUNNING after $p was deleted"; }
  echo "$1: deleted ${p#pod/} at $(sed -n '$s/KILL t=\([0-9]*\).*/\1/p' kills.txt); RUNNING again at $(date +%s), Flink's restart count $(job_restarts)"
}

# The lab's runner has no Python, so analyze.sh runs in a small python:3.12-slim pod on the load node, on copies of the logs.
analyzer() {
  kubectl -n demo get pod analyzer >/dev/null 2>&1 && return 0
  kubectl -n demo run analyzer --image=python:3.12-slim --restart=Never \
    --overrides="{\"spec\":{\"nodeName\":\"$LOAD_NODE\",\"enableServiceLinks\":false}}" -- sleep 86400 >/dev/null || fail "could not start the analyzer pod"
  kubectl -n demo wait pod/analyzer --for=condition=Ready --timeout=120s >/dev/null || fail "the analyzer pod did not start"
}

report() {   # phase, window start s, window end s. Writes report-<phase>.txt.
  kubectl -n demo logs "$WATCH" > "watch-$1.log" 2>/dev/null
  analyzer
  for f in analyze.sh "watch-$1.log" kills.txt; do kubectl cp "$f" "demo/analyzer:/tmp/$f" >/dev/null 2>&1 || fail "could not copy $f to the analyzer"; done
  kubectl -n demo exec analyzer -- bash "/tmp/analyze.sh" "$1" "/tmp/watch-$1.log" /tmp/kills.txt "$2" "$3" | tee "report-$1.txt"
  measure() { sed -n "s/^HERO_MEASURE: $1\[$2\]=//p" "report-$2.txt"; }
}

# Splunk Observability Cloud reads. Need SPLUNK_REALM and SPLUNK_API_TOKEN.
API="https://api.${SPLUNK_REALM:-}.signalfx.com"
STREAM="https://stream.${SPLUNK_REALM:-}.signalfx.com"
series() { curl -sS --max-time 20 -G -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/metrictimeseries" --data-urlencode "query=$1" --data-urlencode "limit=1000" 2>/dev/null; }

wait_cluster() {   # seconds. Sets cluster to how many of the cluster's series Splunk holds.
  local t0=$(date +%s); cluster=0
  while [ $(( $(date +%s) - t0 )) -lt "$1" ]; do
    cluster=$(series "k8s.cluster.name:${LAB_NAME}" | jq -r '.count // (.results | length) // 0' 2>/dev/null || echo 0)
    [ "${cluster:-0}" -gt 0 ] && break
    sleep 15
  done
}

flink_names() { series "sf_metric:flink.* AND deployment.environment:${LAB_NAME}" | jq -r '[.results[]?.metric] | unique | .[]' 2>/dev/null; }

# SignalFlow answers as a stream of events; counter() keeps one "seconds value" line per 10 s point, the max across series.
# Every program is printed (unless quiet), so the log shows the rollup. 'latest' on a cumulative counter keeps the raw
# running total in each 10 s point, not a per-point delta, so end - base is the real increase. For a gauge whose dip or
# rise must not be lost inside a point, pass rollup 'min' or 'max'.
counter() {   # metric, SignalFlow filter, start s, stop s, file[, quiet[, rollup (default latest)]]
  local prog="data('$1', filter=$2, rollup='${7:-latest}').max().publish()" try
  [ -n "${6:-}" ] || echo "  program: $prog resolution=10000ms start=$3 stop=$4" >&2   # stderr, so no redirect hides it
  for try in 1 2 3; do
    curl -sS --max-time 60 -X POST -H "X-SF-Token: $SPLUNK_API_TOKEN" -H "Content-Type: text/plain" --data-binary "$prog" \
      "$STREAM/v2/signalflow/execute?start=$(( $3 * 1000 ))&stop=$(( $4 * 1000 ))&resolution=10000&immediate=true" 2>/dev/null |
      awk '/^event:/ { if (b != "") print e "\t" b; e = $2; b = ""; next }
           /^data:/  { sub(/^data: ?/, ""); b = b $0; next }
           END       { if (b != "") print e "\t" b }' |
      jq -Rr 'split("\t") | select(.[0] == "data") | (.[1] | fromjson? // {}) | [.logicalTimestampMs, ([.data[]?.value // empty] | max)]
        | select(.[1] != null) | "\(.[0] / 1000 | floor) \(.[1])"' 2>/dev/null > "$5"
    [ -s "$5" ] && return 0
    [ -n "${6:-}" ] && return 0
    sleep 10
  done
}
metric_type() { curl -sS --max-time 20 -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/metric/$1" 2>/dev/null | jq -r '.type // "unknown"'; }
at() { awk -v t="$2" '$1 <= t {v = $2} END {print (v == "" ? "null" : v)}' "$1"; }   # file, second: the value of the last point at or before it
