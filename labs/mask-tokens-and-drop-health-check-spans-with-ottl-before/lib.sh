# Shared by reproduce.sh and fix.sh. Sourced, never run alone. A run = a fresh gateway with one config, fresh service pods,
# 45 s at 10 orders a second. Runs: before-1/2 (base.yaml), named-1/2 (named.yaml), all-1/2 (all.yaml). Nothing else differs.
export LC_ALL=C

# periods.txt holds when each config ran ("<config> start|end <epoch>"), so health checks are counted inside one config.
start_run() {   # run label, seconds allowed
  local cfg last; cfg=$(config_of "$1")
  last=$(awk '$2 == "start" {c = $1} END {print c}' periods.txt 2>/dev/null)
  [ -n "$last" ] && [ "$last" != "$cfg" ] && echo "$last end $(date +%s)" >> periods.txt
  kubectl -n demo set env deployment/gateway "CONFIG=$cfg.yaml" "LAB_RUN=$1" >/dev/null || fail "could not set the gateway config"
  kubectl -n demo rollout status deploy/gateway --timeout="$(cap "$2" 300)s" >/dev/null || { why gateway; fail "the gateway did not roll out"; }
  kubectl -n demo set env deployment/inventory deployment/orders "LAB_RUN=$1" >/dev/null || fail "could not restart the services"
  kubectl -n demo rollout status deploy/inventory --timeout="$(cap "$2" 300)s" >/dev/null || { why inventory; fail "inventory did not roll out"; }
  kubectl -n demo rollout status deploy/orders --timeout="$(cap "$2" 300)s" >/dev/null || { why orders; fail "orders did not roll out"; }
  [ "$last" != "$cfg" ] && echo "$cfg start $(date +%s)" >> periods.txt
  local g; g=$(newest gateway)
  echo "$1: gateway $(kubectl -n demo get "$g" -o jsonpath='{.spec.containers[0].image}') --config=/conf/$cfg.yaml ($(kubectl -n demo logs "$g" 2>/dev/null | grep -c 'Everything is ready') ready line); services with OTEL_SEMCONV_STABILITY_OPT_IN=$(kubectl -n demo exec "$(newest orders)" -- printenv OTEL_SEMCONV_STABILITY_OPT_IN)"
}

# The gateway's counters right before the load and after the flush; every count is the difference.
load_run() {   # run label, seconds allowed
  kubectl -n demo wait pod/stats --for=condition=Ready --timeout=60s >/dev/null || fail "the stats pod is not ready"
  kubectl -n demo delete pod load --ignore-not-found --wait=true >/dev/null 2>&1
  kubectl -n demo exec stats -- python /stats/gwstats.py > "gw-$1-start.txt" || fail "could not read the gateway's metrics"
  sed -e "s/LOAD_NODE/$LOAD_NODE/" -e "s/value: \"FIRST\"/value: \"$(first_order "$1")\"/" -e "s/value: \"RUN\"/value: \"$1\"/" k8s/20-load.yaml |
    kubectl apply -f - >/dev/null || fail "could not start the load"
  kubectl -n demo wait pod/load --for=jsonpath='{.status.phase}'=Succeeded --timeout="$2s" >/dev/null || { kubectl -n demo logs load --tail=20; why orders; fail "the load did not finish"; }
  kubectl -n demo logs load > "load-$1.txt"
  sleep 8   # the services' exporters and the gateway's batch flush
  kubectl -n demo exec stats -- python /stats/gwstats.py > "gw-$1-end.txt" || fail "could not read the gateway's metrics"
  grep -E '^LOAD (start|end)' "load-$1.txt" | tr '\n' ' '; echo
}

gwd() {   # run label, count name: end minus start
  local a b; a=$(sed -n "s/^GW $2=//p" "gw-$1-start.txt"); b=$(sed -n "s/^GW $2=//p" "gw-$1-end.txt")
  awk -v a="${a:-0}" -v b="${b:-0}" 'BEGIN { printf "%.0f", b - a }'
}

