#!/usr/bin/env bash
# Remove everything Kubernetes created outside Terraform (ALB, target groups, SGs) and the Helm releases.
# One overall deadline keeps the script under 15 minutes; it never fails hard.
set -u
cd "$(dirname "$0")"

END=$((SECONDS + 780))
# cap N -> min(N, seconds left), at least 5
cap() {
  local left=$((END - SECONDS))
  [ "$left" -lt 5 ] && left=5
  [ "$1" -lt "$left" ] && echo "$1" || echo "$left"
}

CLUSTER="$(terraform output -raw cluster_name 2>/dev/null || true)"
[ -n "$CLUSTER" ] || CLUSTER="${LAB_NAME:-}"
[ -n "$CLUSTER" ] || { echo "no cluster name, nothing to clean"; exit 0; }

lb_arns() {
  for arn in $(aws elbv2 describe-load-balancers --query 'LoadBalancers[].LoadBalancerArn' --output text 2>/dev/null); do
    aws elbv2 describe-tags --resource-arns "$arn" \
      --query "TagDescriptions[?Tags[?Key=='elbv2.k8s.aws/cluster' && Value=='${CLUSTER}']].ResourceArn" --output text 2>/dev/null
  done | tr '\t' '\n' | grep -v '^$'
}
tg_arns() {
  for arn in $(aws elbv2 describe-target-groups --query 'TargetGroups[].TargetGroupArn' --output text 2>/dev/null); do
    aws elbv2 describe-tags --resource-arns "$arn" \
      --query "TagDescriptions[?Tags[?Key=='elbv2.k8s.aws/cluster' && Value=='${CLUSTER}']].ResourceArn" --output text 2>/dev/null
  done | tr '\t' '\n' | grep -v '^$'
}
sg_ids() {
  aws ec2 describe-security-groups --filters "Name=tag:elbv2.k8s.aws/cluster,Values=${CLUSTER}" \
    --query 'SecurityGroups[].GroupId' --output text 2>/dev/null | tr '\t' '\n' | grep -v '^$'
}

# wait_gone <function> <max seconds>: poll until the function prints nothing
wait_gone() {
  local fn="$1" stop=$((SECONDS + $(cap "$2")))
  while [ "$SECONDS" -lt "$stop" ]; do
    [ -z "$($fn)" ] && return 0
    sleep 10
  done
  [ -z "$($fn)" ]
}

# Remove rules in other security groups that reference $1, so $1 can be deleted.
revoke_references() {
  local sg="$1" other ids
  for other in $(aws ec2 describe-security-groups --filters "Name=ip-permission.group-id,Values=${sg}" \
      --query 'SecurityGroups[].GroupId' --output text 2>/dev/null); do
    ids="$(aws ec2 describe-security-group-rules --filters "Name=group-id,Values=${other}" \
      --query "SecurityGroupRules[?IsEgress==\`false\` && ReferencedGroupInfo.GroupId=='${sg}'].SecurityGroupRuleId" --output text 2>/dev/null)"
    [ -n "$ids" ] && [ "$ids" != "None" ] && {
      echo "revoking ingress rules in ${other} that reference ${sg}"
      aws ec2 revoke-security-group-ingress --group-id "$other" --security-group-rule-ids $ids >/dev/null 2>&1
    }
  done
  for other in $(aws ec2 describe-security-groups --filters "Name=egress.ip-permission.group-id,Values=${sg}" \
      --query 'SecurityGroups[].GroupId' --output text 2>/dev/null); do
    ids="$(aws ec2 describe-security-group-rules --filters "Name=group-id,Values=${other}" \
      --query "SecurityGroupRules[?IsEgress==\`true\` && ReferencedGroupInfo.GroupId=='${sg}'].SecurityGroupRuleId" --output text 2>/dev/null)"
    [ -n "$ids" ] && [ "$ids" != "None" ] && {
      echo "revoking egress rules in ${other} that reference ${sg}"
      aws ec2 revoke-security-group-egress --group-id "$other" --security-group-rule-ids $ids >/dev/null 2>&1
    }
  done
}

if aws eks update-kubeconfig --name "$CLUSTER" >/dev/null 2>&1 && kubectl get ns --request-timeout=20s >/dev/null 2>&1; then
  echo "== Deleting Ingresses and LoadBalancer Services (controller removes the ALB)"
  kubectl delete ingress --all -A --wait=true --timeout="$(cap 150)s" 2>&1 | tail -5
  kubectl get svc -A --no-headers 2>/dev/null | awk '$3=="LoadBalancer"{print $1" "$2}' | while read -r ns name; do
    kubectl -n "$ns" delete svc "$name" --wait=true --timeout="$(cap 60)s" 2>&1 | tail -2
  done
else
  echo "cluster not reachable, falling back to AWS CLI cleanup"
fi

echo "== Waiting for ALBs of ${CLUSTER} to disappear (controller cleanup)"
wait_gone lb_arns 90 || echo "ALBs still present, will force delete"

if command -v helm >/dev/null 2>&1; then
  echo "== Uninstalling Helm releases"
  helm uninstall splunk-otel-collector -n splunk --wait --timeout "$(cap 90)s" 2>&1 | tail -2
  helm uninstall aws-load-balancer-controller -n kube-system --wait --timeout "$(cap 90)s" 2>&1 | tail -2
fi

# ---- AWS CLI fallback: delete leftovers directly if the controller could not do it.
for arn in $(lb_arns); do
  echo "force deleting load balancer $arn"
  aws elbv2 delete-load-balancer --load-balancer-arn "$arn" 2>&1 | tail -1
done
echo "== Waiting until tagged load balancers are really gone"
wait_gone lb_arns 180 || echo "WARNING: load balancers still exist after waiting"

echo "== Deleting leftover target groups (retry while ALB teardown finishes)"
stop=$((SECONDS + $(cap 120)))
while [ "$SECONDS" -lt "$stop" ]; do
  left="$(tg_arns)"
  [ -z "$left" ] && break
  for arn in $left; do
    echo "force deleting target group $arn"
    aws elbv2 delete-target-group --target-group-arn "$arn" 2>&1 | tail -1
  done
  sleep 10
done

echo "== Deleting controller-managed security groups (retry on DependencyViolation)"
stop=$((SECONDS + $(cap 150)))
while [ "$SECONDS" -lt "$stop" ]; do
  left="$(sg_ids)"
  [ -z "$left" ] && break
  for sg in $left; do
    revoke_references "$sg"
    echo "deleting security group $sg"
    aws ec2 delete-security-group --group-id "$sg" 2>&1 | tail -1
  done
  sleep 10
done
[ -z "$(sg_ids)" ] || echo "WARNING: controller security groups still exist: $(sg_ids | tr '\n' ' ')"

echo "cleanup finished"
exit 0
