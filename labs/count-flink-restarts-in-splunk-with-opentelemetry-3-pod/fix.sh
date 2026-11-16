#!/usr/bin/env bash
# AFTER: the upstream OpenTelemetry Collector (contrib 0.165.0) with the Flink metrics receiver, sending through the Splunk
# agent. Three more TaskManager deletions. Splunk's flink.job.restart.count must rise by exactly the restarts Flink's REST
# API counts between the same two instants. Splunk is also polled every 3 s during the deletions, for arrival lag.
set -uo pipefail
cd "$(dirname "$0")"
. ./lib.sh

start_clock 840 not_fixed
aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null || fail "could not write kubeconfig"
APP_NODE=$(cat app-node.txt); LOAD_NODE=$(cat load-node.txt)
[ -n "${SPLUNK_API_TOKEN:-}" ] || fail "SPLUNK_API_TOKEN is not set"

echo "== 1. The Collector with the Flink receiver"
sed -e "s/APP_NODE/$APP_NODE/" -e "s/LAB_ENV/$LAB_NAME/g" k8s/30-collector.yaml | kubectl apply -f - >/dev/null || fail "could not deploy the Collector"
kubectl -n demo rollout status deploy/flink-otel --timeout="$(cap 180 600)s" >/dev/null || { kubectl -n demo logs deploy/flink-otel --tail=20; fail "the Collector did not start"; }
sleep 15
kubectl -n demo logs deploy/flink-otel 2>&1 | grep -m3 -iE 'version|error|everything is ready' | cut -c1-200
start_watch watch-receiver 480
wait_running "$(cap 60 560)" 0 || { why; fail "the job is not RUNNING"; }
sleep 40                                            # Splunk gets the counter's value from before the first deletion
F="filter('deployment.environment', '$LAB_NAME')"
echo "  metric type of flink.job.restart.count: $(metric_type flink.job.restart.count)"

echo "== 2. Deleting the TaskManager pod three times, 30 s apart, while Splunk is polled every 3 s"
w0=$(date +%s)
: > arrivals.txt
( while :; do counter flink.job.restart.count "$F" $(( w0 - 60 )) "$(date +%s)" poll.tmp quiet
    echo "$(date +%s) $(awk 'END {print ($2 == "" ? "null" : $2)}' poll.tmp)" >> arrivals.txt; sleep 3; done ) &
POLL=$!
kill_tm receiver "$(cap 120 420)"; sleep 30
kill_tm receiver "$(cap 120 360)"; sleep 30
kill_tm receiver "$(cap 120 300)"; sleep 30
w1=$(date +%s)
report receiver "$w0" "$w1"
rest=$(measure flink_rest_restarts receiver)
read -r _ _ bumps <<<"$(grep '^RESTARTS receiver' report-receiver.txt) "
W=watch-receiver.log

echo "== 3. The same window in Splunk Observability Cloud"
t0=$(date +%s)
while [ $(( $(date +%s) - t0 )) -lt "$(cap 150 200)" ]; do   # until two reads 15 s apart agree
  counter flink.job.restart.count "$F" $(( w0 - 60 )) "$w1" sf-restarts.txt quiet
  now=$(md5sum < sf-restarts.txt)
  [ -s sf-restarts.txt ] && [ "$now" = "${was:-}" ] && break
  was=$now; sleep 15
