#!/usr/bin/env bash
# Shared helpers, sourced by reproduce.sh and fix.sh. Never prints the admin token.
# State that must survive between reproduce.sh and fix.sh is written to the lab directory (hero_*), never /tmp.
#
# TIME BUDGET (experiment, worst case, all waits included; limit is 14 min = 840 s):
#   sink ready      <=  75 s  (time-bounded)
#   run-task        ~   10 s
#   tasks RUNNING   <= 180 s  (one shared deadline for all tasks)
#   steady state    <=  75 s  (time-bounded)
#   outage           ~ 190 s  (6 x 30 s + status queries)
#   drain           ends at experiment start + 680 s (and at least 90 s after recovery)
#   evidence/stop   <=  ~110 s worst case (every aws call under `timeout`)

export AWS_PAGER=""
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-eu-central-1}}"

declare -A TASK DRAINED

init_env() {
  CLUSTER="$(terraform output -raw cluster_name)"
  SUBNET="$(terraform output -raw subnet_id)"
  SG="$(terraform output -raw task_security_group_id)"
  SINK_IP="$(terraform output -raw sink_public_ip)"
  TOKEN="$(terraform output -raw sink_admin_token)"
  LOG_GROUP="$(terraform output -raw log_group)"
  ADMIN="http://${SINK_IP}:8081"
}

adm() { # adm PATH [extra curl args]
  local p="$1"; shift
  local sep='?'
  [[ "$p" == *\?* ]] && sep='&'
  curl -s --max-time 15 "$@" "${ADMIN}${p}${sep}t=${TOKEN}"
}

rf() { adm "/report?run=$1" | jq -r "$2" 2>/dev/null; }
num() { if [[ "$1" =~ ^[0-9]+$ ]]; then echo "$1"; else echo 0; fi; }

wait_sink() { # time-bounded: 75 s
  local dl=$(( $(date +%s) + 75 ))
  while (( $(date +%s) < dl )); do
    if curl -s --max-time 5 "${ADMIN}/health" | jq -e '.ok' >/dev/null 2>&1; then return 0; fi
    sleep 3
  done
  return 1
}

start_task() { # cfg -> task ARN
  local td
  td="$(terraform output -raw "task_def_$1")"
  timeout 60 aws ecs run-task --cluster "$CLUSTER" --launch-type FARGATE --task-definition "$td" --count 1 \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNET],securityGroups=[$SG],assignPublicIp=ENABLED}" \
    --query 'tasks[0].taskArn' --output text 2>&1
}

wait_running() { # task arn, deadline (epoch seconds)
  local st
  while (( $(date +%s) < $2 )); do
    st="$(timeout 20 aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$1" --query 'tasks[0].lastStatus' --output text 2>/dev/null)"
    [[ "$st" == "RUNNING" ]] && return 0
    if [[ "$st" == "STOPPED" ]]; then
      timeout 20 aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$1" \
        --query 'tasks[0].[stoppedReason,containers[].[name,reason]]' --output json 2>&1
      return 1
    fi
    sleep 5
  done
  return 1
}

task_state() { # cfg
  timeout 20 aws ecs describe-tasks --cluster "$CLUSTER" --tasks "${TASK[$1]}" \
    --query 'tasks[0].[lastStatus,stoppedReason,containers[].[name,lastStatus,exitCode,reason]]' --output json 2>&1 | jq -c .
}

