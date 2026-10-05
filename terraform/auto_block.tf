# =============================================================================
# 자동 차단(TC-08): block-ip / unblock-ip Lambda + Function URL + 1분 타이머
#  - Grafana 알림(R1/R3 firing) → block Function URL → NACL DENY + SSM 기록
#  - EventBridge rate(1min) → unblock Lambda → 만료분 NACL 삭제(성공시에만 SSM 삭제)
#  - lambda_assume / aws_caller_identity 는 monitoring_infra.tf 의 것을 재사용
# =============================================================================

# ── 설정값 (terraform.tfvars 에서 조정. 기본값은 안전한 무해 기본) ────────────
variable "block_nacl_ids" {
  type        = list(string)
  default     = [] # 비우면 VPC 기본 NACL(퍼블릭 서브넷이 공유)을 자동 사용 → VPC 재생성 시에도 갱신 불필요
  description = "ALB 퍼블릭 서브넷에 연결된 NACL ID 목록. 비우면 aws_vpc.main 의 기본 NACL 을 사용."
}

locals {
  # 퍼블릭 서브넷에 별도 NACL 을 연결하지 않으므로 VPC 기본 NACL 이 ALB 앞단 NACL 이다.
  block_nacl_ids = length(var.block_nacl_ids) > 0 ? var.block_nacl_ids : [aws_vpc.main.default_network_acl_id]
}
variable "block_whitelist_cidrs" {
  type        = list(string)
  default     = ["10.0.0.0/16"] # VPC 내부(인프라)만. 팀원(사람) IP는 넣지 말 것(TC-07/08 검증 위해).
  description = "절대 차단 금지 = 인프라 IP만(VPC CIDR/NAT EIP/ALB). 팀원(사람) IP는 넣지 말 것."
}
variable "block_ttl_min" {
  type        = number
  default     = 2 # 테스트 단계: 2분(빨리 해제 확인). 시연 때 5로.
  description = "차단 유지(분). 테스트 2 / 시연 5. 재범가중치·영구차단 없음."
}
variable "block_ssm_prefix" {
  type        = string
  default     = "/automovitsec-soc"
  description = "차단 기록 SSM 경로 접두사. block/unblock 이 공유."
}
variable "block_nacl_max_rules" {
  type        = number
  default     = 20
  description = "NACL 인바운드 규칙 수 상한(기존 포함). 초과 시 차단 거부+Slack 경고."
}
variable "block_slack_webhook" {
  type        = string
  default     = ""
  sensitive   = true
  description = "(선택) 슬롯 초과 등 운영 경고용 Slack Incoming Webhook. 커밋 금지 — tfvars/CI secret 로."
}

# Grafana webhook 이 보낼 비밀토큰. 코드/깃에 남지 않도록 terraform 이 생성(state 에만 존재).
resource "random_password" "block_secret" {
  length  = 40
  special = false
}

# ── 패키징 ────────────────────────────────────────────────────────────────
data "archive_file" "block_ip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/block-ip"
  output_path = "${path.module}/lambda/block-ip.zip"
  excludes    = ["__pycache__"]
}
data "archive_file" "unblock_ip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/unblock-ip"
  output_path = "${path.module}/lambda/unblock-ip.zip"
  excludes    = ["__pycache__"]
}

