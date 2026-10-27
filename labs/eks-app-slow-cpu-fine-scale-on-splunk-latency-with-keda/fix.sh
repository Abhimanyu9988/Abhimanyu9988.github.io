#!/usr/bin/env bash
# BUILD: Splunk OTel Collector -> Splunk Observability Cloud, KEDA splunk-observability scaler.
# Wall-clock budget: 870 s (limit is 900 s). The Splunk proof is retried for up to 8 minutes
# when time allows; every wait is capped so the later stages always keep a reserve.
set -uo pipefail
cd "$(dirname "$0")"

START=$(date +%s)
LIMIT=870
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
fail() { echo "FAIL: $*"; [ "$HERO_DONE" = 1 ] || emit not_fixed; exit 0; }
trap '[ "$HERO_DONE" = 1 ] || { HERO_DONE=1; echo "HERO_RESULT: not_fixed"; }' EXIT

aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null || fail "could not write kubeconfig"

[ -n "${SPLUNK_REALM:-}" ] || fail "SPLUNK_REALM is not set"
[ -n "${SPLUNK_ACCESS_TOKEN:-}" ] || fail "SPLUNK_ACCESS_TOKEN is not set"
[ -n "${SPLUNK_API_TOKEN:-}" ] || fail "SPLUNK_API_TOKEN is not set"
API="https://api.${SPLUNK_REALM}.signalfx.com"

echo "== 1. Install the Splunk OTel Collector (0.163.1) and KEDA (2.21.0)"
helm repo add splunk-otel-collector-chart https://signalfx.github.io/splunk-otel-collector-chart >/dev/null 2>&1
helm repo add kedacore https://kedacore.github.io/charts >/dev/null 2>&1
helm repo update >/dev/null 2>&1

helm upgrade --install splunk-otel-collector splunk-otel-collector-chart/splunk-otel-collector \
  --version 0.163.1 --namespace splunk --create-namespace \
  --set splunkObservability.realm="$SPLUNK_REALM" \
  --set splunkObservability.accessToken="$SPLUNK_ACCESS_TOKEN" \
  --set clusterName="$LAB_NAME" \
  --set cloudProvider=aws \
  --set distribution=eks \
  --set autodetect.prometheus=true \
  >/dev/null || fail "splunk collector helm install failed"

# The collector starts in the background while KEDA installs.
helm upgrade --install keda kedacore/keda --version 2.21.0 \
  --namespace keda --create-namespace --wait --timeout "$(cap 200 560)s" >/dev/null || fail "keda helm install failed"

kubectl -n splunk rollout status daemonset/splunk-otel-collector-agent --timeout="$(cap 120 520)s" || fail "collector agent not ready"

echo
echo "Installed charts:"
helm list -A --filter 'keda|splunk' || true

proof_budget=$(cap 480 330)
echo
echo "== 2. Prove the data reaches Splunk Observability Cloud (retry up to ${proof_budget}s, 8 min max)"
q() {
  curl -sS --max-time 20 -G -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/metrictimeseries" \
    --data-urlencode "query=$1" --data-urlencode "limit=1000" 2>/dev/null
}
cnt() { jq -r '.count // (.results|length) // 0' 2>/dev/null || echo 0; }

t0=$(date +%s)
c1=0; c2=0; r1=''; r2=''
while [ $(( $(date +%s) - t0 )) -lt "$proof_budget" ]; do
  r1=$(q "k8s.cluster.name:${LAB_NAME}"); c1=$(cnt <<<"$r1")
  r2=$(q "sf_metric:app_request_latency_ms AND k8s.cluster.name:${LAB_NAME}"); c2=$(cnt <<<"$r2")
  echo "  t+$(( $(date +%s) - t0 ))s: cluster time series=${c1:-0}, app latency time series=${c2:-0}"
  [ "${c1:-0}" -gt 0 ] && [ "${c2:-0}" -gt 0 ] && break
  sleep 20
done
if ! { [ "${c1:-0}" -gt 0 ] && [ "${c2:-0}" -gt 0 ]; }; then
  echo "--- collector agent log (errors)"
  kubectl -n splunk logs daemonset/splunk-otel-collector-agent --tail=300 2>/dev/null | grep -iE 'error|prometheus' | tail -15
  fail "data did not arrive in Splunk within ${proof_budget}s"
fi

echo "k8s.cluster.name=${LAB_NAME}: ${c1} metric time series, distinct metric names: $(jq '[.results[].metric]|unique|length' <<<"$r1")"
echo "sample metric names:"
jq -r '[.results[].metric]|unique|.[:8][]' <<<"$r1" | sed 's/^/  - /'
echo "app metric app_request_latency_ms: ${c2} time series; dimensions of one series:"
jq -c '.results[0].dimensions | with_entries(select(.key|test("^(app|k8s\\.(cluster|namespace|pod)\\.name|k8s\\.node\\.name)$")))' <<<"$r2"

echo
echo "== 3. KEDA: read-only API token as TriggerAuthentication + ScaledObject on the latency query"
kubectl -n demo create secret generic splunk-observability \
  --from-literal=accessToken="$SPLUNK_API_TOKEN" \
  --from-literal=realm="$SPLUNK_REALM" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null || fail "could not create secret"

cat <<EOF | kubectl apply -f - || fail "could not apply ScaledObject"
apiVersion: keda.sh/v1alpha1
kind: TriggerAuthentication
metadata:
  name: splunk-observability-auth
  namespace: demo
spec:
  secretTargetRef:
    - parameter: accessToken
      name: splunk-observability
      key: accessToken
    - parameter: realm
      name: splunk-observability
      key: realm
