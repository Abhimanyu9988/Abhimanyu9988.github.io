#!/usr/bin/env bash
# Fix: wrap RunningTaskCount in FILL(running, 0) so the expression falls back to raw backlog at 0 tasks.
set -uo pipefail
export AWS_DEFAULT_REGION="${AWS_REGION:-eu-central-1}"

CLUSTER=$(terraform output -raw cluster_name)
SERVICE=$(terraform output -raw service_name)
QURL=$(terraform output -raw queue_url)
QNAME=$(terraform output -raw queue_name)
RID=$(terraform output -raw resource_id)
POLICY=$(terraform output -raw policy_name)

START_TS=$(date +%s)
desired() { aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].desiredCount' --output text; }
running() { aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].runningCount' --output text; }
attr() { aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names "$1" --query "Attributes.$1" --output text; }

echo "== Before: desired=$(desired) running=$(running) visible=$(attr ApproximateNumberOfMessages)"

EXPR='IF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog)'
cat > /tmp/policy-fixed.json <<EOF
{
 "TargetValue": 2,
 "ScaleOutCooldown": 60,
 "ScaleInCooldown": 300,
 "CustomizedMetricSpecification": {
  "Metrics": [
   {"Id":"backlog","ReturnData":false,"MetricStat":{"Metric":{"Namespace":"AWS/SQS","MetricName":"ApproximateNumberOfMessagesVisible","Dimensions":[{"Name":"QueueName","Value":"$QNAME"}]},"Stat":"Sum"}},
   {"Id":"running","ReturnData":false,"MetricStat":{"Metric":{"Namespace":"ECS/ContainerInsights","MetricName":"RunningTaskCount","Dimensions":[{"Name":"ClusterName","Value":"$CLUSTER"},{"Name":"ServiceName","Value":"$SERVICE"}]},"Stat":"Average"}},
   {"Id":"bpt","Label":"Backlog per task","ReturnData":true,"Expression":"$EXPR"}
  ]
 }
}
EOF

echo "== Updating policy with: $EXPR"
if ! aws application-autoscaling put-scaling-policy \
  --policy-name "$POLICY" --policy-type TargetTrackingScaling \
  --service-namespace ecs --resource-id "$RID" --scalable-dimension ecs:service:DesiredCount \
  --target-tracking-scaling-policy-configuration file:///tmp/policy-fixed.json \
  --query 'PolicyARN' --output text; then
  echo "put-scaling-policy failed"
  echo "HERO_RESULT: not_fixed"
  exit 0
fi

echo "== Waiting up to 10 minutes for the service to scale out from 0 and a task to reach RUNNING"
OK=0
for i in $(seq 1 20); do
  sleep 30
  D=$(desired); R=$(running)
  echo "t+$((i*30))s desired=$D running=$R visible=$(attr ApproximateNumberOfMessages) in_flight=$(attr ApproximateNumberOfMessagesNotVisible)"
  if [ "$D" != "0" ] && [ "$D" != "None" ] && [ "$R" != "0" ] && [ "$R" != "None" ]; then OK=1; break; fi
done

echo
echo "== Scaling activities"
aws application-autoscaling describe-scaling-activities --service-namespace ecs --resource-id "$RID" \
  --query 'ScalingActivities[].[StatusCode,Description]' --output text | head -5

if [ "$OK" = "1" ]; then
  echo "== Watching queue drain (up to 2.5 min, bounded by script time budget)"
  for i in $(seq 1 5); do
    [ $(( $(date +%s) - START_TS )) -gt 780 ] && break
    V=$(attr ApproximateNumberOfMessages); N=$(attr ApproximateNumberOfMessagesNotVisible)
    echo "visible=$V in_flight=$N desired=$(desired) running=$(running)"
    [ "$V" = "0" ] && [ "$N" = "0" ] && break
    sleep 30
  done
  echo "HERO_RESULT: fixed"
else
  echo "Service still at desired=$(desired) running=$(running)"
  echo "HERO_RESULT: not_fixed"
fi
exit 0
