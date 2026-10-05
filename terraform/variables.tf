variable "project_name" {
  description = "리소스 이름 접두사"
  type        = string
  default     = "automovitsec"
}

variable "region" {
  description = "AWS 리전"
  type        = string
  default     = "ap-northeast-2"
}

# -----------------------------------------------------------------------------
# 네트워크
# -----------------------------------------------------------------------------
variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "azs" {
  description = "사용할 가용 영역 (ALB는 최소 2개 필요)"
  type        = list(string)
  default     = ["ap-northeast-2a", "ap-northeast-2c"]
}

variable "public_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.11.0/24", "10.0.12.0/24"]
}

variable "enable_flow_logs" {
  description = "VPC Flow Logs 생성 여부 (연습 시 false로 끄면 리소스와 비용이 줄어듦)"
  type        = bool
  default     = true
}

variable "flow_log_retention_days" {
  description = "VPC Flow Logs 보관 기간(일). 장기 보관은 S3 Cold 스토리지로 넘기는 구조"
  type        = number
  default     = 14
}

variable "admin_cidrs" {
  description = "Grafana(3000) 접근을 허용할 팀원 공인 IP 목록 (예: [\"1.2.3.4/32\"]). 비워두면 SSM 포트 포워딩으로 접속"
  type        = list(string)
  default     = []
}

# -----------------------------------------------------------------------------
# 웹서버군
# -----------------------------------------------------------------------------
variable "enable_exporters" {
  description = "웹서버에 Node Exporter(9100)/cAdvisor(8080) 실행 여부. 메트릭을 CloudWatch로만 수집한다면 false"
  type        = bool
  default     = true
}

variable "web_instance_type" {
  type    = string
  default = "t3.micro"
}

# 초반(임시 타깃 서버)에는 1대 → 검증 후 3으로 바꾸면 정식 웹서버군으로 전환
variable "web_desired_capacity" {
  type    = number
  default = 1
}

variable "web_min_size" {
  type    = number
  default = 1
}

variable "web_max_size" {
  type    = number
  default = 3
}

variable "upload_max_size" {
  description = "Nginx client_max_body_size. 파일 업로드 검증 우회 시나리오 테스트에 필요"
  type        = string
  default     = "10m"
}

variable "app_image" {
  description = "웹서버에서 실행할 앱 컨테이너 이미지. ECR 준비 전까지는 공개 nginx 이미지로 임시 운영"
  type        = string
  default     = "bkimminich/juice-shop:v17.3.0"
}

# -----------------------------------------------------------------------------
# ALB 접근 허용 IP — Juice Shop은 일부러 취약하게 만든 앱이므로 인터넷 전체 공개 금지
# -----------------------------------------------------------------------------
variable "alb_ingress_cidrs" {
  description = "미사용 (테스트 기간 ALB 전체 공개). 호환성 위해 변수만 유지"
  type        = list(string)
  default     = []
}

