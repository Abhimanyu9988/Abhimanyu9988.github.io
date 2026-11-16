#!/usr/bin/env bash
# Runs a lab's real reproduce.sh and fix.sh on a small cluster on a GitHub runner, before the lab pays for AWS.
# Each node gets the CPU of a t3.large, so a pod that wouldn't fit on a real node doesn't fit here either.
# Only cluster behaviour is tested. Terraform and EKS are not, and `aws` and `terraform` are stand-ins here.
set -uo pipefail
lab=$(cd "$1" && pwd)
home=$(mktemp -d)
ALLOCATABLE_M=1930                      # what a t3.large offers to pods on EKS
reserved=$(( $(nproc) * 1000 - ALLOCATABLE_M ))

cat > "$home/kind.yaml" <<YAML
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
kubeadmConfigPatches:
  - |
    kind: KubeletConfiguration
    systemReserved:
      cpu: "${reserved}m"
nodes:
  - role: control-plane
  - role: worker
YAML

kind create cluster --name lab --config "$home/kind.yaml" --wait 180s || { echo "REHEARSAL FAILED: no cluster"; exit 1; }
kubectl get nodes -o custom-columns='NAME:.metadata.name,CPU:.status.allocatable.cpu,MEMORY:.status.allocatable.memory'

mkdir "$home/bin"
printf '#!/bin/sh\nexit 0\n' > "$home/bin/aws"
printf '#!/bin/sh\necho "$LAB_NAME"\n' > "$home/bin/terraform"
chmod +x "$home/bin/aws" "$home/bin/terraform"
# The paid lab runs in pipeline/lab.Dockerfile, which has none of these. The runner here has them, so a lab that calls one
# passes the rehearsal and then fails on AWS (issue 203 lost a paid run to python3). Each is replaced by a stub that fails
# and leaves a note, so even a call hidden behind `|| true` stops the rehearsal.
MISSING_ON_LAB_RUNNER="python python3 pip pip3 node npm npx yq bc envsubst docker wget ruby java go nc dig zip"
for t in $MISSING_ON_LAB_RUNNER; do
  printf '#!/bin/sh\necho "%s is not on the lab runner (pipeline/lab.Dockerfile); run it in a pod or use bash, jq or awk" >&2\necho %s >> "%s/missing-tools"\nexit 127\n' "$t" "$t" "$home" > "$home/bin/$t"
  chmod +x "$home/bin/$t"
done
export PATH="$home/bin:$PATH" LAB_NAME="rehearsal-${GITHUB_RUN_ID:-local}" AWS_REGION=eu-central-1
# The paid run keeps only the last 12,000 characters of each step as evidence (lab.py tail()), and EKS prints a little more
# than this cluster does, so a step that prints more than this here loses its first runs from the evidence.
EVIDENCE_CHARS=11000

why() {
  kubectl get pods -A -o wide | cut -c1-130 | tail -25
  kubectl get events -A --sort-by=.lastTimestamp | tail -20 | cut -c1-190
  kubectl describe nodes | grep -A8 'Allocated resources' | cut -c1-90
}
step() {   # script, the result that passes
  echo "===== $1"
  bash "$lab/$1" 2>&1 | tee "$home/$1.log"
  if [ -s "$home/missing-tools" ]; then
    echo "REHEARSAL FAILED: $1 called $(sort -u "$home/missing-tools" | tr '\n' ' ')which the lab runner doesn't have"
    exit 1
  fi
  local size
  size=$(wc -c < "$home/$1.log")
  echo "$1 printed $size characters (the evidence keeps the last 12000; the limit here is $EVIDENCE_CHARS)"
  if [ "$size" -gt "$EVIDENCE_CHARS" ]; then
    echo "REHEARSAL FAILED: $1 printed $size characters, so the paid run's evidence would lose its start. Print less."
    exit 1
  fi
  grep -q "^HERO_RESULT: $2\$" "$home/$1.log" && return 0
  why
  echo "REHEARSAL FAILED: $1 didn't reach $2"
  exit 1
}
step reproduce.sh reproduced
step fix.sh fixed
bash "$lab/cleanup.sh"
[ -s "$home/missing-tools" ] && { echo "REHEARSAL FAILED: cleanup.sh called $(sort -u "$home/missing-tools" | tr '\n' ' ')which the lab runner doesn't have"; exit 1; }
echo "REHEARSAL PASSED"
