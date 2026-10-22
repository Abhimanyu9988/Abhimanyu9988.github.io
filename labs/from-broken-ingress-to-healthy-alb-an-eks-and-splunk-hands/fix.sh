#!/usr/bin/env bash
# BUILD: AWS Load Balancer Controller (Pod Identity) + Ingress -> ALB + Splunk OTel Collector.
# All waits share one deadline (DEADLINE_SECS of loop time) so the script stays under 15 minutes.
set -uo pipefail
cd "$(dirname "$0")"

LBC_CHART_VERSION="${LBC_CHART_VERSION:-1.13.0}"            # app v2.13.0, matches var.lbc_app_version
SPLUNK_CHART_VERSION="${SPLUNK_CHART_VERSION:-0.116.0}"
DEADLINE_SECS=750
DEADLINE=$((SECONDS + DEADLINE_SECS))

# cap N -> min(N, seconds left before the deadline), at least 10
cap() {
  local left=$((DEADLINE - SECONDS))
  [ "$left" -lt 10 ] && left=10
  [ "$1" -lt "$left" ] && echo "$1" || echo "$left"
}

CLUSTER="$(terraform output -raw cluster_name)"
VPC_ID="$(terraform output -raw vpc_id)"
REGION="${AWS_REGION:-$(terraform output -raw region)}"
aws eks update-kubeconfig --name "$CLUSTER" >/dev/null

fail() { echo "FAILED: $*"; echo "HERO_RESULT: not_fixed"; exit 0; }

mts_json() {
  curl -sS --max-time 15 -G "https://api.${SPLUNK_REALM}.signalfx.com/v2/metrictimeseries" \
    -H "X-SF-Token: ${SPLUNK_API_TOKEN}" \
    --data-urlencode "query=$1" --data-urlencode "limit=100" 2>/dev/null
}
mts_count() {
  mts_json "$1" | jq -r 'if .count then .count else (.results|length) end' 2>/dev/null || echo 0
}

[ -n "${SPLUNK_REALM:-}" ] && [ -n "${SPLUNK_ACCESS_TOKEN:-}" ] && [ -n "${SPLUNK_API_TOKEN:-}" ] \
  || fail "export SPLUNK_REALM, SPLUNK_ACCESS_TOKEN and SPLUNK_API_TOKEN first"

kubectl apply -f k8s/app.yaml >/dev/null

# ------------------------------------------------------------------ 1. controller
echo "== 1. Install AWS Load Balancer Controller ${LBC_CHART_VERSION} (IAM through EKS Pod Identity)"
aws eks list-pod-identity-associations --cluster-name "$CLUSTER" \
  --query 'associations[].[namespace,serviceAccount]' --output text

cat > /tmp/lbc-values.yaml <<EOF
clusterName: ${CLUSTER}
region: ${REGION}
vpcId: ${VPC_ID}
replicaCount: 1
serviceAccount:
  create: true
  name: aws-load-balancer-controller
podAnnotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "8080"
  prometheus.io/path: "/metrics"
EOF

timeout 60 helm repo add eks https://aws.github.io/eks-charts --force-update >/dev/null
timeout 60 helm repo add splunk-otel-collector-chart https://signalfx.github.io/splunk-otel-collector-chart --force-update >/dev/null
timeout 120 helm repo update >/dev/null
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
  --version "$LBC_CHART_VERSION" -n kube-system -f /tmp/lbc-values.yaml --wait --timeout "$(cap 240)s" \
  || fail "helm install of the controller"
kubectl -n kube-system rollout status deploy/aws-load-balancer-controller --timeout="$(cap 60)s" || fail "controller rollout"
kubectl -n kube-system get deploy aws-load-balancer-controller
kubectl get ingressclass

echo "Pod Identity credentials injected into the controller pod (variable names only):"
kubectl -n kube-system exec deploy/aws-load-balancer-controller -- env 2>/dev/null \
  | grep -E '^AWS_CONTAINER_(CREDENTIALS_FULL_URI|AUTHORIZATION_TOKEN_FILE)=' | cut -d= -f1

