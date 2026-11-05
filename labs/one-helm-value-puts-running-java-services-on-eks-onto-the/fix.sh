#!/usr/bin/env bash
# BUILD: one Helm value turns on OBI, and nothing in the demo namespace is touched. The map and restart results come from that value alone.
# propagate.sh then adds a second value, and its trace reading mirrors this one: three rounds, the same sampled IDs, one fixed wait, one pass.
# Time plan, 830s in all: before-check 40, OFF rounds 100, first value 100, warm-up and ON rounds 130, TRACE_WAIT 90, pass 40,
# propagate.sh 310 (its own LIMIT, enforced with timeout), report 20. propagate.sh starts only if its whole budget is left, or it reports -1.
set -uo pipefail
cd "$(dirname "$0")"

START=$(date +%s)
LIMIT=830
HERO_DONE=0
export TRACE_WAIT=90
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

round() {   # file, label. One load round, printed next to CPU on all three nodes
  local o i
  o=$(cpu_now orders); i=$(cpu_now inventory)
  load_round "$1"
  echo "$2 round: p95 $(p95s "$1" | tail -1) ms, CPU busy pct orders node $(busy "$o" "$(cpu_now orders)"), inventory node $(busy "$i" "$(cpu_now inventory)"), load node $(grep -oE 'node_busy_pct=[0-9]+' "$1" | tail -1 | cut -d= -f2)"
}

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

echo "== 3. Three rounds with OBI off, right before the change"
: > load-off.txt
for n in 1 2 3; do round load-off.txt OFF; done

echo "== 4. The first value"
echo "helm upgrade --reuse-values --set obi.enabled=true"
helm upgrade splunk-otel-collector $CHART --namespace splunk --reuse-values --set obi.enabled=true >/dev/null || fail "helm upgrade failed"
obi_ready 70 30
helm list -n splunk -o json | jq -r '.[] | "\(.name) \(.chart) app \(.app_version)"'
kubectl -n splunk get ds -l app.kubernetes.io/name=obi -o json | jq -r '.items[0].spec.template.spec as $s | "OBI hostPID=\($s.hostPID) hostNetwork=\($s.hostNetwork) privileged=\($s.containers[0].securityContext.privileged)"'
obi_settings
obi_log | grep -E 'instrumenting process|java agent|java injection|capabilit|level=ERROR' | cut -c1-200 | tail -10

echo "== 5. One discarded round while OBI settles, then three rounds with OBI on, on the same pods"
: > load-settle.txt
load_round load-settle.txt
: > load-on.txt
for n in 1 2 3; do round load-on.txt ON; done
pods_now | tee pods-obi.txt
for s in orders inventory; do
  loaded=$(kubectl -n demo exec "$(newest "$s")" -c app -- grep -c libinstrument /proc/1/maps 2>/dev/null || true)
  echo "$loaded" > "helper-$s.txt"
  echo "$s Java agent mappings after OBI: ${loaded:-0}"
done

echo "== 6. A fixed wait of ${TRACE_WAIT}s, then one pass over the sampled traces"
map=""; services=0
ended=$(date +%s)
while [ $(( $(date +%s) - ended )) -lt "$TRACE_WAIT" ]; do
  map=$(topology)
  services=$(on_map "$map")
  echo "  $(( $(date +%s) - ended ))s after the rounds: services=${services:-0}"
  left=$(( TRACE_WAIT - ( $(date +%s) - ended ) ))
  [ "$left" -gt 0 ] && sleep "$(( left < 15 ? left : 15 ))"
done
read -r default_best default_found default_joined default_searched < <(trace_pass load-on.txt default)

echo "== 7. The second value, in propagate.sh"
one_value_end=$(date +%s)
if [ "$(remaining)" -ge 330 ]; then
  timeout 310 bash ./propagate.sh
else
  echo "propagate.sh did not start: only $(remaining)s left, and it needs 330s"
fi
if [ -s propagate-result.txt ]; then
  read -r prop_best prop_found prop_joined prop_searched < propagate-result.txt
else
  prop_best=-1; prop_found=0; prop_joined=0; prop_searched=0
  pods_now > pods-final.txt
fi
later() { if [ -s propagate-result.txt ]; then restarts_by_pod pods-obi.txt pods-final.txt; else printf '%s -1\n' inventory orders postgres; fi; }

echo "== 8. The result"
# The window ends before the second value, so a map that fills in late still belongs to the first.
one=$(topology "$one_value_end")
got=$(on_map "$one")
[ "${got:-0}" -gt "${services:-0}" ] && { services=$got; map=$one; }
show_map "$map"
kubectl get nodes -o custom-columns='NAME:.metadata.name,KERNEL:.status.nodeInfo.kernelVersion,OS:.status.nodeInfo.osImage'
pods_now
[ "$(grep -c . pods-obi.txt)" -eq 3 ] && [ "$(grep -c . pods-final.txt)" -eq 3 ] || fail "a pod is gone"
echo "services on map from Splunk with the first value: ${services:-0}"
echo "HERO_MEASURE: services_on_map=${services:-0}"
echo "load client on the map, traced by OBI's default of every namespace: $(jq -r '[.data.nodes[]? | select(.serviceName == "load")] | length' <<<"$map" 2>/dev/null)"
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
echo "first value: ${default_joined} joined of ${default_found} known traces found in Splunk, ${default_searched} IDs searched"
echo "second value: ${prop_joined} joined of ${prop_found} known traces found in Splunk, ${prop_searched} IDs searched"
echo "services in trace default, most on one trace, 2 means joined: ${default_best}"
echo "HERO_MEASURE: services_in_trace_default=${default_best}"
echo "services in trace propagation, most on one trace, 2 means joined, -1 means not run: ${prop_best}"
echo "HERO_MEASURE: services_in_trace_propagation=${prop_best}"
for s in orders inventory; do
  held=$(cat "helper-$s.txt")
  flag=$(( ${held:-0} > 0 ))
  echo "$s jvm helper attached: $flag"
  echo "HERO_MEASURE: jvm_helper_attached[$s]=$flag"
done
off=$(p95s load-off.txt | sort -n)
on=$(p95s load-on.txt | sort -n)
echo "p95 with OBI off: min $(head -1 <<<"$off"), median $(sed -n 2p <<<"$off"), max $(tail -1 <<<"$off") ms"
echo "p95 with OBI on: min $(head -1 <<<"$on"), median $(sed -n 2p <<<"$on"), max $(tail -1 <<<"$on") ms"
added=$(awk -v a="$(sed -n 2p <<<"$on")" -v b="$(sed -n 2p <<<"$off")" 'BEGIN{printf "%.2f", a-b}')
echo "p95 added ms, median of three rounds minus median of three rounds: $added"
echo "HERO_MEASURE: p95_added_ms=$added"

if [ "${services:-0}" -ge 2 ] && [ "$restarts" -eq 0 ]; then
  emit fixed
else
  emit not_fixed
fi
