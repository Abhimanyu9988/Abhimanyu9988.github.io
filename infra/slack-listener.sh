#!/bin/bash
# Run once on your Mac after this file is on main. It asks for two secrets without showing them, and prints the Slack URL.
set -euo pipefail
read -rsp "Slack signing secret: " slack < /dev/tty; echo
read -rsp "GitHub token: " token < /dev/tty; echo
template=$(mktemp)
curl -fsS https://raw.githubusercontent.com/Abhimanyu9988/Abhimanyu9988.github.io/main/infra/slack-listener.yml -o "$template"
aws cloudformation deploy --region eu-central-1 --stack-name slack-listener --template-file "$template" \
  --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset \
  --parameter-overrides "SlackSigningSecret=$slack" "GitHubToken=$token"
aws cloudformation describe-stacks --region eu-central-1 --stack-name slack-listener \
  --query "Stacks[0].Outputs[0].OutputValue" --output text
