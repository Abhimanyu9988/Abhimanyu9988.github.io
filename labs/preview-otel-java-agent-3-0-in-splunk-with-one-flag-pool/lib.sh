# The readings behind reproduce.sh and fix.sh: Splunk reads, the trace read, the load pods and small arithmetic.
# Sourced, never run alone. Every SignalFlow program sent is appended to signalflow-programs.txt and printed.
# The default pair (orders, inventory) sends deployment.environment=$DEFAULT_ENV. The preview pair (orders-v3,
# inventory-v3) runs the same jar and image with OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true and sends $PREVIEW_ENV.
# Both pairs report service names orders and inventory, so every read here filters on the environment.
API="https://api.${SPLUNK_REALM}.signalfx.com"
STREAM="https://stream.${SPLUNK_REALM}.signalfx.com"
DEFAULT_ENV="${LAB_NAME}"
PREVIEW_ENV="${LAB_NAME}-v3"
LEGACY_POOL="db.client.connections"     # the 2.x names, as the agent sends them with its defaults
NEW_POOL="db.client.connection"         # the stable names the 3.0 preview sends
# Keys the free check saw on the agent's database spans: the default run, then the preview run.
LEGACY_KEYS="db.connection_string db.name db.operation db.sql.table db.statement db.system db.user"
NEW_KEYS="db.namespace db.query.summary db.query.text db.system.name db.operation.name db.collection.name"