router_evidence() { # cfg: Fluent Bit's own log lines from CloudWatch Logs (best effort)
  local c="$1" out n s
  echo "--- log_router (Fluent Bit) log evidence [$c] ---"
  out="$(timeout 10 aws logs filter-log-events --log-group-name "$LOG_GROUP" --log-stream-name-prefix "router-$c" \
        --filter-pattern '"Fluent Bit v"' --limit 5 --no-paginate --output json 2>/dev/null)"
  echo "version line: $(echo "$out" | jq -r '.events[0].message // "n/a"' 2>/dev/null)"
  out="$(timeout 10 aws logs filter-log-events --log-group-name "$LOG_GROUP" --log-stream-name-prefix "router-$c" \
        --filter-pattern '"failed to flush"' --limit 1000 --no-paginate --output json 2>/dev/null)"
  n="$(echo "$out" | jq -r '.events|length' 2>/dev/null)"
  echo "'failed to flush chunk' (retry scheduled) lines: ${n:-n/a} (cap 1000)"
  echo "sample: $(echo "$out" | jq -r '.events[0].message // "none"' 2>/dev/null)"
  out="$(timeout 10 aws logs filter-log-events --log-group-name "$LOG_GROUP" --log-stream-name-prefix "router-$c" \
        --filter-pattern '"cannot be retried"' --limit 1000 --no-paginate --output json 2>/dev/null)"
  n="$(echo "$out" | jq -r '.events|length' 2>/dev/null)"
  echo "'chunk cannot be retried' (chunk DROPPED) lines: ${n:-n/a} (cap 1000)"
  echo "sample: $(echo "$out" | jq -r '.events[0].message // "none"' 2>/dev/null)"
}

# experiment CFG...: run the given configs side by side through ONE shared outage.
# return 0 = measured, 1 = infrastructure problem, 2 = pipeline never reached steady state
experiment() {
  local cfgs=("$@") c i n ok now t0 exp_start tdl sdl dl rec
  declare -A LAST CHG
  exp_start=$(date +%s)
  wait_sink || { echo "ERROR: sink admin port not reachable"; return 1; }
  adm "/phase?set=steady" >/dev/null
  for c in "${cfgs[@]}"; do adm "/reset?run=$c" >/dev/null; done

  for c in "${cfgs[@]}"; do
    TASK[$c]="$(start_task "$c")"
    if [[ "${TASK[$c]}" != arn:* ]]; then echo "ERROR: run-task failed for $c: ${TASK[$c]}"; return 1; fi
    echo "started Fargate task for config '$c': ${TASK[$c]##*/}"
  done
  tdl=$(( $(date +%s) + 180 ))
  for c in "${cfgs[@]}"; do
    wait_running "${TASK[$c]}" "$tdl" || { echo "ERROR: task '$c' did not reach RUNNING within the shared 180s deadline"; return 1; }
    echo "task '$c' RUNNING"
  done

  echo "== steady state: waiting (max 75s) for >= 8000 records (about 40s at 200/s) per config at the sink"
  ok=0
  sdl=$(( $(date +%s) + 75 ))
  while (( $(date +%s) < sdl )); do
    ok=1
    for c in "${cfgs[@]}"; do
      n="$(num "$(rf "$c" .n_records)")"
      [[ "$n" -ge 8000 ]] || ok=0
    done
    [[ "$ok" == 1 ]] && break
    sleep 5
  done
  for c in "${cfgs[@]}"; do echo "steady [$c] sink n_records=$(rf "$c" .n_records) generator seq=$(rf "$c" .generated)"; done
  if [[ "$ok" != 1 ]]; then echo "pipeline did not reach steady state"; return 2; fi

  echo "== OUTAGE: closing the sink's ingest listener (180s) at $(date -u +%T)"
  adm "/stop" | jq -c .
  curl -s -o /dev/null --max-time 4 -X POST "http://${SINK_IP}:8080/ingest/probe" -d x
  echo "probe of ingest port during outage: curl exit code $? (7=connection refused, 28=timeout; 0 would mean still up)"
  for i in 1 2 3 4 5 6; do
    sleep 30
    for c in "${cfgs[@]}"; do
      echo "outage +$((i*30))s [$c] sink n_records=$(rf "$c" .n_records) generator seq=$(rf "$c" .generated) task=$(task_state "$c" | jq -r '.[0]')"
    done
  done
  echo "== RECOVERY: reopening ingest listener at $(date -u +%T)"
  adm "/start" | jq -c .

  rec=$(date +%s)
  dl=$(( exp_start + 680 ))
  (( dl < rec + 90 )) && dl=$(( rec + 90 ))
  echo "== drain: poll every 5s; a config is drained when its sink record count is unchanged for 60s; hard stop $((dl - rec))s after recovery"
  t0=$rec
  for c in "${cfgs[@]}"; do LAST[$c]="$(num "$(rf "$c" .n_records)")"; CHG[$c]=$t0; DRAINED[$c]=0; done
  i=0
  while (( $(date +%s) < dl )); do
    sleep 5
    i=$((i+1))
    now=$(date +%s); ok=1
    for c in "${cfgs[@]}"; do
      n="$(num "$(rf "$c" .n_records)")"
      if [[ "$n" != "${LAST[$c]}" ]]; then LAST[$c]="$n"; CHG[$c]=$now; DRAINED[$c]=0; fi
      if (( now - CHG[$c] >= 60 )); then DRAINED[$c]=1; fi
      (( DRAINED[$c] == 1 )) || ok=0
    done
    if (( i % 3 == 0 )); then
      echo "drain +$((now - t0))s: $(for c in "${cfgs[@]}"; do printf '[%s] n_records=%s idle=%ss  ' "$c" "${LAST[$c]}" "$((now - CHG[$c]))"; done)"
    fi
    (( ok == 1 )) && break
  done

  for c in "${cfgs[@]}"; do
    adm "/report?run=$c" > "hero_report_$c.json"
    echo "task end state [$c]: $(task_state "$c")"
  done
  for c in "${cfgs[@]}"; do
    timeout 20 aws ecs stop-task --cluster "$CLUSTER" --task "${TASK[$c]}" --reason "lab finished" >/dev/null 2>&1
  done
  sleep 5
  for c in "${cfgs[@]}"; do router_evidence "$c"; done
  return 0
}

