#!/usr/bin/env bash
# Shows that backlog-per-task target tracking with min capacity 0 never leaves 0.
set -uo pipefail
export AWS_DEFAULT_REGION="${AWS_REGION:-eu-central-1}"

CLUSTER=$(terraform output -raw cluster_name)
SERVICE=$(terraform output -raw service_name)
QURL=$(terraform output -raw queue_url)
QNAME=$(terraform output -raw queue_name)
RID=$(terraform output -raw resource_id)

desired() { aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].desiredCount' --output text; }
running() { aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].runningCount' --output text; }
visible() { aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names ApproximateNumberOfMessages --query 'Attributes.ApproximateNumberOfMessages' --output text; }

echo "== Initial state: desired=$(desired) running=$(running)"
aws application-autoscaling describe-scalable-targets --service-namespace ecs --resource-ids "$RID" \
  --query 'ScalableTargets[].{min:MinCapacity,max:MaxCapacity}' --output json

echo "== Sending 10 messages"
for i in $(seq 1 10); do
  aws sqs send-message --queue-url "$QURL" --message-body "job-$i" >/dev/null
done

echo "== Waiting up to 8 minutes for the service to leave 0 tasks"
LEFT=0
for i in $(seq 1 16); do
  sleep 30
  D=$(desired)
  echo "t+$((i*30))s desired=$D running=$(running) visible_in_queue=$(visible)"
  if [ "$D" != "0" ] && [ "$D" != "None" ]; then LEFT=1; break; fi
done

echo
echo "== Policy alarms"
ALARMS=$(aws application-autoscaling describe-scaling-policies --service-namespace ecs --resource-id "$RID" \
  --query 'ScalingPolicies[0].Alarms[].AlarmName' --output text)
if [ -n "$ALARMS" ]; then
  # shellcheck disable=SC2086
  aws cloudwatch describe-alarms --alarm-names $ALARMS --query 'MetricAlarms[].[AlarmName,StateValue]' --output text
  # shellcheck disable=SC2086
  aws cloudwatch describe-alarms --alarm-names $ALARMS --query 'MetricAlarms[0].StateReason' --output text | cut -c1-300
fi

echo
echo "== Metric math as the policy evaluates it (last 15 min, 60s period)"
START=$(date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%SZ)
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat > /tmp/q-broken.json <<EOF
[
 {"Id":"backlog","ReturnData":true,"MetricStat":{"Metric":{"Namespace":"AWS/SQS","MetricName":"ApproximateNumberOfMessagesVisible","Dimensions":[{"Name":"QueueName","Value":"$QNAME"}]},"Period":60,"Stat":"Sum"}},
 {"Id":"running","ReturnData":true,"MetricStat":{"Metric":{"Namespace":"ECS/ContainerInsights","MetricName":"RunningTaskCount","Dimensions":[{"Name":"ClusterName","Value":"$CLUSTER"},{"Name":"ServiceName","Value":"$SERVICE"}]},"Period":60,"Stat":"Average"}},
 {"Id":"bpt","ReturnData":true,"Expression":"IF(running > 0, backlog / running, backlog)"}
]
EOF
METRICS=$(aws cloudwatch get-metric-data --start-time "$START" --end-time "$END" --metric-data-queries file:///tmp/q-broken.json)
echo "$METRICS" | jq -c '.MetricDataResults[] | {Id, datapoints: (.Values|length), Values}'
BPT_N=$(echo "$METRICS" | jq '[.MetricDataResults[] | select(.Id=="bpt") | (.Values|length)] | add // 0' 2>/dev/null)
BPT_N="${BPT_N:-unknown}"
echo "bpt datapoints: $BPT_N"

echo
echo "== Scaling activities"
aws application-autoscaling describe-scaling-activities --service-namespace ecs --resource-id "$RID" \
  --query 'ScalingActivities[].[StatusCode,Description]' --output text | head -5

FD=$(desired); FR=$(running); FV=$(visible)
echo
echo "Final: desired=$FD running=$FR messages_waiting=$FV bpt_datapoints=$BPT_N"
WAITING=0
if [ "$FV" -gt 0 ] 2>/dev/null; then WAITING=1; fi
if [ "$LEFT" = "0" ] && [ "$FD" = "0" ] && [ "$FR" = "0" ] && [ "$WAITING" = "1" ] && [ "$BPT_N" = "0" ]; then
  echo "Messages are waiting but the 'bpt' series has no datapoints (RunningTaskCount missing), so the alarm never fires and the service stays at 0."
  echo "HERO_RESULT: reproduced"
else
  echo "HERO_RESULT: not_reproduced"
fi
exit 0
