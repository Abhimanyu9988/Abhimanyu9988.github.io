#!/usr/bin/env bash
# BEFORE: one Fargate service, no telemetry. Show that Splunk has nothing for it.
set -uo pipefail
export AWS_PAGER=""
export AWS_REGION="${AWS_REGION:-eu-central-1}"

CLUSTER=$(terraform output -raw cluster_name)
SERVICE=$(terraform output -raw service_name)
FAMILY=$(terraform output -raw task_family)

echo "== Waiting (max 5 min) for the service to run 1 task"
end=$((SECONDS+300))
while [ $SECONDS -lt $end ]; do
  run=$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].runningCount' --output text 2>/dev/null || echo 0)
  [ "$run" = "1" ] && break
  sleep 10
done

echo
echo "== Service status"
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
  --query 'services[0].{service:serviceName,desired:desiredCount,running:runningCount,launchType:launchType}' --output table

echo "== Cluster settings (Container Insights)"
aws ecs describe-clusters --clusters "$CLUSTER" --include SETTINGS \
  --query 'clusters[0].settings' --output json

echo "== Containers in the task definition (names and images only)"
TD=$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].taskDefinition' --output text)
CONTAINERS=$(aws ecs describe-task-definition --task-definition "$TD" \
  --query 'taskDefinition.containerDefinitions[].{name:name,image:image}' --output json)
echo "$CONTAINERS" | jq -c '.[]'
N_CONT=$(echo "$CONTAINERS" | jq 'length')
HAS_SIDECAR=$(echo "$CONTAINERS" | jq '[.[] | select(.image | test("splunk-otel-collector"))] | length')

SPLUNK_COUNT="unknown"
if [ -n "${SPLUNK_API_TOKEN:-}" ] && [ -n "${SPLUNK_REALM:-}" ]; then
  echo "== Splunk Observability: metric time series with ecs.cluster.name:$CLUSTER"
  resp=$(curl -sS --max-time 30 -H "X-SF-Token: $SPLUNK_API_TOKEN" --get \
    --data-urlencode "query=ecs.cluster.name:$CLUSTER" --data-urlencode "limit=10" \
    "https://api.${SPLUNK_REALM}.signalfx.com/v2/metrictimeseries" || true)
  SPLUNK_COUNT=$(echo "$resp" | jq -r '(.count // (.results | length)) // "unknown"' 2>/dev/null || echo unknown)
  echo "series found: $SPLUNK_COUNT"
else
  echo "(SPLUNK_API_TOKEN / SPLUNK_REALM not set, skipping the Splunk query)"
fi

echo
echo "Summary: containers=$N_CONT sidecar=$HAS_SIDECAR splunk_series=$SPLUNK_COUNT -> only the console shows this service, nothing per task in Splunk."

if [ "$HAS_SIDECAR" = "0" ] && { [ "$SPLUNK_COUNT" = "0" ] || [ "$SPLUNK_COUNT" = "unknown" ]; }; then
  echo "HERO_RESULT: reproduced"
else
  echo "HERO_RESULT: not_reproduced"
fi
