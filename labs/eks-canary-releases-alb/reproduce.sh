#!/usr/bin/env bash
# BEFORE: two versions of an app, no way to shift traffic gradually or roll back instantly.
set -uo pipefail
aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null

# One global budget (13 min) so the script always prints HERO_RESULT inside the 15 minute limit.
END=$((SECONDS+780))
cap() { # $1 = wanted seconds; returns min($1, time left), at least 1
  local r=$((END-SECONDS))
  [ "$r" -gt "$1" ] && r=$1
  [ "$r" -lt 1 ] && r=1
  echo "$r"
}

echo "== Waiting for worker nodes to join"
deadline=$((SECONDS+$(cap 420)))
while [ $SECONDS -lt $deadline ]; do
  n=$(kubectl --request-timeout=15s get nodes --no-headers 2>/dev/null | wc -l)
  [ "$n" -ge 2 ] && break
  sleep 10
done
kubectl wait --for=condition=Ready node --all --timeout="$(cap 180)s"
kubectl -n kube-system rollout status deploy/coredns --timeout="$(cap 180)s"
kubectl -n kube-system rollout status ds/eks-pod-identity-agent --timeout="$(cap 180)s"
kubectl --request-timeout=20s get nodes -o wide

echo
echo "== Deploying app v1 (3 pods) and app v2 (1 pod)"
kubectl --request-timeout=30s apply -f k8s/app.yaml
kubectl -n demo rollout status deploy/app-v1 --timeout="$(cap 180)s"
kubectl -n demo rollout status deploy/app-v2 --timeout="$(cap 180)s"
kubectl -n demo wait --for=condition=Ready pod/tester --timeout="$(cap 120)s"
kubectl --request-timeout=20s -n demo get deploy,svc,pods -o wide

echo
echo "== Before: how is traffic split between v1 and v2? (40 in-cluster requests to Service 'app')"
out=$(timeout 120 kubectl --request-timeout=100s -n demo exec tester -- sh -c 'for i in $(seq 1 40); do wget -qO- -T 2 http://app/; done' 2>&1)
echo "$out" | sort | uniq -c
v1=$(grep -c '^version-v1$' <<<"$out" || true)
v2=$(grep -c '^version-v2$' <<<"$out" || true)
echo "v1=$v1 v2=$v2  (ratio follows the pod count 3:1; to get 10% you would need 9 v1 pods per v2 pod)"

echo
echo "== Before: is there any weighted entry point or load balancer?"
ing=$(kubectl --request-timeout=20s get ingress -A --no-headers 2>&1)
echo "kubectl get ingress -A -> ${ing:-<none>}"
lbs=$(kubectl --request-timeout=20s get svc -A --no-headers 2>/dev/null | grep -c LoadBalancer || true)
echo "Services of type LoadBalancer: $lbs"
kubectl --request-timeout=20s -n kube-system get deploy aws-load-balancer-controller 2>&1 | head -n1
echo "Rolling back v2 today means scaling/redeploying pods; there is no weight to set to zero."

if [ "${v1:-0}" -gt 0 ] && [ "${v2:-0}" -gt 0 ] && echo "$ing" | grep -qi "no resources"; then
  echo "HERO_RESULT: reproduced"
else
  echo "HERO_RESULT: not_reproduced"
fi
