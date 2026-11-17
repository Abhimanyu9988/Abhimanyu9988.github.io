#!/usr/bin/env bash
# BUILD: run the two hardened configs side by side through one shared 3-minute outage and compare
# with the default result saved by reproduce.sh in this directory (hero_lost_pct_default.txt).
# Budget (see lib.sh): sink <=75s + tasks <=180s + steady <=75s + outage ~190s + drain to 680s after start
# + evidence <=110s  (< 14 min).
cd "$(dirname "$0")" || exit 1
source ./lib.sh

rm -f hero_report_mem.json hero_report_fs.json hero_lost_pct_mem.txt hero_lost_pct_fs.txt
init_env

echo "== BUILD: hardened Fluent Bit configs on the same FireLens path (image $(terraform output -raw fluentbit_image))"
echo "--- (b) memory buffering + Retry_Limit 60 + Mem_Buf_Limit 64MB (+ scheduler.cap 20) ---"
terraform output -raw router_conf_mem
echo "--- (c) filesystem buffering + storage.total_limit_size 512M + Retry_Limit 60 (+ scheduler.cap 20) ---"
terraform output -raw router_conf_fs

experiment mem fs
rc=$?
if [[ $rc -ne 0 ]]; then
  echo "experiment did not complete (rc=$rc)"
  echo "HERO_RESULT: not_fixed"
  exit 0
fi

emit mem
emit fs
echo

d="$(cat hero_lost_pct_default.txt 2>/dev/null)"
if [[ -n "$d" ]]; then
  echo "default result from reproduce.sh: records_lost_pct=$d (file hero_lost_pct_default.txt)"
else
  echo "default result missing (run reproduce.sh first in this directory): comparison will be null"
fi
result=fixed
for c in mem fs; do
  l="$(cat "hero_lost_pct_$c.txt" 2>/dev/null)"
  echo "raw: hero_lost_pct_default.txt [default] -> ${d:-missing}"
  echo "raw: hero_lost_pct_$c.txt [$c] -> ${l:-missing}"
  if [[ -n "$d" && -n "$l" ]]; then
    diff="$(awk -v a="$d" -v b="$l" 'BEGIN{printf "%.2f", a-b}')"
    echo "raw: awk default_lost_pct - ${c}_lost_pct = $d - $l -> $diff"
    awk -v a="$d" -v b="$l" 'BEGIN{exit !(b+0<a+0)}' || result=not_fixed
  else
    diff=null
    result=not_fixed
  fi
  echo "HERO_MEASURE: loss_by_config_comparison[$c]=$diff"
done

echo
table mem fs
echo
for c in mem fs; do
  jq -r '"\(.run): generated=\(.generated) delivered=\(.delivered) lost=\(.lost) (\(.lost_pct)%) | generated during outage=\(.generated_in_outage), arrived after recovery=\(.delivered_after_recovery) | drain \(.drain_time_s)s | router mem \(.mem_baseline_mib) -> \(.mem_peak_outage_mib) MiB | max stdout write during outage \(.max_write_ms_outage) ms"' "hero_report_$c.json"
done

for c in mem fs; do
  [[ "${DRAINED[$c]:-0}" == 1 ]] || { echo "config $c was still draining at the time limit"; result=not_fixed; }
done
echo "HERO_RESULT: $result"
