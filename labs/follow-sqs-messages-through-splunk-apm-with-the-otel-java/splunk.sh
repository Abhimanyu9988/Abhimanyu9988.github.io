# Reads from the Splunk Observability Cloud API for fix.sh. Sourced, never run on its own. Needs API and SPLUNK_API_TOKEN.
series() { curl -sS --max-time 20 -G -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/metrictimeseries" --data-urlencode "query=$1" --data-urlencode "limit=1000" 2>/dev/null; }

topology() {   # Splunk's Go client sends start/end in epoch milliseconds. ISO times are tried second.
  local now range body out
  now=$(jq -rn 'now | floor')
  for range in "$(( (now - 1500) * 1000 ))/$(( now * 1000 ))" "$(jq -rn --argjson n "$now" '$n - 1500 | todate')/$(jq -rn --argjson n "$now" '$n | todate')"; do
    body=$(jq -cn --arg t "$range" --arg e "$LAB_NAME" '{timeRange: $t, tagFilters: [{name: "sf_environment", operator: "equals", scope: "GLOBAL", value: $e}]}')
    out=$(curl -sS --max-time 20 -X POST -H "X-SF-Token: $SPLUNK_API_TOKEN" -H "Content-Type: application/json" -d "$body" "$API/v2/apm/topology" 2>/dev/null)
    jq -e '.data' <<<"$out" >/dev/null 2>&1 && break
  done
  echo "$out"
}

wait_cluster() {   # seconds. Sets cluster to how many of the cluster's series Splunk holds.
  local t0
  t0=$(date +%s)
  cluster=0
  while [ $(( $(date +%s) - t0 )) -lt "$1" ]; do
    cluster=$(series "k8s.cluster.name:${LAB_NAME}" | jq -r '.count // (.results | length) // 0' 2>/dev/null || echo 0)
    [ "${cluster:-0}" -gt 0 ] && break
    sleep 15
  done
}

show_map() { jq -r '(.data.nodes[]? | "  \(.serviceName) inferred=\(.inferred // false)"), (.data.edges[]? | "  \(.fromNode) -> \(.toNode)")' 2>/dev/null || true; }

show_trace() {   # trace ID, its spans
  echo "-- trace $1 from Splunk APM"
  jq -r '.[] | "  \(.serviceName) \(.operationName)"' <<<"$2" 2>/dev/null | sort -u | awk 'NR <= 12'
}

real_services() { jq -r '[.data.nodes[]? | select((.inferred // false) | not) | .serviceName] | map(select(. == "orders" or . == "fulfilment")) | unique | length' 2>/dev/null; }

# 1 when the map runs from orders to fulfilment, directly or through the inferred queue node between them.
queue_path() {
  jq -r '[.data.edges[]?] as $e | ($e | map(select(.fromNode == "orders") | .toNode)) as $n | if ($e | any(.toNode == "fulfilment" and (.fromNode == "orders" or (.fromNode as $f | $n | index($f) != null)))) then 1 else 0 end' 2>/dev/null
}

trace_items() {   # every span Splunk holds for a trace: the newest segment, then each segment, because a queue can split a trace in time
  local seg
  {
    curl -sS --max-time 20 -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/apm/trace/$1/latest" 2>/dev/null | jq -c 'if type == "array" then . else [] end' 2>/dev/null
    for seg in $(curl -sS --max-time 20 -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/apm/trace/$1/segments" 2>/dev/null | jq -r 'if type == "array" then .[] else empty end' 2>/dev/null); do
      curl -sS --max-time 20 -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/apm/trace/$1/$seg" 2>/dev/null | jq -c 'if type == "array" then . else [] end' 2>/dev/null
    done
  } | jq -cs 'add // []' 2>/dev/null
}
