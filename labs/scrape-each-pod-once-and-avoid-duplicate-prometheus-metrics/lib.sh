# Shared by reproduce.sh and fix.sh. Sourced, never run alone. A run = one agent config, both orders pods restarted with
# counters at 0, 20 s at 10 orders a second, then two more scrape intervals. Runs: everywhere-1/2, node-1/2, autodetect-1/2.
# Only the agent's config differs between configs (values-<config>.yaml). The pods, their annotations and the load don't.
export LC_ALL=C

# Splunk's view of one run: how many series hold the Prometheus counter, the counter's total across them, and
# OpenTelemetry's own count of the same POSTs. Counters start at 0 in every run, so the total is the newest value summed.
splunk_run() {   # run label. Sets prom_series, prom_total, otel_total and dims.
  local s t f
  s=$(cat "start-$1.txt"); t=$(date +%s)
  f="filter('run', '$(tag "$1")')"
  # The counter's name as Splunk holds it, from the run's series: orders_created_total, or orders_created if a translator
  # trimmed the suffix. Whichever exists is read, and the names are printed.
  names=$(series "sf_metric:orders_created* AND run:\"$(tag "$1")\"" | jq -r '[.results[]?.metric] | unique | join(",")' 2>/dev/null)
  metric=orders_created_total; case ",$names," in *,orders_created_total,*) ;; *,orders_created,*) metric=orders_created ;; esac
  prom_total=$(sf_max "data('$metric', filter=$f, rollup='latest', extrapolation='last_value', maxExtrapolations=6).sum().publish()" "$(( s - 20 ))" "$t")
  otel_total=$(sf_max "data('http.server.request.duration_count', filter=$f and filter('service.name', 'orders') and filter('http.request.method', 'POST') and filter('http.response.status_code', '200'), rollup='latest', extrapolation='last_value', maxExtrapolations=6).sum().publish()" "$(( s - 20 ))" "$t")
  local got; got=$(series "sf_metric:$metric AND run:\"$(tag "$1")\"")
  jq -e . >/dev/null 2>&1 <<<"$got" || got='{}'
  prom_series=$(jq -r '.results // [] | length' <<<"$got" 2>/dev/null || echo 0)
  # Which dimensions tell the series apart: the keys whose values differ across series of the same pod, and the most
  # series any one pod has. Series are grouped by scrape target (service.instance.id, the pod's IP and port), which every
  # Prometheus receiver sets the same way, so a dimension another component adds can't move a series to another group.
  local by='(.["service.instance.id"] // .instance // .pod // .["k8s.pod.name"] // "?")'
  dims=$(jq -r "[.results[]?.dimensions] | group_by($by) | map(select(length > 1) | (map(keys) | add | unique) as \$k
           | . as \$g | \$k | map(select(. as \$x | (\$g | map(.[\$x]) | unique | length) > 1))) | add // [] | unique | join(\",\")" <<<"$got" 2>/dev/null)
  per_pod=$(jq -r "[.results[]?.dimensions] | group_by($by) | map(length) | max // 0" <<<"$got" 2>/dev/null || echo 0)
  pods_named=$(jq -r "[.results[]?.dimensions | $by] | unique | join(\",\")" <<<"$got" 2>/dev/null)
  echo "$got" > "series-$1.json"
  series "sf_metric:orders_created* AND run:\"$(tag "$1")\"" | jq -r '[.results[]?.metric] | group_by(.) | map("\(.[0]) \(length)") | .[]' > "metric-names-$1.txt" 2>/dev/null
  if [ "$got" = '{}' ] || [ -z "$per_pod" ] || ! jq -e '.results | length > 0' >/dev/null 2>&1 <<<"$got"; then
    dims_n=error; per_pod=error                   # no series read: say so, never 0
  else
    dims_n=$(awk -F, '{print ($0 == "" ? 0 : NF)}' <<<"$dims")
  fi
  # Each series' values for the dimensions that differ, so the article can show exactly what splits a pod's counter.
  split_view=$(jq -r --arg d "$dims" "[.results[]?.dimensions | . as \$x | ($by) + \" \" + ([\$d | split(\",\")[] | select(. != \"\") | . + \"=\" + (\$x[.] // \"-\" | tostring)] | join(\" \"))] | sort | join(\"; \")" <<<"$got" 2>/dev/null)
}