# mline ID CFG FILE JQEXPR: print the unedited jq output the value comes from, then the HERO_MEASURE line
mline() {
  local id="$1" c="$2" f="$3" e="$4" v
  v="$(jq -c "$e // null" "$f" 2>/dev/null)"
  [[ -n "$v" ]] || v=null
  echo "raw: $f (sink /report [$c]) jq '$e' -> $v"
  echo "HERO_MEASURE: $id[$c]=$v"
}

# emit CFG: print the raw sink report, then each measure with its own raw source line above it
emit() {
  local c="$1" f="hero_report_$1.json" gd lost
  echo "--- sink report for config '$c' (unedited output of the sink's /report) ---"
  cat "$f"; echo
  gd="$(jq -r '.generator_done' "$f" 2>/dev/null)"
  if [[ "${DRAINED[$c]:-0}" == 1 && "$gd" == true ]]; then
    lost="$(jq -r '.lost_pct // "null"' "$f")"
    [[ "$lost" != null ]] && echo "$lost" > "hero_lost_pct_$c.txt"
    mline records_lost_pct "$c" "$f" '.lost_pct'
    mline records_delivered_after_recovery "$c" "$f" '.delivered_after_recovery'
    mline drain_time_s "$c" "$f" '.drain_time_s'
  else
    echo "NOTE [$c]: backlog still arriving at the time limit or generator never finished -> loss and drain are NOT final, reported as null (records still arriving are not counted as lost)"
    for id in records_lost_pct records_delivered_after_recovery drain_time_s; do
      echo "raw: gate [$c] drained=${DRAINED[$c]:-0} generator_done=$gd -> not final"
      echo "HERO_MEASURE: $id[$c]=null"
    done
  fi
  mline fluentbit_peak_memory_mib "$c" "$f" '.mem_peak_outage_mib'
  mline app_stdout_max_write_ms "$c" "$f" '.max_write_ms_outage'
  echo "(context) fluentbit baseline memory before outage [$c] MiB: $(jq -r '.mem_baseline_mib // "null"' "$f"), peak while draining MiB: $(jq -r '.mem_peak_drain_mib // "null"' "$f")"
}

table() { # CFG...
  local c f
  printf '%-8s %9s %12s %8s %9s %9s %12s\n' config lost_pct after_recov drain_s mem_base mem_peak max_write_ms
  for c in "$@"; do
    f="hero_report_$c.json"
    jq -r '[.lost_pct,.delivered_after_recovery,.drain_time_s,.mem_baseline_mib,.mem_peak_outage_mib,.max_write_ms_outage]|map(tostring)|join(" ")' "$f" 2>/dev/null \
      | { read -r a b d e g h; printf '%-8s %9s %12s %8s %9s %9s %12s\n' "$c" "$a" "$b" "$d" "$e" "$g" "$h"; }
  done
}
