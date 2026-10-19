#!/usr/bin/env bash
# Fix: stop treating RunningTaskCount as "all tasks that still exist". Build a dashboard and an alarm
# that use LiveTaskCount and the Live-Running difference (= draining tasks), then verify on the scale-in data.
set -uo pipefail

REGION="${AWS_REGION:-eu-central-1}"
LAB="${LAB_NAME:-lab}"
CLUSTER="$(terraform output -raw cluster_name)"
SVC="$(terraform output -raw service_name)"
export AWS_DEFAULT_REGION="$REGION"

# numeric check without bc: true if $1 >= 1
ge1() {
  jq -en --argjson m "${1:-0}" '$m >= 1' >/dev/null 2>&1
}

LIVE="$(aws cloudwatch list-metrics --metric-name LiveTaskCount --output json 2>/dev/null \
  | jq -c --arg s "$SVC" --arg c "$CLUSTER" '[.Metrics[] | select(any(.Dimensions[]; .Name=="ServiceName" and .Value==$s)) | select(all(.Dimensions[]; (.Name!="ClusterName") or .Value==$c))] | first // empty')"
RUN="$(aws cloudwatch list-metrics --namespace ECS/ContainerInsights --metric-name RunningTaskCount \
  --dimensions Name=ClusterName,Value="$CLUSTER" Name=ServiceName,Value="$SVC" --output json 2>/dev/null | jq -c '.Metrics | first // empty')"

if [ -z "$LIVE" ] || [ -z "$RUN" ]; then
  echo "Could not find LiveTaskCount and/or RunningTaskCount metrics"
  echo "HERO_RESULT: not_fixed"
  exit 0
fi

mq() { # $1 = ReturnData for live/running
  jq -n --argjson l "$LIVE" --argjson r "$RUN" --argjson rd "$1" '[
    {Id:"draining",Label:"DrainingTasks (Live - Running)",Expression:"live - running",ReturnData:true},
    {Id:"live",Label:"LiveTaskCount",ReturnData:$rd,MetricStat:{Metric:{Namespace:$l.Namespace,MetricName:$l.MetricName,Dimensions:$l.Dimensions},Period:60,Stat:"Average"}},
    {Id:"running",Label:"RunningTaskCount",ReturnData:$rd,MetricStat:{Metric:{Namespace:$r.Namespace,MetricName:$r.MetricName,Dimensions:$r.Dimensions},Period:60,Stat:"Average"}}]'
}

# 1. alarm on draining tasks, derived from LiveTaskCount
mq false > /tmp/hero-alarm-metrics.json
aws cloudwatch put-metric-alarm --alarm-name "${LAB}-draining-tasks" \
  --alarm-description "Tasks still alive but no longer counted by RunningTaskCount (DEACTIVATING)" \
  --metrics file:///tmp/hero-alarm-metrics.json \
  --comparison-operator GreaterThanThreshold --threshold 0 --evaluation-periods 1 \
  --treat-missing-data notBreaching && echo "alarm ${LAB}-draining-tasks created"

# 2. dashboard with both metrics side by side
L_NS=$(echo "$LIVE" | jq -r .Namespace)
DASH=$(jq -n --arg region "$REGION" --arg ln "$L_NS" --arg c "$CLUSTER" --arg s "$SVC" '{widgets:[{type:"metric",x:0,y:0,width:24,height:8,properties:{region:$region,stat:"Average",period:60,title:"Live vs Running tasks",view:"timeSeries",metrics:[[$ln,"LiveTaskCount","ClusterName",$c,"ServiceName",$s],["ECS/ContainerInsights","RunningTaskCount","ClusterName",$c,"ServiceName",$s]]}}]}')
aws cloudwatch put-dashboard --dashboard-name "${LAB}-tasks" --dashboard-body "$DASH" >/dev/null && echo "dashboard ${LAB}-tasks created"

# 3. verify: the expression catches the draining tasks from the scale-in
mq true > /tmp/hero-verify.json
max=""
for i in $(seq 1 6); do
  max=$(aws cloudwatch get-metric-data --metric-data-queries file:///tmp/hero-verify.json \
    --start-time "$(date -u -d '-60 minutes' +%Y-%m-%dT%H:%M:%SZ)" --end-time "$(date -u -d '+2 minutes' +%Y-%m-%dT%H:%M:%SZ)" --output json 2>/dev/null \
    | jq -r '[.MetricDataResults[]|select(.Id=="draining")|.Values[]]|max // 0')
  ge1 "${max:-0}" && break
  sleep 20
done
echo "Max DrainingTasks (Live - Running) over the last hour: ${max:-0}"
exists=$(aws cloudwatch describe-alarms --alarm-names "${LAB}-draining-tasks" --alarm-types MetricAlarm CompositeAlarm --query 'length(MetricAlarms)' --output text 2>/dev/null)
echo "Alarm exists: ${exists:-0}"

if [ "${exists:-0}" = "1" ] && ge1 "${max:-0}"; then
  echo "Lesson: LiveTaskCount includes DEACTIVATING (draining) tasks; RunningTaskCount does not. Use Live for capacity/cost views, and Live-Running to see drain."
  echo "HERO_RESULT: fixed"
else
  echo "HERO_RESULT: not_fixed"
fi
exit 0
