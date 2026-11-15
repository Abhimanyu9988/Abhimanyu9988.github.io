# The clock, fail and emit, and the queue's address. Sourced by lib.sh and check.sh, never run on its own.

# The clock and the end of a script: every wait is capped so the script always prints its result inside the 14 minutes.
start_clock() {   # seconds allowed, the result to print on failure
  START=$(date +%s)
  LIMIT=$1
  FAILED=$2
  HERO_DONE=0
  trap '[ "$HERO_DONE" = 1 ] || { HERO_DONE=1; echo "HERO_RESULT: $FAILED"; }' EXIT
}
remaining() { echo $(( START + LIMIT - $(date +%s) )); }
cap() { local want=$1 reserve=$2 r; r=$(( $(remaining) - reserve )); [ "$r" -lt 5 ] && r=5; [ "$want" -lt "$r" ] && echo "$want" || echo "$r"; }
emit() { HERO_DONE=1; echo "HERO_RESULT: $1"; }
fail() { echo "FAIL: $*"; [ "$HERO_DONE" = 1 ] || emit "$FAILED"; exit 0; }

standin() { [[ "${LAB_NAME:-}" == rehearsal-* ]]; }    # true only in the free rehearsal, where ElasticMQ plays SQS

queue_settings() {
  if standin; then
    QUEUE_NAME=lab-orders
    QUEUE_URL=http://sqs:9324/000000000000/lab-orders
  else
    QUEUE_NAME=$(terraform output -raw queue_name)
    QUEUE_URL=$(terraform output -raw queue_url)
  fi
}

# The newest running pod of a service, and what to print when one won't start.
newest() { kubectl -n demo get pods -l "app=$1" --field-selector=status.phase=Running --sort-by=.metadata.creationTimestamp -o name | tail -1; }
why() { kubectl -n demo get pods -o wide | tail -8; kubectl -n demo get events --sort-by=.lastTimestamp | tail -10 | cut -c1-180; kubectl -n demo logs deploy/"$1" -c app --tail=25 | cut -c1-220; }
