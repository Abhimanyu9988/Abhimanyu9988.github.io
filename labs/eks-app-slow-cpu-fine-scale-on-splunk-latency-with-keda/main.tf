terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }
}

locals {
  k8s_version = "1.33"
  node_count  = 2
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ssm_parameter" "ami" {
  name = "/aws/service/eks/optimized-ami/${local.k8s_version}/amazon-linux-2023/x86_64/standard/recommended/image_id"
}

# ---------------- network (public subnets only, no NAT) ----------------
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.lab_name}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.lab_name}-igw" }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.lab_name}-public-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${var.lab_name}-public" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ---------------- IAM ----------------
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
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
  ])
  role       = aws_iam_role.node.name
  policy_arn = each.value
}

# The pipeline role only allows IAM resources named hero-lab-*, so the instance profile carries that prefix too.
resource "aws_iam_instance_profile" "node" {
  name = "hero-lab-${var.lab_name}-node"
  role = aws_iam_role.node.name
}

# ---------------- EKS control plane ----------------
resource "aws_eks_cluster" "main" {
  name     = var.lab_name
  role_arn = aws_iam_role.cluster.arn
  version  = local.k8s_version

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
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = aws_iam_role.node.arn
  type          = "EC2_LINUX"
}

# ---------------- self-managed nodes joined with nodeadm ----------------
locals {
  user_data = <<-EOT
    MIME-Version: 1.0
    Content-Type: multipart/mixed; boundary="BOUNDARY"

    --BOUNDARY
    Content-Type: application/node.eks.aws

    ---
    apiVersion: node.eks.aws/v1alpha1
    kind: NodeConfig
    spec:
      cluster:
        name: ${aws_eks_cluster.main.name}
        apiServerEndpoint: ${aws_eks_cluster.main.endpoint}
        certificateAuthority: ${aws_eks_cluster.main.certificate_authority[0].data}
        cidr: ${aws_eks_cluster.main.kubernetes_network_config[0].service_ipv4_cidr}

    --BOUNDARY--
  EOT
}

resource "aws_instance" "node" {
  count                       = local.node_count
  ami                         = nonsensitive(data.aws_ssm_parameter.ami.value)
  instance_type               = "t3.large"
  subnet_id                   = aws_subnet.public[count.index % 2].id
  iam_instance_profile        = aws_iam_instance_profile.node.name
  vpc_security_group_ids      = [aws_eks_cluster.main.vpc_config[0].cluster_security_group_id]
  associate_public_ip_address = true
  user_data                   = local.user_data

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
  }

  tags = { Name = "${var.lab_name}-node-${count.index}" }

  depends_on = [aws_eks_access_entry.node, aws_iam_role_policy_attachment.node]
}

output "cluster_name" {
  value = aws_eks_cluster.main.name
}
