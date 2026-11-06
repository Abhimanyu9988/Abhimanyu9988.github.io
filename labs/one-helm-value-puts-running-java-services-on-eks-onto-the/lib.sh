# Helpers for the lab scripts. They call cap and fail, which each script defines.
APPS='(^|[^a-z])(orders|inventory)$'

newest() { kubectl -n demo get pods -l "app=$1" --field-selector=status.phase=Running -o name | tail -1; }
series() { curl -sS --max-time 20 -G -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/metrictimeseries" --data-urlencode "query=$1" --data-urlencode "limit=1000" 2>/dev/null; }

topology() {   # the map for a window, by default the 25 minutes up to an end time. ISO is tried second
  local end=${1:-$(date +%s)} start=${2:-} range body out
  start=${start:-$(( end - 1500 ))}
  for range in "$(( start * 1000 ))/$(( end * 1000 ))" "$(jq -rn --argjson n "$start" '$n | todate')/$(jq -rn --argjson n "$end" '$n | todate')"; do
    body=$(jq -cn --arg t "$range" --arg e "$LAB_NAME" '{timeRange: $t, tagFilters: [{name: "sf_environment", operator: "equals", scope: "GLOBAL", value: $e}]}')
    out=$(curl -sS --max-time 20 -X POST -H "X-SF-Token: $SPLUNK_API_TOKEN" -H "Content-Type: application/json" -d "$body" "$API/v2/apm/topology" 2>/dev/null)
    jq -e '.data' <<<"$out" >/dev/null 2>&1 && break
  done
  echo "$out"
}

on_map() {   # map JSON. How many of the two services are nodes, inferred ones included
  jq -r --arg a "$APPS" '[.data.nodes[]? | .serviceName // empty | select(test($a))] | unique | length' <<<"$1" 2>/dev/null || echo 0
}

metric_services() {   # an end time. The services with a service.request.count series created by then, so a later stage can't count
  series "sf_metric:service.request.count AND sf_environment:${LAB_NAME}" | jq -r --arg a "$APPS" --argjson e "$(( $1 * 1000 ))" '[.results[]? | select((.created // 0) <= $e) | .dimensions.sf_service // empty | select(test($a))] | unique | .[]' 2>/dev/null
}

map_nodes() { jq -c '[.data.nodes[]? | {(.serviceName // "?"): (.inferred // false)}] | add' <<<"$1" 2>/dev/null; }   # each node and its inferred flag

tap_parse() {   # the tap's output on stdin. Prints "service trace-id span name"
  awk '/^ResourceTraces / { svc = ""; if ($0 ~ /=orders( |$)/) svc = "orders"; else if ($0 ~ /=inventory( |$)/) svc = "inventory"; next }
       svc != "" { for (i = 2; i < NF; i++) if (length($i) == 32 && length($(i + 1)) == 16 && $i ~ /^[0-9a-f]+$/ && $(i + 1) ~ /^[0-9a-f]+$/) {
         name = ""; for (j = 1; j < i; j++) name = name " " $j; print svc, $i name; break } }'
}

tap_ids() {   # parsed tap file, label. What OBI exported. Leaves the orders request trace IDs in ids-<label>.txt
  awk '$1 == "orders" && $3 ~ /^[A-Z]+$/ && $4 !~ /^\/stock/ { print $2 }' "$1" | awk '!seen[$0]++' > "ids-$2.txt"
  awk '$1 == "orders" { print $2 }' "$1" | sort -u > "orders-$2.txt"
  awk '$1 == "inventory" { print $2 }' "$1" | sort -u > "inventory-$2.txt"
  echo "$2 case, as OBI exported it: orders traces $(grep -c . "orders-$2.txt"), inventory traces $(grep -c . "inventory-$2.txt"), in both $(comm -12 "orders-$2.txt" "inventory-$2.txt" | grep -c .)" >&2
}

tap_snapshot() {   # label, start of its load round. OBI sends in batches, so this waits first
  sleep 8
  kubectl -n splunk logs -l component=otel-collector-agent -c otel-collector --tail=-1 --since-time="$2" --max-log-requests=4 2>/dev/null | tap_parse > "tap-$1.txt"
  tap_ids "tap-$1.txt" "$1"
}

cpu_now() { kubectl -n demo exec "$(newest "$1")" -c app -- head -1 /proc/stat 2>/dev/null; }
busy() { printf '%s\n%s\n' "$1" "$2" | awk 'NR==1{for(i=2;i<=NF;i++)a+=$i; x=$5+$6} NR==2{for(i=2;i<=NF;i++)b+=$i; y=$5+$6} END{if(b>a)printf "%.0f",100*(1-(y-x)/(b-a)); else printf "0"}'; }

load_round() {   # the file that collects the result
  kubectl -n demo delete pod load --ignore-not-found --wait=true >/dev/null 2>&1
  sed "s/LOAD_NODE/$LOAD_NODE/" k8s/20-load.yaml | kubectl apply -f - >/dev/null || fail "could not start the load"
  kubectl -n demo wait pod/load --for=jsonpath='{.status.phase}'=Succeeded --timeout="$(cap 55 10)s" >/dev/null || { kubectl -n demo logs load --tail=20; fail "the load did not finish"; }
  kubectl -n demo logs load | tee -a "$1"
}

round() {   # file, label. One load round, printed next to CPU on each node
  local o i
  o=$(cpu_now orders); i=$(cpu_now inventory)
  load_round "$1"
  echo "$2 round: p95 $(p95s "$1" | tail -1) ms, CPU busy pct orders node $(busy "$o" "$(cpu_now orders)"), inventory node $(busy "$i" "$(cpu_now inventory)"), load node $(grep -oE 'node_busy_pct=[0-9]+' "$1" | tail -1 | cut -d= -f2)"
}

tap_lines() { kubectl -n splunk get cm splunk-otel-collector-otel-agent -o jsonpath='{.data.relay}' 2>/dev/null | grep -c 'traces/tap' || true; }

read_case() {   # label, end of its round. Starts the read TRACE_WAIT after the round, and records the real delay
  local left=$(( $2 + TRACE_WAIT - $(date +%s) )) took
  [ "$left" -gt 0 ] && sleep "$left"
  took=$(( $(date +%s) - $2 ))
  echo "read delay $1 seconds after its round: $took, overshoot $(( left < 0 ? -left : 0 ))"
  echo "HERO_MEASURE: read_delay_$1_s=$took"
  bash ./propagate.sh read "$1" > "read-$1.txt"
}

p95s() { grep -oE 'p95_ms=[0-9.]+' "$1" | cut -d= -f2; }   # in round order

obi_log() { kubectl -n splunk logs -l app.kubernetes.io/name=obi --tail=-1 --max-log-requests=4 2>/dev/null; }

obi_ready() {   # seconds for the rollout, seconds for OBI to report both JVMs
  local ds i n
  ds=$(kubectl -n splunk get ds -l app.kubernetes.io/name=obi -o name | head -1)
  [ -n "$ds" ] || fail "no OBI DaemonSet"
  kubectl -n splunk rollout status "$ds" --timeout="${1}s" >/dev/null || { kubectl -n splunk get pods -o wide | cut -c1-120; kubectl -n splunk get events --sort-by=.lastTimestamp | tail -8 | cut -c1-180; fail "OBI did not roll out"; }
  for i in $(seq 1 $(( $2 / 5 ))); do
    n=$(obi_log | grep 'instrumenting process' | grep -c 'java' || true)
    [ "${n:-0}" -ge 2 ] && break
    sleep 5
  done
  echo "JVMs OBI reports instrumenting: ${n:-0}"
}

obi_settings() {   # what the running OBI pods hold. Only obi values are read, so the token is never printed
  kubectl -n splunk get pods -l app.kubernetes.io/name=obi -o json | jq -r '.items[] | .spec.containers[0] | "running OBI pod: \(.image) \([.env[] | select(.name | test("^OTEL_EBPF_")) | select(.value != null) | "\(.name)=\(.value)"] | join(" "))"'
  echo "helm values under obi: $(helm get values splunk-otel-collector -n splunk -o json | jq -c '.obi // {}')"
}

restarts_by_pod() {   # pod and restarts added between two snapshots. A replaced pod counts as one
  awk 'NR==FNR{uid[$1]=$2; count[$1]=$3; began[$1]=$4; next} {n = $3 - count[$1]; if ($2 != uid[$1] || $4 != began[$1]) n++; print $1, n}' "$1" "$2"
}

pods_now() {
  kubectl -n demo get pods -l 'app in (orders,inventory,postgres)' -o json | jq -r '.items[] | [.metadata.labels.app, .metadata.uid, .status.containerStatuses[0].restartCount, .status.containerStatuses[0].state.running.startedAt, .spec.containers[0].image] | @tsv' | sort
}

show_map() {
  echo "-- service map from Splunk APM"
  jq -r '.data.nodes[]? | "  \(.serviceName) inferred=\(.inferred // false)"' <<<"$1" 2>/dev/null
  jq -r '.data.edges[]? | "  \(.fromNode) -> \(.toNode)"' <<<"$1" 2>/dev/null
}
