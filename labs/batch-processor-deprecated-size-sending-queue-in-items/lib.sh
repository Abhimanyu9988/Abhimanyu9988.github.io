# Shared by reproduce.sh and fix.sh. Sourced, never run alone.
# A run = a fresh gateway with one config, the relay up, 50 s of load at 60 single-span requests a second, and a 30 s
# outage (the relay scaled to 0) from 10 s to 40 s into the load. A watcher reads the gateway's queue size, accepted and
# sent counts every 2 s. seconds_to_drain = from the relay's scale-up to the first of 3 reads in a row whose queue size is
# back at or below its highest read before the outage. After the load, the final counts wait until the queue reads 0 and sent hasn't moved
# for 3 reads in a row (6 s, longer than the 5 s retry cap), for 60 s at most. Runs: batch-1/2, migrated-1/2, sized-1/2. Nothing else differs.
# runs.txt holds each run's window ("<run> <load start> <drain end>"), and runs are at least 30 s apart, so Splunk is
# read per run with no overlap.
export LC_ALL=C
OUTAGE_AFTER=10 OUTAGE_S=30 DRAIN_MAX=60 GAP=30

start_run() {   # run label, seconds allowed
  local cfg=${1%-*} last
  last=$(awk 'END {print $3}' runs.txt 2>/dev/null)
  kubectl -n demo scale deploy/relay --replicas=1 >/dev/null || fail "could not start the relay"
  kubectl -n demo rollout status deploy/relay --timeout="$(cap "$2" 300)s" >/dev/null || { why relay; fail "the relay is not up"; }
  kubectl -n demo set env deployment/gateway "CONFIG=$cfg.yaml" "LAB_RUN=$1" >/dev/null || fail "could not set the gateway config"
  kubectl -n demo rollout status deploy/gateway --timeout="$(cap "$2" 300)s" >/dev/null || { why gateway; fail "the gateway did not roll out"; }
  local g; g=$(newest gateway)
  for i in $(seq 1 30); do kubectl -n demo logs "$g" 2>/dev/null | grep -q 'Everything is ready' && break; sleep 1; done
  echo "$1: gateway $(kubectl -n demo get "$g" -o jsonpath='{.spec.containers[0].image}') --config=/conf/$cfg.yaml ($(kubectl -n demo logs "$g" 2>/dev/null | grep -c 'Everything is ready') ready line)"
  # Keep the previous run's drain and this run's load at least GAP seconds apart, so their Splunk windows can't overlap.
  if [ -n "$last" ]; then local w=$(( last + GAP - $(date +%s) )); [ "$w" -gt 0 ] && sleep "$w"; fi
  return 0
}

