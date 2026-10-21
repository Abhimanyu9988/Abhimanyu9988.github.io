#!/usr/bin/env bash
# BEFORE: a Java service on EKS with no tracing at all.
# Overall deadline: 14 minutes (the lab limit is 15). Every wait is capped to the time left.
set -uo pipefail

DEADLINE=$(( $(date +%s) + 840 ))
left() { echo $(( DEADLINE - $(date +%s) )); }
cap() { # cap <wanted-seconds> -> min(wanted, time left), at least 1
  local w="$1" l
  l=$(left)
  [ "$l" -lt "$w" ] && w=$l
  [ "$w" -lt 1 ] && w=1
  echo "$w"
}
fail() {
  echo "ERROR: $1"
  echo "HERO_RESULT: not_reproduced"
  exit 0
}

timeout 60 aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "${AWS_REGION:-eu-central-1}" >/dev/null || fail "could not write kubeconfig"
export KUBECTL_REQUEST_TIMEOUT=30s
K="kubectl --request-timeout=30s"

echo "== Waiting for the two nodeadm-joined worker nodes"
UP=0
for i in $(seq 1 30); do
  n=$($K get nodes --no-headers 2>/dev/null | wc -l)
  if [ "$n" -ge 2 ]; then UP=1; break; fi
  [ "$(left)" -lt 400 ] && break
  sleep 10
done
[ "$UP" = "1" ] || fail "nodes did not register"
$K wait --for=condition=Ready node --all --timeout="$(cap 150)s" || fail "nodes not Ready"
$K get nodes -o wide
$K -n kube-system rollout status deployment/coredns --timeout="$(cap 120)s" || fail "coredns not ready"

echo
echo "== Deploying the sample Java service (plain eclipse-temurin:17.0.13_11-jdk image, no agent, no OTel config)"
$K apply -f k8s/app.yaml || fail "could not apply app manifests"
$K -n demo rollout status deployment/orders-java --timeout="$(cap 180)s" || fail "orders-java did not roll out"
$K -n demo rollout status deployment/loadgen --timeout="$(cap 60)s" || fail "loadgen did not roll out"
sleep 15

POD=$($K -n demo get pods -l app=orders-java -o jsonpath='{.items[0].metadata.name}')
[ -n "$POD" ] || fail "no orders-java pod"

echo
echo "== Service is serving traffic (app log + load generator log)"
$K -n demo logs "$POD" --tail=5
$K -n demo logs deploy/loadgen --tail=3

echo
echo "== Evidence of the gap"
echo "-- Tracing-related env vars in the app container (JAVA_TOOL_OPTIONS / OTEL_*):"
ENVS=$($K -n demo get pod "$POD" -o jsonpath='{range .spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}' | grep -E 'OTEL|JAVA_TOOL_OPTIONS' || true)
[ -z "$ENVS" ] && echo "(none)" || echo "$ENVS"
echo "-- Init containers on the pod:"
INITS=$($K -n demo get pod "$POD" -o jsonpath='{.spec.initContainers[*].name}')
[ -z "$INITS" ] && echo "(none)" || echo "$INITS"
echo "-- Java agent mentions in app logs:"
AG=$($K -n demo logs "$POD" | grep -ci 'javaagent' || true)
echo "count=$AG"
echo "-- OpenTelemetry CRDs / collector in the cluster:"
CRDS=$($K get crd 2>/dev/null | grep -c 'opentelemetry.io' || true)
echo "opentelemetry CRDs installed: $CRDS"
$K get ns observability 2>&1 | head -1
echo "=> requests are served, but no spans exist anywhere and nothing could receive them."

if [ -z "$ENVS" ] && [ "$AG" = "0" ] && [ "$CRDS" = "0" ]; then
  echo "HERO_RESULT: reproduced"
else
  echo "HERO_RESULT: not_reproduced"
fi
exit 0
