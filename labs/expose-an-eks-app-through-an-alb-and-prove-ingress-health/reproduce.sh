#!/usr/bin/env bash
# BEFORE: sample app + Ingress on a cluster with no load balancer controller.
# Worst case runtime: ~300s nodes + ~240s rollout + 30s sleep + short queries (< 15 min).
set -uo pipefail
cd "$(dirname "$0")"

CLUSTER_NAME="$(terraform output -raw cluster_name)"
aws eks update-kubeconfig --name "$CLUSTER_NAME" >/dev/null

mts_count() {
  curl -sS --max-time 15 -G "https://api.${SPLUNK_REALM}.signalfx.com/v2/metrictimeseries" \
    -H "X-SF-Token: ${SPLUNK_API_TOKEN}" \
    --data-urlencode "query=$1" --data-urlencode "limit=50" 2>/dev/null \
    | jq -r 'if .count then .count else (.results|length) end' 2>/dev/null || echo 0
}

echo "== Waiting for the two worker nodes to be Ready (max 300s)"
kubectl wait --for=condition=Ready node --all --timeout=300s || true
kubectl get nodes -o wide

echo
echo "== Deploying the sample web app and an Ingress (ingressClassName: alb)"
kubectl apply -f k8s/app.yaml
kubectl apply -f k8s/ingress.yaml
kubectl -n demo rollout status deploy/web --timeout=240s
kubectl -n demo get pods -o wide

echo
echo "== Giving the Ingress 30s to get a load balancer"
sleep 30

echo "== Ingress status (ADDRESS should be empty: nothing reconciles it)"
kubectl -n demo get ingress web
HOST="$(kubectl -n demo get ingress web -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)"
echo "Ingress hostname: '${HOST}'"

echo
echo "== Ingress events"
kubectl -n demo describe ingress web | sed -n '/^Events:/,$p'

echo
echo "== IngressClass objects and controller pods"
kubectl get ingressclass 2>&1 | head -5
CTRL="$(kubectl -n kube-system get deploy -o name 2>/dev/null | grep -c aws-load-balancer-controller || true)"
echo "aws-load-balancer-controller deployments: ${CTRL}"

echo
echo "== ALBs in this account tagged for this cluster"
N_ALB=0
for arn in $(aws elbv2 describe-load-balancers --query 'LoadBalancers[].LoadBalancerArn' --output text 2>/dev/null); do
  c=$(aws elbv2 describe-tags --resource-arns "$arn" --query "length(TagDescriptions[?Tags[?Key=='elbv2.k8s.aws/cluster' && Value=='${CLUSTER_NAME}']])" --output text 2>/dev/null || echo 0)
  N_ALB=$((N_ALB + c))
done
echo "ALBs created for the cluster: ${N_ALB}"

echo
echo "== Splunk Observability Cloud: time series for k8s.cluster.name=${LAB_NAME}"
if [ -n "${SPLUNK_API_TOKEN:-}" ] && [ -n "${SPLUNK_REALM:-}" ]; then
  echo "cluster MTS count: $(mts_count "k8s.cluster.name:${LAB_NAME}")"
else
  echo "SPLUNK_REALM / SPLUNK_API_TOKEN not set, skipping Splunk check"
fi

echo
if [ -z "$HOST" ] && [ "${CTRL}" = "0" ] && [ "${N_ALB}" = "0" ]; then
  echo "Gap shown: the Ingress exists, but no controller, no ALB, no address and no telemetry."
  echo "HERO_RESULT: reproduced"
else
  echo "HERO_RESULT: not_reproduced"
fi
