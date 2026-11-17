#!/usr/bin/env bash
# BEFORE: default FireLens config (agent-generated, no tuning) through a 3-minute HTTP destination outage.
# Budget (see lib.sh): sink <=75s + tasks <=180s + steady <=75s + outage ~190s + drain to 680s after start
# + evidence <=110s  (< 14 min).
cd "$(dirname "$0")" || exit 1
source ./lib.sh

rm -f hero_report_*.json hero_lost_pct_*.txt
init_env

echo "== BEFORE: ECS Fargate task, FireLens + AWS for Fluent Bit ($(terraform output -raw fluentbit_image)), default settings"
echo "== log generator: 200 lines/s to stdout -> awsfirelens -> http output -> sink on EC2 ($SINK_IP:8080)"

experiment default
rc=$?

if [[ $rc -eq 1 ]]; then
  echo "script could not run the experiment (infrastructure problem above)"
  echo "HERO_RESULT: not_reproduced"
  exit 1
fi
if [[ $rc -eq 2 ]]; then
  echo "HERO_RESULT: not_reproduced"
  exit 0
fi

emit default
echo
table default
echo
f=hero_report_default.json
jq -r '"default config: generated=\(.generated) delivered=\(.delivered) lost=\(.lost) (\(.lost_pct)%) | generated during outage=\(.generated_in_outage), arrived after recovery=\(.delivered_after_recovery), lost in outage window=\(.lost_in_outage) | lost before outage=\(.lost_pre_outage)"' "$f"

lost="$(cat hero_lost_pct_default.txt 2>/dev/null)"
if [[ -n "$lost" ]] && awk -v x="$lost" 'BEGIN{exit !(x+0>0)}'; then
  echo "With default settings, ${lost}% of the generated records never reached the destination after a 3-minute outage."
  echo "HERO_RESULT: reproduced"
else
  echo "No confirmed loss for the default config (lost_pct='${lost:-null}', drain not final or nothing lost)."
  echo "HERO_RESULT: not_reproduced"
fi
