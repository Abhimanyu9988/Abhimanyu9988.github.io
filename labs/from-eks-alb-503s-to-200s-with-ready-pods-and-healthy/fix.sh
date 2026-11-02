#!/usr/bin/env bash
# BUILD: fix probe + health check + readiness gate, then prove 200, healthy targets and Splunk data.
# Time budget: the whole script is held under ~14.5 minutes (limit 15); the Splunk retry uses what is left (max 8 min).
set -uo pipefail
cd "$(dirname "$0")"
export AWS_PAGER=""

START=$(date +%s)
LIMIT=$(( START + 840 ))
bail() { echo "$*"; echo "HERO_RESULT: not_fixed"; exit 1; }

[ -n "${LAB_NAME:-}" ] || bail "LAB_NAME must be set"
[ -n "${SPLUNK_REALM:-}" ] || bail "SPLUNK_REALM must be set"
[ -n "${SPLUNK_API_TOKEN:-}" ] || bail "SPLUNK_API_TOKEN must be set"
aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" >/dev/null || bail "cannot write kubeconfig"
OK=1
fail() { echo "FAILED: $*"; OK=0; }

echo "== Installed chart versions"
helm list -A

HOST="$(kubectl get ingress web -n lab-web --request-timeout=20s -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')"
TG="$(kubectl get targetgroupbinding -n lab-web --request-timeout=20s -o jsonpath='{.items[0].spec.targetGroupARN}')"
[ -n "$HOST" ] && [ -n "$TG" ] || bail "no ingress hostname or target group found (did reproduce.sh run?)"
echo "ALB: $HOST"
echo "curl before the fix: $(curl -s -o /dev/null -m 8 -w '%{http_code}' "http://${HOST}/")"

echo
echo "== 1. Apply the fix"
echo "  - readiness probe path /ready -> /   (the app serves /, not /ready)"
echo "  - ALB health check path /healthz -> /  (success-codes 200)"
echo "  - pod readiness gate injection on the namespace: a pod only turns Ready once the ALB reports its target healthy"
echo "  - preStop sleep so pods keep answering while the ALB deregisters them"
kubectl label namespace lab-web elbv2.k8s.aws/pod-readiness-gate-inject=enabled --overwrite
kubectl apply -f k8s/app-fixed.yaml || bail "apply failed"
T0=$(date +%s)

echo
echo "== 2. Wait for the rollout (new pods must pass the probe AND the ALB health check; max 3.5 min)"
if kubectl rollout status deployment/web -n lab-web --timeout=210s; then
  echo "rollout finished in $(( $(date +%s) - T0 ))s"
else
  fail "rollout did not finish"
  kubectl get pods -n lab-web -o wide
  kubectl describe pods -n lab-web -l app=web | grep -A8 -i 'Conditions\|Events' | tail -30
fi
kubectl get pods -n lab-web -o wide
kubectl get pods -n lab-web -l app=web -o json | jq -r '.items[] | "  \(.metadata.name) readinessGates=\([.spec.readinessGates[]?.conditionType]|join(",")) Ready=\([.status.conditions[]|select(.type=="Ready")|.status][0])"'

echo
echo "== 3. curl the ALB"
CODE=000
end=$(( $(date +%s) + 45 ))
while [ "$(date +%s)" -lt "$end" ]; do
  CODE="$(curl -s -o /tmp/after_body.txt -m 5 -w '%{http_code}' "http://${HOST}/" || true)"
  [ "$CODE" = "200" ] && break
  sleep 3
done
echo "first 200 after $(( $(date +%s) - T0 ))s from kubectl apply (status now: $CODE)"
curl -s -D - -o /dev/null -m 5 "http://${HOST}/" | head -n 4
grep -io '<title>[^<]*</title>' /tmp/after_body.txt | head -n 1
[ "$CODE" = "200" ] || fail "curl did not return 200"
DIST=""
for i in $(seq 1 20); do DIST="$DIST $(curl -s -o /dev/null -m 3 -w '%{http_code}' "http://${HOST}/" || true)"; done
echo "20 requests -> $(echo $DIST | tr ' ' '\n' | sort | uniq -c | tr '\n' ';')"

