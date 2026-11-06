# The steps behind reproduce.sh and fix.sh: how each reading is taken and judged. Sourced, never run alone.
# tools.sh holds the plain helpers: series, signalflow (pinned to resolution=60000), median, gap, pg, the CPU reads,
# other_routes (database/dup on inventory, the system property on orders, on the other side of the trim),
# breakdown (series by suffix, service and statement), series_for and same_as_before (jar, agent options and env names).
source ./tools.sh

# A statement's filter, from the dimensions in series.json, with the value exactly as Splunk stores it: db.query.summary
# when it is there, or else db.operation.name with db.collection.name. The summary reads "<operation> <table>".
statement_filter() {
  local op=${1%% *} table=${1##* } v
  v=$(jq -r --arg s "$1" '[.results[]?.dimensions["db.query.summary"] // empty | select(ascii_downcase == $s)][0] // empty' series.json 2>/dev/null)
  if [ -n "$v" ]; then
    echo "filter('deployment.environment', '${LAB_NAME}') and filter('db.query.summary', '$v')"
  else
    echo "filter('deployment.environment', '${LAB_NAME}') and filter('db.operation.name', '$op', '${op^^}') and filter('db.collection.name', '$table')"
  fi
}

# The known statement against references outside the histogram: a timer in inventory around the JDBC call, logged per
# minute, and pg_stat_statements. Sets p95, crosscheck, from_program, lower, upper, mean, mean_gap, client_p95 and truth_gap for fix.sh.
read_known() {
  local k P95 full native by_buckets got server
  k=$(statement_filter "select reserved")
  P95="data('${METRIC}_bucket', filter=${k}, rollup='delta').sum(by=['le']).histogram_percentile(pct=95, bucket_name='le').scale(1000).publish('p95_ms')"
  echo "-- the known statement, pg_sleep(0.03), one reading per minute"
  jq -r '"  minute ending \(.ts / 1000 | todate): queries=\(.queries) p95_ms=\(.p95_ms * 100 | round / 100) in the bucket \(.lower_ms) to \(.upper_ms) ms"' known-minutes.jsonl
  echo "  from the bucket series: $(stats known-minutes.jsonl)"
  full=$(jq -sc 'map(.ts)' known-minutes.jsonl)
  by_buckets=$(jq -r '.p95_ms' known-minutes.jsonl | median)
  # Only this program can give the p95. The bucket arithmetic is printed under its own name, never in its place.
  from_program=0
  native=$(signalflow "$P95" "$from_ms" "$to_ms" | jq -r --argjson full "$full" 'select(.ts as $t | $full | index($t)) | .value' 2>/dev/null)
  got=$(grep -c . <<<"$native" || true)
  echo "  program as it ran: $P95"
  echo "  readings from it: ${got:-0}, stream resolution: $(grep -oE '"resolutionMs" *: *[0-9]+' signalflow.txt | head -1)"
  [ "${got:-0}" -ge 1 ] && [ "${got:-0}" -le "$known" ] && from_program=1
  [ "$from_program" -eq 1 ] || echo "  SignalFlow reply: $(head -c 300 signalflow.txt | tr '\n' ' ')"
  grep . <<<"$native" | sed 's/^/  histogram_percentile() reading: /'
  echo "  histogram_percentile() p95: $(grep . <<<"$native" | spread)"
  crosscheck=$(awk -v v="$by_buckets" 'BEGIN{printf "%.2f", v}')
  p95=""
  [ "$from_program" -eq 1 ] && p95=$(grep . <<<"$native" | median | awk '{printf "%.2f", $1}')
  lower=$(jq -r '.lower_ms' known-minutes.jsonl | median)
  upper=$(jq -r '.upper_ms' known-minutes.jsonl | median)
  signalflow "data('${METRIC}_sum', filter=${k}, rollup='delta').sum().publish('seconds')" "$from_ms" "$to_ms" > known-sum.jsonl
  mean=$(jq -rn --slurpfile m known-minutes.jsonl --slurpfile s known-sum.jsonl '($m | map(.ts)) as $t | ([$s[] | select(.ts as $x | $t | index($x)) | .value] | add) / ($m | map(.queries) | add) * 1000 | . * 100 | round / 100')
  server=$(pg "select round(mean_exec_time::numeric, 2) from pg_stat_statements where query like '%pg_sleep%' and query not like '%pg_stat_statements%' order by calls desc limit 1")
  pg "select calls, round(mean_exec_time::numeric, 2), round(max_exec_time::numeric, 2), round(stddev_exec_time::numeric, 2), left(query, 60) from pg_stat_statements where query ~ '(stock|reserved|audit)' order by calls desc" | sed 's/^/  pg_stat_statements calls|mean|max|stddev|query: /'
  mean_gap=$(gap "$mean" "${server:-0}")
  kubectl -n demo logs "$(newest inventory)" -c app 2>/dev/null | grep '^KNOWN' | awk -v a="$(( from_ms / 1000 - 60 ))" -v b="$(( to_ms / 1000 ))" '{ split($2, m, "="); if (m[2] >= a && m[2] < b) print }' > known-timer.txt
  sed 's/^KNOWN /  timer in inventory: /' known-timer.txt
  echo "  timer p95: $(grep -oE 'p95_ms=[0-9.]+' known-timer.txt | cut -d= -f2 | spread)"
  client_p95=$(grep -oE 'p95_ms=[0-9.]+' known-timer.txt | cut -d= -f2 | median)
  truth_gap=$(gap "$p95" "${client_p95:-0}")
  echo "known statement p95 from the program: ${p95:-none} ms, in the bucket from $lower to $upper ms as Splunk stores it"
  echo "known query p95 bucket crosscheck ms $crosscheck"
  echo "known statement p95 from the timer around the JDBC call: ${client_p95:-none} ms, so the histogram reading sits ${truth_gap}% away from it"
  echo "known statement mean: $mean ms from Splunk's sum and count, $server ms from pg_stat_statements, a gap of ${mean_gap}%"
}

# The opt-in alone, with no trim. It runs here so the trim branch still fits the 14 minutes fix.sh has.
# db.client.connection.count exists only with the opt-in and sits well under 36 dimensions, so it shows this run reached
# Splunk. The audit statement's bucket series then decides, after about two minutes: stored means one setting is enough.
opt_in_alone() {
  local s dims
  for s in orders inventory; do
    kubectl -n demo set env "deployment/$s" OTEL_SEMCONV_STABILITY_OPT_IN=database >/dev/null || fail "could not set the opt-in on $s"
  done
  kubectl -n demo rollout status deploy/inventory --timeout="$(cap 120 240)s" >/dev/null || fail "inventory did not roll out"
  kubectl -n demo rollout status deploy/orders --timeout="$(cap 120 240)s" >/dev/null || fail "orders did not roll out"
  kubectl -n demo delete pod load --ignore-not-found --wait=true >/dev/null 2>&1
  sed -e "s/LOAD_NODE/$(cat load-node.txt)/" -e 's/value: "30"/value: "2"/' -e 's/value: "540"/value: "75"/' k8s/20-load.yaml | kubectl apply -f - >/dev/null || fail "could not start the load"
  kubectl -n demo wait pod/load --for=jsonpath='{.status.phase}'=Succeeded --timeout="$(cap 110 130)s" >/dev/null || fail "the load did not finish"
  kubectl -n demo logs load > load-alone.txt
  grep -q 'errors=0' load-alone.txt || fail "the load saw errors, so this read can't be used"
  wait_audit "deployment.environment:${LAB_NAME}"
  statements_stored
  control=$(series "sf_metric:db.client.connection.count AND deployment.environment:${LAB_NAME}" | jq '.results | length')
  held=$(audit_dims | jq -s length)
  dims=$(audit_dims | jq -s 'max // 0')
  [ "$held" -gt 0 ] || [ "${control:-0}" -gt 0 ] || fail "nothing from the opt-in reached Splunk, so this read can't decide"
  title_changes=$(( held > 0 ? 1 : 2 ))
  echo "opt-in alone in Splunk after a read of ${waited}s: control series $control, audit statement bucket series $held, dimensions on them $dims"
  echo "HTTP, separate from this metric, because it explains the earlier failed run:"
  report_families
  printf 'title_changes=%s\nheld=%s\ncontrol=%s\nalone_dims=%s\n' "$title_changes" "$held" "$control" "$dims" > probe.env
  for kv in "untrimmed_series_held=$held" "untrimmed_control_series=$control" "title_changes=$title_changes"; do
    echo "trim evidence: ${kv%%=*} ${kv#*=}"
    echo "HERO_MEASURE: trim_evidence[${kv%%=*}]=${kv#*=}"
  done
}

# The numbers around the 36-dimension limit. Splunk lists sf_metric as a key, which isn't a dimension, so counts leave it
# out. With title_changes=2 the opt-in-alone series wasn't stored, so its size is the trimmed series plus what the trim
# removes, and that has to pass 36 to agree.
# Sets max_dimensions, trimmed, untrimmed, collector_added, untrimmed_dims and agrees for fix.sh.
trim_numbers() {
  max_dimensions=$(jq --argjson t "$title_changes" '[.results[]? | select((.metric | endswith("_bucket")) and ($t == 1 or (.dimensions["process.pid"] | not))) | .dimensions | del(.sf_metric) | length] | max // 0' series.json)
  collector_added=$(grep -c . <<<"$(cloud_keys)")
  resource_keys dup.log > keys-a.txt; resource_keys sysprop.log > keys-b.txt
  a=$(grep -c . keys-a.txt); b=$(grep -c . keys-b.txt)
  trimmed=$(( a < b ? a : b )); untrimmed=$(( a < b ? b : a ))
  untrimmed_dims=$(( title_changes == 1 ? alone_dims : max_dimensions + untrimmed - trimmed ))
  agrees=$(( title_changes == 1 ? untrimmed_dims <= 36 : untrimmed_dims > 36 ))
  echo "attributes the trim removes: $(comm -3 keys-a.txt keys-b.txt | tr -d '\t' | tr '\n' ' ')"
  echo "cloud and host attributes on these series: $(cloud_keys | tr '\n' ' ')"
  echo "this run's $METRIC bucket series: $max_dimensions dimensions, $(( 36 - max_dimensions )) under Splunk's 36. Title changes: $title_changes"
  echo "the same series with the opt-in alone: $untrimmed_dims dimensions ($untrimmed agent attributes without the trim, $trimmed with it)"
}