# ------------------------------------------------------------------ 2. splunk
# Installed without --wait so the collector starts shipping data while the ALB is built.
# Recent Helm versions validate the chart's values.schema.json against the JSON Schema
# metaschema, and splunk-otel-collector 0.116.0 ships a schema with
# "deprecated": "<string>" (must be boolean), so Helm refuses to install it.
# --skip-schema-validation (Helm >= 3.16) skips that check; the pinned chart is unchanged.
SCHEMA_FLAG=""
if helm upgrade --help 2>/dev/null | grep -q -- '--skip-schema-validation'; then
  SCHEMA_FLAG="--skip-schema-validation"
fi
echo
echo "== 2. Install Splunk OpenTelemetry Collector ${SPLUNK_CHART_VERSION} (cluster ${LAB_NAME}) ${SCHEMA_FLAG}"
helm upgrade --install splunk-otel-collector splunk-otel-collector-chart/splunk-otel-collector \
  --version "$SPLUNK_CHART_VERSION" -n splunk --create-namespace \
  ${SCHEMA_FLAG} \
  --set splunkObservability.realm="$SPLUNK_REALM" \
  --set-string splunkObservability.accessToken="$SPLUNK_ACCESS_TOKEN" \
  --set clusterName="$LAB_NAME" \
  --set cloudProvider=aws \
  --set distribution=eks \
  --set autodetect.prometheus=true \
  --timeout 2m \
  >/dev/null || fail "helm install of the Splunk collector"
kubectl -n splunk get pods

# ------------------------------------------------------------------ 3. ingress -> ALB
echo
echo "== 3. Apply the Ingress and wait for the ALB"
kubectl apply -f k8s/ingress.yaml
kubectl -n demo rollout status deploy/web --timeout="$(cap 60)s" || fail "web rollout"

HOST=""
end=$((SECONDS + $(cap 240)))
while [ "$SECONDS" -lt "$end" ]; do
  HOST="$(kubectl -n demo get ingress web -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)"
  [ -n "$HOST" ] && break
  sleep 10
done
[ -n "$HOST" ] || { kubectl -n demo describe ingress web | tail -15; fail "Ingress never got an ALB hostname"; }
kubectl -n demo get ingress web

LB_ARN=""; LB_STATE=""
end=$((SECONDS + $(cap 120)))
while [ "$SECONDS" -lt "$end" ]; do
  read -r LB_ARN LB_STATE < <(aws elbv2 describe-load-balancers \
    --query "LoadBalancers[?DNSName=='${HOST}'] | [0].[LoadBalancerArn,State.Code]" --output text 2>/dev/null)
  [ "$LB_STATE" = "active" ] && break
  sleep 10
done
[ "$LB_STATE" = "active" ] || fail "ALB not active (state: ${LB_STATE:-unknown})"
aws elbv2 describe-load-balancers --load-balancer-arns "$LB_ARN" \
  --query 'LoadBalancers[0].[LoadBalancerName,Scheme,Type,State.Code]' --output text

# ------------------------------------------------------------------ 4. target health
echo
echo "== 4. ALB target health (target-type ip, one target per pod)"
TG_ARN="$(aws elbv2 describe-target-groups --load-balancer-arn "$LB_ARN" --query 'TargetGroups[0].TargetGroupArn' --output text)"
WANT="$(kubectl -n demo get deploy web -o jsonpath='{.spec.replicas}')"
HEALTHY=0
end=$((SECONDS + $(cap 150)))
while [ "$SECONDS" -lt "$end" ]; do
  HEALTHY="$(aws elbv2 describe-target-health --target-group-arn "$TG_ARN" \
    --query "length(TargetHealthDescriptions[?TargetHealth.State=='healthy'])" --output text 2>/dev/null)"
  [ "${HEALTHY:-0}" -ge "$WANT" ] 2>/dev/null && break
  sleep 10
done
aws elbv2 describe-target-health --target-group-arn "$TG_ARN" \
  --query 'TargetHealthDescriptions[].[Target.Id,Target.Port,TargetHealth.State]' --output text
