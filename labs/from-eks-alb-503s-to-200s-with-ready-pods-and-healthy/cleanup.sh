#!/usr/bin/env bash
# Removes everything Kubernetes created outside Terraform (ALB, target groups, security groups) and the Helm releases.
# Worst case runtime is about 10 minutes (limit 15). Never fails hard.
set -u
cd "$(dirname "$0")"
export AWS_PAGER=""

CLUSTER="$(terraform output -raw cluster_name 2>/dev/null)" || CLUSTER=""
if [ -z "$CLUSTER" ]; then echo "no cluster in state; nothing to clean"; exit 0; fi

lab_albs() {
  for arn in $(aws elbv2 describe-load-balancers --query 'LoadBalancers[].LoadBalancerArn' --output text 2>/dev/null); do
    aws elbv2 describe-tags --resource-arns "$arn" \
      --query "TagDescriptions[?Tags[?Key=='elbv2.k8s.aws/cluster' && Value=='${CLUSTER}']].ResourceArn" --output text 2>/dev/null
  done
}

API=0
if aws eks update-kubeconfig --name "$CLUSTER" >/dev/null 2>&1 && kubectl get ns --request-timeout=20s >/dev/null 2>&1; then
  API=1
fi

if [ "$API" = "1" ]; then
  echo "deleting the app namespace (ingress -> ALB)"
  kubectl delete ingress --all -A --wait=false --request-timeout=30s >/dev/null 2>&1
  kubectl delete svc -A --field-selector spec.type=LoadBalancer --wait=false --request-timeout=30s >/dev/null 2>&1
  kubectl delete namespace lab-web --wait=true --timeout=120s --request-timeout=30s 2>&1 | tail -n 2
fi

echo "waiting for the ALB to disappear (max 3 min)"
for i in $(seq 1 18); do
  [ -z "$(lab_albs)" ] && break
  sleep 10
done

if [ "$API" = "1" ]; then
  echo "uninstalling Helm releases"
  helm uninstall aws-load-balancer-controller -n kube-system --timeout 60s >/dev/null 2>&1
  helm uninstall splunk-otel-collector -n splunk --timeout 60s >/dev/null 2>&1
fi

# Fallback: remove leftovers the controller could not delete
for arn in $(lab_albs); do
  echo "force-deleting ALB $arn"
  aws elbv2 delete-load-balancer --load-balancer-arn "$arn" >/dev/null 2>&1
done
sleep 15
for tg in $(aws elbv2 describe-target-groups --query 'TargetGroups[].TargetGroupArn' --output text 2>/dev/null); do
  m=$(aws elbv2 describe-tags --resource-arns "$tg" --query "TagDescriptions[?Tags[?Key=='elbv2.k8s.aws/cluster' && Value=='${CLUSTER}']].ResourceArn" --output text 2>/dev/null)
  [ -n "$m" ] && { echo "deleting target group $m"; aws elbv2 delete-target-group --target-group-arn "$m" >/dev/null 2>&1; }
done

CSG="$(aws eks describe-cluster --name "$CLUSTER" --query 'cluster.resourcesVpcConfig.clusterSecurityGroupId' --output text 2>/dev/null)"
for i in $(seq 1 8); do
  SGS="$(aws ec2 describe-security-groups --filters "Name=tag:elbv2.k8s.aws/cluster,Values=${CLUSTER}" --query 'SecurityGroups[].GroupId' --output text 2>/dev/null)"
  [ -z "$SGS" ] && break
  for sg in $SGS; do
    if [ -n "$CSG" ] && [ "$CSG" != "None" ]; then
      RULES="$(aws ec2 describe-security-group-rules --filters "Name=group-id,Values=${CSG}" --output json 2>/dev/null \
        | jq -r --arg sg "$sg" '[.SecurityGroupRules[] | select(.ReferencedGroupInfo.GroupId==$sg) | .SecurityGroupRuleId] | join(" ")')"
      [ -n "$RULES" ] && aws ec2 revoke-security-group-rules --group-id "$CSG" --security-group-rule-ids $RULES >/dev/null 2>&1
    fi
    aws ec2 delete-security-group --group-id "$sg" >/dev/null 2>&1 && echo "deleted security group $sg"
  done
  sleep 15
done
echo "cleanup finished"
exit 0
