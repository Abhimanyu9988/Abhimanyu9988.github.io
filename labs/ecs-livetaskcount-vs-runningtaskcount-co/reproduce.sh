#!/usr/bin/env bash
# Scale an ECS service in while tasks drain from an ALB and compare
# LiveTaskCount with Container Insights RunningTaskCount.
set -uo pipefail

REGION="${AWS_REGION:-eu-central-1}"
CLUSTER="$(terraform output -raw cluster_name)"
SVC="$(terraform output -raw service_name)"
export AWS_DEFAULT_REGION="$REGION"

task_states() {
  # all tasks of the service that still exist (RUNNING or STOPPED desired status), grouped by lastStatus
  local arns
  arns=$( { aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SVC" --desired-status RUNNING --query 'taskArns' --output text;
            aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SVC" --desired-status STOPPED --query 'taskArns' --output text; } 2>/dev/null | tr '\t' '\n' | grep -v '^$' | grep -v '^None$' | head -100)
  if [ -z "$arns" ]; then echo "none"; return; fi
  # shellcheck disable=SC2086
  aws ecs describe-tasks --cluster "$CLUSTER" --tasks $arns --query 'tasks[].lastStatus' --output json 2>/dev/null \
    | jq -r 'map(select(. != "STOPPED")) | group_by(.) | map("\(.[0])=\(length)") | join(" ")'
}

echo "== Cluster=$CLUSTER Service=$SVC"
aws ecs wait services-stable --cluster "$CLUSTER" --services "$SVC" 2>/dev/null || true
echo "Initial: $(aws ecs describe-services --cluster "$CLUSTER" --services "$SVC" --query 'services[0].[desiredCount,runningCount]' --output text)  (desired running)"
echo "Waiting 90s so CloudWatch gets baseline datapoints with 4 tasks..."
sleep 90

echo "== Scale in: desiredCount 4 -> 1 (target group deregistration delay is 120s)"
aws ecs update-service --cluster "$CLUSTER" --service "$SVC" --desired-count 1 >/dev/null || { echo "update-service failed"; exit 1; }

maxdeact=0
for i in $(seq 1 20); do
  sleep 10
  states="$(task_states)"
  rc="$(aws ecs describe-services --cluster "$CLUSTER" --services "$SVC" --query 'services[0].[desiredCount,runningCount]' --output text 2>/dev/null)"
  d=$(echo "$states" | grep -o 'DEACTIVATING=[0-9]*' | cut -d= -f2)
  d=${d:-0}
  [ "$d" -gt "$maxdeact" ] && maxdeact=$d
  echo "$(date -u +%T) API: service desired/running=[$rc] tasks by lastStatus: $states"
  # stop sampling once drain is over
  if [ "$i" -gt 6 ] && [ "$d" -eq 0 ]; then break; fi
done
echo "Max tasks seen in DEACTIVATING (draining): $maxdeact"

# ---- CloudWatch ----
find_live() {
  aws cloudwatch list-metrics --metric-name LiveTaskCount --output json 2>/dev/null \
    | jq -c --arg s "$SVC" --arg c "$CLUSTER" '[.Metrics[] | select(any(.Dimensions[]; .Name=="ServiceName" and .Value==$s)) | select(all(.Dimensions[]; (.Name!="ClusterName") or .Value==$c))] | first // empty'
}
find_running() {
  aws cloudwatch list-metrics --namespace ECS/ContainerInsights --metric-name RunningTaskCount \
    --dimensions Name=ClusterName,Value="$CLUSTER" Name=ServiceName,Value="$SVC" --output json 2>/dev/null \
    | jq -c '.Metrics | first // empty'
}

LIVE=""; RUN=""
reproduced=0
samples=0
for attempt in $(seq 1 12); do
  [ -z "$LIVE" ] && LIVE="$(find_live)"
  [ -z "$RUN" ] && RUN="$(find_running)"
  if [ -n "$LIVE" ] && [ -n "$RUN" ]; then
    jq -n --argjson l "$LIVE" --argjson r "$RUN" '[
      {Id:"live",Label:"LiveTaskCount",ReturnData:true,MetricStat:{Metric:{Namespace:$l.Namespace,MetricName:$l.MetricName,Dimensions:$l.Dimensions},Period:60,Stat:"Average"}},
      {Id:"running",Label:"RunningTaskCount",ReturnData:true,MetricStat:{Metric:{Namespace:$r.Namespace,MetricName:$r.MetricName,Dimensions:$r.Dimensions},Period:60,Stat:"Average"}}]' > /tmp/hero-queries.json
    aws cloudwatch get-metric-data --metric-data-queries file:///tmp/hero-queries.json \
      --start-time "$(date -u -d '-30 minutes' +%Y-%m-%dT%H:%M:%SZ)" --end-time "$(date -u -d '+2 minutes' +%Y-%m-%dT%H:%M:%SZ)" \
      --output json > /tmp/hero-data.json 2>/dev/null
    samples=$(jq '[.MetricDataResults[].Values|length]|add // 0' /tmp/hero-data.json)
    reproduced=$(jq '(.MetricDataResults|map({(.Id):([.Timestamps,.Values]|transpose|map({(.[0]):.[1]})|add // {})})|add) as $d
      | [ ($d.live // {}) | keys[] as $t | select(($d.running[$t] != null) and (($d.live[$t] - $d.running[$t]) >= 0.5)) ] | length' /tmp/hero-data.json)
    echo "-- attempt $attempt: $samples datapoints, minutes where Live > Running: $reproduced"
    [ "${reproduced:-0}" -gt 0 ] && break
  else
    lf=missing; rf=missing
    [ -n "$LIVE" ] && lf=found
    [ -n "$RUN" ] && rf=found
    echo "-- attempt $attempt: metrics not listed yet (LiveTaskCount: $lf, RunningTaskCount: $rf)"
  fi
  sleep 30
done

if [ -n "$LIVE" ] && [ -n "$RUN" ] && [ -s /tmp/hero-data.json ]; then
  echo "== LiveTaskCount namespace: $(echo "$LIVE" | jq -r .Namespace)   RunningTaskCount namespace: $(echo "$RUN" | jq -r .Namespace)"
  echo "== Per-minute averages (UTC)"
  jq -r '(.MetricDataResults|map({(.Id):([.Timestamps,.Values]|transpose|map({(.[0]):.[1]})|add // {})})|add) as $d
    | ($d.live // {}) | keys[] as $t | "\($t)  LiveTaskCount=\($d.live[$t])  RunningTaskCount=\($d.running[$t] // "n/a")"' /tmp/hero-data.json
fi

if [ "${reproduced:-0}" -gt 0 ]; then
  echo "Observation: while tasks were DEACTIVATING, LiveTaskCount stayed higher than RunningTaskCount - the two metrics disagree during scale-in."
  echo "HERO_RESULT: reproduced"
else
  echo "Observation: no minute found where LiveTaskCount > RunningTaskCount (max draining tasks seen via API: $maxdeact)."
  echo "HERO_RESULT: not_reproduced"
fi
exit 0
