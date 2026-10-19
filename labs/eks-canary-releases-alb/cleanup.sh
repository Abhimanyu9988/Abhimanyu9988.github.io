#!/usr/bin/env bash
# Remove the ALB, target groups and security groups the controller created (Terraform does not know them).
# Never fails hard and always stays inside a global 14 minute budget.
set +e
export AWS_PAGER=""
REGION="${AWS_REGION:-eu-central-1}"
END=$((SECONDS+840))
cap() { # min($1, time left), at least 1
  local r=$((END-SECONDS))
  [ "$r" -gt "$1" ] && r=$1
  [ "$r" -lt 1 ] && r=1
  echo "$r"
}
CLUSTER="$(terraform output -raw cluster_name 2>/dev/null)"
VPC="$(terraform output -raw vpc_id 2>/dev/null)"

if [ -n "$CLUSTER" ] && aws eks update-kubeconfig --name "$CLUSTER" --region "$REGION" >/dev/null 2>&1; then
  echo "Deleting Ingresses and LoadBalancer Services (controller removes the ALB)..."
  t=$(cap 240)
  timeout "$t" kubectl --request-timeout=30s delete ingress --all -A --wait=true --timeout="${t}s"
  timeout 60 kubectl --request-timeout=30s get svc -A --no-headers 2>/dev/null | awk '$3=="LoadBalancer"{print $1" "$2}' | while read -r ns name; do
    t=$(cap 100)
    timeout "$t" kubectl --request-timeout=30s -n "$ns" delete svc "$name" --wait=true --timeout="${t}s"
  done
  t=$(cap 60)
  timeout "$t" kubectl --request-timeout=30s delete ns demo --wait=true --timeout="${t}s"
fi

[ -n "$VPC" ] || { echo "No VPC in state, nothing more to clean."; exit 0; }

list_lbs() { aws elbv2 describe-load-balancers --region "$REGION" --query "LoadBalancers[?VpcId=='$VPC'].LoadBalancerArn" --output text 2>/dev/null; }

echo "Waiting for load balancers in $VPC to disappear..."
for i in $(seq 1 12); do
  [ -z "$(list_lbs)" ] && break
  [ $SECONDS -ge $END ] && break
  sleep 10
done

for arn in $(list_lbs); do
  echo "Force-deleting leftover load balancer $arn"
  aws elbv2 delete-load-balancer --load-balancer-arn "$arn" --region "$REGION"
done
for i in $(seq 1 6); do
  [ -z "$(list_lbs)" ] && break
  [ $SECONDS -ge $END ] && break
  sleep 10
done

for i in 1 2 3 4; do
  [ $SECONDS -ge $END ] && break
  tgs=$(aws elbv2 describe-target-groups --region "$REGION" --query "TargetGroups[?VpcId=='$VPC'].TargetGroupArn" --output text 2>/dev/null)
  [ -z "$tgs" ] && break
  for tg in $tgs; do aws elbv2 delete-target-group --target-group-arn "$tg" --region "$REGION" >/dev/null 2>&1; done
  sleep 10
done

# Controller-created security groups: other groups (the cluster/node SG) hold rules that reference
# them, which makes the delete fail with DependencyViolation. Revoke those rules first.
revoke_refs() { # $1 = controller security group id
  local target="$1" referrers ref rule
  referrers=$( { aws ec2 describe-security-groups --region "$REGION" \
      --filters "Name=vpc-id,Values=$VPC" "Name=ip-permission.group-id,Values=$target" \
      --query 'SecurityGroups[].GroupId' --output text 2>/dev/null
    aws ec2 describe-security-groups --region "$REGION" \
      --filters "Name=vpc-id,Values=$VPC" "Name=egress.ip-permission.group-id,Values=$target" \
      --query 'SecurityGroups[].GroupId' --output text 2>/dev/null; } | tr '\t' '\n' | sort -u)
  for ref in $referrers; do
    [ "$ref" = "$target" ] && continue
    for rule in $(aws ec2 describe-security-group-rules --region "$REGION" --filters "Name=group-id,Values=$ref" --output json 2>/dev/null \
        | jq -r --arg t "$target" '.SecurityGroupRules[] | select(.IsEgress==false and .ReferencedGroupInfo.GroupId==$t) | .SecurityGroupRuleId'); do
      echo "Revoking ingress rule $rule in $ref (references $target)"
      aws ec2 revoke-security-group-ingress --region "$REGION" --group-id "$ref" --security-group-rule-ids "$rule" >/dev/null 2>&1
    done
    for rule in $(aws ec2 describe-security-group-rules --region "$REGION" --filters "Name=group-id,Values=$ref" --output json 2>/dev/null \
        | jq -r --arg t "$target" '.SecurityGroupRules[] | select(.IsEgress==true and .ReferencedGroupInfo.GroupId==$t) | .SecurityGroupRuleId'); do
      echo "Revoking egress rule $rule in $ref (references $target)"
      aws ec2 revoke-security-group-egress --region "$REGION" --group-id "$ref" --security-group-rule-ids "$rule" >/dev/null 2>&1
    done
  done
}

for i in 1 2 3 4 5 6; do
  [ $SECONDS -ge $END ] && break
  sgs=$(aws ec2 describe-security-groups --region "$REGION" \
    --filters "Name=vpc-id,Values=$VPC" "Name=tag-key,Values=elbv2.k8s.aws/cluster" \
    --query 'SecurityGroups[].GroupId' --output text 2>/dev/null)
  [ -z "$sgs" ] && break
  for sg in $sgs; do revoke_refs "$sg"; done
  for sg in $sgs; do aws ec2 delete-security-group --group-id "$sg" --region "$REGION" >/dev/null 2>&1; done
  sleep 10
done

left=$(aws ec2 describe-security-groups --region "$REGION" \
  --filters "Name=vpc-id,Values=$VPC" "Name=tag-key,Values=elbv2.k8s.aws/cluster" \
  --query 'SecurityGroups[].GroupId' --output text 2>/dev/null)
[ -n "$left" ] && echo "WARNING: controller security groups still present: $left"

echo "Cleanup finished."
exit 0
