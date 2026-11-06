#!/usr/bin/env bash
# The second value, in its own script with its own time limit. fix.sh starts it under `timeout`, while the first case waits.
# It sets the value, runs one load round and saves what OBI exported. fix.sh then reads the traces TRACE_WAIT after the round.
# It writes propagate-result.txt only when every step finished, so anything else is reported as -1, not run.
# Its other jobs: `read <label>` reads one case's traces, the same way for both, and `own <label>` looks up each service's server spans.
# LIMIT 170s: helm and rollout 65, load round 35, snapshot 10, which leaves about 60 spare.
set -uo pipefail
cd "$(dirname "$0")"

START=$(date +%s)
LIMIT=170
remaining() { echo $(( START + LIMIT - $(date +%s) )); }
cap() { local want=$1 reserve=$2 r; r=$(( $(remaining) - reserve )); [ "$r" -lt 5 ] && r=5; [ "$want" -lt "$r" ] && echo "$want" || echo "$r"; }
fail() { echo "FAIL in propagate.sh: $*"; exit 0; }

source ./lib.sh

api_get() {   # path, file. Saves the body and prints "status seconds", or 000 for a timeout
  curl -sS --max-time 8 -o "$2" -w '%{http_code} %{time_total}' -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API$1" 2>/dev/null || true
}

one_trace() {   # trace ID, folder, pass. Saves every span of a trace holding orders or inventory, from all its segments, or logs why not
  local f="$2/$1" raw n a b seg
  a=$(api_get "/v2/apm/trace/$1/latest" "$f.latest")
  b=$(api_get "/v2/apm/trace/$1/segments" "$f.seg")
  raw=$({ jq -c '.[]?' "$f.latest"
          for seg in $(jq -r '.[]?' "$f.seg"); do curl -sS --max-time 8 -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/apm/trace/$1/$seg" | jq -c '.[]?'; done
        } 2>/dev/null | jq -s '[.[] | select(type == "object" and .spanId != null)] | unique_by(.spanId)' 2>/dev/null)
  echo "${a#* } ${b#* }" >> "$2/times"
  n=$(jq -r --arg a "$APPS" '[.[] | .serviceName // empty | select(test($a))] | unique | length' <<<"$raw" 2>/dev/null)
  if [ "${n:-0}" -gt 0 ]; then echo "$raw" > "$f.json"; echo "$n" > "$f.count"
  else echo "pass $3 latest ${a%% *}, $(wc -c < "$f.latest" 2>/dev/null) bytes, segments ${b%% *}" >> "$2/misses"; fi
}

read_ids() {   # folder, pass, ID file. Eight at a time, stopping after 40 seconds
  local id i=0 from
  from=$(date +%s)
  for id in $(cat "$3"); do
    [ $(( $(date +%s) - from )) -lt 40 ] || break
    [ -e "$1/$id.count" ] && continue
    one_trace "$id" "$1" "$2" &
    i=$(( i + 1 ))
    [ $(( i % 8 )) -eq 0 ] && wait
  done
  wait
  echo "$1 pass $2, oldest first, F found: $(while read -r id; do [ -e "$1/$id.count" ] && printf F || printf .; done < "$3")" >&2
}

trace_pass() {   # label. 30 evenly spaced orders traces, then each miss once more 15s later, the same in both cases.
  # Prints "traces found, traces joined, IDs searched", the misses, and one sample trace.
  local dir="traces-$1" found joined sample n step
  rm -rf "$dir"; mkdir -p "$dir"
  n=$(grep -c . "ids-$1.txt")
  step=$(( n > 30 ? n / 30 : 1 ))
  awk -v s="$step" 'NR % s == 0' "ids-$1.txt" | head -30 > "sample-$1.txt"
  read_ids "$dir" 1 "sample-$1.txt"
  if [ "$(ls "$dir" | grep -c '\.count$')" -lt "$(grep -c . "sample-$1.txt")" ]; then
    sleep 15
    read_ids "$dir" 2 "sample-$1.txt"
  fi
  { sort "$dir/misses" 2>/dev/null | uniq -c | sed "s/^/$1 misses: /"
    echo "$1 lookup seconds, slowest: $(tr ' ' '\n' < "$dir/times" 2>/dev/null | grep . | sort -n | tail -1), at the 8s limit: $(grep -c '8\.[0-9]' "$dir/times" 2>/dev/null)"; } >&2
  found=$(ls "$dir" | grep -c '\.count$' || true)
  joined=$(cat "$dir"/*.count 2>/dev/null | grep -c '^2$' || true)
  sample=$(grep -l '^2$' "$dir"/*.count 2>/dev/null | head -1)
  [ -n "$sample" ] || sample=$(ls "$dir"/*.count 2>/dev/null | head -1)
  if [ -n "$sample" ]; then
    echo "sample trace from the $1 case: $(basename "$sample" .count), $(jq length "${sample%.count}.json") spans in $(jq length "${sample%.count}.seg") segments" >&2
    jq -r '.[] | "  \(.serviceName) \(.tags."span.kind" // "?") \(.operationName) span=\(.spanId) parent=\(.parentId // "none")"' "${sample%.count}.json" 2>/dev/null | head -12 >&2
  else
    echo "sample trace from the $1 case: none found" >&2
  fi
  echo "${found:-0} ${joined:-0} $(grep -c . "sample-$1.txt")"
}

own_evidence() {   # label. Reads 5 evenly spaced traces per service, and lists each service Splunk returned a server span for
  local svc dir n have
  : > "own-$1.txt"
  for svc in orders inventory; do
    dir="own-$1-$svc"; rm -rf "$dir"; mkdir -p "$dir"
    n=$(grep -c . "$svc-$1.txt")
    awk -v s=$(( n > 5 ? n / 5 : 1 )) 'NR % s == 0' "$svc-$1.txt" | head -5 > "$dir.ids"
    read_ids "$dir" 1 "$dir.ids"
    have=$(cat "$dir"/*.json 2>/dev/null | jq -s --arg s "$svc" '[.[][] | select(.serviceName == $s and .tags."span.kind" == "server")] | length')
    echo "$svc server spans returned by Splunk: ${have:-0}, in $(ls "$dir" | grep -c '\.count$') traces" >&2
    [ "${have:-0}" -gt 0 ] && echo "$svc" >> "own-$1.txt"
  done
}

[ "${1:-}" = read ] && { trace_pass "$2"; exit 0; }
[ "${1:-}" = own ] && { own_evidence "$2"; exit 0; }

rm -f propagate-result.txt

echo "helm upgrade --reuse-values --set-string obi.env.OTEL_EBPF_BPF_CONTEXT_PROPAGATION=headers"
helm upgrade splunk-otel-collector $CHART --namespace splunk --reuse-values --set-string obi.env.OTEL_EBPF_BPF_CONTEXT_PROPAGATION=headers >/dev/null || fail "helm upgrade failed"
obi_ready 60 20
obi_settings
obi_log | grep -iE 'propagation' | cut -c1-200 | tail -4
kubectl -n splunk get pods -l app.kubernetes.io/name=obi -o json | jq -e '[.items[].spec.containers[0].env[] | select(.name == "OTEL_EBPF_BPF_CONTEXT_PROPAGATION" and .value == "headers")] | length >= 2' >/dev/null || fail "the running OBI pods do not hold the propagation value"

: > load-prop.txt
prop_start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
first=$(date +%s)
load_round load-prop.txt
ended=$(date +%s)
tap_snapshot propagation "$prop_start"
echo "$first $ended" > propagate-result.txt
