# Shared by reproduce.sh and fix.sh: the clock, starting a run, the load and the per-run report. Sourced, never run alone.
# One run = fresh orders and inventory pods with one RETRY_MODE, 110 s of open-loop load at 40 orders a second, and a
# 20 s inventory slowdown at second 20 that the load pod itself asks for. analyze.sh turns the three logs into counts.
# Runs alternate: naive-1, budget-1 and naive-2 in reproduce.sh, then budget-2, naive-3 and budget-3 in fix.sh.
export LC_ALL=C

start_clock() {   # seconds allowed, the result to print on failure. Every wait is capped so a result always prints.
  START=$(date +%s); LIMIT=$1; FAILED=$2; HERO_DONE=0
  trap '[ "$HERO_DONE" = 1 ] || { HERO_DONE=1; echo "HERO_RESULT: $FAILED"; }' EXIT
}
remaining() { echo $(( START + LIMIT - $(date +%s) )); }
cap() { local want=$1 reserve=$2 r; r=$(( $(remaining) - reserve )); [ "$r" -lt 5 ] && r=5; [ "$want" -lt "$r" ] && echo "$want" || echo "$r"; }
emit() { HERO_DONE=1; echo "HERO_RESULT: $1"; }
fail() { echo "FAIL: $*"; [ "$HERO_DONE" = 1 ] || emit "$FAILED"; exit 0; }

newest() { kubectl -n demo get pods -l "app=$1" --field-selector=status.phase=Running --sort-by=.metadata.creationTimestamp -o name | tail -1; }
why() { kubectl -n demo get pods -o wide | tail -6; kubectl -n demo get events --sort-by=.lastTimestamp | tail -8 | cut -c1-180; kubectl -n demo logs deploy/"$1" --tail=20 | cut -c1-200; }

# Each run has its own order numbers, so no log can mix runs: naive-1 from 1, budget-1 from 10001, naive-2 from 20001...
first_order() { case "$1" in naive-1) echo 1 ;; budget-1) echo 10001 ;; naive-2) echo 20001 ;; budget-2) echo 30001 ;; naive-3) echo 40001 ;; budget-3) echo 50001 ;; esac; }

# Fresh pods for both services with one retry mode, then proof of what each pod really runs.
start_run() {   # run label (naive-1, budget-2, ...), seconds allowed
  local mode=${1%-*}
  kubectl -n demo set env deployment/orders "RETRY_MODE=$mode" "LAB_RUN=$1" >/dev/null || fail "could not set orders"
  kubectl -n demo set env deployment/inventory "LAB_RUN=$1" >/dev/null || fail "could not set inventory"
  kubectl -n demo rollout status deploy/inventory --timeout="$(cap "$2" 300)s" >/dev/null || { why inventory; fail "inventory did not roll out"; }
  kubectl -n demo rollout status deploy/orders --timeout="$(cap "$2" 300)s" >/dev/null || { why orders; fail "orders did not roll out"; }
  local s p
  for s in inventory orders; do
    p=$(newest "$s")
    echo "$s $p image $(kubectl -n demo get "$p" -o jsonpath='{.spec.containers[0].image}')"
    kubectl -n demo exec "$p" -- env | grep -E '^(RETRY_MODE|THREADS|WORK_MS|INVENTORY_URL|OTEL_SERVICE_NAME|OTEL_RESOURCE_ATTRIBUTES)=' | sed 's/^/  /'
    kubectl -n demo exec "$p" -- sha256sum "/lab/app/$s.py" | sed 's/^/  /'
  done
  [ "$(kubectl -n demo exec "$(newest orders)" -- printenv RETRY_MODE)" = "$mode" ] || fail "orders is not in $mode mode"
  echo "orders retry settings: $(kubectl -n demo exec "$(newest orders)" -- sh -c 'grep -E "^(RETRIES|TIMEOUT|BASE, CAP|RATIO, MAX_TOKENS) " /lab/app/orders.py' | tr '\n' ';')"
  # What else could limit the services: inventory's server settings, the node's listen queue limit, and CPU limits (none set).
  echo "inventory server as it runs: $(kubectl -n demo logs "$(newest inventory)" | grep -m1 'INVENTORY server' || echo 'line missing'); node somaxconn $(kubectl -n demo exec "$(newest inventory)" -- cat /proc/sys/net/core/somaxconn)"
  echo "CPU limits on the two pods: $(kubectl -n demo get pods -l 'app in (orders,inventory)' -o jsonpath='{range .items[*]}{.metadata.labels.app}={.spec.containers[0].resources.limits.cpu} {end}')(empty means no limit)"
}