done
kill "$POLL" 2>/dev/null
counter flink.job.restart.count "$F" $(( w0 - 60 )) "$w1" sf-restarts.txt
echo "  flink.job.restart.count per 10 s (point start = value): $(awk '{printf "%s=%s ", $1, $2}' sf-restarts.txt)"
# Both counts are read at the same two instants: the end of the last 10 s point that ends by w0, and by w1. No restart
# happens within 25 s of either instant, so the scrape timing inside a point can't move a restart across a bound.
tb=$(awk -v w="$w0" '$1 + 10 <= w {t = $1} END {print t}' sf-restarts.txt)
te=$(awk -v w="$w1" '$1 + 10 <= w {t = $1} END {print t}' sf-restarts.txt)
[ -n "$tb" ] && [ -n "$te" ] || fail "Splunk has no restart counter point before the first deletion or after the last"
base=$(at sf-restarts.txt "$tb"); end=$(at sf-restarts.txt "$te")
rb=$(rest_at "$W" restarts $(( tb + 10 ))); re=$(rest_at "$W" restarts $(( te + 10 )))
inc=$(awk -v a="$base" -v b="$end" 'BEGIN { if (a == "null" || b == "null") print "null"; else printf "%d", b - a }')
rinc=$(awk -v a="$rb" -v b="$re" 'BEGIN { if (a == "null" || b == "null") print "null"; else printf "%d", b - a }')
diff=$(awk -v a="$inc" -v b="$rinc" 'function ab(x) { return x < 0 ? -x : x } BEGIN { if (a == "null" || b == "null") print "null"; else printf "%d", ab(a - b) }')
echo "at $(( tb + 10 )) and $(( te + 10 )): Splunk's counter $base -> $end (+$inc); Flink's REST API $rb -> $re (+$rinc)"
echo "HERO_MEASURE: restart_increase[splunk]=$inc"
echo "HERO_MEASURE: restart_increase[rest]=$rinc"
echo "HERO_MEASURE: splunk_rest_restart_diff[receiver]=$diff"
echo "  3 s polls (poll time = value): $(awk '{printf "%s=%s ", $1, $2}' arrivals.txt | cut -c1-900)"
k=0
for t in $bumps; do                                  # lag after the REST API first showed restart k
  k=$((k + 1)); want=$(( ${base:-0} + k ))
  pt=$(awk -v want=$want '$2 != "null" && $2 >= want {print $1; exit}' sf-restarts.txt)
  ar=$(awk -v want=$want '$2 != "null" && $2 >= want {print $1; exit}' arrivals.txt)
  pl=$([ -n "$pt" ] && echo $(( pt + 10 - t )) || echo null); al=$([ -n "$ar" ] && echo $(( ar - t )) || echo null)
  echo "restart $k: in Flink's REST API at $t; Splunk point $pt..$(( ${pt:-0} + 10 )) holds it (${pl} s); a 3 s poll first returned it at ${ar:-never} (${al} s)"
  echo "HERO_MEASURE: restart_visible_lag_s[point-$k]=$pl"
  echo "HERO_MEASURE: restart_visible_lag_s[arrival-$k]=$al"
done
echo "  metric type of flink.job.checkpoint.count: $(metric_type flink.job.checkpoint.count)"
for c in completed failed; do                        # checkpoints run every 5 s, so these can differ by a scrape's worth
  counter flink.job.checkpoint.count "$F and filter('checkpoint', '$c')" $(( w0 - 60 )) "$w1" "sf-$c.txt"
  f=$([ $c = completed ] && echo ckpt_ok || echo ckpt_fail)
  sc=$(awk -v a="$(at "sf-$c.txt" "$tb")" -v z="$(at "sf-$c.txt" "$te")" 'BEGIN { if (a == "null" || z == "null") print "null"; else printf "%d", z - a }')
  rc=$(awk -v a="$(rest_at "$W" $f $(( tb + 10 )))" -v z="$(rest_at "$W" $f $(( te + 10 )))" 'BEGIN { if (a == "null" || z == "null") print "null"; else printf "%d", z - a }')
  echo "checkpoints $c from $(( tb + 10 )) to $(( te + 10 )): Splunk +$sc, Flink's REST API +$rc"
  echo "HERO_MEASURE: checkpoints[splunk-$c]=$sc"
  echo "HERO_MEASURE: checkpoints[rest-$c]=$rc"
done
flink_names > names-after.txt
echo "flink.* metric names in Splunk now: $(wc -l < names-after.txt | tr -d ' ') ($(tr '\n' ' ' < names-after.txt | cut -c1-600))"
gc=$(series "sf_metric:flink.jvm.gc.collections.count AND deployment.environment:${LAB_NAME}")
for r in jobmanager taskmanager; do
  n=$(jq -r --arg r "$r" '[.results[]? | select(.dimensions["flink.resource.type"] == $r)] | length' <<<"$gc")
  echo "flink.jvm.gc.collections.count series for $r: $n ($(jq -r --arg r "$r" '[.results[]? | select(.dimensions["flink.resource.type"] == $r) | .dimensions.name] | unique | join(",")' <<<"$gc"))"
  echo "HERO_MEASURE: splunk_gc_series[$r]=$n"
done
[ "$diff" = 0 ] && [ "${rinc:-0}" != null ] && [ "${rinc:-0}" -ge 3 ] && [ "${rest:-0}" -ge 3 ] || fail "Splunk's restart count didn't match the restarts Flink counted"
emit fixed
