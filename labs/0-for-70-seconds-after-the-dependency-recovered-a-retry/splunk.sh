# Splunk Observability Cloud reads for fix.sh: request counts from the services' HTTP metrics, and the APM service map. Sourced, never run alone. Needs SPLUNK_REALM and SPLUNK_API_TOKEN.
# Every SignalFlow program sent is printed, and its points are summed over one window.
API="https://api.${SPLUNK_REALM}.signalfx.com"
STREAM="https://stream.${SPLUNK_REALM}.signalfx.com"
series() { curl -sS --max-time 20 -G -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/metrictimeseries" --data-urlencode "query=$1" --data-urlencode "limit=1000" 2>/dev/null; }

wait_cluster() {   # seconds. Sets cluster to how many of the cluster's series Splunk holds.
  local t0=$(date +%s); cluster=0
  while [ $(( $(date +%s) - t0 )) -lt "$1" ]; do
    cluster=$(series "k8s.cluster.name:${LAB_NAME}" | jq -r '.count // (.results | length) // 0' 2>/dev/null || echo 0)
    [ "${cluster:-0}" -gt 0 ] && break
    sleep 15
  done
}

# SignalFlow answers as a stream of events. This prints one value per point between start and stop, summed across series.
signalflow() {   # program, start ms, stop ms, resolution ms
  curl -sS --max-time 60 -X POST -H "X-SF-Token: $SPLUNK_API_TOKEN" -H "Content-Type: text/plain" --data-binary "$1" \
    "$STREAM/v2/signalflow/execute?start=$2&stop=$3&resolution=${4:-10000}&immediate=true" 2>/dev/null |
    awk '/^event:/ { if (b != "") print e "\t" b; e = $2; b = ""; next }
         /^data:/  { sub(/^data: ?/, ""); b = b $0; next }
         END       { if (b != "") print e "\t" b }' |
    jq -Rr --argjson from "$2" --argjson to "$3" 'split("\t") | select(.[0] == "data") | (.[1] | fromjson? // {})
      | select(.logicalTimestampMs >= $from and .logicalTimestampMs < $to) | "\(.logicalTimestampMs) \([.data[]?.value // empty] | add // 0)"' 2>/dev/null
}

# Reads the same count until two reads in a row agree, so a count isn't taken before Splunk has all of it. Sets count.
stable_count() {   # service, errors: all|true, start s, stop s
  local prev="" try
  for try in 1 2 3 4 5 6; do
    count=$(apm_count "$@")
    [ -n "$prev" ] && [ "$count" = "$prev" ] && [ "$count" != 0 ] && return 0
    [ "$2" = true ] && [ -n "$prev" ] && [ "$count" = "$prev" ] && return 0   # zero errors twice is a real zero
    prev=$count; sleep 15
  done
  echo "  $1 $2: reads didn't settle, last read $count" >&2
}

# The service map Splunk APM builds for this lab's environment over the last 25 minutes.
topology() {
  local now=$(date +%s) body
  body=$(jq -cn --arg t "$(( (now - 1500) * 1000 ))/$(( now * 1000 ))" --arg e "$LAB_NAME" '{timeRange: $t, tagFilters: [{name: "sf_environment", operator: "equals", scope: "GLOBAL", value: $e}]}')
  curl -sS --max-time 20 -X POST -H "X-SF-Token: $SPLUNK_API_TOKEN" -H "Content-Type: application/json" -d "$body" "$API/v2/apm/topology" 2>/dev/null
}
show_map() { jq -r '(.data.nodes[]? | "  \(.serviceName) inferred=\(.inferred // false)"), (.data.edges[]? | "  \(.fromNode) -> \(.toNode)")' 2>/dev/null || true; }

# Request counts for one service's route in one window, from the stable HTTP server metric the services send through the
# Collector (http.server.request.duration, so Splunk stores http.server.request.duration_count). Readiness probes hit
# /healthz, so filtering on the route keeps them out. Prints the per-10 s points to stderr.
route_of() { case "$1" in orders) echo '/orders/<int:order>' ;; inventory) echo '/reserve/<int:order>' ;; esac; }
apm_count() {   # service, errors: all|true, start s, stop s
  local f="filter('deployment.environment', '$LAB_NAME') and filter('service.name', '$1') and filter('http.route', '$(route_of "$1")')" try n
  [ "$2" = true ] && f="$f and filter('http.response.status_code', '503')"
  local prog="data('http.server.request.duration_count', filter=$f, rollup='delta').sum().publish()"
  echo "  program: $prog" >&2
  for try in 1 2 3; do   # a stream can come back empty once, so an empty read is asked again
    signalflow "$prog" "$(( $3 * 1000 ))" "$(( $4 * 1000 ))" > "sf-$1-$2.txt"
    n=$(awk '{t += $2} END {printf "%.0f", t}' "sf-$1-$2.txt")
    [ -s "sf-$1-$2.txt" ] && break
    echo "  read $try came back empty; reading again" >&2; sleep 10
  done
  echo "  $1 $2 from $3 to $4: $(awk '{printf "%s ", $2}' "sf-$1-$2.txt")" >&2
  if [ "${n:-0}" = 0 ] && [ "$2" = all ]; then   # say what Splunk does hold for this service, so a wrong name or label shows
    series "sf_metric:http.server* AND deployment.environment:$LAB_NAME AND service.name:$1" | jq -r '.results[:6][]? | "  held: \(.metric) \(.dimensions | to_entries | map("\(.key)=\(.value)") | join(" "))"' >&2
  fi
  echo "${n:-0}"
}
