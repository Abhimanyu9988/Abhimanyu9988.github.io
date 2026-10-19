#!/usr/bin/env bash
# Remove the CloudWatch alarm and dashboard created by fix.sh (unknown to Terraform). Never fails hard.
export AWS_DEFAULT_REGION="${AWS_REGION:-eu-central-1}"
LAB="${LAB_NAME:-lab}"
aws cloudwatch delete-alarms --alarm-names "${LAB}-draining-tasks" >/dev/null 2>&1 || true
aws cloudwatch delete-dashboards --dashboard-names "${LAB}-tasks" >/dev/null 2>&1 || true
echo "cleanup done"
exit 0
