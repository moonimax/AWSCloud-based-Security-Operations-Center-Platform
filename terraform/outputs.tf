# =============================================================================
# Outputs — 팀원들이 이 인프라를 참조하는 창구
# =============================================================================

output "vpc_id" {
  description = "통합 구성에서 생성한 VPC"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "통합 구성에서 생성한 퍼블릭 서브넷"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "web_sg_id" {
  value = aws_security_group.web.id
}

output "monitoring_sg_id" {
  description = "웹서버 메트릭 접근이 허용된 보안 그룹 (자체 구성 모니터링 서버용)"
  value       = aws_security_group.monitoring.id
}

output "alb_url" {
  description = "웹 서비스 접속 주소 / OWASP ZAP 점검 대상"
  value       = "http://${aws_lb.web.dns_name}"
}

output "web_asg_name" {
  description = "배포 대상 식별용 (app-deploy.yml)"
  value       = aws_autoscaling_group.web.name
}

output "web_role_name" {
  description = "웹서버 IAM 역할 이름"
  value       = aws_iam_role.web.name
}

output "log_buffer_queue_name" {
  description = "관제 Worker가 자동으로 사용하는 SQS 큐"
  value       = aws_sqs_queue.log_buffer.name
}

output "flow_log_group_name" {
  description = "VPC Flow Logs 로그 그룹 (구독 필터 연결 가능)"
  value       = var.enable_flow_logs ? aws_cloudwatch_log_group.flow_logs[0].name : null
}

