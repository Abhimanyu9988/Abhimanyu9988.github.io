#!/usr/bin/env bash
# BEFORE: sample HTTP service, fixed replica count, no latency-based autoscaling.
# Wall-clock budget: 840 s (limit is 900 s). Every wait is capped to what is left.
set -uo pipefail
cd "$(dirname "$0")"

START=$(date +%s)
LIMIT=840
HERO_DONE=0
remaining() { echo $(( START + LIMIT - $(date +%s) )); }
# cap WANT RESERVE -> min(WANT, remaining-RESERVE), at least 5 seconds
cap() {
  local want=$1 reserve=$2 r
  r=$(( $(remaining) - reserve ))
  [ "$r" -lt 5 ] && r=5
  [ "$want" -lt "$r" ] && echo "$want" || echo "$r"
}
emit() { HERO_DONE=1; echo "HERO_RESULT: $1"; }
fail() { echo "FAIL: $*"; [ "$HERO_DONE" = 1 ] || emit not_reproduced; exit 0; }
# Safety net: whatever happens, one HERO_RESULT line is printed.
trap '[ "$HERO_DONE" = 1 ] || { HERO_DONE=1; echo "HERO_RESULT: not_reproduced"; }' EXIT

aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null || fail "could not write kubeconfig"

echo "== Waiting for 2 worker nodes to join (up to 5 min)"
ready=0
t0=$(date +%s)
while [ $(( $(date +%s) - t0 )) -lt 300 ]; do
  ready=$(kubectl --request-timeout=15s get nodes --no-headers 2>/dev/null | grep -c ' Ready' || true)
  [ "${ready:-0}" -ge 2 ] && break
  sleep 10
done
kubectl --request-timeout=15s get nodes -o wide || true
[ "${ready:-0}" -ge 2 ] || fail "nodes did not become Ready"
kubectl -n kube-system rollout status deploy/coredns --timeout="$(cap 90 600)s" || true

echo "== Deploying the sample service (1 replica, no autoscaler) and the load generator (0 replicas)"
kubectl apply -f k8s/ || fail "kubectl apply failed"
kubectl -n demo rollout status deploy/latency-app --timeout="$(cap 150 400)s" || fail "app did not roll out"

echo
echo "== Autoscaling objects in the namespace:"
kubectl -n demo get hpa 2>&1 || true
kubectl -n demo get deploy latency-app

# Read the app's /stats from inside the pod (no API-server proxy). Prints one JSON line, or nothing on failure.
stats() {
  local out
  out=$(kubectl --request-timeout=20s -n demo exec deploy/latency-app -c app -- python -c \
    "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/stats',timeout=10).read().decode())" 2>/dev/null) || return 0
  jq -c . <<<"$out" 2>/dev/null || true
}

echo
echo "== Idle baseline"
stats

echo
echo "== Starting 60 concurrent clients"
kubectl -n demo scale deploy/loadgen --replicas=1 >/dev/null || fail "could not scale loadgen"
kubectl -n demo rollout status deploy/loadgen --timeout="$(cap 120 150)s" || fail "loadgen did not start"

prev_cpu=$(jq -r '.cpu_seconds // 0' <<<"$(stats)" 2>/dev/null || echo 0)
[ -n "$prev_cpu" ] || prev_cpu=0
prev_t=$(date +%s.%N)
maxlat=0
maxcpu=0
for i in $(seq 1 6); do
  [ "$(remaining)" -gt 30 ] || break
  sleep 10
  s=$(stats)
  [ -z "$s" ] && { echo "t=$((i*10))s  (stats not available this round)"; continue; }
  cpu=$(jq -r '.cpu_seconds // 0' <<<"$s")
  lat=$(jq -r '.avg_latency_ms // 0' <<<"$s")
  now=$(date +%s.%N)
  pct=$(awk -v a="$cpu" -v b="$prev_cpu" -v t="$now" -v u="$prev_t" 'BEGIN{printf "%.1f",(a-b)/(t-u)*100}')
  reps=$(kubectl --request-timeout=15s -n demo get deploy latency-app -o jsonpath='{.status.readyReplicas}')
  printf "t=%3ss  replicas=%s  avg_latency=%8s ms  cpu=%s%% of one core\n" "$((i*10))" "$reps" "$lat" "$pct"
  maxlat=$(awk -v a="$maxlat" -v b="$lat" 'BEGIN{print (b>a)?b:a}')
  maxcpu=$(awk -v a="$maxcpu" -v b="$pct" 'BEGIN{print (b>a)?b:a}')
  prev_cpu=$cpu
  prev_t=$now
done

replicas=$(kubectl --request-timeout=15s -n demo get deploy latency-app -o jsonpath='{.status.replicas}')
kubectl -n demo scale deploy/loadgen --replicas=0 >/dev/null

echo
echo "== Summary: peak latency ${maxlat} ms, peak CPU ${maxcpu}% of one core, replicas still ${replicas}"
echo "   Users feel multi-second latency, CPU stays low, nothing scales."

if [ "$replicas" = "1" ] && awk -v a="$maxlat" 'BEGIN{exit !(a>1000)}'; then
  emit reproduced
else
  emit not_reproduced
fi
exit 0