# The load pod sends 40 orders a second for 110 s and asks inventory for the slowdown at second 20.
load_run() {   # run label, seconds allowed
  kubectl -n demo delete pod load --ignore-not-found --wait=true >/dev/null 2>&1
  sed -e "s/LOAD_NODE/$LOAD_NODE/" -e "s/value: \"FIRST\"/value: \"$(first_order "$1")\"/" k8s/20-load.yaml | kubectl apply -f - >/dev/null || fail "could not start the load"
  kubectl -n demo wait pod/load --for=jsonpath='{.status.phase}'=Succeeded --timeout="$2s" >/dev/null || { kubectl -n demo logs load --tail=20; why orders; fail "the load did not finish"; }
  kubectl -n demo logs load > "load-$1.txt"
  kubectl -n demo logs "$(newest orders)" > "orders-$1.log" 2>/dev/null
  kubectl -n demo logs "$(newest inventory)" > "inventory-$1.log" 2>/dev/null
  grep -E '^(LOAD start|SLOWDOWN|LOAD end)' "load-$1.txt"
  grep -c '^LOAD second' "load-$1.txt" | sed 's/^/per-second lines: /'
}

# The lab's runner has no Python, so analyze.sh runs in a small python:3.12-slim pod on the load node, on copies of the logs.
analyzer() {
  kubectl -n demo get pod analyzer >/dev/null 2>&1 && return 0
  kubectl -n demo run analyzer --image=python:3.12-slim --restart=Never \
    --overrides="{\"spec\":{\"nodeName\":\"$LOAD_NODE\",\"enableServiceLinks\":false}}" -- sleep 86400 >/dev/null || fail "could not start the analyzer pod"
  kubectl -n demo wait pod/analyzer --for=condition=Ready --timeout=120s >/dev/null || fail "the analyzer pod did not start"
}

report() {   # run label. Sets before_pct, recovered and failed_after from analyze.sh's output.
  analyzer
  local f
  kubectl -n demo exec analyzer -- mkdir -p /work >/dev/null
  for f in analyze.sh "load-$1.txt" "orders-$1.log" "inventory-$1.log"; do kubectl -n demo cp "$f" "analyzer:/work/$f" >/dev/null || fail "could not copy $f"; done
  kubectl -n demo exec analyzer -- bash /work/analyze.sh "$1" "/work/load-$1.txt" "/work/orders-$1.log" "/work/inventory-$1.log" | tee "report-$1.txt"
  sed -n "s/^WINDOW $1 //p" "report-$1.txt" > "window-$1.txt"
  before_pct=$(sed -n "s/^HERO_MEASURE: orders_success_before_pct\[$1\]=//p" "report-$1.txt")
  recovered=$(sed -n "s/^HERO_MEASURE: recovery_after_slowdown\[$1\/recovered\]=//p" "report-$1.txt")
  failed_after=$(sed -n "s/^HERO_MEASURE: failed_orders_after_slowdown\[$1\]=//p" "report-$1.txt")
  awk -v p="${before_pct:-0}" 'BEGIN { exit !(p >= 95) }' || fail "the $1 run started unhealthy: ${before_pct:-no} % success before the slowdown"
  # The positive control for the slowdown itself: if no order failed during it, a recovery would prove nothing.
  [ "$(sed -n "s/^HERO_MEASURE: failed_orders_during_slowdown\[$1\]=//p" "report-$1.txt")" -gt 0 ] 2>/dev/null || fail "no order failed during the $1 slowdown, so it had no effect"
}
