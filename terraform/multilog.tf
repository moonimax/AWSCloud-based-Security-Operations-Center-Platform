# ALB legacy S3 access logs -> Lambda -> CloudWatch -> existing SQS forwarder.
# No S3 expiry rule: retention must be explicitly decided by the operator.
# force_destroy=true: destroy 시 ALB가 남긴 로그 객체 때문에 버킷 삭제가 막히는 걸 방지.
resource "aws_s3_bucket" "alb_logs" {
  bucket_prefix = "${var.project_name}-alb-logs-"
  force_destroy = true
}
resource "aws_s3_bucket_public_access_block" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  block_public_acls = true
  block_public_policy = true
  ignore_public_acls = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_policy" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  policy = jsonencode({Version="2012-10-17", Statement=[{
    Effect="Allow", Principal={Service="logdelivery.elasticloadbalancing.amazonaws.com"},
    Action="s3:PutObject", Resource="${aws_s3_bucket.alb_logs.arn}/alb/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
  }]})
}
resource "aws_cloudwatch_log_group" "alb_access" {
  name = "/${var.project_name}/alb/access"
  retention_in_days = 14
}
resource "aws_cloudwatch_log_stream" "alb_access" {
  name = "alb-s3"
  log_group_name = aws_cloudwatch_log_group.alb_access.name
}
resource "aws_iam_role" "alb_bridge" {
  name = "${var.project_name}-alb-log-bridge"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}
resource "aws_iam_role_policy_attachment" "alb_bridge" {
  role = aws_iam_role.alb_bridge.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_sqs_queue" "alb_bridge_failures" {
  name = "${var.project_name}-alb-bridge-failures"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled = true
}
resource "aws_iam_role_policy" "alb_bridge" {
  role = aws_iam_role.alb_bridge.id
  policy = jsonencode({Version="2012-10-17", Statement=[
    {Effect="Allow",Action="s3:GetObject",Resource="${aws_s3_bucket.alb_logs.arn}/alb/*"},
    {Effect="Allow",Action="logs:PutLogEvents",Resource="${aws_cloudwatch_log_group.alb_access.arn}:*"},
    {Effect="Allow",Action="sqs:SendMessage",Resource=aws_sqs_queue.alb_bridge_failures.arn}
  ]})
}
data "archive_file" "alb_bridge" {
  type = "zip"
  source_file = "${path.module}/lambda/alb-to-cloudwatch/lambda_function.py"
  output_path = "${path.module}/lambda/alb-to-cloudwatch.zip"
}
resource "aws_lambda_function" "alb_bridge" {
  function_name = "${var.project_name}-alb-to-cloudwatch"
  role = aws_iam_role.alb_bridge.arn
  handler = "lambda_function.lambda_handler"
  runtime = "python3.13"
  timeout = 120
  memory_size = 256
  filename = data.archive_file.alb_bridge.output_path
  source_code_hash = data.archive_file.alb_bridge.output_base64sha256
  environment {
    variables = { LOG_GROUP=aws_cloudwatch_log_group.alb_access.name, LOG_STREAM=aws_cloudwatch_log_stream.alb_access.name }
  }
  dead_letter_config { target_arn = aws_sqs_queue.alb_bridge_failures.arn }
  depends_on = [aws_iam_role_policy.alb_bridge, aws_iam_role_policy_attachment.alb_bridge]
}
resource "aws_lambda_permission" "alb_s3" {
  statement_id = "AllowALBLogBucket"
  action = "lambda:InvokeFunction"
  function_name = aws_lambda_function.alb_bridge.function_name
  principal = "s3.amazonaws.com"
  source_arn = aws_s3_bucket.alb_logs.arn
  source_account = data.aws_caller_identity.current.account_id
}
resource "aws_s3_bucket_notification" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  lambda_function {
    lambda_function_arn = aws_lambda_function.alb_bridge.arn
    events = ["s3:ObjectCreated:*"]
    filter_prefix = "alb/AWSLogs/"
    filter_suffix = ".log.gz"
  }
  depends_on = [aws_lambda_permission.alb_s3]
}
locals {
  extra_log_groups = merge(
    {alb=aws_cloudwatch_log_group.alb_access},
    var.enable_flow_logs ? {vpc_flow=aws_cloudwatch_log_group.flow_logs[0]} : {}
  )
}
resource "aws_lambda_permission" "extra_logs" {
  for_each = local.extra_log_groups
  statement_id = "Allow-${each.key}"
  action = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cw_logs_to_sqs.function_name
  principal = "logs.amazonaws.com"
  source_arn = "${each.value.arn}:*"
  source_account = data.aws_caller_identity.current.account_id
}
resource "aws_cloudwatch_log_subscription_filter" "extra_logs" {
  for_each = local.extra_log_groups
  name = "${var.project_name}-${each.key}-to-sqs"
  log_group_name = each.value.name
  filter_pattern = ""
  destination_arn = aws_lambda_function.cw_logs_to_sqs.arn
  depends_on = [aws_lambda_permission.extra_logs]
}
output "alb_log_bucket" { value = aws_s3_bucket.alb_logs.id }
output "multilog_cloudwatch_groups" { value = {for k,v in local.extra_log_groups:k=>v.name} }
