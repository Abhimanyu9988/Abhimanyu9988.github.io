# Helpers shared by reproduce.sh and fix.sh. They call cap and fail, which each script defines for its own time limit.
APPS='(^|[^a-z])(orders|inventory)$'

newest() { kubectl -n demo get pods -l "app=$1" --field-selector=status.phase=Running -o name | tail -1; }
series() { curl -sS --max-time 20 -G -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/metrictimeseries" --data-urlencode "query=$1" --data-urlencode "limit=1000" 2>/dev/null; }

topology() {   # the map up to an end time, so a result can be tied to the setting that was on then. Epoch milliseconds work; ISO is tried second
  local end=${1:-$(date +%s)} range body out
  for range in "$(( (end - 1500) * 1000 ))/$(( end * 1000 ))" "$(jq -rn --argjson n "$end" '$n - 1500 | todate')/$(jq -rn --argjson n "$end" '$n | todate')"; do
    body=$(jq -cn --arg t "$range" --arg e "$LAB_NAME" '{timeRange: $t, tagFilters: [{name: "sf_environment", operator: "equals", scope: "GLOBAL", value: $e}]}')
    out=$(curl -sS --max-time 20 -X POST -H "X-SF-Token: $SPLUNK_API_TOKEN" -H "Content-Type: application/json" -d "$body" "$API/v2/apm/topology" 2>/dev/null)
    jq -e '.data' <<<"$out" >/dev/null 2>&1 && break
  done
  echo "$out"
}

on_map() {   # how many of the two services Splunk APM reports, from the map or else from its service metric
  local n
  n=$(jq -r --arg a "$APPS" '[.data.nodes[]? | select((.inferred // false) | not) | .serviceName // empty | select(test($a))] | unique | length' <<<"$1" 2>/dev/null)
  [ "${n:-0}" -ge 2 ] || n=$(series "sf_metric:service.request.count AND sf_environment:${LAB_NAME}" | jq -r --arg a "$APPS" '[.results[]?.dimensions.sf_service // empty | select(test($a))] | unique | length' 2>/dev/null)
  echo "${n:-0}"
}

trace_spans() {   # the newest segment, or every segment when that route has nothing
  local out seg
  out=$(curl -sS --max-time 5 -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/apm/trace/$1/latest" 2>/dev/null)
  if jq -e 'type == "array" and length > 0' <<<"$out" >/dev/null 2>&1; then echo "$out"; return; fi
  for seg in $(curl -sS --max-time 5 -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/apm/trace/$1/segments" 2>/dev/null | jq -r '.[]?' 2>/dev/null); do
    curl -sS --max-time 5 -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/apm/trace/$1/$seg" 2>/dev/null
  done | jq -s 'map(if type == "array" then . else [] end) | add // []' 2>/dev/null
}

one_trace() {   # trace ID, folder. Saves the trace when it holds orders or inventory, with how many of the two it holds
  local raw n
  raw=$(trace_spans "$1")
  n=$(jq -r --arg a "$APPS" '[(if type == "array" then . else (.spans // []) end)[] | (.serviceName // .service // .localEndpoint.serviceName // empty) | select(test($a))] | unique | length' <<<"$raw" 2>/dev/null)
  [ "${n:-0}" -gt 0 ] || return 0
  echo "$raw" > "$2/$1.json"
  echo "$n" > "$2/$1.count"
}

trace_pass() {   # load file, label. One pass over every sampled ID, eight at a time, stopping after 28 seconds, the same in both cases.
  # Prints "most services on one trace, traces found, traces joined, IDs searched", and the span list of one sample trace.
  local dir="traces-$2" id i=0 searched=0 from best found joined sample
  rm -rf "$dir"; mkdir -p "$dir"
  from=$(date +%s)
  for id in $(grep -oE 'sampled_trace_ids=[0-9a-f,]+' "$1" | cut -d= -f2 | tr ',' ' '); do
    [ $(( $(date +%s) - from )) -lt 28 ] || break
    one_trace "$id" "$dir" &
    i=$(( i + 1 )); searched=$i
    [ $(( i % 8 )) -eq 0 ] && wait
  done
  wait
  found=$(ls "$dir" | grep -c '\.count$' || true)
  joined=$(cat "$dir"/*.count 2>/dev/null | grep -c '^2$' || true)
  best=$(cat "$dir"/*.count 2>/dev/null | sort -n | tail -1)
  sample=$(grep -l '^2$' "$dir"/*.count 2>/dev/null | head -1)
  [ -n "$sample" ] || sample=$(ls "$dir"/*.count 2>/dev/null | head -1)
  if [ -n "$sample" ]; then
    echo "sample trace from the $2 case: $(basename "$sample" .count)" >&2
    jq -r 'if type == "array" then .[] else .spans[]? end | "  \(.serviceName) \(.kind) \(.operationName) span=\(.spanId // "?") parent=\(.parentId // "none")"' "${sample%.count}.json" 2>/dev/null | head -12 >&2
  else
    echo "sample trace from the $2 case: none found" >&2
  fi
  echo "${best:-0} ${found:-0} ${joined:-0} ${searched}"
}

cpu_now() { kubectl -n demo exec "$(newest "$1")" -c app -- head -1 /proc/stat 2>/dev/null; }
busy() { printf '%s\n%s\n' "$1" "$2" | awk 'NR==1{for(i=2;i<=NF;i++)a+=$i; x=$5+$6} NR==2{for(i=2;i<=NF;i++)b+=$i; y=$5+$6} END{if(b>a)printf "%.0f",100*(1-(y-x)/(b-a)); else printf "0"}'; }

load_round() {   # the file that collects the result
  kubectl -n demo delete pod load --ignore-not-found --wait=true >/dev/null 2>&1
  sed "s/LOAD_NODE/$LOAD_NODE/" k8s/20-load.yaml | kubectl apply -f - >/dev/null || fail "could not start the load"
  kubectl -n demo wait pod/load --for=jsonpath='{.status.phase}'=Succeeded --timeout="$(cap 55 10)s" >/dev/null || { kubectl -n demo logs load --tail=20; fail "the load did not finish"; }
  kubectl -n demo logs load | grep -v sampled_trace_ids
  kubectl -n demo logs load >> "$1"
}

p95s() { grep -oE 'p95_ms=[0-9.]+' "$1" | cut -d= -f2; }   # in round order

obi_log() { kubectl -n splunk logs -l app.kubernetes.io/name=obi --tail=-1 --max-log-requests=4 2>/dev/null; }

obi_ready() {   # seconds for the rollout, seconds for OBI to report both JVMs. A rolled DaemonSet starts its log again
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

obi_settings() {   # what the running OBI pods really hold. helm get values is narrowed to obi, so the token is never printed
  kubectl -n splunk get pods -l app.kubernetes.io/name=obi -o json | jq -r '.items[] | .spec.containers[0] | "running OBI pod: \(.image) \([.env[] | select(.name | test("^OTEL_EBPF_")) | select(.value != null) | "\(.name)=\(.value)"] | join(" "))"'
  echo "helm values under obi: $(helm get values splunk-otel-collector -n splunk -o json | jq -c '.obi // {}')"
}

restarts_by_pod() {   # pod name and restarts added between two snapshots. A replaced pod counts as one, because a new pod starts at zero
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
