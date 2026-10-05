# =============================================================================
# 웹서버용 IAM 역할 (인스턴스 프로파일)
#  - SSM: SSH(22) 없이 Session Manager로 접속, Run Command로 배포
#  - CloudWatch Agent: 로그 송출
#  - ECR ReadOnly: 앱 이미지 pull
# =============================================================================
data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "web" {
  name               = "${var.project_name}-web-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "web" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
  ])

  role       = aws_iam_role.web.name
  policy_arn = each.value
}

resource "aws_iam_instance_profile" "web" {
  name = "${var.project_name}-web-profile"
  role = aws_iam_role.web.name
}

# TODO(팀 협의):
#  - 모니터링 서버용 역할 (SQS 읽기, S3/Athena, EC2 Describe → Prometheus 자동 탐색)
#  - GitHub Actions용 OIDC 역할 (terraform apply / ECR push)