outage_run() {   # run label
  kubectl -n demo wait pod/stats --for=condition=Ready --timeout=60s >/dev/null || fail "the stats pod is not ready"
  kubectl -n demo delete pod load --ignore-not-found --wait=true >/dev/null 2>&1
  kubectl -n demo exec stats -- python /stats/gwstats.py > "gw-$1-start.txt" || fail "could not read the gateway's metrics"
  local t_load t_down t_up sent prev same q
  sed -e "s/LOAD_NODE/$LOAD_NODE/" -e "s/LAB_ENV/$LAB_NAME/" -e "s/lab.run=\"RUN\"/lab.run=\"$1\"/" k8s/20-load.yaml |
    kubectl apply -f - >/dev/null || fail "could not start the load"
  kubectl -n demo wait pod/load --for=condition=Ready --timeout=60s >/dev/null || { why gateway; fail "the load did not start"; }
  t_load=$(date +%s)
  timeout 200 kubectl -n demo exec stats -- python /stats/gwstats.py watch 60 > "q-$1.txt" 2>/dev/null &
  local watcher=$!
  sleep "$OUTAGE_AFTER"
  kubectl -n demo scale deploy/relay --replicas=0 >/dev/null || fail "could not stop the relay"
  t_down=$(date +%s)
  kubectl -n demo wait pod -l app=relay --for=delete --timeout=25s >/dev/null 2>&1
  echo "$1: relay scaled to 0 at +$(( t_down - t_load ))s; relay endpoints now: [$(kubectl -n demo get endpoints relay -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)]"
  local w=$(( OUTAGE_S - ($(date +%s) - t_down) )); [ "$w" -gt 0 ] && sleep "$w"
  kubectl -n demo scale deploy/relay --replicas=1 >/dev/null || fail "could not restart the relay"
  t_up=$(date +%s)
  kubectl -n demo rollout status deploy/relay --timeout=60s >/dev/null || { why relay; fail "the relay did not come back"; }
  echo "$1: relay scaled to 1 at +$(( t_up - t_load ))s, ready at +$(( $(date +%s) - t_load ))s (outage $(( t_up - t_down ))s)"
  kubectl -n demo wait pod/load --for=jsonpath='{.status.phase}'=Succeeded --timeout=60s >/dev/null || { kubectl -n demo logs load --tail=10; fail "the load did not finish"; }
  kubectl -n demo logs load > "load-$1.txt" 2>/dev/null
  prev=-1 same=0
  for i in $(seq 1 $(( DRAIN_MAX / 2 ))); do
    kubectl -n demo exec stats -- python /stats/gwstats.py > "gw-$1-now.txt" 2>/dev/null
    q=$(sed -n 's/^GW queue_size=//p' "gw-$1-now.txt"); sent=$(sed -n 's/^GW sent=//p' "gw-$1-now.txt")
    if [ "${q:-1}" = 0 ] && [ "${sent:-x}" = "$prev" ]; then same=$(( same + 1 )); else same=0; fi
    prev=${sent:--1}
    [ "$same" -ge 2 ] && break
    sleep 2
  done
  # Complete only when the queue is empty, sent has stopped moving, and sent covers everything the exporter took. With the
  # batch processor (batch-*), spans that failed to enqueue were accepted first, so the target is accepted - enqueue failed.
  # Without it (migrated-*, sized-*), the queue's error goes back to the receiver and those spans are refused, never
  # accepted, so the target is accepted.
  drain_complete=0
  local a e
  a=$(sed -n 's/^GW accepted=//p' "gw-$1-now.txt"); e=$(sed -n 's/^GW enqueue_failed=//p' "gw-$1-now.txt")
  case "$1" in batch-*) drain_target=$(awk -v a="${a:-0}" -v e="${e:-0}" 'BEGIN { printf "%.0f", a - e }') ;; *) drain_target=$(awk -v a="${a:-0}" 'BEGIN { printf "%.0f", a }') ;; esac
  [ "$same" -ge 2 ] && awk -v s="${sent:-0}" -v t="$drain_target" 'BEGIN { exit !(s + 0 >= t + 0) }' && drain_complete=1
  echo "$1: drain $( [ "$drain_complete" = 1 ] && echo complete || echo "INCOMPLETE after ${DRAIN_MAX}s"): queue ${q:-?}, sent ${sent:-?}, target ${drain_target} (accepted ${a:-?}, enqueue failed ${e:-?}; rule for ${1%-*}: $(case "$1" in batch-*) echo 'accepted - enqueue failed' ;; *) echo accepted ;; esac))"
  kubectl -n demo exec stats -- python /stats/gwstats.py > "gw-$1-end.txt" || fail "could not read the gateway's metrics"
  kill "$watcher" 2>/dev/null; wait "$watcher" 2>/dev/null
  echo "$1 $t_load $(date +%s) $t_down $t_up" >> runs.txt
}

# Generic helpers: the clock and pods.
start_clock() {   # seconds allowed, the result to print on failure. Every wait is capped so a result always prints.
  START=$(date +%s); LIMIT=$1; FAILED=$2; HERO_DONE=0
  trap '[ "$HERO_DONE" = 1 ] || { HERO_DONE=1; echo "HERO_RESULT: $FAILED"; }' EXIT
}
remaining() { echo $(( START + LIMIT - $(date +%s) )); }
cap() { local want=$1 reserve=$2 r; r=$(( $(remaining) - reserve )); [ "$r" -lt 5 ] && r=5; [ "$want" -lt "$r" ] && echo "$want" || echo "$r"; }
emit() { HERO_DONE=1; echo "HERO_RESULT: $1"; }
fail() { echo "FAIL: $*"; [ "$HERO_DONE" = 1 ] || emit "$FAILED"; exit 0; }

newest() { kubectl -n demo get pods -l "app=$1" --field-selector=status.phase=Running --sort-by=.metadata.creationTimestamp -o name | tail -1; }
why() { kubectl -n demo get pods -o wide | tail -8; kubectl -n demo get events --sort-by=.lastTimestamp | tail -8 | cut -c1-180; kubectl -n demo logs deploy/"$1" --tail=20 2>/dev/null | cut -c1-200; }
