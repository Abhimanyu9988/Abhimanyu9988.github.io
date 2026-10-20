#!/usr/bin/env bash
# BUILD: OpenTelemetry Operator + Collector + Instrumentation, then ONE annotation on the Deployment.
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
  echo "HERO_RESULT: not_fixed"
  exit 0
}

CHART_VERSION="${OTEL_OPERATOR_CHART_VERSION:-0.71.2}"

timeout 60 aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "${AWS_REGION:-eu-central-1}" >/dev/null || fail "could not write kubeconfig"
K="kubectl --request-timeout=30s"

retry_apply() {
  # webhooks may need a few seconds after the operator is Ready
  local f="$1"
  for i in $(seq 1 10); do
    $K apply -f "$f" && return 0
    [ "$(left)" -lt 120 ] && return 1
    echo "apply of $f failed (attempt $i), retrying in 10s..."
    sleep 10
  done
  return 1
}

echo "== Image and pod template BEFORE"
$K -n demo get deploy orders-java -o jsonpath='image={.spec.template.spec.containers[0].image}{"\n"}annotations={.spec.template.metadata.annotations}{"\n"}' || fail "demo app is missing (run reproduce.sh first)"

echo
echo "== 1/4 Install the OpenTelemetry Operator (helm chart ${CHART_VERSION}, self-generated webhook cert, no cert-manager)"
timeout 60 helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts >/dev/null 2>&1
timeout 90 helm repo update >/dev/null 2>&1
HELM_T=$(cap 270)
timeout $((HELM_T + 30)) helm upgrade --install opentelemetry-operator open-telemetry/opentelemetry-operator \
  --version "$CHART_VERSION" \
  -n opentelemetry-operator-system --create-namespace \
  --set manager.collectorImage.repository=otel/opentelemetry-collector-k8s \
  --set admissionWebhooks.certManager.enabled=false \
  --set admissionWebhooks.autoGenerateCert.enabled=true \
  --set crds.create=true \
  --wait --timeout "${HELM_T}s" || fail "pinned operator chart ${CHART_VERSION} failed to install"
$K -n opentelemetry-operator-system rollout status deploy --timeout="$(cap 120)s" || fail "operator not ready"
$K get pods -n opentelemetry-operator-system
helm list -n opentelemetry-operator-system

echo
echo "== 2/4 Collector that prints spans to its logs (debug exporter)"
retry_apply k8s/collector.yaml || fail "could not apply collector"
for i in $(seq 1 24); do
  $K -n observability get deploy otel-collector >/dev/null 2>&1 && break
  [ "$(left)" -lt 300 ] && break
  sleep 5
done
$K -n observability rollout status deploy/otel-collector --timeout="$(cap 150)s" || fail "collector not ready"
$K -n observability get pods,svc

echo
echo "== 3/4 Instrumentation resource for Java"
retry_apply k8s/instrumentation.yaml || fail "could not apply Instrumentation"
$K -n demo get instrumentation java-auto

echo
echo "== 4/4 The one change: annotate the Deployment (no source change, same image)"
T0=$(date +%s)
$K -n demo patch deployment orders-java --type merge \
  -p '{"spec":{"template":{"metadata":{"annotations":{"instrumentation.opentelemetry.io/inject-java":"true"}}}}}' || fail "patch failed"
$K -n demo rollout status deployment/orders-java --timeout="$(cap 180)s" || fail "instrumented rollout did not finish"
T1=$(date +%s)
echo "rollout finished in $((T1-T0))s"
$K -n demo get deploy orders-java -o jsonpath='image={.spec.template.spec.containers[0].image}{"\n"}annotations={.spec.template.metadata.annotations}{"\n"}'

POD=$($K -n demo get pods -l app=orders-java --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].metadata.name}')
echo
echo "== Injected pod: $POD"
echo "-- init containers:"
INITS=$($K -n demo get pod "$POD" -o jsonpath='{.spec.initContainers[*].name}')
echo "$INITS"
echo "-- agent image:"
$K -n demo get pod "$POD" -o jsonpath='{.spec.initContainers[0].image}{"\n"}'
echo "-- injected env:"
$K -n demo get pod "$POD" -o jsonpath='{range .spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}' | grep -E 'OTEL|JAVA_TOOL_OPTIONS' || true
echo "-- app log head:"
$K -n demo logs "$POD" -c app 2>&1 | grep -iE 'javaagent|JAVA_TOOL_OPTIONS|listening' | head -5

echo
echo "== Waiting for spans to arrive in the Collector debug output"
FOUND=0
for i in $(seq 1 30); do
  $K -n observability logs deploy/otel-collector --tail=-1 > /tmp/collector.log 2>/dev/null
  if grep -qF 'service.name: Str(orders-java)' /tmp/collector.log && grep -q 'Span #' /tmp/collector.log; then
    FOUND=1
    break
  fi
  [ "$(left)" -lt 20 ] && break
  sleep 5
done

echo
echo "== Collector log excerpt (resource attributes and spans)"
grep -E 'ResourceSpans|service.name|k8s\.(namespace|deployment|pod)\.name|telemetry.sdk|InstrumentationScope|Span #|Trace ID|Parent ID|Name +:|Kind +:|http\.(request\.method|route|response\.status_code)|url\.path' /tmp/collector.log | tail -45

NSPANS=$(grep -c 'Span #' /tmp/collector.log || true)
echo
echo "== Summary"
echo "spans received so far: $NSPANS"
echo "span names / counts:"
grep -E '^ +Name +:' /tmp/collector.log | sed -E 's/^ +//' | sort | uniq -c | sort -rn | head
echo "service.name values seen:"
grep -E 'service.name:' /tmp/collector.log | sed -E 's/^ +//' | sort | uniq -c

if [ "$FOUND" = "1" ] && echo "$INITS" | grep -q 'opentelemetry-auto-instrumentation'; then
  echo "HERO_RESULT: fixed"
else
  echo "HERO_RESULT: not_fixed"
fi
exit 0
