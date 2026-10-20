#!/usr/bin/env bash
# Nothing here creates AWS resources outside Terraform (no LoadBalancers/PVCs), but remove the
# Kubernetes objects anyway so destroy is clean. Never fails hard.
set +e

CLUSTER=$(terraform output -raw cluster_name 2>/dev/null)
[ -z "$CLUSTER" ] && exit 0
timeout 60 aws eks update-kubeconfig --name "$CLUSTER" --region "${AWS_REGION:-eu-central-1}" >/dev/null 2>&1 || exit 0

timeout 60 kubectl --request-timeout=30s get ns >/dev/null 2>&1 || exit 0

timeout 120 helm uninstall opentelemetry-operator -n opentelemetry-operator-system >/dev/null 2>&1
timeout 120 kubectl delete namespace demo observability opentelemetry-operator-system --wait=true --timeout=100s >/dev/null 2>&1
exit 0