series() { curl -sS --max-time 20 -G -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/metrictimeseries" --data-urlencode "query=$1" --data-urlencode "limit=1000" 2>/dev/null; }

# The metric names exactly as Splunk stores them, for one environment.
names_in() { series "sf_metric:db.* AND deployment.environment:$1" | jq -r '[.results[]?.metric // empty] | unique | .[]' 2>/dev/null; }

# Pool series under one name family for one environment and service. The trailing dot keeps db.client.connection.
# from also matching db.client.connections.
pool_series() {   # environment, family, service
  series "sf_metric:$2.* AND deployment.environment:$1 AND service.name:$3" | jq --arg f "$2." '[.results[]? | select(.metric | startswith($f))] | length' 2>/dev/null || echo 0
}

op_series() { series "sf_metric:db.client.operation.duration* AND deployment.environment:$1" | jq '.results | length' 2>/dev/null || echo 0; }

# SignalFlow answers as a stream of events. This prints one value per point between start and stop, summed across series.
signalflow() {   # program, start ms, stop ms
  curl -sS --max-time 60 -X POST -H "X-SF-Token: $SPLUNK_API_TOKEN" -H "Content-Type: text/plain" --data-binary "$1" \
    "$STREAM/v2/signalflow/execute?start=$2&stop=$3&resolution=60000&immediate=true" 2>/dev/null |
    awk '/^event:/ { if (b != "") print e "\t" b; e = $2; b = ""; next }
         /^data:/  { sub(/^data: ?/, ""); b = b $0; next }
         END       { if (b != "") print e "\t" b }' |
    jq -Rr --argjson from "$2" --argjson to "$3" 'split("\t") | select(.[0] == "data") | (.[1] | fromjson? // {})
      | select(.logicalTimestampMs >= $from and .logicalTimestampMs <= $to) | [.data[]?.value // empty] | add // empty' 2>/dev/null
}

# The mean connection use time as Splunk stores it, over one window: the sum divided by the count, across both services.
# No unit conversion happens here, so the old metric reads in milliseconds and the new one in seconds.
use_time_mean() {   # environment, family, start ms, stop ms
  local f="filter('deployment.environment', '$1')" s c
  local ps="data('$2.use_time_sum', filter=$f, rollup='delta').sum().publish('s')"
  local pc="data('$2.use_time_count', filter=$f, rollup='delta').sum().publish('c')"
  printf '%s\n%s\n' "$ps" "$pc" | tee -a signalflow-programs.txt | sed 's/^/  program: /' >&2
  local try
  for try in 1 2 3; do   # a stream can come back empty once, so a sum or count of zero is read again
    s=$(signalflow "$ps" "$3" "$4" | awk '{t += $1} END {printf "%.6f", t}')
    c=$(signalflow "$pc" "$3" "$4" | awk '{t += $1} END {printf "%.0f", t}')
    awk -v s="$s" -v c="$c" 'BEGIN{ exit !(s > 0 && c > 0) }' && break
    echo "  read $try gave sum $s over $c uses; reading again" >&2; sleep 5
  done
  echo "  $2.use_time in $1 from $(( $3 / 1000 )) to $(( $4 / 1000 )): sum $s over $c uses" >&2
  awk -v s="$s" -v c="$c" 'BEGIN { if (c > 0) printf "%.6f", s / c; else print "" }'
}

# The trace as Splunk APM returns it. Splunk can take a minute or two to index a trace, so this asks again for up to
# about three minutes, and keeps the raw reply in the lab directory.
fetch_trace() {   # trace id, file
  local i
  for i in $(seq 1 12); do
    curl -sS --max-time 20 -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/apm/trace/$1/latest" -o "$2" 2>/dev/null
    [ -n "$(db_keys "$2")" ] && return 0
    sleep 15
  done
  return 1
}

# Every database span's key=value pairs, sorted, one line per span. Tags can come as an object or as key/value pairs.
db_spans() {
  jq -r '.. | objects | select(has("tags")) | . as $s
    | (.tags | if type == "object" then to_entries elif type == "array" then map({key: .key, value: .value}) else [] end
       | map(select(.key | startswith("db.")))) as $t
    | select($t | length > 0)
    | "\($s.serviceName // $s.service // "?") \($s.operationName // $s.name // "?"): \($t | sort_by(.key) | map("\(.key)=\(.value | tostring)") | join(" "))"' "$1" 2>/dev/null | sort -u
}

# How many services have database spans on a trace, from db_spans' first word.
db_services() { db_spans "$1" | awk '{print $1}' | sort -u | grep -c . || true; }

db_keys() { db_spans "$1" | sed 's/^[^:]*: //' | tr ' ' '\n' | sed 's/=.*//' | grep '^db\.' | sort -u | tr '\n' ' ' | sed 's/ $//'; }

count_in() {   # space-separated keys found, space-separated keys to count
  local n=0 k
  for k in $2; do [[ " $1 " == *" $k "* ]] && n=$(( n + 1 )); done
  echo "$n"
}

# What a failed run needs to explain itself: the agent's export errors, the Collector's logs and what Splunk holds.
diagnose() {
  local s pod
  echo "-- diagnostics: agent export errors"
  for s in orders inventory orders-v3 inventory-v3; do
    kubectl -n demo logs "deploy/$s" -c app --tail=2000 2>/dev/null | grep -iE 'failed to export|exporter.*(error|fail)|connection refused|\bERROR\b' | tail -4 | cut -c1-200 | sed "s/^/  $s: /"
  done
  for pod in $(kubectl -n splunk get pods -l component=otel-collector-agent -o name 2>/dev/null | sed 's|pod/||'); do
    kubectl -n splunk logs "$pod" --tail=500 2>/dev/null | grep -iE 'error|dropp|refused|unauthorized|forbidden' | tail -5 | cut -c1-220 | sed "s/^/  collector $pod: /"
  done
  for e in "$DEFAULT_ENV" "$PREVIEW_ENV"; do
    echo "-- database metric names in Splunk for $e"
    names_in "$e" | sed 's/^/  /'
  done
}

# One load pod for one pair. The load script's ConfigMap is already in place from reproduce.sh.
load_pod() {   # pod name, target service
  awk 'f; /^---$/ { f = 1 }' k8s/20-load.yaml |
    sed -e "s/LOAD_NODE/$LOAD_NODE/" -e 's/value: "540"/value: "90"/' -e "s/^  name: load\$/  name: $1/" \
        -e "s/{ name: TARGET, value: \"orders\" }/{ name: TARGET, value: \"$2\" }/" | kubectl apply -f - >/dev/null
}

# Failed requests and the rate reached per pair, over the three rounds. A missing count is a failed read, never a zero.
# Writes default_rps, preview_rps, default_errors and preview_errors to load.env.
load_counts() {
  local mode r e n errs sent name rps
  : > load.env
  for mode in d v; do
    errs=0; sent=0
    for r in 1 2 3; do
      e=$(grep -oE 'errors=[0-9]+' "load-$mode$r.txt" | tail -1 | cut -d= -f2)
      n=$(grep -oE 'requests=[0-9]+' "load-$mode$r.txt" | cut -d= -f2)
      [ -n "$e" ] && [ -n "$n" ] || fail "round $r: load-$mode$r printed no counts"
      errs=$(( errs + e )); sent=$(( sent + n ))
    done
    name=$([ "$mode" = d ] && echo default || echo preview)
    rps=$(awk -v n="$sent" 'BEGIN{printf "%.2f", n / 180}')
    echo "$name: failed requests $errs, requests after warm-up $sent over 3 x 60 s, $rps per second (target 20)"
    echo "HERO_MEASURE: load_errors[$name]=$errs"
    echo "HERO_MEASURE: achieved_rps[$name]=$rps"
    printf '%s_rps=%s\n%s_errors=%s\n' "$name" "$rps" "$name" "$errs" >> load.env
  done
}

# A new counter has no earlier point to take a delta from, so the preview pair gets one unmeasured 90-second load before
# round 1, the way the default pair was already warmed by reproduce.sh. Nothing from it is read.
warm_preview() {
  kubectl -n demo delete pod load-warm --ignore-not-found --wait=true >/dev/null 2>&1
  load_pod load-warm orders-v3 || fail "could not start the warm-up load"
  kubectl -n demo wait pod/load-warm --for=jsonpath='{.status.phase}'=Succeeded --timeout="$(cap 130 400)s" >/dev/null || fail "the warm-up load did not finish"
  echo "warm-up on the preview pair: $(kubectl -n demo logs load-warm | grep -oE 'requests=[0-9]+ errors=[0-9]+')"
  sleep 30
}
