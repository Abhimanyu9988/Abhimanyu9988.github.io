# Sandbox for everything the AI writes: Terraform and the lab scripts run here, never on the runner itself.
# The container gets only the AWS credentials and lab variables; no model keys, GitHub tokens or runner files.
FROM ubuntu:24.04
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl unzip jq gnupg bash git less groff openssl \
 && curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/hashicorp.gpg] https://apt.releases.hashicorp.com noble main" \
      > /etc/apt/sources.list.d/hashicorp.list \
 && apt-get update && apt-get install -y --no-install-recommends terraform \
 && curl -fsSLo /tmp/aws.zip https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip \
 && unzip -q /tmp/aws.zip -d /tmp && /tmp/aws/install && rm -rf /tmp/aws /tmp/aws.zip \
 && K=$(curl -fsSL https://dl.k8s.io/release/stable.txt) \
 && curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/$K/bin/linux/amd64/kubectl" \
 && chmod +x /usr/local/bin/kubectl \
 && curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash \
 && rm -rf /var/lib/apt/lists/* \
 && terraform version && aws --version && kubectl version --client && helm version
