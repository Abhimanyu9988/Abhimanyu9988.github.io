#!/bin/bash
# Run once in AWS CloudShell, in the lab account. Creates a role that can only read costs. It stores no key: GitHub proves
# its identity through OIDC, and only the main-only live-check environment can use the role. Prints the role ARN.
set -euo pipefail
account=$(aws sts get-caller-identity --query Account --output text)
subject='repo:Abhimanyu9988@32825278/Abhimanyu9988.github.io@1423743121:environment:live-check'
provider="arn:aws:iam::${account}:oidc-provider/token.actions.githubusercontent.com"
aws iam create-role --role-name hero-pipeline-billing-read --query Role.Arn --output text --assume-role-policy-document "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [{\"Effect\": \"Allow\", \"Action\": \"sts:AssumeRoleWithWebIdentity\", \"Principal\": {\"Federated\": \"${provider}\"},
    \"Condition\": {\"StringEquals\": {\"token.actions.githubusercontent.com:aud\": \"sts.amazonaws.com\",
      \"token.actions.githubusercontent.com:sub\": \"${subject}\"}}}]}"
aws iam put-role-policy --role-name hero-pipeline-billing-read --policy-name read-costs --policy-document \
  '{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "ce:GetCostAndUsage", "Resource": "*"}]}'
