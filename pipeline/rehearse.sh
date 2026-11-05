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
export PATH="$home/bin:$PATH" LAB_NAME="rehearsal-${GITHUB_RUN_ID:-local}" AWS_REGION=eu-central-1

why() {
  kubectl get pods -A -o wide | cut -c1-130 | tail -25
  kubectl get events -A --sort-by=.lastTimestamp | tail -20 | cut -c1-190
  kubectl describe nodes | grep -A8 'Allocated resources' | cut -c1-90
}
step() {   # script, the result that passes
  echo "===== $1"
  bash "$lab/$1" 2>&1 | tee "$home/$1.log"
  grep -q "^HERO_RESULT: $2\$" "$home/$1.log" && return 0
  why
  echo "REHEARSAL FAILED: $1 didn't reach $2"
  exit 1
}
step reproduce.sh reproduced
step fix.sh fixed
bash "$lab/cleanup.sh"
echo "REHEARSAL PASSED"
