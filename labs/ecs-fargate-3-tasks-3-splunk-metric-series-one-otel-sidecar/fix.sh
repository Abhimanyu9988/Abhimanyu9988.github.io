#!/usr/bin/env bash
# BUILD: add a Splunk OTel Collector sidecar (awsecscontainermetrics receiver), scale 1 -> 3, prove it in Splunk.
set -uo pipefail
export AWS_PAGER=""
export AWS_REGION="${AWS_REGION:-eu-central-1}"

CLUSTER=$(terraform output -raw cluster_name)
SERVICE=$(terraform output -raw service_name)
FAMILY=$(terraform output -raw task_family)
LOG_GROUP=$(terraform output -raw log_group_name)
REGION="$AWS_REGION"
# One global deadline for every wait (script limit is 15 min = 900s; keep room for the proof output).
DEADLINE=$((SECONDS+780))

fail() { echo "$1"; echo "HERO_RESULT: not_fixed"; exit 0; }

[ -n "${SPLUNK_REALM:-}" ] && [ -n "${SPLUNK_ACCESS_TOKEN:-}" ] && [ -n "${SPLUNK_API_TOKEN:-}" ] \
  || fail "Set SPLUNK_REALM, SPLUNK_ACCESS_TOKEN and SPLUNK_API_TOKEN"
export SPLUNK_REALM SPLUNK_ACCESS_TOKEN
API="https://api.${SPLUNK_REALM}.signalfx.com"

