# Plain helpers for lib.sh: Splunk and Postgres calls, and small arithmetic. Sourced, never run alone.
API="https://api.${SPLUNK_REALM}.signalfx.com"
STREAM="https://stream.${SPLUNK_REALM}.signalfx.com"
METRIC=db.client.operation.duration
TRIM="-Dotel.java.disabled.resource.providers=io.opentelemetry.instrumentation.resources.ProcessResourceProvider,io.opentelemetry.instrumentation.resources.ProcessRuntimeResourceProvider"

series() { curl -sS --max-time 20 -G -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/metrictimeseries" --data-urlencode "query=$1" --data-urlencode "limit=1000" 2>/dev/null; }

duration_series() { series "sf_metric:${METRIC}* AND deployment.environment:${LAB_NAME}"; }

# The Collector's main exporter decides how a histogram is stored, so its one setting is printed from the rendered
# config. Only this key is printed: the rest of the config stays out of the log.
exporter_config() {
  local relay
  relay=$(kubectl -n splunk get configmap splunk-otel-collector-otel-agent -o jsonpath='{.data.relay}' 2>/dev/null)
  [ -n "$relay" ] || { echo "  the Collector's rendered config could not be read"; return; }
  awk '/^  signalfx:$/ { on = 1; next } on && /^  [^ ]/ { on = 0 } on && /send_otlp_histograms/ { found = 1; print "  signalfx " $0 } END { if (!found) print "  signalfx send_otlp_histograms: not set, the chart default" }' <<<"$relay"
}

# The metric names exactly as Splunk stores them.
raw_names() { series "$1" | jq -r '[.results[]?.metric // empty] | unique | .[]' 2>/dev/null; }

# Cumulative CPU steal for the app node, as jiffies stolen and jiffies in total, from the node's /proc/stat.
cpu_stat() { kubectl -n demo exec "$1" -c app -- head -1 /proc/stat 2>/dev/null | awk '{ t = 0; for (i = 2; i <= NF; i++) t += $i; print $9, t }'; }

# SignalFlow answers as a stream of events, and each event's JSON spans several `data:` lines. This writes one
# {ts, le, value} line per point, and keeps the raw reply in signalflow.txt. Resolution is pinned to the agent's 60 seconds.
signalflow() {   # program, start ms, stop ms
  curl -sS --max-time 60 -X POST -H "X-SF-Token: $SPLUNK_API_TOKEN" -H "Content-Type: text/plain" --data-binary "$1" \
    "$STREAM/v2/signalflow/execute?start=$2&stop=$3&resolution=60000&immediate=true" 2>&1 | tee signalflow.txt |
    awk '/^event:/ { if (b != "") print e "\t" b; e = $2; b = ""; next }
         /^data:/  { sub(/^data: ?/, ""); b = b $0; next }
         END       { if (b != "") print e "\t" b }' |
    jq -Rcn --argjson from "$2" --argjson to "$3" 'reduce (inputs | split("\t") | {e: .[0], b: (.[1] | fromjson? // {})}) as $x ({ids: {}, out: []};
        if $x.e == "metadata" then .ids[$x.b.tsId] = ($x.b.properties.le // "all")
        elif $x.e == "data" then .ids as $ids | .out += [$x.b.data[]? | select(.value != null)
          | {ts: $x.b.logicalTimestampMs, le: ($ids[.tsId] // "all"), value: .value}]
        else . end) | .out[] | select(.ts >= $from and .ts <= $to)' 2>/dev/null
}

median() { sort -n | awk '{ v[NR] = $1 } END { if (NR) print v[int((NR + 1) / 2)] }'; }

# Psql in the Postgres pod, as a server-side reference the agent has no part in.
pg() { kubectl -n demo exec deploy/postgres -- psql -U postgres -tAc "$1" 2>/dev/null; }

# Percent gap between two numbers, without its sign.
gap() { awk -v a="$1" -v b="$2" 'BEGIN{d = (a - b) / b * 100; printf "%.1f", (d < 0 ? -d : d)}'; }

# The t3 credit balance for the app node, because a credit squeeze only partly shows as steal.
cpu_credits() {   # node name, start epoch, stop epoch
  local id
  id=$(kubectl get node "$1" -o jsonpath='{.spec.providerID}' 2>/dev/null | sed 's|.*/||')
  timeout 25 aws cloudwatch get-metric-statistics --namespace AWS/EC2 --metric-name CPUCreditBalance --region "$AWS_REGION" \
    --dimensions "Name=InstanceId,Value=$id" --start-time "$(date -u -d "@$2" +%FT%TZ)" --end-time "$(date -u -d "@$3" +%FT%TZ)" \
    --period 300 --statistics Minimum Maximum --output json 2>/dev/null |
    jq -r 'if (.Datapoints | length) > 0 then "CPU credit balance on the app node: min \([.Datapoints[].Minimum] | min), max \([.Datapoints[].Maximum] | max), \(.Datapoints | length) points" else "CPU credit balance on the app node: no points yet" end' 2>/dev/null ||
    echo "CPU credit balance on the app node: not available here"
}

# The database span attribute keys in one service's log.
span_keys() { grep LoggingSpanExporter "$1" | grep -E ' CLIENT .*db\.' | tail -1 | grep -oE '\bdb\.[a-z_.]+=' | tr -d = | sort -u | tr '\n' ' '; }

stats() { jq -r '.p95_ms' "$1" | sort -n | awk '{ v[NR] = $1 } END { if (NR) printf "median %.2f, min %.2f, max %.2f", v[int((NR + 1) / 2)], v[1], v[NR] }'; }

# Series by suffix, by service and by statement, from the file `duration_series` wrote. A rollout repeats this set
# once per statement per service.
breakdown() {
  jq -r '
    [.results[]? | .kind = (.metric | sub("^.*duration_?"; "")) | .d = .dimensions] as $r
    | ($r | group_by(.kind) | .[] | "  _\(.[0].kind): \(length) series"),
      ($r | group_by([.d["service.name"], .d["db.query.summary"]]) | .[] | "  service.name=\(.[0].d["service.name"]) db.query.summary=\(.[0].d["db.query.summary"]): \(length) series"),
      "  bucket boundaries in Splunk (le): \([$r[] | .d.le // empty] | unique | sort_by(tonumber? // 1e9) | join(", "))"' "$1" 2>/dev/null
}

series_for() { jq --arg s "$2" --arg t "${2##* }" --arg o "${2%% *}" '[.results[]? | .dimensions as $d | select(($d["db.query.summary"] // "" | ascii_downcase) == $s or (($d["db.collection.name"] == $t) and (($d["db.operation.name"] // "" | ascii_downcase) == $o)))] | length' "$1" 2>/dev/null; }

# Proves the setting is the only change: jar, agent options and environment variable names against the before run.
same_as_before() {
  : > agent-options-after.txt
  for s in orders inventory; do
    p=$(newest "$s")
    echo "$s effective setting: $(kubectl -n demo exec "$p" -c app -- printenv OTEL_SEMCONV_STABILITY_OPT_IN)"
    kubectl -n demo get "$p" -o json | jq -r '.spec.containers[0].env[] | select(.name == "JAVA_TOOL_OPTIONS") | .value' >> agent-options-after.txt
    kubectl -n demo logs "$p" -c app 2>/dev/null | grep -m1 'opentelemetry-javaagent - version' | sed "s/^/$s /"
  done
  same=$(kubectl -n demo exec "$(newest orders)" -c app -- sha256sum /lab/app.jar | awk '{print $1}')
  [ "$same" = "$(cat jar-sha.txt)" ] && echo "jar unchanged from the before run" || fail "the jar changed, so the comparison would be unfair"
  sed "s| $TRIM||" agent-options-after.txt | cmp -s agent-options.txt - && echo "JAVA_TOOL_OPTIONS unchanged from the before run, apart from the trim when title_changes is 2" || fail "the agent options changed, so the comparison would be unfair"
  kubectl -n demo get "$(newest inventory)" -o json | jq -r '.spec.containers[0].env[].name' | sort > env-after.txt
  echo "environment variable names changed since the before run: $(diff env-before.txt env-after.txt | grep '^[<>]' | tr '\n' ' ')"
}

# Lookups Splunk counted against what the load sent, minute by minute. A point holds the export from its minute, and
# that export covers a stretch across the two load minutes before the point. Its phase isn't known, so `low` and `high`
# are the two ends, and `expected` is their middle. Which minutes count is decided from the load's sent lines alone,
# and a minute that doesn't count is still printed with the reason.
# Sets queries, expected, low, high, compared and empty for fix.sh.
compare_counts() {
  grep -oE 'minute=[0-9]+ sent=[0-9]+' load-after.txt | sed 's/minute=//; s/sent=//' > load-minutes.txt
  jq -cRn --slurpfile counts lookup-count.jsonl --argjson from "$from_ms" --argjson to "$to_ms" '
    ([inputs | split(" ") | {key: .[0], value: (.[1] | tonumber)}] | from_entries) as $sent
    | ($sent | [.[]] | sort | .[length / 2 | floor]) as $usual
    | range(($from / 60000 | ceil); ($to / 60000 | floor) + 1) | . * 60
    | . as $m | ($sent[($m - 120 | tostring)]) as $before | ($sent[($m - 60 | tostring)]) as $during
    | (if $before == null or $during == null then "the load printed no line for it"
       elif $before < $usual * 0.9 or $during < $usual * 0.9 then "the load sent under 90% of its usual \($usual)"
       else "" end) as $why
    | {m: $m, before: ($before // 0), during: ($during // 0), why: $why,
       splunk: ([$counts[] | select(.ts == $m * 1000) | .value] | add // 0)}' load-minutes.txt > count-all.jsonl
  echo "-- lookups in Splunk against requests the load sent, minute by minute"
  jq -r '"  minute ending \(.m | todate): Splunk=\(.splunk), load sent \(.before) the minute before and \(.during) in this minute, bounds \([.before, .during] | min) to \([.before, .during] | max), " + (if .why == "" then "kept" else "dropped: \(.why)" end)' count-all.jsonl
  jq -c 'select(.why == "")' count-all.jsonl > count-table.jsonl
  queries=$(jq -s 'map(.splunk) | add // 0' count-table.jsonl)
  low=$(jq -s 'map(.before) | add // 0' count-table.jsonl)
  high=$(jq -s 'map(.during) | add // 0' count-table.jsonl)
  [ "$low" -le "$high" ] || { local swap=$low; low=$high; high=$swap; }
  expected=$(( (low + high) / 2 ))
  compared=$(grep -c . count-table.jsonl || true)
  empty=$(jq -s 'map(select(.splunk == 0)) | length' count-table.jsonl)
}

# Which stored bucket holds the timer's p95, from the `le` values Splunk holds, and whether the SignalFlow p95 is in it.
# Sets timer_lower, timer_upper and in_bucket for fix.sh.
timer_bucket() {   # timer p95 ms, SignalFlow p95 ms
  read -r timer_lower timer_upper <<<"$(jq -r --argjson x "${1:-0}" '
    [.results[]?.dimensions.le // empty | select(. != "+Inf") | tonumber * 1000000 | round / 1000] | unique as $e
    | "\(([0] + [$e[] | select(. < $x)]) | max) \(([$e[] | select(. >= $x)] | min) // "+Inf")"' series.json 2>/dev/null)"
  in_bucket=$(awk -v p="$2" -v l="${timer_lower:-0}" -v u="${timer_upper:-0}" 'BEGIN{print (p > l && (u == "+Inf" || p <= u + 0)) ? 1 : 0}')
}

# The dimensions Splunk really holds on the bucket series, printed before any read.
dimensions() {
  jq -r '[.results[]? | select(.metric | endswith("_bucket")) | .dimensions] as $d
    | ($d | map(keys) | add // [] | unique | .[]) as $k | "  \($k): \([$d[][$k] // empty] | unique | join(", "))"' series.json 2>/dev/null
}

# Series and the largest dimension count for the metrics that matter, as Splunk holds them.
report_families() {
  local m
  echo "-- series in Splunk for this environment, with the most dimensions on any one"
  for m in jvm.memory.used db.client.connections.usage http.server.request.duration_count http.server.request.duration_bucket "${METRIC}_count" "${METRIC}_bucket"; do
    series "sf_metric:$m AND deployment.environment:${LAB_NAME}" | jq -r --arg m "$m" '"  \($m): series=\(.results | length), most dimensions=\([.results[]?.dimensions | del(.sf_metric) | length] | max // 0)"' 2>/dev/null
  done
}

# What a failed run needs to explain itself: the agent's own errors, the Collector's counters and logs, and Splunk's view.
diagnose() {
  local s pod
  echo "-- diagnostics: agent export errors"
  for s in orders inventory; do
    kubectl -n demo logs "$(newest "$s")" -c app --tail=2000 2>/dev/null | grep -iE 'failed to export|exporter.*(error|fail)|connection refused|\bERROR\b' | tail -4 | cut -c1-200 | sed "s/^/  $s: /"
  done
  for pod in $(kubectl -n splunk get pods -l component=otel-collector-agent -o name 2>/dev/null | sed 's|pod/||'); do
    echo "-- diagnostics: Collector counters on $pod"
    kubectl get --raw "/api/v1/namespaces/splunk/pods/$pod:8889/proxy/metrics" 2>/dev/null |
      grep -E '^otelcol_(receiver_accepted|receiver_refused|processor_refused|processor_dropped|exporter_sent|exporter_send_failed|exporter_enqueue_failed)_metric_points' | grep -E 'exporter="signalfx"|receiver="otlp"|processor=' | cut -c1-150 | head -14 | sed 's/^/  /'
    kubectl -n splunk logs "$pod" --tail=500 2>/dev/null | grep -iE 'error|dropp|refused|unauthorized|forbidden' | tail -5 | cut -c1-220 | sed 's/^/  log: /'
  done
  report_families
}

# Sends one point each with 30, 36, 37 and 40 dimensions, to show what Splunk keeps at its limit.
dimension_probe_send() {
  local n body
  for n in 30 36 37 40; do
    body=$(jq -cn --arg e "$LAB_NAME" --argjson n "$n" '{gauge: [{metric: "lab.dimension.probe.\($n)", value: 1, dimensions: (([range(1; $n) | {key: "d\(.)", value: "x"}] | from_entries) + {"deployment.environment": $e})}]}')
    echo "probe with $n dimensions: HTTP $(curl -sS --max-time 20 -o /dev/null -w '%{http_code}' -X POST -H "X-SF-Token: $SPLUNK_ACCESS_TOKEN" -H "Content-Type: application/json" -d "$body" "https://ingest.${SPLUNK_REALM}.signalfx.com/v2/datapoint")"
  done
}
dimension_probe_read() {
  local i
  for i in 1 2 3 4; do
    got=$(series "sf_metric:lab.dimension.probe* AND deployment.environment:${LAB_NAME}" | jq -r '[.results[]?.metric] | unique | join(" ")' 2>/dev/null)
    grep -q 'probe.30' <<<"$got" && break
    sleep 15
  done
  echo "probes Splunk holds: ${got:-none}"
}

# Resource attribute names on the first metric line of a console log.
resource_keys() { grep -m1 'LoggingMetricExporter - metric:' "$1" | sed -E 's/, instrumentationScopeInfo.*//; s/^.*[ {]attributes=\{//' | grep -oE '(^|, )[a-z_.]+=' | sed -E 's/^, //; s/=$//' | sort -u; }

# Median, min and max of the numbers on stdin.
spread() { sort -n | awk '{ v[NR] = $1 } END { if (NR) printf "median %.2f, min %.2f, max %.2f", v[int((NR + 1) / 2)], v[1], v[NR] }'; }

# Each minute's reading from the real bucket counts, with a straight-line estimate inside the bucket. The query count
# comes from the _count series. Every minute Splunk returned is kept, because that count is what is being tested.
minutes() {   # bucket points file, count points file
  jq -cs --slurpfile counts "$2" 'group_by(.ts) | map(
    .[0].ts as $ts | ([$counts[] | select(.ts == $ts) | .value] | add // 0) as $n
    | (map(select(.le != "+Inf" and .le != "all") | .le |= tonumber) | sort_by(.le)) as $b
    | ($n * 0.95) as $rank | select($n > 0)
    | (reduce $b[] as $x ({low: 0, cum: 0, upper: null, p: null};
        if .p != null then .
        elif $x.value >= $rank then .upper = $x.le | .p = (.low + ($x.le - .low) * ($rank - .cum) / (if $x.value > .cum then $x.value - .cum else 1 end))
        else .low = $x.le | .cum = $x.value end)) as $r
    | {ts: $ts, queries: $n, p95_ms: (($r.p // $r.low) * 1000), lower_ms: ($r.low * 1000), upper_ms: (($r.upper // 0) * 1000),
       under: [$b[] | {le_ms: (.le * 1000), share: (.value / $n * 100)}]}) | .[]' "$1" 2>/dev/null
}

# Two reads for one statement: buckets and query count per minute, then that statement's own minutes.
read_statement() {   # db.query.summary, label
  local f
  f=$(statement_filter "$1")
  signalflow "data('${METRIC}_bucket', filter=${f}, rollup='delta').sum(by=['le']).publish('buckets')" "$from_ms" "$to_ms" > "$2-buckets.jsonl"
  signalflow "data('${METRIC}_count', filter=${f}, rollup='delta').sum().publish('queries')" "$from_ms" "$to_ms" > "$2-count.jsonl"
  minutes "$2-buckets.jsonl" "$2-count.jsonl" > "$2-minutes.jsonl"
}

# What the routes in other_routes showed, from the pod logs and the metric names. Sets dup_sets, new_pool and routes.
route_results() {
  local keys dup_exports prop_exports
  keys=$(span_keys dup.log)
  dup_exports=$(grep -c "name=$METRIC," dup.log || true)
  prop_exports=$(grep -c "name=$METRIC," sysprop.log || true)
  dup_sets=$(( $(grep -c 'db.query.text' <<<"$keys") + $(grep -c 'db.statement' <<<"$keys") ))
  [ "$dup_exports" -ge 1 ] || dup_sets=0
  echo "name sets on the database/dup route: $dup_sets, with $METRIC exports=$dup_exports and span keys: $keys"
  echo "-- connection pool metric names, before and with the setting"
  grep '^db\.client\.connections\?\.' names-before.txt | sed 's/^/  before: /'
  grep '^db\.client\.connection\.' names-after.txt | sed 's/^/  with the setting: /'
  new_pool=$(grep -cE '^db\.client\.connection\.' names-after.txt || true)
  echo "pool metric names with the setting: $new_pool"
  routes=$(( $([ "$known" -ge 1 ] && echo 1 || echo 0) + $([ "$prop_exports" -ge 1 ] && echo 1 || echo 0) ))
  echo "the agent read this setting from $routes routes: the environment variable on inventory, and otel.semconv-stability.opt-in on orders with $prop_exports exports"
}

# Cloud and host attribute names on the series in series.json.
cloud_keys() { jq -r '[.results[]?.dimensions | keys[] | select(test("^cloud\\.|^host\\.(id|image\\.id|type)$"))] | unique | .[]' series.json 2>/dev/null; }

# Dimensions on each stored audit statement series with the opt-in alone, leaving out sf_metric.
audit_dims() { jq '.results[]?.dimensions | select(.["process.pid"] and (.["db.query.summary"] // "" | ascii_downcase) == "select audit") | del(.sf_metric) | length' untrimmed.json; }

# Reads the series for about 2 minutes or until the audit statement appears, so a zero isn't only lag.
# Sets waited, and leaves the last reply in untrimmed.json.
wait_audit() {   # the query's environment term
  local t0 i limit
  t0=$(date +%s)
  limit=$(cap 120 20)
  for i in 1 2 3 4 5 6 7 8 9; do
    series "sf_metric:${METRIC}_bucket AND $1" > untrimmed.json
    waited=$(( $(date +%s) - t0 ))
    [ "$(audit_dims | jq -s length)" -gt 0 ] || [ "$waited" -ge "$limit" ] && break
    sleep 15
  done
}

# What Splunk stored from the opt-in alone, per statement.
statements_stored() { jq -r '.results[]? | .dimensions | "\(.["db.query.summary"] // "no summary"), \(del(.sf_metric) | length) dimensions"' untrimmed.json | sort | uniq -c | sed 's/^/  stored from the opt-in alone, series and statement: /'; }

# inventory takes database/dup through the environment variable, on the options the main run used. orders takes the system
# property on the other side of the trim, so the run can count what the trim removes. Console output shows both in pod
# logs. This runs after the read window. Sets dup_sets, new_pool and routes for fix.sh.
other_routes() {
  local con="-Dotel.traces.exporter=otlp,console -Dotel.metrics.exporter=otlp,console -Dotel.metric.export.interval=5000" main="" other="$TRIM"
  [ "$title_changes" -eq 2 ] && { main="$TRIM"; other=""; }
  kubectl -n demo set env deployment/inventory OTEL_SEMCONV_STABILITY_OPT_IN=database/dup "JAVA_TOOL_OPTIONS=$(sed -n 2p agent-options.txt) $con $main" >/dev/null
  kubectl -n demo set env deployment/orders OTEL_SEMCONV_STABILITY_OPT_IN- "JAVA_TOOL_OPTIONS=$(sed -n 1p agent-options.txt) $con $other -Dotel.semconv-stability.opt-in=database" >/dev/null
  kubectl -n demo rollout status deploy/inventory --timeout="$(cap 60 45)s" >/dev/null
  kubectl -n demo rollout status deploy/orders --timeout="$(cap 60 45)s" >/dev/null
  kubectl -n demo delete pod load --ignore-not-found --wait=true >/dev/null 2>&1
  sed -e "s/LOAD_NODE/$LOAD_NODE/" -e 's/value: "30"/value: "2"/' -e 's/value: "540"/value: "15"/' k8s/20-load.yaml | kubectl apply -f - >/dev/null
  kubectl -n demo wait pod/load --for=jsonpath='{.status.phase}'=Succeeded --timeout="$(cap 45 15)s" >/dev/null
  sleep 8
  kubectl -n demo logs "$(newest inventory)" -c app > dup.log 2>/dev/null
  kubectl -n demo logs "$(newest orders)" -c app > sysprop.log 2>/dev/null
  route_results
}
