#!/usr/bin/env bash
# Never fail hard. Nothing here creates AWS resources outside Terraform.
set +e
cd "$(dirname "$0")"
name=$(terraform output -raw cluster_name 2>/dev/null)
if [ -n "$name" ]; then
  aws eks update-kubeconfig --name "$name" --region "$AWS_REGION" >/dev/null 2>&1
  if kubectl --request-timeout=15s get nodes >/dev/null 2>&1; then
    timeout 180 helm uninstall splunk-otel-collector -n splunk --timeout 120s >/dev/null 2>&1
    timeout 120 kubectl delete namespace demo splunk --wait=false >/dev/null 2>&1
    echo "cleanup: helm release uninstalled, namespaces deleting"
  else
    echo "cleanup: cluster not reachable, nothing to remove"
  fi
else
  echo "cleanup: no cluster output, nothing to remove"
fi
exit 0