splunk_run_patiently() {   # run label, seconds allowed. Splunk takes a minute or so to hold the newest points and series.
  local t0 want e w; t0=$(date +%s); want=$(cat "truth-$1.txt")
  e=$(sed -n 's/^LOAD end=\([0-9]*\).*/\1/p' "load-$1.txt"); w=$(( ${e:-0} + 50 - t0 ))
  [ "$w" -gt 0 ] && [ "$w" -lt "$2" ] && sleep "$w"   # every agent's last scrape of this run has reached Splunk by then
  for try in 1 2 3 4 5 6 7 8; do
    splunk_run "$1"
    [ "${prom_total:-0}" -ge "$want" ] && [ "${otel_total:-0}" -ge "$want" ] && [ "${prom_series:-0}" -ge 2 ] && break
    [ $(( $(date +%s) - t0 + 25 )) -lt "$2" ] || break
    sleep 20
  done
  echo "$1 in Splunk: $metric (series by name: $(tr '\n' ' ' < "metric-names-$1.txt" 2>/dev/null)) $prom_total over $prom_series series, at most $per_pod a target (targets: ${pods_named:-none}); OpenTelemetry POST 200 count $otel_total; the pods counted $want"
  [ "$dims_n" != error ] && [ "$dims_n" -gt 0 ] && echo "  $1 series told apart by ${dims}: ${split_view}" | cut -c1-600
}

# The largest point SignalFlow returns between start and stop (10 s resolution), each point summed across series. A series
# that skipped one 10 s point keeps its last value (extrapolation), so a missed point can't lower the sum.
sf_max() {   # program, start s, stop s
  local out n
  for try in 1 2 3; do
    out=$(signalflow "$1" "$(( $2 * 1000 ))" "$(( $3 * 1000 ))")
    [ -n "$out" ] && break
    sleep 5
  done
  n=$(awk 'BEGIN {m = 0} $2 > m {m = $2} END {printf "%.0f", m}' <<<"$out")
  echo "${n:-0}"
}

# The Splunk OTel Collector chart with one config. Every run uses the same chart version and the same base settings.
set_config() {   # config, seconds allowed
  local args=(--version 0.163.1 --namespace splunk --create-namespace
    --set splunkObservability.realm="$SPLUNK_REALM" --set splunkObservability.accessToken="$SPLUNK_ACCESS_TOKEN"
    --set clusterName="$LAB_NAME" --set cloudProvider=aws --set distribution=eks -f "values-$1.yaml")
  helm upgrade --install splunk-otel-collector splunk-otel-collector-chart/splunk-otel-collector "${args[@]}" >/dev/null || fail "helm could not apply values-$1.yaml"
  kubectl -n splunk rollout status daemonset/splunk-otel-collector-agent --timeout="$(cap "$2" 300)s" >/dev/null || { why_agent; fail "the agent did not roll out with values-$1.yaml"; }
  echo "agent config $1: ready on $(kubectl -n splunk get daemonset splunk-otel-collector-agent -o jsonpath='{.status.numberReady}') nodes, chart $(helm -n splunk list -o json | jq -r '.[0].chart')"
  # The config the agents run: the chart's rendered ConfigMap, saved whole to config-<config>.yaml, checked, and the
  # parts that decide who scrapes the orders pods printed. Each agent pod's node is K8S_NODE_NAME on that pod.
  kubectl -n splunk get configmap splunk-otel-collector-otel-agent -o jsonpath='{.data.relay}' > "config-$1.yaml" || fail "could not read the agent's ConfigMap"
  case "$1" in
    everywhere) grep -q 'prometheus/orders:' "config-$1.yaml" && ! grep -A16 'prometheus/orders:' "config-$1.yaml" | grep -q 'spec.nodeName=' || fail "config-$1.yaml isn't the cluster-wide scrape job" ;;
    node) grep -A16 'prometheus/orders:' "config-$1.yaml" | grep -q 'spec.nodeName=' || fail "config-$1.yaml has no node field selector" ;;
    autodetect) grep -q 'receiver_creator' "config-$1.yaml" && grep -q 'prometheus.io/scrape' "config-$1.yaml" && ! grep -q 'prometheus/orders:' "config-$1.yaml" ||
                  fail "config-$1.yaml has no receiver_creator rule for prometheus.io/scrape, or still has the scrape job" ;;
  esac
  if [ "$1" = autodetect ]; then
    grep -B16 -A1 'prometheus.io/scrape"\] == "true"' "config-$1.yaml" | grep -E 'prometheus/autodetect|job_name|scrape_interval|targets|rule:' |
      sed 's/^ *//' | sed "s/^/  config-$1.yaml: /" | cut -c1-170
  else
    grep -A16 'prometheus/orders:' "config-$1.yaml" | grep -E 'prometheus/orders|job_name|role: pod|field:|regex: orders|scrape_interval' |
      sed 's/^ *//' | tr '\n' ';' | sed "s/^/  config-$1.yaml: /"; echo
    # The scrape job's own pipeline. Chart 0.163.1 names the processor resource_detection (templates/config/_common.tpl and
    # the default metrics pipeline; upstream calls it resourcedetection), so both spellings count. With override: true and
    # the eks detector it sets host.name and host.id per node, so series from two agents are expected to differ in those.
    local pipe; pipe=$(grep -A12 'metrics/orders:' "config-$1.yaml" | grep -E 'metrics/orders|- ' | tr -s ' ' | tr '\n' ' ')
    [ -n "$pipe" ] || fail "config-$1.yaml has no metrics/orders pipeline"
    echo "  config-$1.yaml pipeline: $pipe"
    grep -qE 'resource_?detection' <<<"$pipe" || fail "the metrics/orders pipeline has no resource detection, so the agents' series can't differ by node"
  fi
  # K8S_NODE_NAME on each agent comes from the pod's spec.nodeName (a fieldRef Kubernetes fills in). The agent image has
  # no shell, so the fieldRef and the node are printed and checked, and the pods' own scraper records show the outcome.
  local ap fp nn
  for ap in $(kubectl -n splunk get pods -l component=otel-collector-agent -o name); do
    fp=$(kubectl -n splunk get "$ap" -o jsonpath='{.spec.containers[0].env[?(@.name=="K8S_NODE_NAME")].valueFrom.fieldRef.fieldPath}')
    nn=$(kubectl -n splunk get "$ap" -o jsonpath='{.spec.nodeName} {.status.podIP}')
    echo "  ${ap#pod/}: K8S_NODE_NAME from $fp, node and IP $nn"
    [ "$fp" = spec.nodeName ] && [ -n "${nn%% *}" ] || fail "${ap#pod/} has no K8S_NODE_NAME from its node"
  done
  CONFIG=$1
}

