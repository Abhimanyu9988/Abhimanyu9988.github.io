terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

# ---------------------------------------------------------------- network
resource "aws_vpc" "this" {
  cidr_block           = "10.60.0.0/16"
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
  cidr_block              = cidrsubnet("10.60.0.0/16", 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.lab_name}-public-${count.index}" }
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

resource "aws_security_group" "worker" {
  name        = "${var.lab_name}-worker"
  description = "worker egress only"
  vpc_id      = aws_vpc.this.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ---------------------------------------------------------------- queue
resource "aws_sqs_queue" "jobs" {
  name                       = "${var.lab_name}-jobs"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 3600
}

# ---------------------------------------------------------------- IAM
data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "exec" {
  name                 = "hero-lab-${var.lab_name}-exec"
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_assume.json
  permissions_boundary = var.permissions_boundary
}

resource "aws_iam_role_policy_attachment" "exec" {
  role       = aws_iam_role.exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "task" {
  name                 = "hero-lab-${var.lab_name}-task"
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_assume.json
  permissions_boundary = var.permissions_boundary
}

resource "aws_iam_role_policy" "task" {
  name = "${var.lab_name}-sqs-worker"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]
      Resource = aws_sqs_queue.jobs.arn
    }]
  })
}

# ---------------------------------------------------------------- ECS
resource "aws_cloudwatch_log_group" "worker" {
  name              = "/${var.lab_name}/worker"
  retention_in_days = 1
}

resource "aws_ecs_cluster" "this" {
  name = "${var.lab_name}-cluster"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.lab_name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.exec.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name       = "worker"
    image      = "public.ecr.aws/aws-cli/aws-cli:latest"
    essential  = true
    entryPoint = ["/bin/sh", "-c"]
    command = [
      "echo worker started; while true; do rh=$(aws sqs receive-message --queue-url \"$QUEUE_URL\" --wait-time-seconds 10 --visibility-timeout 60 --query 'Messages[0].ReceiptHandle' --output text 2>/dev/null); if [ -n \"$rh\" ] && [ \"$rh\" != \"None\" ]; then echo processing message; sleep 20; aws sqs delete-message --queue-url \"$QUEUE_URL\" --receipt-handle \"$rh\" && echo done message; fi; done"
    ]
    environment = [
      { name = "QUEUE_URL", value = aws_sqs_queue.jobs.url },
      { name = "AWS_DEFAULT_REGION", value = var.region }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.worker.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "worker"
      }
    }
  }])
}

resource "aws_ecs_service" "worker" {
  name            = "${var.lab_name}-worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 0
  launch_type     = "FARGATE"
  force_delete    = true

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = true
  }

  lifecycle {
    ignore_changes = [desired_count]
  }
}

# ---------------------------------------------------------------- autoscaling
resource "aws_appautoscaling_target" "worker" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = 0
  max_capacity       = 5
}

# The asker's policy, unchanged apart from names.
resource "aws_appautoscaling_policy" "worker_backlog" {
  name               = "${var.lab_name}-backlog-per-task"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = 2
    scale_out_cooldown = 60
    scale_in_cooldown  = 300

    customized_metric_specification {
      metrics {
        id          = "backlog"
        return_data = false
        metric_stat {
          stat = "Sum"
          metric {
            namespace   = "AWS/SQS"
            metric_name = "ApproximateNumberOfMessagesVisible"
            dimensions {
              name  = "QueueName"
              value = aws_sqs_queue.jobs.name
            }
          }
        }
      }
      metrics {
        id          = "running"
        return_data = false
        metric_stat {
          stat = "Average"
          metric {
            namespace   = "ECS/ContainerInsights"
            metric_name = "RunningTaskCount"
            dimensions {
              name  = "ClusterName"
              value = aws_ecs_cluster.this.name
            }
            dimensions {
              name  = "ServiceName"
              value = aws_ecs_service.worker.name
            }
          }
        }
      }
      metrics {
        id          = "bpt"
        label       = "Backlog per task"
        expression  = "IF(running > 0, backlog / running, backlog)"
        return_data = true
      }
    }
  }
}

# ---------------------------------------------------------------- outputs
output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "service_name" {
  value = aws_ecs_service.worker.name
}

output "queue_url" {
  value = aws_sqs_queue.jobs.url
}

output "queue_name" {
  value = aws_sqs_queue.jobs.name
}

output "resource_id" {
  value = aws_appautoscaling_target.worker.resource_id
}

output "policy_name" {
  value = aws_appautoscaling_policy.worker_backlog.name
}
