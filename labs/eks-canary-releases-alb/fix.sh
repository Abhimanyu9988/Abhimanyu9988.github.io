#!/usr/bin/env bash
# BUILD: AWS Load Balancer Controller (Pod Identity) + weighted ALB forward action, then 90/10 -> 50/50 -> rollback.
set -uo pipefail
CLUSTER="$(terraform output -raw cluster_name)"
VPC_ID="$(terraform output -raw vpc_id)"
aws eks update-kubeconfig --name "$CLUSTER" --region "$AWS_REGION" >/dev/null
export AWS_PAGER=""

# Global budget: 14 min in total. The last 3 min are reserved for the proof stages, so the
# install/provisioning waits are capped to what is left before that reserve.
GLOBAL_END=$((SECONDS+840))
PROOF_RESERVE=180
cap_pre() { # min($1, time left before the proof reserve), at least 1
  local r=$((GLOBAL_END-PROOF_RESERVE-SECONDS))
  [ "$r" -gt "$1" ] && r=$1
  [ "$r" -lt 1 ] && r=1
  echo "$r"
}
cap_all() { # min($1, time left overall), at least 1
  local r=$((GLOBAL_END-SECONDS))
  [ "$r" -gt "$1" ] && r=$1
  [ "$r" -lt 1 ] && r=1
  echo "$r"
}

die() { echo "ERROR: $*"; echo "HERO_RESULT: not_fixed"; exit 0; }

echo "== 1. Install AWS Load Balancer Controller chart 1.11.0 (controller v2.11.0), credentials via Pod Identity"
kubectl -n kube-system rollout status ds/eks-pod-identity-agent --timeout="$(cap_pre 120)s" || die "pod identity agent not ready"
aws eks list-pod-identity-associations --cluster-name "$CLUSTER" --region "$AWS_REGION" \
  --query 'associations[].{ns:namespace,sa:serviceAccount}' --output table
timeout 60 helm repo add eks https://aws.github.io/eks-charts >/dev/null 2>&1
timeout 60 helm repo update >/dev/null 2>&1
HELM_T="$(cap_pre 240)"
timeout $((HELM_T+30)) helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
  --version 1.11.0 -n kube-system \
  --set clusterName="$CLUSTER" \
  --set region="$AWS_REGION" \
  --set vpcId="$VPC_ID" \
  --set serviceAccount.create=true \
  --set serviceAccount.name=aws-load-balancer-controller \
  --set replicaCount=1 \
  --set enableShield=false --set enableWaf=false --set enableWafv2=false \
  --wait --timeout "${HELM_T}s" || die "helm install failed"

echo "Pod Identity env injected into the controller pod:"
kubectl --request-timeout=20s -n kube-system get pod -l app.kubernetes.io/name=aws-load-balancer-controller \
  -o jsonpath='{.items[0].spec.containers[0].env[*].name}' | tr ' ' '\n' | grep AWS_CONTAINER | sed 's/^/  /'

echo
echo "== 2. Create the Ingress with a weighted forward action (v1=90, v2=10)"
kubectl --request-timeout=30s apply -f k8s/ingress.yaml || die "ingress apply failed"
ALB=""
deadline=$((SECONDS+$(cap_pre 240)))
while [ $SECONDS -lt $deadline ]; do
  ALB=$(kubectl --request-timeout=15s -n demo get ingress app -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)
  [ -n "$ALB" ] && break
  sleep 10
done
[ -n "$ALB" ] || { kubectl --request-timeout=20s -n demo describe ingress app | tail -n 20; kubectl --request-timeout=20s -n kube-system logs deploy/aws-load-balancer-controller --tail=20; die "no ALB hostname"; }
echo "ALB: http://$ALB"

LB_ARN=$(aws elbv2 describe-load-balancers --region "$AWS_REGION" --query "LoadBalancers[?DNSName=='$ALB'].LoadBalancerArn | [0]" --output text)
LISTENER=$(aws elbv2 describe-listeners --load-balancer-arn "$LB_ARN" --region "$AWS_REGION" --query 'Listeners[0].ListenerArn' --output text)

rule_weights() {
  aws elbv2 describe-rules --listener-arn "$LISTENER" --region "$AWS_REGION" --output json 2>/dev/null \
    | jq -r '[.Rules | map(select(.IsDefault==false)) | first | .Actions[0].ForwardConfig.TargetGroups[] | .Weight] | sort | map(tostring) | join(" ")' 2>/dev/null
}

show_rule() {
  aws elbv2 describe-rules --listener-arn "$LISTENER" --region "$AWS_REGION" --output json \
    | jq -r '.Rules | map(select(.IsDefault==false)) | first | .Actions[0].ForwardConfig.TargetGroups[] | "  target group \(.TargetGroupArn | split("/")[1])  weight=\(.Weight)"'
}

