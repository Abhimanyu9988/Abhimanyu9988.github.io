#!/usr/bin/env bash
# BUILD: the same gateway Collector with OTTL on the traces pipeline, nothing else changed. Two configs, two runs each:
#   named.yaml  filter/healthchecks: span.attributes["url.path"] == "/healthz"
#               transform/redact:    replace_pattern on url.query and url.full ("token=[^&]*" -> "token=REDACTED")
#   all.yaml    the same filter, and replace_all_patterns(span.attributes, "value", ...) with the same pattern
# The services emit both the current and the older HTTP attribute names (http/dup), so named.yaml shows what naming two
# attributes leaves behind and all.yaml whether masking every attribute value covers it. Runs named-1, named-2, all-1,
# all-2, then reads every run's sample traces (before runs too) and the health checks per config back from Splunk.
set -uo pipefail
cd "$(dirname "$0")"
. ./lib.sh
. ./splunk.sh

start_clock 830 not_fixed

aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null || fail "could not write kubeconfig"
for v in SPLUNK_REALM SPLUNK_ACCESS_TOKEN SPLUNK_API_TOKEN; do [ -n "${!v:-}" ] || fail "$v is not set"; done
LOAD_NODE=$(cat load-node.txt) || fail "no load node recorded"
[ -s load-before-1.txt ] && [ -s load-before-2.txt ] && [ -s periods.txt ] || fail "reproduce.sh's runs are missing"

echo "== 1. The before-check: the cluster's own data reaches Splunk"
wait_cluster "$(cap 60 740)"
echo "cluster metric time series in Splunk: ${cluster:-0}"
[ "${cluster:-0}" -gt 0 ] || fail "no cluster data reached Splunk, so a zero here would prove nothing"

echo "== 2. The OTTL in named.yaml and all.yaml"
for c in named all; do
  echo "  $c.yaml:"
  kubectl -n demo get configmap gateway -o "jsonpath={.data.$c\.yaml}" | awk '/filter\/healthchecks:/{p=1} /batch:/{p=0} p' | sed 's/^/    /'
done

n=3
for run in named-1 named-2 all-1 all-2; do
  echo "== $n. Run $run"
  start_run "$run" 100
  load_run "$run" "$(cap 100 300)"
  measures "$run"
  n=$((n + 1))
done
echo "all end $(date +%s)" >> periods.txt

echo "== 7. Every run's sample traces, read back from Splunk APM by trace ID"
read_traces_patiently all-2 "$(cap 240 170)"   # the newest run first, so its wait covers the others
for run in before-1 before-2 named-1 named-2 all-1 all-2; do
  [ "$run" = all-2 ] || read_traces_patiently "$run" "$(cap 60 140)"
  r=${run//-/_}
  eval "found_$r=$found raw_$r=$raw masked_$r=$masked min_$r=$spans_min both_$r=$both_services"
  echo "HERO_MEASURE: splunk_sample_traces_found[$run]=$found"
  echo "HERO_MEASURE: splunk_spans_per_trace_min[$run]=$spans_min"
  echo "HERO_MEASURE: splunk_spans_with_raw_token[$run]=$raw"
  echo "HERO_MEASURE: splunk_spans_with_masked_token[$run]=$masked"
done

echo "== 8. Health checks per config and service in Splunk"
cat periods.txt | tr '\n' ' '; echo
for cfg in base named all; do
  for svc in orders inventory; do
    health_counts "$cfg" "$svc"
    echo "HERO_MEASURE: splunk_apm_healthz_requests[$cfg/$svc]=$apm_health"
    echo "HERO_MEASURE: splunk_metric_healthz_requests[$cfg/$svc]=$metric_health"
  done
done

echo "== 9. Verdict"
problems=0
for run in all-1 all-2; do
  r=${run//-/_}; f="found_$r"; x="raw_$r"; m="masked_$r"; s="min_$r"; b="both_$r"
  total=$(sample_count "$run")
  [ "${!f}" -ge $(( total * 9 / 10 )) ] || { echo "  $run: only ${!f} of $total sample traces reached Splunk"; problems=$((problems + 1)); }
  [ "${!x}" = 0 ] || { echo "  $run: ${!x} spans still hold the raw token"; problems=$((problems + 1)); }
  [ "${!m}" -ge $(( ${!f} * 3 )) ] || { echo "  $run: ${!m} masked spans for ${!f} traces, expected 3 a trace"; problems=$((problems + 1)); }
  [ "${!s}" -ge 3 ] && [ "${!b}" = "${!f}" ] || { echo "  $run: a trace lost spans or a service"; problems=$((problems + 1)); }
  [ "$(sed -n 's/^LOAD end=.* ok=\([0-9]*\).*/\1/p' "load-$run.txt")" = "$(sed -n 's/^LOAD end=.* sent=\([0-9]*\).*/\1/p' "load-$run.txt")" ] || { echo "  $run: some orders failed"; problems=$((problems + 1)); }
  [ "$(( $(gwd "$run" filter_in) - $(gwd "$run" filter_out) ))" -gt 0 ] || { echo "  $run: the filter dropped no health-check span"; problems=$((problems + 1)); }
done
[ "${raw_before_1:-0}" -gt 0 ] || [ "${raw_before_2:-0}" -gt 0 ] || { echo "  no before run shows the raw token in Splunk, so a zero after proves nothing"; problems=$((problems + 1)); }
echo "problems: $problems"
[ "$problems" = 0 ] && emit fixed || emit not_fixed
