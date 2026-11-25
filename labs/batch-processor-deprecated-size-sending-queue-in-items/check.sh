#!/usr/bin/env bash
# The free check, on a GitHub runner with Docker, before AWS is paid for. It runs the lab's real parts: the gateway image
# with all three real configs from k8s/30-gateway.yaml, the relay's real config, the real telemetrygen image and flags from
# k8s/20-load.yaml, and the real stats script. Only the relay's exporter changes: it writes to a file instead of the
# Splunk agent. Each config gets a short version of a run: 30 s of load, the relay stopped for 15 s from 5 s in.
# It proves, before AWS: every config is valid on contrib 0.165.0, telemetrygen takes the lab's flags and sends single-span
# requests of 2 spans a trace (checked against the gateway), the stats script reads the receiver, exporter and queue metrics, and the relay passes spans on once it's back.
# What each config refuses or drops is printed, not asserted: that is the lab's finding.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p c/conf c/stats c/out
chmod 777 c/out

python3 - <<'PY'
import re
def block(path, key):
    text = open(path).read()
    m = re.search(rf"^  {re.escape(key)}: \|\n((?:    .*\n|\n)+)", text, re.M)
    return "\n".join(line[4:] for line in m[1].splitlines()) + "\n"
for k in ("batch.yaml", "migrated.yaml", "sized.yaml"):
    conf = block("k8s/30-gateway.yaml", k)
    assert "endpoint: http://relay:4318" in conf, k
    open(f"c/conf/{k}", "w").write(conf)
relay = block("k8s/30-gateway.yaml", "relay.yaml")
open("c/conf/relay-real.yaml", "w").write(relay)
# The one change from the cluster: the relay writes what it would send to the Splunk agent into a file.
relay = relay.replace("otlphttp:\n    endpoint: http://${env:HOST_IP}:4318", "file:\n    path: /out/spans.json")
relay = relay.replace("exporters: [otlphttp]", "exporters: [file]")
assert "otlphttp" not in relay and "/out/spans.json" in relay
open("c/conf/relay.yaml", "w").write(relay)
open("c/stats/gwstats.py", "w").write(block("k8s/30-gateway.yaml", "gwstats.py"))
load = open("k8s/20-load.yaml").read()
args = re.findall(r"^        - (.+)$", load, re.M)
assert args[0] == "traces" and "--batch=false" in args and not any(a.startswith("--child-spans") for a in args), args
args = [a.replace("gateway.demo.svc.cluster.local", "gateway").replace("--duration=50s", "--duration=30s")
         .replace("LAB_ENV", "check").replace('"RUN"', '"check"') for a in args]
open("c/load-args.txt", "w").write("\n".join(args) + "\n")
PY
GATEWAY=$(grep -m1 -oE 'otel/opentelemetry-collector-contrib:[0-9.]+' k8s/30-gateway.yaml)
LOADGEN=$(grep -m1 -oE 'ghcr.io/[^ ]+telemetrygen:v[0-9.]+' k8s/20-load.yaml)
PY=$(grep -m1 -oE 'python:[^ "]+' k8s/30-gateway.yaml)
for img in "$GATEWAY" "$LOADGEN" "$PY"; do
  for i in 1 2 3; do docker pull -q "$img" >/dev/null && break; sleep 10; done
done
fail() { echo "CHECK FAILED: $*"; exit 1; }

echo "== Every config is valid on $GATEWAY"
for k in batch.yaml migrated.yaml sized.yaml relay-real.yaml relay.yaml; do
  docker run --rm -e HOST_IP=127.0.0.1 -v "$PWD/c/conf:/conf:ro" "$GATEWAY" validate "--config=/conf/$k" || fail "$k is not valid"
  echo "valid: $k"
done
docker network create lab >/dev/null

