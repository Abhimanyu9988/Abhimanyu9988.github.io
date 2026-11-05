#!/usr/bin/env bash
# The second value, in its own script with its own time limit. fix.sh starts it under `timeout` and reads what it saves.
# It writes propagate-result.txt only when every step finished, so anything else is reported as -1, not run.
# The traces are read the same way as the first value's: three rounds, the same sampled IDs, one fixed wait of TRACE_WAIT, one pass.
# LIMIT 310s: second value 80, three rounds 100, TRACE_WAIT 90, pass 40.
set -uo pipefail
cd "$(dirname "$0")"

START=$(date +%s)
LIMIT=310
remaining() { echo $(( START + LIMIT - $(date +%s) )); }
cap() { local want=$1 reserve=$2 r; r=$(( $(remaining) - reserve )); [ "$r" -lt 5 ] && r=5; [ "$want" -lt "$r" ] && echo "$want" || echo "$r"; }
fail() { echo "FAIL in propagate.sh: $*"; exit 0; }

source ./lib.sh
rm -f propagate-result.txt

echo "helm upgrade --reuse-values --set-string obi.env.OTEL_EBPF_BPF_CONTEXT_PROPAGATION=headers"
helm upgrade splunk-otel-collector $CHART --namespace splunk --reuse-values --set-string obi.env.OTEL_EBPF_BPF_CONTEXT_PROPAGATION=headers >/dev/null || fail "helm upgrade failed"
obi_ready 60 20
obi_settings
obi_log | grep -iE 'propagation' | cut -c1-200 | tail -4
kubectl -n splunk get pods -l app.kubernetes.io/name=obi -o json | jq -e '[.items[].spec.containers[0].env[] | select(.name == "OTEL_EBPF_BPF_CONTEXT_PROPAGATION" and .value == "headers")] | length >= 2' >/dev/null || fail "the running OBI pods do not hold the propagation value"

: > load-prop.txt
for round in 1 2 3; do load_round load-prop.txt; done
sleep "$TRACE_WAIT"
counts=$(trace_pass load-prop.txt propagation)
pods_now > pods-final.txt
echo "$counts" > propagate-result.txt