tag() { echo "$LAB_NAME-$1"; }   # a run's label in Splunk, unique to this lab, so no older series is counted

start_run() {   # run label, seconds allowed
  local cfg=${1%-*}
  if [ "${CONFIG:-}" != "$cfg" ]; then
    # The last run's pods go before the agents change config. Otherwise the new config scrapes them too, under its own job
    # name, and the last run gets extra series in Splunk (the rehearsal showed node-2 at 400 this way). Nothing of a run is
    # ever scraped by two configs.
    kubectl -n demo scale deploy/orders-a deploy/orders-b --replicas=0 >/dev/null 2>&1
    kubectl -n demo wait --for=delete pod -l app=orders --timeout="$(cap 90 300)s" >/dev/null 2>&1 || true
    set_config "$cfg" "$2"
  fi
  kubectl -n demo set env deployment/orders-a deployment/orders-b "LAB_RUN=$(tag "$1")" >/dev/null || fail "could not restart the orders pods"
  kubectl -n demo scale deploy/orders-a deploy/orders-b --replicas=1 >/dev/null || fail "could not start the orders pods"
  kubectl -n demo rollout status deploy/orders-a --timeout="$(cap "$2" 300)s" >/dev/null || { why orders; fail "orders-a did not roll out"; }
  kubectl -n demo rollout status deploy/orders-b --timeout="$(cap "$2" 300)s" >/dev/null || { why orders; fail "orders-b did not roll out"; }
  date +%s > "start-$1.txt"
  sleep 25   # two scrape intervals and more at 0 before the first order, so every scraper has its baseline
  local p line out=""
  for p in $(kubectl -n demo get pods -l app=orders --field-selector=status.phase=Running -o name); do
    line=$(pod_stats "$p") || continue
    case "$line" in "$(tag "$1") "*) set -- "$1" $line; out="$out $3: $(( $# - 4 )) scraper(s) at 0;" ;; esac
  done
  echo "$1 before the load:$out"
}

