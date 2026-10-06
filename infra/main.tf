terraform {
  required_version = ">= 1.9, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" { region = var.region }

variable "region" { type = string }
variable "vpc_id" { type = string }
variable "public_subnets" { type = list(string) }
variable "private_subnets" { type = list(string) }
variable "certificate_arn" { type = string }
variable "container_image" {
  type = string
  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.container_image))
    error_message = "Use an immutable ARM64 image digest, not a mutable tag."
  }
}
variable "runtime_secret_arn" { type = string }
variable "model_config_json" { type = string }
variable "allowed_cidrs" {
  type = list(string)
  validation {
    condition     = length(var.allowed_cidrs) > 0 && !contains(var.allowed_cidrs, "0.0.0.0/0")
    error_message = "Specify the intended client networks; do not expose this reference service to the whole Internet."
  }
}
variable "name" {
  type    = string
  default = "inference-router"
}

resource "aws_cloudwatch_log_group" "router" {
  name              = "/ecs/${var.name}"
  retention_in_days = 14
}

resource "aws_ecs_cluster" "router" { name = var.name }

resource "aws_iam_role" "execution" {
  name = "${var.name}-execution"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "runtime_secret" {
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = var.runtime_secret_arn }]
  })
}

resource "aws_security_group" "load_balancer" {
  name_prefix = "${var.name}-alb-"
  vpc_id      = var.vpc_id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidrs
  }
  egress {
    from_port   = 8104
    to_port     = 8104
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
  }
}

resource "aws_security_group" "tasks" {
  name_prefix = "${var.name}-task-"
  vpc_id      = var.vpc_id
  ingress {
    from_port       = 8104
    to_port         = 8104
    protocol        = "tcp"
    security_groups = [aws_security_group.load_balancer.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "router" {
  name               = var.name
  load_balancer_type = "application"
  subnets            = var.public_subnets
  security_groups    = [aws_security_group.load_balancer.id]
}

resource "aws_lb_target_group" "router" {
  name        = var.name
  port        = 8104
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"
  health_check { path = "/ready" }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.router.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.router.arn
  }
}

resource "aws_ecs_task_definition" "router" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.execution.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }
  container_definitions = jsonencode([{
    name                   = "router"
    image                  = var.container_image
    essential              = true
    user                   = "10001"
    readonlyRootFilesystem = true
    portMappings           = [{ containerPort = 8104, protocol = "tcp" }]
    environment = [
      { name = "MODELS_CONFIG_JSON", value = var.model_config_json },
      { name = "DAILY_BUDGET_USD", value = "1" },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" }
    ]
    secrets = [
      { name = "API_KEYS_JSON", valueFrom = "${var.runtime_secret_arn}:api_keys_json::" },
      { name = "ROUTER_DATABASE_URL", valueFrom = "${var.runtime_secret_arn}:postgres_dsn::" }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.router.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "router"
      }
    }
  }])
}

resource "aws_ecs_service" "router" {
  name             = var.name
  cluster          = aws_ecs_cluster.router.id
  task_definition  = aws_ecs_task_definition.router.arn
  desired_count    = 2
  launch_type      = "FARGATE"
  platform_version = "1.4.0"
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = var.private_subnets
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.router.arn
    container_name   = "router"
    container_port   = 8104
  }
  depends_on = [aws_lb_listener.https, aws_iam_role_policy.runtime_secret, aws_iam_role_policy_attachment.execution]
}

output "load_balancer_dns" { value = aws_lb.router.dns_name }
output "task_security_group" { value = aws_security_group.tasks.id }