echo "healthy targets: ${HEALTHY}/${WANT}"
kubectl -n demo get targetgroupbinding

# ------------------------------------------------------------------ 5. curl
echo
echo "== 5. curl through the load balancer: http://${HOST}/"
CODE=000
end=$((SECONDS + $(cap 90)))
while [ "$SECONDS" -lt "$end" ]; do
  CODE="$(curl -sS -o /tmp/alb-body --max-time 10 -w '%{http_code}' "http://${HOST}/" 2>/dev/null || true)"
  [ "$CODE" = "200" ] && break
  sleep 10
done
echo "HTTP status: ${CODE}"
for i in 1 2 3 4 5 6; do curl -s --max-time 10 "http://${HOST}/"; done | sort | uniq -c

# ------------------------------------------------------------------ 6. controller logs
echo
echo "== 6. Controller log lines showing reconciliation"
kubectl -n kube-system logs deploy/aws-load-balancer-controller --tail=500 \
  | grep -E 'creating (loadBalancer|targetGroup)|created (loadBalancer|targetGroup)|registering targets|successfully deployed model|Reconciler error' \
  | cut -c1-230 | tail -10
RECONCILED="$(kubectl -n kube-system logs deploy/aws-load-balancer-controller --tail=1000 | grep -c 'successfully deployed model')"
ERRORS="$(kubectl -n kube-system logs deploy/aws-load-balancer-controller --tail=1000 | grep -c 'Reconciler error')"
echo "successful reconciliations: ${RECONCILED}, reconciler errors: ${ERRORS}"

# ------------------------------------------------------------------ 7. splunk
echo
echo "== 7. Splunk Observability Cloud (realm ${SPLUNK_REALM}), retrying until data arrives (max 8 min, bounded by the script deadline)"
Q_CLUSTER="k8s.cluster.name:${LAB_NAME}"
Q_CTRL="k8s.cluster.name:${LAB_NAME} AND k8s.pod.name:aws-load-balancer-controller*"
Q_APP="k8s.cluster.name:${LAB_NAME} AND k8s.namespace.name:demo AND k8s.pod.name:web-*"
N_CLUSTER=0; N_CTRL=0; N_APP=0
end=$((SECONDS + $(cap 480)))
i=0
while true; do
  i=$((i + 1))
  N_CLUSTER="$(mts_count "$Q_CLUSTER")"
  N_CTRL="$(mts_count "$Q_CTRL")"
  N_APP="$(mts_count "$Q_APP")"
  echo "attempt ${i}: cluster=${N_CLUSTER} controller-pods=${N_CTRL} app-pods=${N_APP}"
  [ "${N_CLUSTER:-0}" -gt 0 ] && [ "${N_CTRL:-0}" -gt 0 ] && [ "${N_APP:-0}" -gt 0 ] && break
  [ "$SECONDS" -ge "$end" ] && break
  sleep 20
done
echo "-- metric names for the controller pod:"
mts_json "$Q_CTRL" | jq -r '[.results[]?.sf_metric] | unique | .[0:10] | join(", ")'
echo "-- metric names for the web pods:"
mts_json "$Q_APP" | jq -r '[.results[]?.sf_metric] | unique | .[0:10] | join(", ")'
echo "-- controller Prometheus metrics (best effort):"
echo "controller_runtime_reconcile_total series: $(mts_count "sf_metric:controller_runtime_reconcile_total AND k8s.cluster.name:${LAB_NAME}")"

# ------------------------------------------------------------------ verdict
echo
if [ "$CODE" = "200" ] && [ "${HEALTHY:-0}" -ge "$WANT" ] && [ "${RECONCILED:-0}" -gt 0 ] \
   && [ "${N_CLUSTER:-0}" -gt 0 ] && [ "${N_CTRL:-0}" -gt 0 ] && [ "${N_APP:-0}" -gt 0 ]; then
  echo "Proof: curl 200 via ${HOST}, ${HEALTHY}/${WANT} targets healthy, controller reconciled, Splunk has cluster/controller/app series."
  echo "HERO_RESULT: fixed"
else
  echo "HERO_RESULT: not_fixed"
fi