N=200
V1=0; V2=0; OTHER=0
tally() { # $1 = number of requests; sets V1 V2 OTHER
  local out
  out=$(seq 1 "$1" | xargs -P 20 -I{} sh -c 'r=$(curl -s --max-time 5 "http://$0/"); echo "${r:-no-response}" | head -n1' "$ALB")
  V1=$(grep -c '^version-v1$' <<<"$out" || true)
  V2=$(grep -c '^version-v2$' <<<"$out" || true)
  OTHER=$(( $1 - V1 - V2 ))
}
report() { echo "  $1: v1=$V1 v2=$V2 other=$OTHER of $N  => v1 $((100*V1/N))% / v2 $((100*V2/N))%"; }
loop20() { for i in $(seq 1 20); do curl -s --max-time 5 "http://$ALB/" | head -n1; done | sed 's/version-//' | tr '\n' ' '; echo; }

set_weights() { # $1 = v1 weight, $2 = v2 weight
  local json
  json="{\"type\":\"forward\",\"forwardConfig\":{\"targetGroups\":[{\"serviceName\":\"app-v1\",\"servicePort\":80,\"weight\":$1},{\"serviceName\":\"app-v2\",\"servicePort\":80,\"weight\":$2}]}}"
  kubectl --request-timeout=30s -n demo annotate ingress app --overwrite "alb.ingress.kubernetes.io/actions.weighted=$json" >/dev/null
}

wait_rule() { # wait until the ALB listener rule carries the wanted weights (max 60s)
  local want end
  want=$(printf '%s\n' "$1" "$2" | sort -n | paste -sd' ' -)
  end=$((SECONDS+$(cap_all 60)))
  while [ $SECONDS -lt $end ]; do
    [ "$(rule_weights)" = "$want" ] && return 0
    sleep 1
  done
  return 1
}

echo "Waiting for the ALB to become active and both target groups to be healthy..."
deadline=$((SECONDS+$(cap_pre 420)))
ok=0
while [ $SECONDS -lt $deadline ]; do
  tally 40
  if [ "$V1" -gt 0 ] && [ "$V2" -gt 0 ]; then ok=1; break; fi
  sleep 10
done
[ "$ok" = 1 ] || { kubectl --request-timeout=20s -n kube-system logs deploy/aws-load-balancer-controller --tail=30; die "ALB never served both versions"; }

echo
echo "ALB listener rule created by the controller:"
show_rule
kubectl --request-timeout=20s -n kube-system logs deploy/aws-load-balancer-controller 2>/dev/null | grep -i "successfully deployed model" | tail -n 1 | cut -c1-200

echo
echo "== 3. Stage A: weights 90/10 (canary takes 10%)"
echo "  first 20 responses: $(loop20)"
tally $N; report "90/10"; A1=$V1; A2=$V2; AO=$OTHER

echo
echo "== 4. Stage B: kubectl annotate -> 50/50"
set_weights 50 50
wait_rule 50 50 || die "rule did not update to 50/50"
sleep 5
show_rule
echo "  first 20 responses: $(loop20)"
tally $N; report "50/50"; B1=$V1; B2=$V2; BO=$OTHER

echo
echo "== 5. Stage C: errors rise on v2 -> ROLLBACK, v1=100 v2=0 (no redeploy)"
T0=$SECONDS
set_weights 100 0
wait_rule 100 0 || die "rule did not update to 100/0"
T_RULE=$((SECONDS-T0))
C_OK=0
end=$((SECONDS+$(cap_all 45)))
while [ $SECONDS -lt $end ]; do
  tally 50
  if [ "$V2" -eq 0 ] && [ "$V1" -eq 50 ]; then C_OK=1; break; fi
  sleep 1
done
T_ALL=$((SECONDS-T0))
echo "  ALB rule updated ${T_RULE}s after kubectl annotate; 100% of sampled traffic on v1 after ${T_ALL}s"
show_rule
echo "  first 20 responses: $(loop20)"
tally $N; report "100/0"; C1=$V1; C2=$V2; CO=$OTHER

echo
echo "== Summary (requests per stage: $N)"
echo "  90/10  -> v1=$A1 v2=$A2 other=$AO"
echo "  50/50  -> v1=$B1 v2=$B2 other=$BO"
echo "  100/0  -> v1=$C1 v2=$C2 other=$CO   (rollback in ${T_ALL}s, pods untouched)"
kubectl --request-timeout=20s -n demo get pods -o wide --no-headers | awk '{print "  pod " $1 " " $3 " restarts=" $4 " age=" $5}'

if [ "$A2" -ge 3 ] && [ "$A2" -le 50 ] && [ "$A1" -gt "$A2" ] \
   && [ "$B2" -ge 70 ] && [ "$B2" -le 130 ] \
   && [ "$C_OK" = 1 ] && [ "$C2" -eq 0 ] && [ "$C1" -eq "$N" ]; then
  echo "HERO_RESULT: fixed"
else
  echo "HERO_RESULT: not_fixed"
fi