splunk_mts() {
  curl -sS --max-time 30 -H "X-SF-Token: $SPLUNK_API_TOKEN" --get \
    --data-urlencode "query=$1" --data-urlencode "limit=200" "$API/v2/metrictimeseries"
}
splunk_task_ids() {
  splunk_mts "sf_metric:ecs.task.memory.utilized AND ecs.cluster.name:$CLUSTER" 2>/dev/null \
    | jq -r '[.results[]? | (.dimensions["aws.ecs.task.id"] // empty)] | unique | .[]' 2>/dev/null
}
running_ids() {
  aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" --desired-status RUNNING \
    --query 'taskArns[]' --output text | tr '\t' '\n' | awk -F/ 'NF{print $NF}' | sort
}
wait_rollout() { # desired, max seconds (also capped by the global DEADLINE)
  local want=$1 end=$((SECONDS+$2)) out running deployments
  [ $end -gt $DEADLINE ] && end=$DEADLINE
  while [ $SECONDS -lt $end ]; do
    out=$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
      --query 'services[0].[runningCount,length(deployments)]' --output text 2>/dev/null)
    read -r running deployments <<< "$out"
    if [ "${running:-}" = "$want" ] && [ "${deployments:-}" = "1" ]; then
      return 0
    fi
    sleep 10
  done
  return 1
}
wait_splunk() { # expected task count, max seconds (also capped by the global DEADLINE)
  local want=$1 end=$((SECONDS+$2)) ids run missing
  [ $end -gt $DEADLINE ] && end=$DEADLINE
  while [ $SECONDS -lt $end ]; do
    ids=$(splunk_task_ids | sort)
    run=$(running_ids)
    missing=$(comm -23 <(echo "$run") <(echo "$ids") | sed '/^$/d')
    if [ "$(echo "$run" | sed '/^$/d' | wc -l)" = "$want" ] && [ -n "$ids" ] && [ -z "$missing" ]; then
      return 0
    fi
    echo "  waiting for Splunk: running=$(echo "$run" | sed '/^$/d' | wc -l) in_splunk=$(echo "$ids" | sed '/^$/d' | wc -l)"
    sleep 20
  done
  return 1
}
show_values() { # metric name
  local mts now start win
  mts=$(splunk_mts "sf_metric:$1 AND ecs.cluster.name:$CLUSTER")
  now=$(date +%s); start=$((now-600))
  win=$(curl -sS --max-time 30 -H "X-SF-Token: $SPLUNK_API_TOKEN" --get \
    --data-urlencode "query=sf_metric:$1 AND ecs.cluster.name:$CLUSTER" \
    --data-urlencode "startMs=${start}000" --data-urlencode "endMs=${now}000" \
    --data-urlencode "resolution=60000" "$API/v1/timeserieswindow") || return 0
  echo "$win" | jq -r --argjson m "$mts" '
    ($m.results | map({key: .id, value: (.dimensions["aws.ecs.task.id"] // "?")}) | from_entries) as $map
    | (.data // {}) | to_entries[]
    | "  \($map[.key] // .key)  last=\((.value | map(select(.[1] != null)) | last | .[1]) // "n/a")"' 2>/dev/null || true
}

# ---- 1. collector image, pinned to the current Splunk OTel Collector release
TAG=0.163.1
IMAGE="quay.io/signalfx/splunk-otel-collector:$TAG"
echo "== Sidecar image: $IMAGE"

# ---- 2. collector config: ECS task metadata -> Splunk Observability (token comes from the container env)
CFG=$(cat <<'EOF'
receivers:
  awsecscontainermetrics:
    collection_interval: 20s
processors:
  batch: {}
  resource/lab:
    attributes:
      - key: ecs.cluster.name
        value: "@CLUSTER@"
        action: upsert
      - key: ecs.service.name
        value: "@SERVICE@"
        action: upsert
      - key: deployment.environment
        value: lab
        action: upsert
exporters:
  signalfx:
    access_token: "${env:SPLUNK_ACCESS_TOKEN}"
    realm: "${env:SPLUNK_REALM}"
service:
  pipelines:
    metrics:
      receivers: [awsecscontainermetrics]
      processors: [resource/lab, batch]
      exporters: [signalfx]
EOF
)
CFG="${CFG//@CLUSTER@/$CLUSTER}"
CFG="${CFG//@SERVICE@/$SERVICE}"

SIDECAR=$(jq -n --arg img "$IMAGE" --arg cfg "$CFG" --arg lg "$LOG_GROUP" --arg region "$REGION" '{
  name: "otel-collector", image: $img, essential: false,
  environment: [
    {name: "SPLUNK_CONFIG_YAML", value: $cfg},
    {name: "SPLUNK_ACCESS_TOKEN", value: env.SPLUNK_ACCESS_TOKEN},
    {name: "SPLUNK_REALM", value: env.SPLUNK_REALM}
  ],
  logConfiguration: {logDriver: "awslogs", options: {
    "awslogs-group": $lg, "awslogs-region": $region, "awslogs-stream-prefix": "otel"}}
}')

# ---- 3. new task definition revision = current one + sidecar
CUR_TD=$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].taskDefinition' --output text)
NEW_TD_JSON=$(aws ecs describe-task-definition --task-definition "$CUR_TD" --output json \
  | jq --argjson sc "$SIDECAR" '.taskDefinition
      | {family, networkMode, requiresCompatibilities, cpu, memory, executionRoleArn, runtimePlatform, containerDefinitions}
      | with_entries(select(.value != null))
      | .containerDefinitions |= (map(select(.name != "otel-collector")) + [$sc])')
NEW_TD=$(aws ecs register-task-definition --cli-input-json "$NEW_TD_JSON" --query 'taskDefinition.taskDefinitionArn' --output text) \
  || fail "register-task-definition failed"
unset NEW_TD_JSON SIDECAR
echo "== Registered $NEW_TD"
aws ecs describe-task-definition --task-definition "$NEW_TD" \
  --query 'taskDefinition.containerDefinitions[].{name:name,image:image,essential:essential}' --output json | jq -c '.[]'

# ---- 4. roll the service (1 task) and check Splunk
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --task-definition "$NEW_TD" \
  --desired-count 1 --query 'service.deployments[0].rolloutState' --output text >/dev/null || fail "update-service failed"
T0=$SECONDS
wait_rollout 1 360 || { echo "rollout did not finish"; aws logs tail "$LOG_GROUP" --since 15m --format short 2>/dev/null | tail -n 30; fail "rollout timeout"; }
echo "== Rollout with sidecar finished in $((SECONDS-T0))s (1 task)"

echo "== Waiting for the first task's series in Splunk"
if ! wait_splunk 1 300; then
  echo "--- sidecar log tail"
  aws logs tail "$LOG_GROUP" --since 15m --log-stream-name-prefix otel --format short 2>/dev/null | tail -n 30
  fail "No series for the 1 task in Splunk"
fi
echo "== 1 task visible in Splunk"

# ---- 5. scale out 1 -> 3
T1=$SECONDS
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --desired-count 3 >/dev/null || fail "scale-out failed"
wait_rollout 3 420 || fail "service did not reach 3 running tasks"
echo "== Scaled 1 -> 3 running tasks in $((SECONDS-T1))s"

echo "== Waiting for all 3 tasks in Splunk"
if ! wait_splunk 3 480; then
  aws logs tail "$LOG_GROUP" --since 15m --log-stream-name-prefix otel --format short 2>/dev/null | tail -n 30
  fail "Splunk does not show all 3 running tasks"
fi

# ---- 6. proof summary
echo
echo "================ PROOF ================"
echo "Collector image: $IMAGE"
echo "Service: desired=$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].desiredCount' --output text) running=$(running_ids | wc -l)"
echo "ECS running task ids:"; running_ids | sed 's/^/  /'
echo "Splunk task ids for ecs.cluster.name=$CLUSTER:"; splunk_task_ids | sed 's/^/  /'
echo "Metric names with series for this cluster:"
splunk_mts "ecs.cluster.name:$CLUSTER" | jq -r '[.results[]?.sf_metric] | group_by(.) | map("  \(.[0]) x\(length)") | .[]' 2>/dev/null | head -n 25
echo "ecs.task.cpu.utilized per task (last 1-min value):"; show_values ecs.task.cpu.utilized
echo "ecs.task.memory.utilized per task (last 1-min value):"; show_values ecs.task.memory.utilized
N_SPLUNK=$(splunk_task_ids | wc -l)
echo "Tasks in Splunk: $N_SPLUNK, desired count: 3"
if [ "$N_SPLUNK" -ge 3 ]; then
  echo "HERO_RESULT: fixed"
else
  echo "HERO_RESULT: not_fixed"
fi
