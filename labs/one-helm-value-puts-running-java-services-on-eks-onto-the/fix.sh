#!/usr/bin/env bash
# BUILD: one Helm value turns on OBI, and nothing in the demo namespace is touched. The map and restart results come from it alone.
# Five OFF rounds end reproduce.sh, five ON rounds run here, and the Collector tap goes in after them.
# OBI assigns its own trace IDs, so each trace case reads them from its exported spans: one round, TRACE_WAIT, then 30 traces.
# Each case waits the same. The first case's wait overlaps propagate.sh, which adds the second value and runs its round.
# Time plan of 830s: before-check 15, first value 40, warm-up and ON rounds 205, tap 45, first round 45, propagate.sh 100 of its 170,
# then each read at TRACE_WAIT after its round, 60 each, own spans 20, up to 75 for the map. It ends near 800. propagate.sh needs 380s left, or -1.
set -uo pipefail
cd "$(dirname "$0")"

START=$(date +%s)
LIMIT=830
HERO_DONE=0
export TRACE_WAIT=200
remaining() { echo $(( START + LIMIT - $(date +%s) )); }
cap() { local want=$1 reserve=$2 r; r=$(( $(remaining) - reserve )); [ "$r" -lt 5 ] && r=5; [ "$want" -lt "$r" ] && echo "$want" || echo "$r"; }
emit() { HERO_DONE=1; echo "HERO_RESULT: $1"; }
fail() { echo "FAIL: $*"; [ "$HERO_DONE" = 1 ] || emit not_fixed; exit 0; }
trap '[ "$HERO_DONE" = 1 ] || { HERO_DONE=1; echo "HERO_RESULT: not_fixed"; }' EXIT

source ./lib.sh
aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null || fail "could not write kubeconfig"
[ -n "${SPLUNK_REALM:-}" ] || fail "SPLUNK_REALM is not set"
[ -n "${SPLUNK_ACCESS_TOKEN:-}" ] || fail "SPLUNK_ACCESS_TOKEN is not set"
[ -n "${SPLUNK_API_TOKEN:-}" ] || fail "SPLUNK_API_TOKEN is not set"
export API="https://api.${SPLUNK_REALM}.signalfx.com"
LOAD_NODE=$(cat load-node.txt) || fail "no load node recorded"
export LOAD_NODE
export CHART="splunk-otel-collector-chart/splunk-otel-collector --version 0.163.1"
helm repo add splunk-otel-collector-chart https://signalfx.github.io/splunk-otel-collector-chart >/dev/null 2>&1
helm repo update >/dev/null 2>&1

echo "== 2. The before-check: the cluster reaches Splunk, and the map is empty"
cluster=0
t0=$(date +%s)
while [ $(( $(date +%s) - t0 )) -lt 40 ]; do
  cluster=$(series "k8s.cluster.name:${LAB_NAME}" | jq -r '.count // (.results | length) // 0' 2>/dev/null || echo 0)
  [ "${cluster:-0}" -gt 0 ] && break
  sleep 10
done
echo "cluster metric time series in Splunk: ${cluster:-0}"
[ "${cluster:-0}" -gt 0 ] || fail "no cluster data reached Splunk, so an empty map would prove nothing"
before=$(on_map "$(topology)")
echo "services on the map before the change: ${before:-0}"
[ "${before:-0}" -eq 0 ] || fail "the map already held these services, so the comparison would be unfair"
ids=$(terraform output -raw node_instance_ids 2>/dev/null)
credits=$(aws ec2 describe-instance-credit-specifications --instance-ids $ids --query 'InstanceCreditSpecifications[].CpuCredits' --output text 2>/dev/null | tr '\t' ' ')
echo "instance CPU credit mode: ${credits:-not readable here}"

echo "== 4. The first value"
echo "helm upgrade --reuse-values --set obi.enabled=true"
helm upgrade splunk-otel-collector $CHART --namespace splunk --reuse-values --set obi.enabled=true >/dev/null || fail "helm upgrade failed"
obi_ready 70 30
helm list -n splunk -o json | jq -r '.[] | "\(.name) \(.chart) app \(.app_version)"'
kubectl -n splunk get ds -l app.kubernetes.io/name=obi -o json | jq -r '.items[0].spec.template.spec as $s | "OBI hostPID=\($s.hostPID) hostNetwork=\($s.hostNetwork) privileged=\($s.containers[0].securityContext.privileged)"'
obi_settings
obi_log | grep -E 'instrumenting process|java agent|java injection|capabilit|level=ERROR' | cut -c1-200 | tail -10

echo "== 5. One discarded round while OBI settles, then five rounds with OBI on, on the same pods"
echo "tap lines during the timed rounds: $(tap_lines)"
: > load-settle.txt
load_round load-settle.txt
: > load-on.txt
for n in 1 2 3 4 5; do round load-on.txt ON; done
pods_now | tee pods-obi.txt
for s in orders inventory; do
  loaded=$(kubectl -n demo exec "$(newest "$s")" -c app -- grep -c libinstrument /proc/1/maps 2>/dev/null || true)
  echo "$loaded" > "helper-$s.txt"
  echo "$s Java agent mappings after OBI: ${loaded:-0}"
done