# Which agent each scraper IP is: agent pod IPs and their nodes, so a pod's scrapers can be named by node.
agent_map() { kubectl -n splunk get pods -l component=otel-collector-agent -o jsonpath='{range .items[*]}{.status.podIP}={.spec.nodeName}{"\n"}{end}' 2>/dev/null; }
name_ips() {   # text with IP:count pairs: each IP followed by its agent's node, when it is an agent
  local t=$1 ip node
  while IFS== read -r ip node; do [ -n "$ip" ] && t=${t//"$ip:"/"$ip(agent on $node):"}; done < <(agent_map)
  echo "$t"
}

load_run() {   # run label, seconds allowed
  kubectl -n demo delete pod load --ignore-not-found --wait=true >/dev/null 2>&1
  sed -e "s/NODE_A/$NODE_A/" -e "s/value: \"RUN\"/value: \"$1\"/" -e "s/value: \"FIRST\"/value: \"$(first_order "$1")\"/" k8s/20-load.yaml |
    kubectl apply -f - >/dev/null || fail "could not start the load"
  kubectl -n demo wait pod/load --for=jsonpath='{.status.phase}'=Succeeded --timeout="$2s" >/dev/null || { kubectl -n demo logs load --tail=10; why orders; fail "the load did not finish"; }
  kubectl -n demo logs load > "load-$1.txt"
  sleep 22   # two more scrapes and two more OTLP exports after the last order
}

pod_stats() {   # pod: one line from the pod's own /scrapes
  kubectl -n demo exec "$1" -- /lab/venv/bin/python -c 'import json, urllib.request
d = json.load(urllib.request.urlopen("http://localhost:8080/scrapes", timeout=5))
print(d["run"], d["pod"], d["orders"], " ".join(f"{ip}:{n}" for ip, n in sorted(d["scrapers"].items())))' 2>/dev/null
}

measures() {   # run label: the load, each pod's own count, and who scraped each pod
  local sent ok p line r pod orders rest s n=0 pod_orders=0 pairs=0
  sent=$(sed -n 's/^LOAD end=.* sent=\([0-9]*\).*/\1/p' "load-$1.txt"); ok=$(sed -n 's/^LOAD end=.* ok=\([0-9]*\).*/\1/p' "load-$1.txt")
  : > "pods-$1.txt"
  for p in $(kubectl -n demo get pods -l app=orders --field-selector=status.phase=Running -o name); do
    line=$(pod_stats "$p") || continue                 # a pod of the last run that is still shutting down
    read -r r pod orders rest <<<"$line"
    [ "$r" = "$(tag "$1")" ] || continue
    echo "$pod $orders $rest" >> "pods-$1.txt"; n=$(( n + 1 ))
    pod_orders=$(( pod_orders + orders ))
    for s in $rest; do [ "${s##*:}" -ge 2 ] && pairs=$(( pairs + 1 )); done
  done
  [ "$n" = 2 ] || fail "found $n orders pods of $1, expected 2"
  echo "$1: orders sent ${sent:-0}, ok ${ok:-0}; the pods counted $pod_orders; scrapers per pod (IP:scrapes): $(name_ips "$(awk '{s = ""; for (i = 3; i <= NF; i++) s = s " " $i; printf "%s [%s ] ", $1, s}' "pods-$1.txt")")"
  echo "HERO_MEASURE: orders_ok[$1]=${ok:-0}"
  echo "HERO_MEASURE: pod_scrapers[$1]=$pairs"
  echo "$pod_orders" > "truth-$1.txt"
  [ "${ok:-0}" -gt 0 ] || fail "no order succeeded in $1"
}

# Generic helpers: the clock, pods, and each run's first order number.
start_clock() {   # seconds allowed, the result to print on failure. Every wait is capped so a result always prints.
  START=$(date +%s); LIMIT=$1; FAILED=$2; HERO_DONE=0
  trap '[ "$HERO_DONE" = 1 ] || { HERO_DONE=1; echo "HERO_RESULT: $FAILED"; }' EXIT
}
remaining() { echo $(( START + LIMIT - $(date +%s) )); }
cap() { local want=$1 reserve=$2 r; r=$(( $(remaining) - reserve )); [ "$r" -lt 5 ] && r=5; [ "$want" -lt "$r" ] && echo "$want" || echo "$r"; }
emit() { HERO_DONE=1; echo "HERO_RESULT: $1"; }
fail() { echo "FAIL: $*"; [ "$HERO_DONE" = 1 ] || emit "$FAILED"; exit 0; }

why() { kubectl -n demo get pods -o wide | tail -6; kubectl -n demo get events --sort-by=.lastTimestamp | tail -6 | cut -c1-180; }
why_agent() { kubectl -n splunk get pods -o wide | tail -6; kubectl -n splunk logs daemonset/splunk-otel-collector-agent --tail=15 2>&1 | cut -c1-220; }

first_order() { case "$1" in everywhere-1) echo 1 ;; everywhere-2) echo 10001 ;; node-1) echo 20001 ;; node-2) echo 30001 ;; autodetect-1) echo 40001 ;; autodetect-2) echo 50001 ;; esac; }