measures() {   # run label: the load and the gateway's counts inside the load window
  local sent ok rec exp fin fout drop
  sent=$(sed -n 's/^LOAD end=.* sent=\([0-9]*\).*/\1/p' "load-$1.txt"); ok=$(sed -n 's/^LOAD end=.* ok=\([0-9]*\).*/\1/p' "load-$1.txt")
  rec=$(gwd "$1" spans_received); exp=$(gwd "$1" spans_exported); fin=$(gwd "$1" filter_in); fout=$(gwd "$1" filter_out); drop=$(( fin - fout ))
  echo "$1 gateway counters at start: $(grep -E 'spans_(received|exported)|filter_(in|out)' "gw-$1-start.txt" | tr '\n' ' ')"
  echo "$1 gateway counters at end:   $(grep -E 'spans_(received|exported)|filter_(in|out)' "gw-$1-end.txt" | tr '\n' ' ')"
  echo "$1: orders sent ${sent:-0}, ok ${ok:-0}; in the window the gateway received $rec spans, exported $exp, filter in $fin, out $fout, dropped $drop, failed exports $(gwd "$1" spans_export_failed)"
  echo "$1 check: exported $exp = received $rec - dropped $drop? $([ "$exp" = "$(( rec - drop ))" ] && echo yes || echo no); spans kept $(( rec - drop )) vs 3 x orders ok $(( 3 * ${ok:-0} )) (health checks not dropped are in the first number)"
  echo "HERO_MEASURE: orders_ok[$1]=${ok:-0}"
  echo "HERO_MEASURE: gateway_spans_received[$1]=$rec"
  echo "HERO_MEASURE: gateway_spans_exported[$1]=$exp"
  echo "HERO_MEASURE: gateway_spans_dropped_by_filter[$1]=$drop"
  [ "${ok:-0}" = "${sent:-x}" ] || echo "note: $1 had $(( ${sent:-0} - ${ok:-0} )) failed orders"
  [ "$rec" -gt 0 ] || fail "the gateway received no spans in $1"
}

sample_count() { grep -c '^SAMPLE ' "load-$1.txt"; }