# ── IAM: block-ip (NACL 읽기/추가, SSM 기록) ─────────────────────────────────
resource "aws_iam_role" "block_ip" {
  name               = "${var.project_name}-block-ip-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}
resource "aws_iam_role_policy_attachment" "block_ip_basic" {
  role       = aws_iam_role.block_ip.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_iam_role_policy" "block_ip" {
  name = "${var.project_name}-block-ip"
  role = aws_iam_role.block_ip.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ec2:DescribeNetworkAcls", "ec2:CreateNetworkAclEntry", "ec2:DeleteNetworkAclEntry"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:PutParameter", "ssm:DeleteParameter"]
        Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${var.block_ssm_prefix}/blocklist/*"
      }
    ]
  })
}

# ── IAM: unblock-ip (NACL 삭제, SSM 조회/삭제) ───────────────────────────────
resource "aws_iam_role" "unblock_ip" {
  name               = "${var.project_name}-unblock-ip-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}
resource "aws_iam_role_policy_attachment" "unblock_ip_basic" {
  role       = aws_iam_role.unblock_ip.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_iam_role_policy" "unblock_ip" {
  name = "${var.project_name}-unblock-ip"
  role = aws_iam_role.unblock_ip.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ec2:DeleteNetworkAclEntry"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        # GetParametersByPath는 조회 경로(트레일링 슬래시 포함/미포함) 리소스에 대해 허용해야 함.
        # 코드가 Path="/{prefix}/blocklist/" (슬래시 포함)로 부르므로 두 형태 모두 허용.
        Action = ["ssm:GetParametersByPath"]
        Resource = [
          "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${var.block_ssm_prefix}/blocklist",
          "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${var.block_ssm_prefix}/blocklist/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:DeleteParameter"]
        Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${var.block_ssm_prefix}/blocklist/*"
      }
    ]
  })
}

# ── Lambda 함수 ──────────────────────────────────────────────────────────────
resource "aws_lambda_function" "block_ip" {
  function_name    = "${var.project_name}-block-ip"
  role             = aws_iam_role.block_ip.arn
  handler          = "lambda_function.handler"
  runtime          = "python3.13"
  memory_size      = 128
  timeout          = 30
  filename         = data.archive_file.block_ip.output_path
  source_code_hash = data.archive_file.block_ip.output_base64sha256
  depends_on       = [aws_iam_role_policy.block_ip, aws_iam_role_policy_attachment.block_ip_basic]
  environment {
    variables = {
      FUNCTION_SECRET = random_password.block_secret.result
      NACL_IDS        = join(",", local.block_nacl_ids)
      WHITELIST_CIDRS = join(",", var.block_whitelist_cidrs)
      BLOCK_TTL_MIN   = tostring(var.block_ttl_min)
      SSM_PREFIX      = var.block_ssm_prefix
      RULE_NUMBER_MIN = "1"
      RULE_NUMBER_MAX = "50"
      NACL_MAX_RULES  = tostring(var.block_nacl_max_rules)
      SLACK_WEBHOOK   = var.block_slack_webhook
    }
  }
}

resource "aws_lambda_function" "unblock_ip" {
  function_name    = "${var.project_name}-unblock-ip"
  role             = aws_iam_role.unblock_ip.arn
  handler          = "lambda_function.handler"
  runtime          = "python3.13"
  memory_size      = 128
  timeout          = 60
  filename         = data.archive_file.unblock_ip.output_path
  source_code_hash = data.archive_file.unblock_ip.output_base64sha256
  depends_on       = [aws_iam_role_policy.unblock_ip, aws_iam_role_policy_attachment.unblock_ip_basic]
  environment {
    variables = {
      SSM_PREFIX = var.block_ssm_prefix
    }
  }
}

# ── block Function URL (인증 NONE — 대신 Lambda 코드가 Bearer 비밀토큰 검증) ──
resource "aws_lambda_function_url" "block_ip" {
  function_name      = aws_lambda_function.block_ip.function_name
  authorization_type = "NONE"
}

# ── unblock 1분 타이머 (EventBridge rate) ────────────────────────────────────
resource "aws_cloudwatch_event_rule" "unblock_tick" {
  name                = "${var.project_name}-unblock-tick"
  schedule_expression = "rate(1 minute)"
}
resource "aws_cloudwatch_event_target" "unblock" {
  rule = aws_cloudwatch_event_rule.unblock_tick.name
  arn  = aws_lambda_function.unblock_ip.arn
}
resource "aws_lambda_permission" "unblock_from_eventbridge" {
  statement_id  = "AllowFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.unblock_ip.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.unblock_tick.arn
}

# ── 출력 (Grafana 차단 컨택트포인트 연결에 사용) ─────────────────────────────
output "block_function_url" {
  value       = aws_lambda_function_url.block_ip.function_url
  description = "Grafana webhook 컨택트포인트의 URL로 사용"
}
output "block_webhook_authorization" {
  value       = "Bearer ${random_password.block_secret.result}"
  sensitive   = true
  description = "Grafana webhook 컨택트포인트의 Authorization 헤더 값 (Bearer 토큰)"
}
