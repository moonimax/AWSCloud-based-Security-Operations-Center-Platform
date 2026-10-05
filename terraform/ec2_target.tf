# Web Target Fleet: Launch Template & Auto Scaling Group (+ ALB)

# CloudWatch Agent 설정 (인스턴스 부팅 시 SSM Parameter Store에서 자동으로 fetch)
#  - 파라미터 이름은 CloudWatchAgentServerPolicy가 이미 허용하는 "AmazonCloudWatch-*" 규칙을 따름
#  - ASG가 인스턴스를 교체해도 새 인스턴스가 항상 이 설정을 받아가므로 로그 수집이 끊기지 않음
resource "aws_ssm_parameter" "cwagent_web_config" {
  name = "AmazonCloudWatch-${var.project_name}-web"
  type = "String"
  value = jsonencode({
    logs = {
      logs_collected = {
        files = {
          collect_list = [
            {
              file_path         = "/var/log/nginx/access.log"
              log_group_name    = aws_cloudwatch_log_group.web_nginx_access.name
              log_stream_name   = "{instance_id}"
              retention_in_days = 14
            }
          ]
        }
      }
    }
  })
}

# 최신 Amazon Linux 2023 AMI
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# =============================================================================
# 1. Launch Template (집 설계도)
# =============================================================================
resource "aws_launch_template" "web" {
  name_prefix            = "${var.project_name}-web-"
  image_id               = data.aws_ami.al2023.id
  instance_type          = var.web_instance_type
  vpc_security_group_ids = [aws_security_group.web.id]

  iam_instance_profile {
    arn = aws_iam_instance_profile.web.arn
  }

  # IMDSv2 강제 (SSRF로 인한 자격 증명 탈취 방지)
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1 # 컨테이너(브리지 네트워크)에서는 메타데이터 접근 불가
  }

  # 루트 볼륨 암호화
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 30
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  monitoring {
    enabled = true
  }

  user_data = base64encode(templatefile("${path.module}/user_data/web.sh", {
    region            = var.region
    app_image         = var.app_image
    vpc_cidr          = var.vpc_cidr
    enable_exporters  = var.enable_exporters
    upload_max_size   = var.upload_max_size
    cwagent_ssm_param = aws_ssm_parameter.cwagent_web_config.name
  }))

  # Role=web 태그 → Prometheus ec2_sd_configs에서 이 태그로 웹서버 자동 탐색
  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "${var.project_name}-web"
      Role = "web"
    }
  }

  tag_specifications {
    resource_type = "volume"
    tags = {
      Name = "${var.project_name}-web-volume"
    }
  }
}

# =============================================================================
# 2. Auto Scaling Group (설계도대로 서버 수 유지)
# =============================================================================
resource "aws_autoscaling_group" "web" {
  depends_on = [aws_iam_role_policy_attachment.web, aws_cloudwatch_log_subscription_filter.web_nginx_to_sqs]
  name                      = "${var.project_name}-web-asg"
  vpc_zone_identifier       = aws_subnet.private[*].id
  desired_capacity          = var.web_desired_capacity
  min_size                  = var.web_min_size
  max_size                  = var.web_max_size
  target_group_arns         = [aws_lb_target_group.web.arn]
  health_check_type         = "ELB"
  health_check_grace_period = 300

  launch_template {
    id      = aws_launch_template.web.id
    version = aws_launch_template.web.latest_version
  }

  # Launch Template이 바뀌면 서버를 순차 교체 (배포 방식 후보)
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
    }
  }

  tag {
    key                 = "Project"
    value               = var.project_name
    propagate_at_launch = true
  }
}

# =============================================================================
# 3. ALB (정문 안내 데스크) — 퍼블릭 서브넷에 위치, ZAP 점검 대상 주소
# =============================================================================
resource "aws_lb" "web" {
  access_logs {
    bucket = aws_s3_bucket.alb_logs.id
    prefix = "alb"
    enabled = true
  }
  depends_on = [aws_s3_bucket_policy.alb_logs]
  name               = "${var.project_name}-alb"
  load_balancer_type = "application"
  internal           = false # 외부 서비스용이므로 의도적으로 공개
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  drop_invalid_header_fields = true # HTTP 요청 스머글링 방지
}

resource "aws_lb_target_group" "web" {
  name     = "${var.project_name}-web-tg"
  port     = 80
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id

  health_check {
    path                = "/healthz"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

# 도메인/인증서가 없어 HTTP로 시작. 인증서가 준비되면 HTTPS(443) 리스너 추가 후 80은 리다이렉트로 변경
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.web.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}