echo
echo "== 4. ALB target health (AWS)"
TH=""; N=0; H=0
end=$(( $(date +%s) + 45 ))
while [ "$(date +%s)" -lt "$end" ]; do
  TH="$(aws elbv2 describe-target-health --target-group-arn "$TG" --output json)"
  N=$(jq '.TargetHealthDescriptions|length' <<<"$TH")
  H=$(jq '[.TargetHealthDescriptions[]|select(.TargetHealth.State=="healthy")]|length' <<<"$TH")
  [ "$N" -ge 2 ] && [ "$N" = "$H" ] && break
  sleep 5
done
jq -r '.TargetHealthDescriptions[] | "  target \(.Target.Id):\(.Target.Port)  state=\(.TargetHealth.State)"' <<<"$TH"
echo "healthy targets: ${H}/${N}"
[ "$N" -ge 2 ] && [ "$N" = "$H" ] || fail "targets are not all healthy"

echo
echo "== 5. Splunk Observability Cloud (realm ${SPLUNK_REALM}) - cluster ${LAB_NAME}"
q() {
  curl -sS -G -m 10 "https://api.${SPLUNK_REALM}.signalfx.com/v2/metrictimeseries" \
    --data-urlencode "query=$1" --data-urlencode "limit=1000" \
    -H "X-SF-Token: ${SPLUNK_API_TOKEN}" 2>/dev/null
}
cnt() { local c; c=$(jq -r '(.results // []) | length' <<<"$1" 2>/dev/null); echo "${c:-0}"; }
# Retry up to 8 minutes, but never past the script's own time limit.
DEADLINE=$(( $(date +%s) + 480 ))
[ "$DEADLINE" -gt "$(( LIMIT - 30 ))" ] && DEADLINE=$(( LIMIT - 30 ))
CN=0; WN=0; LN=0; A=""; B=""; C=""
while :; do
  A="$(q "k8s.cluster.name:${LAB_NAME}")";                                                    CN=$(cnt "$A")
  B="$(q "k8s.cluster.name:${LAB_NAME} AND k8s.namespace.name:lab-web")";                      WN=$(cnt "$B")
  C="$(q "k8s.cluster.name:${LAB_NAME} AND k8s.pod.name:aws-load-balancer-controller*")";     LN=$(cnt "$C")
  if [ "$WN" = "0" ]; then
    B="$(q "k8s.cluster.name:${LAB_NAME} AND k8s.pod.name:web-*")"; WN=$(cnt "$B")
  fi
  [ "$CN" -gt 0 ] && [ "$WN" -gt 0 ] && [ "$LN" -gt 0 ] && break
  [ "$(date +%s)" -ge "$DEADLINE" ] && break
  echo "  waiting for data (cluster series=$CN, app series=$WN, controller series=$LN) ..."
  sleep 15
done
echo "time series with k8s.cluster.name=${LAB_NAME}: ${CN}"
echo "  distinct metric names: $(jq -r '[(.results // [])[].metric] | unique | length' <<<"$A" 2>/dev/null)"
echo "  sample metrics: $(jq -r '[(.results // [])[].metric] | unique | .[:8] | join(", ")' <<<"$A" 2>/dev/null)"
echo "time series for the app pods (namespace lab-web): ${WN}"
echo "  metrics: $(jq -r '[(.results // [])[].metric] | unique | .[:6] | join(", ")' <<<"$B" 2>/dev/null)"
echo "  pods: $(jq -r '[(.results // [])[] | ((.dimensions // {})["k8s.pod.name"] // empty)] | unique | .[:6] | join(", ")' <<<"$B" 2>/dev/null)"
echo "time series for the AWS Load Balancer Controller pod: ${LN}"
echo "  metrics: $(jq -r '[(.results // [])[].metric] | unique | .[:6] | join(", ")' <<<"$C" 2>/dev/null)"
echo "  pods: $(jq -r '[(.results // [])[] | ((.dimensions // {})["k8s.pod.name"] // empty)] | unique | .[:3] | join(", ")' <<<"$C" 2>/dev/null)"
[ "$CN" -gt 0 ] || fail "no Splunk series for the cluster"
[ "$WN" -gt 0 ] || fail "no Splunk series for the app pods"
[ "$LN" -gt 0 ] || fail "no Splunk series for the AWS Load Balancer Controller pod"

echo
if [ "$OK" = "1" ]; then
  echo "HERO_RESULT: fixed"
else
  echo "HERO_RESULT: not_fixed"
fi
exit 0