run() {   # config
  local cfg=$1
  echo "== ${cfg%.yaml}: 30 s of load, the relay stopped from 5 s to 20 s"
  rm -f c/out/spans.json
  docker rm -f gateway relay load >/dev/null 2>&1 || true
  docker run -d --name relay --network lab -v "$PWD/c/conf:/conf:ro" -v "$PWD/c/out:/out" "$GATEWAY" --config=/conf/relay.yaml >/dev/null
  docker run -d --name gateway --network lab -v "$PWD/c/conf:/conf:ro" "$GATEWAY" "--config=/conf/$cfg" >/dev/null
  for n in gateway relay; do
    for i in $(seq 1 30); do docker logs "$n" 2>&1 | grep -q 'Everything is ready' && break; sleep 1; done
    docker logs "$n" 2>&1 | grep -q 'Everything is ready' || { docker logs "$n" 2>&1 | tail -20; fail "$n didn't start with $cfg"; }
  done
  docker run -d --name load --network lab "$LOADGEN" $(tr '\n' ' ' < c/load-args.txt) >/dev/null
  sleep 5
  docker stop -t 2 relay >/dev/null
  local peak=0 cap=0 q
  for i in $(seq 1 7); do
    q=$(docker run --rm --network lab -v "$PWD/c/stats:/stats:ro" "$PY" python /stats/gwstats.py | sed -n 's/^GW queue_size=//p;s/^GW queue_capacity=/C /p' | tr '\n' ' ')
    set -- $q; [ "${1%.*}" -gt "$peak" ] && peak=${1%.*}; cap=${3:-0}
    sleep 1
  done
  docker start relay >/dev/null
  for i in $(seq 1 30); do [ "$(docker inspect -f '{{.State.Running}}' load 2>/dev/null)" = true ] || break; sleep 1; done
  sleep 15   # the drain
  docker run --rm --network lab -v "$PWD/c/stats:/stats:ro" "$PY" python /stats/gwstats.py | tee "c/gw-${cfg%.yaml}.txt"
  docker logs load > "c/load-${cfg%.yaml}.txt" 2>&1
  echo "load log: $(grep 'traces generated' "c/load-${cfg%.yaml}.txt" | cut -c1-160 | tr '\n' ' ')"
  local relayed; relayed=$(python3 -c "
import json; n=0
for l in open('c/out/spans.json'):
    for rs in json.loads(l).get('resourceSpans', []):
        for ss in rs.get('scopeSpans', []): n += len(ss.get('spans', []))
print(n)" 2>/dev/null || echo 0)
  echo "CHECK ${cfg%.yaml}: queue peak $peak of capacity $cap; spans the relay wrote after it was back: $relayed"
  echo "$peak $cap $relayed" > "c/result-${cfg%.yaml}.txt"
  docker rm -f load gateway relay >/dev/null 2>&1 || true
}

for cfg in batch.yaml migrated.yaml sized.yaml; do run "$cfg"; done
for r in batch migrated sized; do
  read -r peak cap relayed < "c/result-$r.txt"
  grep -q '^GW accepted=[1-9]' "c/gw-$r.txt" || fail "$r: the stats script read no accepted spans"
  grep -q '^GW queue_capacity=[1-9]' "c/gw-$r.txt" || fail "$r: the stats script read no queue capacity"
  grep -q '^GW sent=[1-9]' "c/gw-$r.txt" || fail "$r: the gateway sent nothing after the relay came back"
  [ "$relayed" -gt 0 ] || fail "$r: the relay wrote no spans"
  [ "$(grep -c 'traces generated' "c/load-$r.txt")" = 4 ] || fail "$r: telemetrygen did not log a count for each of its 4 workers"
  gen=$(grep 'traces generated' "c/load-$r.txt" | grep -oE '"traces": ?[0-9]+|traces=[0-9]+' | grep -oE '[0-9]+$' | awk '{t += 2 * $1} END {print t + 0}')
  [ "$gen" -gt 0 ] || fail "$r: could not read the sender's count"
  # The lab counts 2 spans a trace (a parent and telemetrygen's default one child). With nothing refused or dropped, the
  # gateway must have accepted exactly that many, which proves the factor.
  if grep -q '^GW refused=0$' "c/gw-$r.txt" && grep -q '^GW enqueue_failed=0$' "c/gw-$r.txt"; then
    grep -q "^GW accepted=$gen\$" "c/gw-$r.txt" || fail "$r: the gateway accepted $(sed -n 's/^GW accepted=//p' "c/gw-$r.txt") spans, not 2 x traces = $gen"
  fi
  echo "$r: generated $gen; $(grep -E '^GW (accepted|refused|enqueue_failed|sent|send_failed|queue_capacity)=' "c/gw-$r.txt" | tr '\n' ' ') (printed, not asserted)"
done
echo "CHECK PASSED"
