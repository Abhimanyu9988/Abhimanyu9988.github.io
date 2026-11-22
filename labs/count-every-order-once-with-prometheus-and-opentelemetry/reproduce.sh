#!/usr/bin/env bash
# BEFORE: the Splunk OTel Collector agent (a DaemonSet, one per node) gets the Prometheus scrape job many teams add first
# (values-everywhere.yaml): pod discovery for app=orders, no node filter. Two orders pods, one per node, count every order
# in a Prometheus counter and, side by side, push OpenTelemetry's own request metric over OTLP. Sets up the cluster, runs
# everywhere-1 and everywhere-2, and reads everywhere-1 back from Splunk. fix.sh runs the two node-scoped configs.
set -uo pipefail
cd "$(dirname "$0")"
. ./lib.sh
. ./splunk.sh

start_clock 820 not_reproduced

aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null || fail "could not write kubeconfig"
for v in SPLUNK_REALM SPLUNK_ACCESS_TOKEN SPLUNK_API_TOKEN; do [ -n "${!v:-}" ] || fail "$v is not set"; done

echo "== 1. Waiting for 2 worker nodes"
ready=0
t0=$(date +%s)
while [ $(( $(date +%s) - t0 )) -lt 300 ]; do
  ready=$(kubectl --request-timeout=15s get nodes --no-headers 2>/dev/null | grep -c ' Ready' || true)
  [ "${ready:-0}" -ge 2 ] && break
  sleep 10
done
[ "${ready:-0}" -ge 2 ] || fail "nodes did not become Ready"
kubectl -n kube-system rollout status deploy/coredns --timeout="$(cap 90 600)s" >/dev/null || true
NODE_A=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
NODE_B=$(kubectl get nodes -o jsonpath='{.items[1].metadata.name}')
echo "$NODE_A" > node-a.txt; echo "$NODE_B" > node-b.txt
echo "node A: $NODE_A; node B: $NODE_B"

echo "== 2. Splunk OTel Collector chart 0.163.1 with values-everywhere.yaml"
for i in 1 2 3; do
  helm repo add splunk-otel-collector-chart https://signalfx.github.io/splunk-otel-collector-chart >/dev/null 2>&1 && helm repo update >/dev/null 2>&1 && break
  sleep 10
done
set_config everywhere "$(cap 180 560)"

echo "== 3. One virtualenv with pinned packages on each node"
sed -e "s/NODE_A/$NODE_A/" -e "s/NODE_B/$NODE_B/" k8s/00-build.yaml | kubectl apply -f - >/dev/null || fail "could not start the builds"
t0=$(date +%s); limit=$(cap 360 420)
done_jobs=0
while [ $(( $(date +%s) - t0 )) -lt "$limit" ]; do
  done_jobs=0
  for j in build-a build-b; do
    case "$(kubectl -n demo get job "$j" -o jsonpath='{.status.conditions[?(@.status=="True")].type}' 2>/dev/null)" in
      *Complete*) done_jobs=$(( done_jobs + 1 )) ;;
      *Failed*) kubectl -n demo logs "job/$j" --tail=10; fail "$j failed" ;;
    esac
  done
  [ "$done_jobs" = 2 ] && break
  sleep 10
done
[ "$done_jobs" = 2 ] || fail "the builds did not finish"
kubectl -n demo logs job/build-a --tail=8 2>/dev/null | grep -v '^\[notice\]' | cut -c1-200
echo "builds took $(( $(date +%s) - t0 ))s"

echo "== 4. orders-a on node A, orders-b on node B, one Service"
sed -e "s/NODE_A/$NODE_A/" -e "s/NODE_B/$NODE_B/" -e "s/LAB_ENV/$LAB_NAME/g" k8s/10-app.yaml | kubectl apply -f - >/dev/null || fail "could not deploy orders"
kubectl -n demo rollout status deploy/orders-a --timeout="$(cap 120 380)s" >/dev/null || { why; fail "orders-a did not start"; }
kubectl -n demo rollout status deploy/orders-b --timeout="$(cap 120 370)s" >/dev/null || { why; fail "orders-b did not start"; }
kubectl -n demo get pods -l app=orders -o wide --no-headers | awk '{print "  " $1, $6, $7}'

for run in everywhere-1 everywhere-2; do
  echo "== Run $run"
  start_run "$run" 120
  load_run "$run" "$(cap 90 200)"
  measures "$run"
done

echo "== 5. everywhere-1 in Splunk"
splunk_run_patiently everywhere-1 "$(cap 200 15)"
want=$(cat truth-everywhere-1.txt)
# The before-state is the cause: every agent scraped every pod (4 scraper-and-pod pairs, from the pods' own records), with
# OpenTelemetry's count in Splunk as the fair reference. What Splunk's Prometheus counter shows is measured, not gated:
# fix.sh reads every run again and records it.
[ "${otel_total:-0}" = "$want" ] || fail "OpenTelemetry's count in Splunk ($otel_total) doesn't match the pods ($want), so there is no fair reference"
for run in everywhere-1 everywhere-2; do
  pairs=0
  while read -r pod orders rest; do for s in $rest; do [ "${s##*:}" -ge 2 ] && pairs=$(( pairs + 1 )); done; done < "pods-$run.txt"
  [ "$pairs" = 4 ] || fail "expected both agents to scrape both pods in $run, found $pairs scraper-pod pairs"
done

emit reproduced
