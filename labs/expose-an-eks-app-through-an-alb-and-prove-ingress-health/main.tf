terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
    http = {
      source  = "hashicorp/http"
      version = "~> 3.4"
    }
  }
}

variable "kubernetes_version" {
  type    = string
  default = "1.32"
}

variable "node_instance_type" {
  type    = string
  default = "t3.large"
}

# Git tag of aws-load-balancer-controller whose IAM policy is used.
# Must match the Helm chart pinned in fix.sh (chart 1.13.0 = app v2.13.0).
variable "lbc_app_version" {
  type    = string
  default = "v2.13.0"
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ssm_parameter" "eks_ami" {
  name = "/aws/service/eks/optimized-ami/${var.kubernetes_version}/amazon-linux-2023/x86_64/standard/recommended/image_id"
}

# ---------------------------------------------------------------- network
resource "aws_vpc" "this" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.lab_name}-vpc" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.lab_name}-igw" }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.this.id
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  cidr_block              = cidrsubnet(aws_vpc.this.cidr_block, 8, count.index)
  map_public_ip_on_launch = true
  tags = {
    Name                                    = "${var.lab_name}-public-${count.index}"
    "kubernetes.io/role/elb"                = "1"
    "kubernetes.io/cluster/${var.lab_name}" = "shared"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = { Name = "${var.lab_name}-public" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ---------------------------------------------------------------- IAM
resource "aws_iam_role" "cluster" {
  name                 = "hero-lab-${var.lab_name}-cluster"
  permissions_boundary = var.permissions_boundary
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "eks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_iam_role" "node" {
  name                 = "hero-lab-${var.lab_name}-node"
  permissions_boundary = var.permissions_boundary
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "node" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
  ])
  role       = aws_iam_role.node.name
  policy_arn = each.value
}

resource "aws_iam_instance_profile" "node" {
  name = "hero-lab-${var.lab_name}-node"
  role = aws_iam_role.node.name
}

# Role for the AWS Load Balancer Controller, assumed through EKS Pod Identity.
data "http" "lbc_policy" {
  url = "https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/${var.lbc_app_version}/docs/install/iam_policy.json"
  lifecycle {
    postcondition {
      condition     = self.status_code == 200
      error_message = "Could not download the load balancer controller IAM policy for ${var.lbc_app_version}."
    }
  }
}

resource "aws_iam_role" "lbc" {
  name                 = "hero-lab-${var.lab_name}-lbc"
  permissions_boundary = var.permissions_boundary
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["sts:AssumeRole", "sts:TagSession"]
      Principal = { Service = "pods.eks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "lbc" {
  name   = "aws-load-balancer-controller"
  role   = aws_iam_role.lbc.id
  policy = jsonencode(jsondecode(data.http.lbc_policy.response_body))
}

# ---------------------------------------------------------------- EKS
resource "aws_eks_cluster" "this" {
  name     = var.lab_name
  role_arn = aws_iam_role.cluster.arn
  version  = var.kubernetes_version

  vpc_config {
    subnet_ids              = aws_subnet.public[*].id
    endpoint_public_access  = true
    endpoint_private_access = false
  }

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = true
  }

  depends_on = [aws_iam_role_policy_attachment.cluster]
}

resource "aws_eks_access_entry" "node" {
  cluster_name  = aws_eks_cluster.this.name
  principal_arn = aws_iam_role.node.arn
  type          = "EC2_LINUX"
}

# ALB (in the VPC) -> pod IPs on the nodes, port 80.
resource "aws_vpc_security_group_ingress_rule" "alb_to_pods" {
  security_group_id = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
  cidr_ipv4         = aws_vpc.this.cidr_block
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  description       = "ALB to pods"
}

locals {
  user_data = <<EOT
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="BOUNDARY"

--BOUNDARY
Content-Type: application/node.eks.aws

---
apiVersion: node.eks.aws/v1alpha1
kind: NodeConfig
spec:
  cluster:
    name: ${aws_eks_cluster.this.name}
    apiServerEndpoint: ${aws_eks_cluster.this.endpoint}
    certificateAuthority: ${aws_eks_cluster.this.certificate_authority[0].data}
    cidr: ${aws_eks_cluster.this.kubernetes_network_config[0].service_ipv4_cidr}

--BOUNDARY--
EOT
}

resource "aws_instance" "node" {
  count                       = 2
  ami                         = data.aws_ssm_parameter.eks_ami.value
  instance_type               = var.node_instance_type
  subnet_id                   = aws_subnet.public[count.index % 2].id
  vpc_security_group_ids      = [aws_eks_cluster.this.vpc_config[0].cluster_security_group_id]
  iam_instance_profile        = aws_iam_instance_profile.node.name
  associate_public_ip_address = true
  user_data                   = local.user_data

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
  }

  tags = { Name = "${var.lab_name}-node-${count.index}" }

  depends_on = [
    aws_eks_access_entry.node,
    aws_iam_role_policy_attachment.node,
  ]
}

resource "aws_eks_addon" "pod_identity" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "eks-pod-identity-agent"
  resolve_conflicts_on_create = "OVERWRITE"
  depends_on                  = [aws_instance.node]
}

resource "aws_eks_pod_identity_association" "lbc" {
  cluster_name    = aws_eks_cluster.this.name
  namespace       = "kube-system"
  service_account = "aws-load-balancer-controller"
  role_arn        = aws_iam_role.lbc.arn
  depends_on      = [aws_eks_addon.pod_identity]
}

# ---------------------------------------------------------------- outputs
output "cluster_name" {
  value = aws_eks_cluster.this.name
}

output "vpc_id" {
  value = aws_vpc.this.id
}

output "region" {
  value = var.region
}

output "lbc_role_arn" {
  value = aws_iam_role.lbc.arn
}
