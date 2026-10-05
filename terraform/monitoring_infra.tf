# Monitoring Pipeline: SQS Buffer Queue, EventBridge Rules, S3 Cold Storage

variable "queue_name" {
  type        = string
  default     = "log-buffer-queue"
  description = "로그 버퍼링용 SQS Standard 큐 이름"
}

variable "sqs_message_retention_seconds" {
  type        = number
  default     = 345600 # new-branch1 기존 4일 유지
  description = "메인 큐 메시지 보관 기간(초)"
}

variable "sqs_visibility_timeout_seconds" {
  type        = number
  default     = 60
  description = "가시성 타임아웃(초). sqs-worker의 처리 시간보다 넉넉하게 설정"
}

variable "sqs_max_receive_count" {
  type        = number
  default     = 5
  description = "DLQ로 이동시키기 전 최대 수신(처리 실패) 횟수"
}

# 처리 실패한 메시지가 쌓이는 Dead Letter Queue
resource "aws_sqs_queue" "log_buffer_dlq" {
  name                      = "${var.queue_name}-dlq"
  message_retention_seconds = 1209600 # new-branch1 기존 14일 유지
  sqs_managed_sse_enabled   = true
}

# CloudWatch Logs -> Lambda 에서 전달받는 메인 버퍼 큐
resource "aws_sqs_queue" "log_buffer" {
  name                       = var.queue_name
  message_retention_seconds  = var.sqs_message_retention_seconds
  visibility_timeout_seconds = var.sqs_visibility_timeout_seconds
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.log_buffer_dlq.arn
    maxReceiveCount     = var.sqs_max_receive_count
  })
}

# DLQ가 log_buffer 큐로부터만 redrive를 허용하도록 명시
resource "aws_sqs_queue_redrive_allow_policy" "log_buffer_dlq" {
  queue_url = aws_sqs_queue.log_buffer_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.log_buffer.arn]
  })
}

output "log_buffer_queue_arn" {
  value = aws_sqs_queue.log_buffer.arn
}

output "log_buffer_queue_url" {
  value = aws_sqs_queue.log_buffer.url
}

# =============================================================================
# 웹서버 nginx 로그 -> CloudWatch Logs -> Lambda -> SQS 배선
#  - Lambda(cw-logs-to-sqs)까지 전부 이 terraform이 관리 (다른 AWS 계정으로 옮겨도 apply만으로 재현됨)
#  - 주의: /${var.project_name}/nginx/access 로그그룹이 이미 수동으로 만들어져 있다면
#          최초 apply 전에 `terraform import aws_cloudwatch_log_group.web_nginx_access /${var.project_name}/nginx/access` 필요
# =============================================================================
data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cw_logs_to_sqs" {
  name               = "${var.project_name}-cw-logs-to-sqs-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "cw_logs_to_sqs_basic" {
  role       = aws_iam_role.cw_logs_to_sqs.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# SendMessage만 필요 (기존엔 AmazonSQSFullAccess를 썼지만 최소 권한으로 좁힘)
resource "aws_iam_role_policy" "cw_logs_to_sqs_sqs_send" {
  name = "sqs-send"
  role = aws_iam_role.cw_logs_to_sqs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.log_buffer.arn
      }
    ]
  })
}

data "archive_file" "cw_logs_to_sqs" {
  type        = "zip"
  source_file = "${path.module}/lambda/cw-logs-to-sqs/lambda_function.py"
  output_path = "${path.module}/lambda/cw-logs-to-sqs.zip"
}

# CloudWatch Logs 구독 이벤트(gzip+base64)를 풀어서 SQS로 전달
resource "aws_lambda_function" "cw_logs_to_sqs" {
  function_name    = "${var.project_name}-cw-logs-to-sqs"
  role             = aws_iam_role.cw_logs_to_sqs.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.13" # provider(aws ~> 5.100)가 python3.14를 아직 인식 못 해서 한 단계 낮춤
  memory_size      = 128
  timeout          = 120
  filename         = data.archive_file.cw_logs_to_sqs.output_path
  source_code_hash = data.archive_file.cw_logs_to_sqs.output_base64sha256
  depends_on = [aws_iam_role_policy.cw_logs_to_sqs_sqs_send, aws_iam_role_policy_attachment.cw_logs_to_sqs_basic]

  environment {
    variables = {
      QUEUE_URL = aws_sqs_queue.log_buffer.url
    }
  }
}

resource "aws_cloudwatch_log_group" "web_nginx_access" {
  name              = "/${var.project_name}/nginx/access"
  retention_in_days = 14
}

resource "aws_lambda_permission" "allow_cwl_web_nginx" {
  statement_id   = "AllowCWL${replace(title(var.project_name), "-", "")}Nginx"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.cw_logs_to_sqs.function_name
  principal      = "logs.amazonaws.com"
  source_arn     = "${aws_cloudwatch_log_group.web_nginx_access.arn}:*"
  source_account = data.aws_caller_identity.current.account_id
}

resource "aws_cloudwatch_log_subscription_filter" "web_nginx_to_sqs" {
  name            = "${var.project_name}-nginx-to-sqs"
  log_group_name  = aws_cloudwatch_log_group.web_nginx_access.name
  filter_pattern  = ""
  destination_arn = aws_lambda_function.cw_logs_to_sqs.arn

  depends_on = [aws_lambda_permission.allow_cwl_web_nginx]
}