echo "== 6. The Collector tap and the first trace round"
helm upgrade splunk-otel-collector $CHART --namespace splunk --reuse-values -f k8s/collector-tap.yaml >/dev/null || fail "could not add the tap"
kubectl -n splunk rollout status daemonset/splunk-otel-collector-agent --timeout=60s >/dev/null || fail "the Collector did not roll out"
echo "tap lines for the trace rounds: $(tap_lines)"
echo "helm values under obi, unchanged: $(helm get values splunk-otel-collector -n splunk -o json | jq -c '.obi // {}')"
: > load-trace.txt
on_start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
load_round load-trace.txt
on_end=$(date +%s)
tap_snapshot default "$on_start"
one_value_end=$(date +%s)   # the first value's map ends here

echo "== 7. The second value, in propagate.sh, while the first case waits"
if [ "$(remaining)" -ge 380 ]; then
  timeout 170 bash ./propagate.sh
else
  echo "propagate.sh did not start: only $(remaining)s left, and it needs 380s"
fi
echo "== 7b. Reading each case"
read_case default "$on_end"
read -r default_found default_joined default_searched < read-default.txt
if [ -s propagate-result.txt ]; then
  read -r prop_start prop_end < propagate-result.txt
  read_case propagation "$prop_end"
  read -r prop_found prop_joined prop_searched < read-propagation.txt
else
  prop_found=0; prop_joined=0; prop_searched=0
fi
bash ./propagate.sh own default
pods_now > pods-final.txt
later() { if [ -s propagate-result.txt ]; then restarts_by_pod pods-obi.txt pods-final.txt; else printf '%s -1\n' inventory orders postgres; fi; }

echo "== 8. The result"
if [ -s propagate-result.txt ]; then   # from the first value's end to now, retried for map lag
  for n in 1 2 3 4 5 6; do
    second=$(map_nodes "$(topology "$(date +%s)" "$one_value_end")")
    [ "$second" != null ] || [ "$(remaining)" -lt 20 ] && break
    sleep 15
  done
  echo "map inferred flag per service, second value window, $(( $(date +%s) - prop_end ))s after its round: $second"
fi
map=$(topology "$one_value_end")   # this window ends before the second value
services=$(on_map "$map")
show_map "$map"
echo "map inferred flag per service, first value window: $(map_nodes "$map")"
metric_services "$one_value_end" | sort > metric-services.txt
echo "services with a request metric by then: $(tr '\n' ' ' < metric-services.txt)"
own=$(sort own-default.txt 2>/dev/null | comm -12 - metric-services.txt | grep -c . || true)
pods_now
[ "$(grep -c . pods-obi.txt)" -eq 3 ] && [ "$(grep -c . pods-final.txt)" -eq 3 ] || fail "a pod is gone"
echo "services with own spans, from a server span and a request metric, the headline figure: ${own:-0}"
echo "HERO_MEASURE: services_with_own_spans=${own:-0}"
echo "services on map from Splunk, inferred nodes included: ${services:-0}"
echo "HERO_MEASURE: services_on_map=${services:-0}"
restarts=0
while read -r pod n; do
  echo "$pod pod restarts added by the first value: $n"
  echo "HERO_MEASURE: pod_restarts_added[$pod]=$n"
  restarts=$(( restarts + n ))
done < <(restarts_by_pod pods-before.txt pods-obi.txt)
while read -r pod n; do
  echo "$pod pod restarts after propagation, -1 means not run: $n"
  echo "HERO_MEASURE: pod_restarts_after_propagation[$pod]=$n"
done < <(later)
echo "traces joined default ${default_joined} found ${default_found} of ${default_searched}, joined propagation ${prop_joined} found ${prop_found} of ${prop_searched}"
for m in joined_default:$default_joined found_default:$default_found joined_propagation:$prop_joined found_propagation:$prop_found; do echo "HERO_MEASURE: traces_${m%%:*}=${m##*:}"; done
for s in orders inventory; do
  held=$(cat "helper-$s.txt")
  flag=$(( ${held:-0} > 0 ))
  echo "$s jvm helper attached: $flag"
  echo "HERO_MEASURE: jvm_helper_attached[$s]=$flag"
done
off=$(p95s load-off.txt | sort -n)
on=$(p95s load-on.txt | sort -n)
echo "p95 with OBI off: min $(head -1 <<<"$off"), median $(sed -n 3p <<<"$off"), max $(tail -1 <<<"$off") ms"
echo "p95 with OBI on: min $(head -1 <<<"$on"), median $(sed -n 3p <<<"$on"), max $(tail -1 <<<"$on") ms"
added=$(awk -v a="$(sed -n 3p <<<"$on")" -v b="$(sed -n 3p <<<"$off")" 'BEGIN{printf "%.2f", a-b}')
echo "p95 added ms, medians of five rounds, about 20 requests a second: $added"
echo "HERO_MEASURE: p95_added_ms=$added"
noise=$(awk -v a="$(head -1 <<<"$off")" -v b="$(tail -1 <<<"$off")" 'BEGIN{printf "%.2f", b-a}')
echo "p95 noise ms, the spread of the five OFF rounds: $noise"
echo "HERO_MEASURE: p95_noise_ms=$noise"

if [ "${own:-0}" -ge 2 ] && [ "$restarts" -eq 0 ]; then
  emit fixed
else
  emit not_fixed
fi
