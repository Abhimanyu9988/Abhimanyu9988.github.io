#!/usr/bin/env bash
# Stop tasks and deregister task definition revisions created by the scripts. Never fails hard.
export AWS_PAGER=""
export AWS_REGION="${AWS_REGION:-eu-central-1}"

CLUSTER=$(terraform output -raw cluster_name 2>/dev/null) || { echo "no cluster output, nothing to clean"; exit 0; }
SERVICE=$(terraform output -raw service_name 2>/dev/null) || SERVICE=""
FAMILY=$(terraform output -raw task_family 2>/dev/null) || FAMILY=""

if [ -n "$SERVICE" ]; then
  aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --desired-count 0 >/dev/null 2>&1 || true
fi
for t in $(aws ecs list-tasks --cluster "$CLUSTER" --query 'taskArns[]' --output text 2>/dev/null); do
  aws ecs stop-task --cluster "$CLUSTER" --task "$t" >/dev/null 2>&1 || true
done

end=$((SECONDS+180))
while [ $SECONDS -lt $end ]; do
  n=$(aws ecs list-tasks --cluster "$CLUSTER" --query 'length(taskArns)' --output text 2>/dev/null || echo 0)
  [ "$n" = "0" ] && break
  sleep 10
done

if [ -n "$FAMILY" ]; then
  for td in $(aws ecs list-task-definitions --family-prefix "$FAMILY" --status ACTIVE --query 'taskDefinitionArns[]' --output text 2>/dev/null); do
    aws ecs deregister-task-definition --task-definition "$td" >/dev/null 2>&1 || true
  done
fi
echo "cleanup done"
exit 0
