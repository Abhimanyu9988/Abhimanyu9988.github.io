#!/usr/bin/env bash
# BEFORE: Splunk collector + AWS Load Balancer Controller + a misconfigured app behind an ALB.
# Time budget: every wait is bounded and the whole script is held under ~14 minutes (limit 15).
set -uo pipefail
cd "$(dirname "$0")"
export AWS_PAGER=""

START=$(date +%s)
BUDGET=840
left() { echo $(( BUDGET - ($(date +%s) - START) )); }
bail() { echo "$*"; echo "HERO_RESULT: not_reproduced"; exit 1; }

[ -n "${AWS_REGION:-}" ] || bail "AWS_REGION must be set"
[ -n "${LAB_NAME:-}" ] || bail "LAB_NAME must be set"
[ -n "${SPLUNK_REALM:-}" ] || bail "SPLUNK_REALM must be set"
[ -n "${SPLUNK_ACCESS_TOKEN:-}" ] || bail "SPLUNK_ACCESS_TOKEN must be set"

aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" >/dev/null || bail "cannot write kubeconfig"
CLUSTER="$(terraform output -raw cluster_name)"
VPC_ID="$(terraform output -raw vpc_id)"
REGION="$AWS_REGION"
SPLUNK_CHART_VERSION="${SPLUNK_CHART_VERSION:-0.163.1}"
LBC_CHART_VERSION="${LBC_CHART_VERSION:-1.13.4}"

echo "== 1. Wait for the two self-managed worker nodes (max ~3.5 min)"
end=$(( $(date +%s) + 150 ))
while [ "$(date +%s)" -lt "$end" ]; do
  n=$(kubectl get nodes --no-headers --request-timeout=10s 2>/dev/null | wc -l)
  [ "$n" -ge 2 ] && break
  sleep 5
done
kubectl wait --for=condition=Ready node --all --timeout=60s --request-timeout=20s || { kubectl get nodes; bail "nodes not ready"; }
kubectl get nodes -o wide

echo
echo "== 2. Install the Splunk OpenTelemetry Collector (chart ${SPLUNK_CHART_VERSION}) for cluster ${LAB_NAME}"
helm repo add splunk-otel-collector-chart https://signalfx.github.io/splunk-otel-collector-chart >/dev/null 2>&1
helm repo add eks https://aws.github.io/eks-charts >/dev/null 2>&1
helm repo update >/dev/null 2>&1
helm upgrade --install splunk-otel-collector splunk-otel-collector-chart/splunk-otel-collector \
  --version "$SPLUNK_CHART_VERSION" --namespace splunk --create-namespace \
  --set clusterName="$LAB_NAME" \
  --set splunkObservability.realm="$SPLUNK_REALM" \
  --set splunkObservability.accessToken="$SPLUNK_ACCESS_TOKEN" \
  --set cloudProvider=aws --set distribution=eks \
  --set splunkObservability.profilingEnabled=false \
  --wait --timeout 3m >/dev/null 2>&1 || echo "WARNING: splunk helm install did not finish cleanly"
kubectl get pods -n splunk --request-timeout=20s

echo
echo "== 3. Install the AWS Load Balancer Controller (chart ${LBC_CHART_VERSION}, EKS Pod Identity)"
# Installed after Splunk so no other chart creates Services while the controller webhook is starting.
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
  --version "$LBC_CHART_VERSION" --namespace kube-system \
  --set clusterName="$CLUSTER" --set region="$REGION" --set vpcId="$VPC_ID" \
  --set replicaCount=1 --set enableShield=false --set enableWaf=false --set enableWafv2=false \
  --wait --timeout 150s >/dev/null || { kubectl get pods -n kube-system --request-timeout=20s; bail "LBC install failed"; }
helm list -A

echo
echo "== 4. Deploy the misconfigured app and ingress"
kubectl apply -f k8s/app-broken.yaml || bail "apply failed"

HOST=""
end=$(( $(date +%s) + 100 ))
while [ "$(date +%s)" -lt "$end" ]; do
  HOST="$(kubectl get ingress web -n lab-web --request-timeout=10s -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)"
  [ -n "$HOST" ] && break
  sleep 5
done
if [ -z "$HOST" ]; then
  kubectl describe ingress web -n lab-web --request-timeout=20s | tail -20
  bail "ingress never got an ALB address"
fi
echo "ALB: $HOST"

echo
echo "== 5. Pods: Running but never Ready"
kubectl wait --for=condition=Ready pod -l app=web -n lab-web --timeout=20s || true
for i in 1 2 3 4; do
  kubectl get events -n lab-web --field-selector reason=Unhealthy 2>/dev/null | grep -q Readiness && break
  sleep 5
done
kubectl get pods -n lab-web -o wide
echo "-- readiness probe events:"
kubectl get events -n lab-web --field-selector reason=Unhealthy 2>/dev/null | tail -n 3
echo "-- EndpointSlice conditions (ready=false means the Service has no usable backends):"
kubectl get endpointslices -n lab-web -l kubernetes.io/service-name=web -o json --request-timeout=20s \
  | jq -r '.items[].endpoints[]? | "  pod \(.targetRef.name)  ip \(.addresses[0])  ready=\(.conditions.ready)"'

echo
echo "== 6. ALB target group registered targets"
TG=""
for i in 1 2 3 4 5 6 7 8; do
  TG="$(kubectl get targetgroupbinding -n lab-web --request-timeout=10s -o jsonpath='{.items[0].spec.targetGroupARN}' 2>/dev/null)"
  [ -n "$TG" ] && break
  sleep 5
done
if [ -n "$TG" ]; then
  echo "target group: $TG"
  echo "registered targets: $(aws elbv2 describe-target-health --target-group-arn "$TG" --query 'length(TargetHealthDescriptions)' --output text)"
  aws elbv2 describe-target-health --target-group-arn "$TG" --output json | jq -c '.TargetHealthDescriptions'
else
  echo "no TargetGroupBinding found"
fi

echo
echo "== 7. curl the ALB (waits for ALB DNS + provisioning, bounded by the script budget)"
CODE=000
while [ "$(left)" -gt 40 ]; do
  CODE="$(curl -s -o /tmp/before_body.txt -m 5 -w '%{http_code}' "http://${HOST}/" || true)"
  case "$CODE" in 503|200) break ;; esac
  sleep 5
done
curl -s -D - -o /dev/null -m 5 "http://${HOST}/" | head -n 5
grep -io '<h1>[^<]*</h1>' /tmp/before_body.txt | head -n 1
echo "curl http status: $CODE"

if [ "$CODE" = "503" ]; then
  echo "HERO_RESULT: reproduced"
else
  echo "HERO_RESULT: not_reproduced"
fi
exit 0
