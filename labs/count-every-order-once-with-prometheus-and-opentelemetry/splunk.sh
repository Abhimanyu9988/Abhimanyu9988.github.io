# Low-level Splunk Observability Cloud reads used by lib.sh: series, traces from APM by trace ID, and SignalFlow sums.
# Sourced, never run alone. Needs SPLUNK_REALM and SPLUNK_API_TOKEN.
API="https://api.${SPLUNK_REALM}.signalfx.com"
STREAM="https://stream.${SPLUNK_REALM}.signalfx.com"
series() { curl -sS --max-time 20 -G -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/metrictimeseries" --data-urlencode "query=$1" --data-urlencode "limit=1000" 2>/dev/null; }

wait_cluster() {   # seconds. Sets cluster to how many of the cluster's series Splunk holds.
  local t0; t0=$(date +%s); cluster=0
  while [ $(( $(date +%s) - t0 )) -lt "$1" ]; do
    cluster=$(series "k8s.cluster.name:${LAB_NAME}" | jq -r '.count // (.results | length) // 0' 2>/dev/null || echo 0)
    [ "${cluster:-0}" -gt 0 ] && break
    sleep 15
  done
}

trace_items() {   # every span Splunk APM holds for a trace: the newest segment, then each segment
  local seg
  {
    curl -sS --max-time 20 -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/apm/trace/$1/latest" 2>/dev/null | jq -c 'if type == "array" then . else [] end' 2>/dev/null
    for seg in $(curl -sS --max-time 20 -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/apm/trace/$1/segments" 2>/dev/null | jq -r 'if type == "array" then .[] else empty end' 2>/dev/null); do
      curl -sS --max-time 20 -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/apm/trace/$1/$seg" 2>/dev/null | jq -c 'if type == "array" then . else [] end' 2>/dev/null
    done
  } | jq -cs 'add // [] | unique_by(.spanId // .id // tostring)' 2>/dev/null
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