---
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: latency-app
  namespace: demo
spec:
  scaleTargetRef:
    name: latency-app
  minReplicaCount: 1
  maxReplicaCount: 5
  pollingInterval: 15
  cooldownPeriod: 60
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleUp:
          stabilizationWindowSeconds: 0
          policies:
            - type: Pods
              value: 4
              periodSeconds: 15
        scaleDown:
          stabilizationWindowSeconds: 15
          policies:
            - type: Percent
              value: 100
              periodSeconds: 15
  triggers:
    - type: splunk-observability
      metricType: Value
      metadata:
        query: "data('app_request_latency_ms', filter=filter('k8s.cluster.name', '${LAB_NAME}')).max().publish()"
        duration: "40"
        queryAggregator: "max"
        targetValue: "400"
        activationTargetValue: "0"
      authenticationRef:
        name: splunk-observability-auth
EOF

if ! kubectl -n demo wait --for=condition=Ready scaledobject/latency-app --timeout="$(cap 90 240)s"; then
  kubectl -n demo describe scaledobject latency-app | tail -25
  kubectl -n keda logs deploy/keda-operator --tail=40 2>/dev/null | grep -i error | tail -8
  fail "ScaledObject never became Ready"
fi

echo "waiting for KEDA to read the Splunk metric (idle value expected ~0)"
m=''
t1=$(date +%s)
hpa_budget=$(cap 60 190)
while [ $(( $(date +%s) - t1 )) -lt "$hpa_budget" ]; do
  m=$(kubectl --request-timeout=15s -n demo get hpa keda-hpa-latency-app -o jsonpath='{.status.currentMetrics[0].external.current.value}' 2>/dev/null)
  [ -n "$m" ] && break
  sleep 10
done
echo "HPA current metric from Splunk: '${m}' (target 400)"
kubectl -n demo get scaledobject,hpa

echo
echo "== 4. Push load: 60 concurrent clients"
t_load=$(date +%s)
kubectl -n demo scale deploy/loadgen --replicas=1 >/dev/null
kubectl -n demo rollout status deploy/loadgen --timeout="$(cap 90 150)s" || true
up_budget=$(cap 240 80)
up_ok=0
while [ $(( $(date +%s) - t_load )) -lt "$up_budget" ]; do
  ready=$(kubectl --request-timeout=15s -n demo get deploy latency-app -o jsonpath='{.status.readyReplicas}'); ready=${ready:-0}
  want=$(kubectl --request-timeout=15s -n demo get deploy latency-app -o jsonpath='{.spec.replicas}')
  metric=$(kubectl --request-timeout=15s -n demo get hpa keda-hpa-latency-app -o jsonpath='{.status.currentMetrics[0].external.current.value}' 2>/dev/null)
  printf "T+%3ss  replicas desired=%s ready=%s  splunk_latency_ms(max)=%s\n" "$(( $(date +%s) - t_load ))" "$want" "$ready" "${metric:-n/a}"
  if [ "$ready" -ge 5 ]; then up_ok=1; break; fi
  sleep 10
done
up_secs=$(( $(date +%s) - t_load ))

echo
echo "== Splunk's own view of the latency series (last 5 min, 10s resolution)"
now=$(date +%s)
curl -sS --max-time 15 -G -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v1/timeserieswindow" \
  --data-urlencode "query=sf_metric:app_request_latency_ms AND k8s.cluster.name:${LAB_NAME}" \
  --data-urlencode "startMS=$(( (now-300)*1000 ))" --data-urlencode "endMS=$(( now*1000 ))" \
  --data-urlencode "resolution=10000" 2>/dev/null \
  | jq -r '[.data[]?[]? | try .[1]] | "datapoints=\(length) max_ms=\(max // 0) min_ms=\(min // 0)"' 2>/dev/null || true

[ "$up_ok" = "1" ] || { kubectl -n demo describe hpa keda-hpa-latency-app | tail -20; fail "replicas never reached 5"; }
echo "Scale-up: 1 -> 5 ready replicas in ${up_secs}s after load started"

echo
echo "== 5. Stop load; replicas should return to 1"
kubectl -n demo scale deploy/loadgen --replicas=0 >/dev/null
t_stop=$(date +%s)
down_budget=$(cap 300 25)
down_ok=0
while [ $(( $(date +%s) - t_stop )) -lt "$down_budget" ]; do
  total=$(kubectl --request-timeout=15s -n demo get deploy latency-app -o jsonpath='{.status.replicas}'); total=${total:-0}
  want=$(kubectl --request-timeout=15s -n demo get deploy latency-app -o jsonpath='{.spec.replicas}')
  metric=$(kubectl --request-timeout=15s -n demo get hpa keda-hpa-latency-app -o jsonpath='{.status.currentMetrics[0].external.current.value}' 2>/dev/null)
  printf "T+%3ss  replicas desired=%s running=%s  splunk_latency_ms(max)=%s\n" "$(( $(date +%s) - t_stop ))" "$want" "$total" "${metric:-n/a}"
  if [ "$total" = "1" ] && [ "$want" = "1" ]; then down_ok=1; break; fi
  sleep 10
done
down_secs=$(( $(date +%s) - t_stop ))

echo
kubectl -n demo get scaledobject,hpa,deploy latency-app 2>/dev/null || kubectl -n demo get scaledobject,hpa
helm list -A --filter 'keda|splunk' || true

[ "$down_ok" = "1" ] || fail "replicas did not return to 1"
echo
echo "Summary: 1 -> 5 replicas in ${up_secs}s under load; back to 1 in ${down_secs}s after load stopped (driven by Splunk latency, not CPU)."
emit fixed
exit 0
