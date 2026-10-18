#!/usr/bin/env bash
# Best-effort: stop the autoscaler re-launching tasks, stop tasks, empty the queue. Never fails hard.
set +e
export AWS_DEFAULT_REGION="${AWS_REGION:-eu-central-1}"

CLUSTER=$(terraform output -raw cluster_name 2>/dev/null)
SERVICE=$(terraform output -raw service_name 2>/dev/null)
QURL=$(terraform output -raw queue_url 2>/dev/null)
RID=$(terraform output -raw resource_id 2>/dev/null)
POLICY=$(terraform output -raw policy_name 2>/dev/null)

if [ -n "$RID" ] && [ -n "$POLICY" ]; then
  aws application-autoscaling delete-scaling-policy --service-namespace ecs --resource-id "$RID" \
    --scalable-dimension ecs:service:DesiredCount --policy-name "$POLICY" >/dev/null 2>&1
fi

if [ -n "$CLUSTER" ] && [ -n "$SERVICE" ]; then
  aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --desired-count 0 >/dev/null 2>&1
  for t in $(aws ecs list-tasks --cluster "$CLUSTER" --query 'taskArns[]' --output text 2>/dev/null); do
    aws ecs stop-task --cluster "$CLUSTER" --task "$t" >/dev/null 2>&1
  done
  for i in $(seq 1 18); do
    N=$(aws ecs list-tasks --cluster "$CLUSTER" --query 'length(taskArns)' --output text 2>/dev/null)
    [ -z "$N" ] || [ "$N" = "0" ] && break
    sleep 10
  done
fi

[ -n "$QURL" ] && aws sqs purge-queue --queue-url "$QURL" >/dev/null 2>&1
exit 0