# A run's sample traces from Splunk APM by ID: per span, the raw token anywhere, "token=REDACTED", and which attributes
# hold the raw token. Sets found, spans_min, raw, masked, both_services and keys.
read_traces() {   # run label
  local order trace token items n r m svc
  found=0; spans_min=999; raw=0; masked=0; both_services=0; keys=""
  : > "traces-$1.txt"
  while read -r _ order trace token; do
    trace=${trace#trace=}; token=${token#token=}
    items=$(trace_items "$trace"); n=$(jq 'length' <<<"${items:-[]}" 2>/dev/null || echo 0)
    [ "${n:-0}" -gt 0 ] || continue
    r=$(jq --arg t "$token" '[.[] | select(tostring | contains($t))] | length' <<<"$items")
    m=$(jq '[.[] | select(tostring | contains("token=REDACTED"))] | length' <<<"$items")
    svc=$(jq -r '[.[] | .serviceName // empty] | unique | join(",")' <<<"$items")
    keys="$keys $(jq -r --arg t "$token" '.[] | (.tags // {}) | if type == "array" then map({(.key): .value}) | add // {} else . end | to_entries[] | select(.value | tostring | contains($t)) | .key' <<<"$items" | tr '\n' ' ')"
    echo "$trace ${order} spans=$n services=$svc raw_token_spans=$r masked_spans=$m" >> "traces-$1.txt"
    found=$(( found + 1 )); raw=$(( raw + r )); masked=$(( masked + m ))
    [ "$n" -lt "$spans_min" ] && spans_min=$n
    [ "$svc" = "inventory,orders" ] && both_services=$(( both_services + 1 ))
  done < <(grep '^SAMPLE ' "load-$1.txt")
  [ "$found" = 0 ] && spans_min=0
  keys=$(tr ' ' '\n' <<<"$keys" | sed '/^$/d' | sort | uniq -c | awk '{printf "%s(%s) ", $2, $1}')
}

read_traces_patiently() {   # run label, seconds allowed. APM takes a minute or two to hold a trace, so missing ones are read again.
  local t0 total; t0=$(date +%s); total=$(sample_count "$1")
  for try in 1 2 3 4 5 6 7 8 9 10; do
    read_traces "$1"
    [ "$found" -ge "$total" ] && break
    [ $(( $(date +%s) - t0 + 30 )) -lt "$2" ] || break
    sleep 30
  done
  echo "$1: $found of $total sample traces in Splunk APM; fewest spans $spans_min; both services in $both_services; raw token in $raw spans (attributes: ${keys:-none}); token=REDACTED in $masked spans"
}

# Health checks per config and service, counted two ways over the same whole 10 s points inside that config's period: APM's
# request count for the GET /healthz operation (from spans that reached Splunk) and the services' own HTTP server metric
# for the /healthz route (made in the app, so the trace filter doesn't touch it). The kubelet's probe periods (2 s and
# 10 s) give about 36 checks a minute per service, printed as a third column.
health_counts() {   # config, service. Sets apm_health and metric_health.
  local s e secs f p1 p2
  s=$(awk -v c="$1" '$1 == c && $2 == "start" {print $3; exit}' periods.txt); e=$(awk -v c="$1" '$1 == c && $2 == "end" {print $3; exit}' periods.txt)
  s=$(( (s + 20 + 9) / 10 * 10 )); e=$(( e / 10 * 10 )); secs=$(( e - s ))   # whole 10 s points, the resolution of both sources
  f="filter('sf_environment', '$LAB_NAME') and filter('sf_service', '$2') and filter('sf_operation', 'GET /healthz')"
  p1="data('spans.count', filter=$f, rollup='delta').sum().publish()"
  p2="data('http.server.request.duration_count', filter=filter('deployment.environment', '$LAB_NAME') and filter('service.name', '$2') and filter('http.route', '/healthz'), rollup='delta').sum().publish()"
  [ "$1/$2" = base/orders ] && echo "  programs: $p1 ; $p2"
  apm_health=$(sf_total "$p1" "$s" "$e"); metric_health=$(sf_total "$p2" "$s" "$e")
  # Orgs on histogram MetricSets hold endpoint counts as histogram('spans'), so that form is read too when the first is empty.
  [ "$apm_health" = 0 ] && apm_health=$(sf_total "histogram('spans', filter=$f).count().publish()" "$s" "$e")
  echo "$1/$2, ${secs} s from $(date -u -d @"$s" +%H:%M:%S) to $(date -u -d @"$e" +%H:%M:%S) UTC: APM GET /healthz $apm_health; HTTP metric /healthz $metric_health; probe periods expect about $(( secs * 36 / 60 ))"
}

# Sums every point SignalFlow returns between start and stop (10 s resolution), across all series.
sf_total() {   # program, start s, stop s. Prints the sum of all points, read up to 3 times if the stream comes back empty.
  local out n
  for try in 1 2 3; do
    out=$(signalflow "$1" "$(( $2 * 1000 ))" "$(( $3 * 1000 ))")
    [ -n "$out" ] && break
    sleep 10
  done
  n=$(awk '{t += $2} END {printf "%.0f", t}' <<<"$out")
  echo "${n:-0}"
}

# Generic helpers: the clock, pods, and each run's order numbers and config.
start_clock() {   # seconds allowed, the result to print on failure. Every wait is capped so a result always prints.
  START=$(date +%s); LIMIT=$1; FAILED=$2; HERO_DONE=0
  trap '[ "$HERO_DONE" = 1 ] || { HERO_DONE=1; echo "HERO_RESULT: $FAILED"; }' EXIT
}
remaining() { echo $(( START + LIMIT - $(date +%s) )); }
cap() { local want=$1 reserve=$2 r; r=$(( $(remaining) - reserve )); [ "$r" -lt 5 ] && r=5; [ "$want" -lt "$r" ] && echo "$want" || echo "$r"; }
emit() { HERO_DONE=1; echo "HERO_RESULT: $1"; }
fail() { echo "FAIL: $*"; [ "$HERO_DONE" = 1 ] || emit "$FAILED"; exit 0; }

newest() { kubectl -n demo get pods -l "app=$1" --field-selector=status.phase=Running --sort-by=.metadata.creationTimestamp -o name | tail -1; }
why() { kubectl -n demo get pods -o wide | tail -8; kubectl -n demo get events --sort-by=.lastTimestamp | tail -8 | cut -c1-180; kubectl -n demo logs deploy/"$1" --tail=20 | cut -c1-200; }

first_order() { case "$1" in before-1) echo 1 ;; before-2) echo 10001 ;; named-1) echo 20001 ;; named-2) echo 30001 ;; all-1) echo 40001 ;; all-2) echo 50001 ;; esac; }
config_of() { echo "${1%-*}" | sed 's/^before$/base/'; }   # before-1 -> base, named-2 -> named, all-1 -> all
