# Weighted canary and instant rollback on an ALB (EKS + AWS Load Balancer Controller)

This lab builds an internet-facing ALB in front of two versions of an app. The AWS Load Balancer Controller manages the ALB. It reads the traffic weights from an Ingress annotation. A new release gets 10% of traffic first. If it misbehaves, you set its weight to 0 and the old version takes all traffic again. You don't redeploy anything.

## What gets built

- Terraform builds a VPC with two public subnets and an EKS 1.31 cluster. Two plain `aws_instance` t3.medium workers join through `nodeadm` and an `EC2_LINUX` access entry. The node instance profile is named `hero-lab-<lab>-node`, like the roles, so it fits the `hero-lab-*` IAM naming rule.
- Terraform also adds the `eks-pod-identity-agent` addon, an IAM role (`hero-lab-<lab>-lbc`) for the controller, and a Pod Identity association for `kube-system/aws-load-balancer-controller`.
- The sample app is busybox `httpd`. `app-v1` has 3 pods and returns `version-v1`. `app-v2` has 1 pod and returns `version-v2`. Both are in namespace `demo`.

## Steps

1. `terraform init && terraform apply` creates the cluster (about 12-15 minutes).
2. `./reproduce.sh` is the BEFORE. It shows that a plain Service splits traffic by pod count (about 75/25), and that there is no Ingress, no load balancer and no weight to change.
3. `./fix.sh` is the BUILD:
   - It installs the AWS Load Balancer Controller Helm chart 1.11.0 (controller v2.11.0) using Pod Identity. No IRSA and no node-role permissions are involved.
   - It creates an Ingress (`k8s/ingress.yaml`) with a `forward` action across `app-v1` and `app-v2`. The weights are 90/10.
   - It runs a curl loop against the ALB at 90/10, then 50/50 after `kubectl annotate --overwrite`, then 100/0 as the rollback. It prints the ALB listener-rule weights from the `elbv2` API at each stage, and the time from annotate to 100% on v1.
4. `./cleanup.sh` deletes the Ingress, so the controller removes the ALB, target groups and security groups. If anything is left, it falls back to the AWS CLI. That fallback also revokes the security group rules in the VPC that reference the controller's security groups, so the VPC can be deleted. Then run `terraform destroy`.

The weights are changed with:

```bash
kubectl -n demo annotate ingress app --overwrite \
  alb.ingress.kubernetes.io/actions.weighted='{"type":"forward","forwardConfig":{"targetGroups":[{"serviceName":"app-v1","servicePort":80,"weight":100},{"serviceName":"app-v2","servicePort":80,"weight":0}]}}'
```

## Run in your own account

You need Terraform, the AWS CLI v2, kubectl, helm, jq and curl. Set `AWS_REGION=eu-central-1` and `LAB_NAME=canary`. Provide the variables `region`, `lab_name` and `permissions_boundary` (use `null` for no boundary), and a backend or local state. Then run the steps above. Expect about 45 minutes and under 1 USD.

## Notes

- The controller IAM policy in `main.tf` is simplified for a disposable lab. In production use the official `iam_policy.json` from the controller release.
- Traffic samples are random, so the percentages are close to the weights but not exact. The checks in `fix.sh` use tolerance bands.
- Weights are independent of replica counts. That is why 1 v2 pod can take 10% while 3 v1 pods take 90%.
- Each script has a global time budget (13-14 minutes) and always ends with its `HERO_RESULT` line (`cleanup.sh` just finishes). If a step is slow, it gives up instead of waiting.
