#!/usr/bin/env bash
# Stops any Fargate tasks started by reproduce.sh/fix.sh (Terraform does not know about them) and waits until
# every one of them is really STOPPED (so the ENIs are released and the subnet/SG can be destroyed).
# Never fails hard. Max runtime ~ 5 minutes.
cd "$(dirname "$0")" 2>/dev/null
export AWS_PAGER=""
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-eu-central-1}}"

C="$(terraform output -raw cluster_name 2>/dev/null)"
if ! [[ "$C" =~ ^[A-Za-z0-9_-]+$ ]]; then C="${LAB_NAME:-}"; fi
[[ -n "$C" ]] || { echo "no cluster name known, nothing to clean"; exit 0; }

declare -A SEEN
deadline=$(( $(date +%s) + 240 ))
clean=0
while (( $(date +%s) < deadline )); do
  # 1. find every task that is RUNNING or PENDING and ask it to stop
  for st in RUNNING PENDING; do
    arns="$(timeout 30 aws ecs list-tasks --cluster "$C" --desired-status "$st" --query 'taskArns[]' --output text 2>/dev/null)"
    [[ "$arns" == "None" ]] && arns=""
    for t in $arns; do
      if [[ -z "${SEEN[$t]:-}" ]]; then echo "stop requested ($st): ${t##*/}"; fi
      SEEN[$t]=1
      timeout 30 aws ecs stop-task --cluster "$C" --task "$t" --reason "lab cleanup" >/dev/null 2>&1
    done
  done
  # 2. nothing known yet and nothing listed -> done
  if (( ${#SEEN[@]} == 0 )); then
    echo "no running or pending tasks in cluster $C"
    clean=1
    break
  fi
  # 3. every task we saw must have lastStatus STOPPED (a stopped task can still be STOPPING/DEPROVISIONING)
  if out="$(timeout 30 aws ecs describe-tasks --cluster "$C" --tasks "${!SEEN[@]}" \
        --query 'tasks[?lastStatus!=`STOPPED`].[taskArn,lastStatus]' --output text 2>/dev/null)"; then
    [[ "$out" == "None" ]] && out=""
    if [[ -z "$out" ]]; then
      # re-check that nothing new appeared in either state
      r="$(timeout 30 aws ecs list-tasks --cluster "$C" --desired-status RUNNING --query 'taskArns[]' --output text 2>/dev/null)"
      p="$(timeout 30 aws ecs list-tasks --cluster "$C" --desired-status PENDING --query 'taskArns[]' --output text 2>/dev/null)"
      [[ "$r" == "None" ]] && r=""
      [[ "$p" == "None" ]] && p=""
      if [[ -z "$r$p" ]]; then
        echo "all ${#SEEN[@]} task(s) in cluster $C are STOPPED"
        clean=1
        break
      fi
    else
      echo "waiting for tasks to reach STOPPED: $(echo "$out" | awk '{print $2}' | sort | uniq -c | tr '\n' ' ')"
    fi
  else
    echo "describe-tasks failed, retrying"
  fi
  sleep 10
done

if [[ "$clean" == 1 && ${#SEEN[@]} -gt 0 ]]; then
  echo "waiting 20s for task ENIs to detach"
  sleep 20
elif [[ "$clean" != 1 ]]; then
  echo "WARNING: tasks may still be stopping after the cleanup timeout"
fi
exit 0
