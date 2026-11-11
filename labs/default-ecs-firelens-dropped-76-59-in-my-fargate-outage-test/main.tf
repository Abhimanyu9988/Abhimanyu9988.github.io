terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

# ---------------------------------------------------------------- network
resource "aws_vpc" "lab" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.lab_name}-vpc" }
}

resource "aws_internet_gateway" "lab" {
  vpc_id = aws_vpc.lab.id
  tags   = { Name = "${var.lab_name}-igw" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.lab.id
  cidr_block              = "10.42.1.0/24"
  availability_zone       = "${var.region}a"
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.lab_name}-public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.lab.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.lab.id
  }
  tags = { Name = "${var.lab_name}-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "sink" {
  name        = "${var.lab_name}-sink"
  description = "HTTP sink: 8080 ingest (stoppable), 8081 admin (token protected)"
  vpc_id      = aws_vpc.lab.id
  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 8081
    to_port     = 8081
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "tasks" {
  name        = "${var.lab_name}-tasks"
  description = "Fargate tasks: egress only"
  vpc_id      = aws_vpc.lab.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ---------------------------------------------------------------- sink on EC2
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "random_password" "admin" {
  length  = 24
  special = false
}

resource "aws_instance" "sink" {
  ami                         = data.aws_ssm_parameter.al2023.value
  instance_type               = "t3.medium"
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.sink.id]
  associate_public_ip_address = true
  user_data_replace_on_change = true

  metadata_options {
    http_tokens = "required"
  }

  user_data = <<EOT
#!/bin/bash
set -eux
mkdir -p /opt
cat > /opt/sink.py <<'PYEOF'
${local.sink_py}
PYEOF
cat > /etc/systemd/system/sink.service <<EOF
[Unit]
Description=lab http sink
After=network.target
[Service]
ExecStart=/usr/bin/python3 /opt/sink.py ${random_password.admin.result}
Restart=always
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now sink.service
EOT

  tags = { Name = "${var.lab_name}-sink" }
}

# ---------------------------------------------------------------- ECS
resource "aws_ecs_cluster" "lab" {
  name = var.lab_name
}

resource "aws_cloudwatch_log_group" "router" {
  name              = "/${var.lab_name}/log-router"
  retention_in_days = 1
}

resource "aws_iam_role" "exec" {
  name                 = "hero-lab-${var.lab_name}-exec"
  permissions_boundary = var.permissions_boundary
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "exec_logs" {
  name = "router-logs"
  role = aws_iam_role.exec.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.router.arn}:*"
    }]
  })
}

locals {
  cfgs      = ["default", "mem", "fs"]
  fb_image  = "public.ecr.aws/aws-observability/aws-for-fluent-bit:3.0.0"
  py_image  = "public.ecr.aws/docker/library/python:3.12-alpine"
  sink_ip   = aws_instance.sink.public_ip
  router_sh = "echo \"$FLB_CONF\" > /tmp/fb.conf && mkdir -p /var/fb-storage && exec /fluent-bit/bin/fluent-bit -c /tmp/fb.conf"

  # (b) memory buffering, raised Retry_Limit, bigger Mem_Buf_Limit, short retry backoff cap
  conf_mem = <<CONF
[SERVICE]
    Flush          1
    Grace          30
    Log_Level      info
    scheduler.base 2
    scheduler.cap  20

[INPUT]
    Name          forward
    unix_path     /var/run/fluent.sock
    Mem_Buf_Limit 64MB

[INPUT]
    Name          forward
    Listen        127.0.0.1
    Port          24224
    Mem_Buf_Limit 64MB

[OUTPUT]
    Name        http
    Match       *
    Host        ${local.sink_ip}
    Port        8080
    URI         /ingest/mem
    Format      json_lines
    tls         Off
    Retry_Limit 60
CONF

  # (c) filesystem buffering on ephemeral task storage, same Retry_Limit and backoff
  conf_fs = <<CONF
[SERVICE]
    Flush                     1
    Grace                     30
    Log_Level                 info
    scheduler.base            2
    scheduler.cap             20
    storage.path              /var/fb-storage
    storage.sync              normal
    storage.max_chunks_up     128
    storage.backlog.mem_limit 64M

[INPUT]
    Name         forward
    unix_path    /var/run/fluent.sock
    storage.type filesystem

[INPUT]
    Name         forward
    Listen       127.0.0.1
    Port         24224
    storage.type filesystem

[OUTPUT]
    Name                     http
    Match                    *
    Host                     ${local.sink_ip}
    Port                     8080
    URI                      /ingest/fs
    Format                   json_lines
    tls                      Off
    Retry_Limit              60
    storage.total_limit_size 512M
CONF

  router_base = { for k in local.cfgs : k => {
    name                  = "log_router"
    image                 = local.fb_image
    essential             = true
    memory                = 256
    firelensConfiguration = { type = "fluentbit" }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.router.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "router-${k}"
      }
    }
  } }

  router_defs = {
    default = local.router_base["default"]
    mem = merge(local.router_base["mem"], {
      entryPoint  = ["/bin/sh", "-c"]
      command     = [local.router_sh]
      environment = [{ name = "FLB_CONF", value = local.conf_mem }]
    })
    fs = merge(local.router_base["fs"], {
      entryPoint  = ["/bin/sh", "-c"]
      command     = [local.router_sh]
      environment = [{ name = "FLB_CONF", value = local.conf_fs }]
    })
  }

  app_defs = { for k in local.cfgs : k => {
    name      = "app"
    image     = local.py_image
    essential = true
    command   = ["python", "-u", "-c", local.gen_py]
    environment = [
      { name = "RUN", value = k },
      { name = "SINK_ADMIN", value = "http://${local.sink_ip}:8081" },
      { name = "SINK_TOKEN", value = random_password.admin.result },
      { name = "RATE", value = "200" },
      { name = "MAX_SECONDS", value = "900" },
    ]
    dependsOn = [{ containerName = "log_router", condition = "START" }]
    logConfiguration = {
      logDriver = "awsfirelens"
      options = {
        Name   = "http"
        Host   = local.sink_ip
        Port   = "8080"
        URI    = "/ingest/${k}"
        Format = "json_lines"
        tls    = "off"
      }
    }
  } }
}

resource "aws_ecs_task_definition" "cfg" {
  for_each                 = toset(local.cfgs)
  family                   = "${var.lab_name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.exec.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([local.router_defs[each.key], local.app_defs[each.key]])
}

# ---------------------------------------------------------------- outputs
output "cluster_name" {
  value = aws_ecs_cluster.lab.name
}

output "subnet_id" {
  value = aws_subnet.public.id
}

output "task_security_group_id" {
  value = aws_security_group.tasks.id
}

output "sink_public_ip" {
  value = aws_instance.sink.public_ip
}

output "sink_admin_token" {
  value     = random_password.admin.result
  sensitive = true
}

output "log_group" {
  value = aws_cloudwatch_log_group.router.name
}

output "task_def_default" {
  value = aws_ecs_task_definition.cfg["default"].arn
}

output "task_def_mem" {
  value = aws_ecs_task_definition.cfg["mem"].arn
}

output "task_def_fs" {
  value = aws_ecs_task_definition.cfg["fs"].arn
}

output "fluentbit_image" {
  value = local.fb_image
}

output "router_conf_mem" {
  value = local.conf_mem
}

output "router_conf_fs" {
  value = local.conf_fs
}
