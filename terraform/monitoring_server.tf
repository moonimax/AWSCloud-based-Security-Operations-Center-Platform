#1001 16:30 쿼리 버그 수정 테라폼 배포
data "aws_ami" "ubuntu" {
  most_recent = true
  owners = ["099720109477"]
  filter {
    name = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name = "virtualization-type"
    values = ["hvm"]
  }
}
locals {
  # 배포 파일을 명시적으로 선택한다. secrets, .env, data는 패키지에 포함하지 않는다.
  runtime_files = concat(
    ["compose.yaml", "gateway/default.conf", "loki/config.yaml"],
    
    # agent 폴더 내 모든 파일 (py, html, Dockerfile, requirements.txt, analysis.js 등) 자동 포함
    [for f in fileset("${path.module}/../monitoring-integrated/runtime/agent", "**") : "agent/${f}"],
    
    # worker: attack_patterns.py는 worker.py가 import하므로 반드시 포함 (누락 시 worker 기동 실패)
    ["worker/worker.py", "worker/attack_patterns.py", "worker/Dockerfile", "worker/requirements.txt"],
    
    # demo 폴더는 Git에서 삭제되었으므로 통째로 제거함
    

    #["grafana/provisioning/datasources/loki.yaml","grafana/provisioning/datasources/cloudwatch.yaml","grafana/provisioning/datasources/testdata.yaml"],
    [for f in fileset("${path.module}/../monitoring-integrated/runtime/grafana/provisioning/datasources", "*.yaml") : "grafana/provisioning/datasources/${f}"],

    # grafana provisioning dashboards
    ["grafana/provisioning/dashboards/default.yaml"],
    
    # grafana dashboards: 실제 폴더에 존재하는 *.json 파일 자동 포함
    [for f in fileset("${path.module}/../monitoring-integrated/runtime/grafana/dashboards", "*.json") : "grafana/dashboards/${f}"],

    ["grafana/provisioning/alerting/rules.yaml"]
  )

  # [추가] Bedrock 모델 설정
  # GitHub Actions에는 terraform.tfvars가 없어 변수 기본값(빈 값)이 적용된다. 그러면 BEDROCK_MODEL_ID가 비어 AI가 꺼진다.
  # 변수/tfvars로 모델 ID를 주면 그 값이 우선하고, 비어 있으면 아래 기본값을 사용한다.
  bedrock_model_id          = coalesce(var.bedrock_model_id, "apac.amazon.nova-pro-v1:0")
  bedrock_analysis_model_id = "apac.amazon.nova-pro-v1:0"
  # 메인·분석 모델 외에 추가로 허용할 추론 프로필 ID (없으면 빈 목록)
  bedrock_extra_profile_ids = []

  # 사용하는 추론 프로필과 그 뒤의 기반 모델 ARN. 모델 ID(추론 프로필 ID)만 바꾸면 IAM 권한이 함께 따라간다.
  bedrock_profile_ids     = distinct(compact(concat([local.bedrock_model_id, local.bedrock_analysis_model_id], local.bedrock_extra_profile_ids)))
  bedrock_profile_arns    = [for id in local.bedrock_profile_ids : "arn:aws:bedrock:${var.region}:${data.aws_caller_identity.current.account_id}:inference-profile/${id}"]
  # apac. / global. 같은 프로필 접두사를 뗀 기반 모델 ARN (실제 리전은 프로필이 정하므로 *)
  bedrock_foundation_arns = [for id in local.bedrock_profile_ids : "arn:aws:bedrock:*::foundation-model/${replace(id, "/^(apac|global|us|eu|jp|au)\\./", "")}"]

  # [변경] user_data와 재배포(terraform_data.deploy)가 같은 값을 쓰도록 locals로 분리
  datasource_b64 = base64encode(yamlencode({
    apiVersion = 1
    datasources = concat([
      { name = "Loki", uid = "loki", type = "loki", access = "proxy", url = "http://loki:3100", isDefault = true, editable = false }
    ], var.enable_cloudwatch_datasource ? [
      { name = "CloudWatch", uid = "cloudwatch", type = "cloudwatch", access = "proxy", editable = false, jsonData = { authType = "default", defaultRegion = var.region } }
    ] : [])
  }))
  runtime_env_b64 = base64encode(join("\n", [
    "AWS_REGION=${var.region}",
    "QUEUE_URL=${aws_sqs_queue.log_buffer.url}",
    # [추가] 허용 IP가 있을 때만 gateway를 외부 인터페이스에 바인딩 (없으면 기존처럼 localhost 전용)
    "GATEWAY_BIND=${length(var.dashboard_cidrs) > 0 ? "0.0.0.0" : "127.0.0.1"}",
    "BEDROCK_MODEL_ID=${local.bedrock_model_id}",
    "BEDROCK_ANALYSIS_MODEL_ID=${local.bedrock_analysis_model_id}",
    "DATA_ROOT=/srv/monitoring-data"
  ]))

  # [추가] Grafana 차단 컨택트포인트/정책 — Function URL + Bearer 비밀을 terraform이 주입(깃엔 미포함).
  #  firing → 이 webhook(차단 Lambda) 로만 보냄. Lambda가 차단(block=on) 후 슬랙 전송, R2(block=off)는 슬랙만.
  contactpoint_b64 = base64encode(yamlencode({
    apiVersion = 1
    contactPoints = [{
      orgId = 1
      name  = "soc-block-webhook"
      receivers = [{
        uid  = "soc-block-webhook"
        type = "webhook"
        settings = {
          url                       = aws_lambda_function_url.block_ip.function_url
          httpMethod                = "POST"
          authorization_scheme      = "Bearer"
          authorization_credentials = random_password.block_secret.result
        }
      }]
    }]
  }))
  policy_b64 = base64encode(yamlencode({
    apiVersion = 1
    policies = [{
      orgId           = 1
      receiver        = "soc-block-webhook"
      group_by        = ["grafana_folder", "alertname", "ip"]
      group_wait      = "10s"
      group_interval  = "30s"
      repeat_interval = "5m"
    }]
  }))

  # [추가] 실행 중인 인스턴스에서 새 번들을 적용하는 명령 (bootstrap.sh.tftpl의 배포 부분과 동일한 경로·옵션)
  deploy_commands = [
    "set -e",
    "/usr/local/bin/aws s3 cp s3://${aws_s3_bucket.artifact.id}/${aws_s3_object.bundle.key} /tmp/monitoring.zip",
    # 번들에서 빠진 대시보드가 남지 않도록 대시보드 폴더만 비우고 푼다 (.env, secrets 링크는 유지)
    "rm -rf /opt/monitoring/runtime/grafana/dashboards",
    "unzip -o /tmp/monitoring.zip -d /opt/monitoring",
    # bootstrap과 동일하게 번들의 loki.yaml을 생성 파일로 덮어쓰고 .env 갱신
    "echo ${local.datasource_b64} | base64 -d > /opt/monitoring/runtime/grafana/provisioning/datasources/loki.yaml",
    "echo ${local.runtime_env_b64} | base64 -d > /opt/monitoring/runtime/.env",
    # [추가] 차단 컨택트포인트/정책 주입 (rules.yaml은 번들에 포함, 이 둘은 비밀 포함이라 base64로 주입)
    "mkdir -p /opt/monitoring/runtime/grafana/provisioning/alerting",
    "echo ${local.contactpoint_b64} | base64 -d > /opt/monitoring/runtime/grafana/provisioning/alerting/contactpoints.yaml",
    "echo ${local.policy_b64} | base64 -d > /opt/monitoring/runtime/grafana/provisioning/alerting/policies.yaml",
    "cd /opt/monitoring/runtime && docker compose --profile aws up -d --build --remove-orphans",
    # 데이터소스 프로비저닝 재적용
    "docker compose --profile aws restart grafana",
    # agent가 재생성되면 IP가 바뀌므로 gateway가 새 주소를 다시 조회하도록 재시작
    "docker compose --profile aws restart gateway"
  ]
}
data "archive_file" "bundle" {
  type = "zip"
  output_path = "${path.module}/deployment.zip"
  dynamic "source" {
    for_each = toset(local.runtime_files)
    content {
      content = file("${path.module}/../monitoring-integrated/runtime/${source.value}")
      filename = "runtime/${source.value}"
    }
  }
  source {
    content = file("${path.module}/../monitoring-integrated/scripts/init_local.py")
    filename = "scripts/init_local.py"
  }
  source {
    content = file("${path.module}/../monitoring-integrated/scripts/password_hash.py")
    filename = "scripts/password_hash.py"
  }
}
resource "aws_s3_bucket" "artifact" {
  bucket_prefix = "${var.name}-"
  force_destroy = true
}
resource "aws_s3_bucket_public_access_block" "artifact" {
  bucket = aws_s3_bucket.artifact.id
  block_public_acls = true
  block_public_policy = true
  ignore_public_acls = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "artifact" {
  bucket = aws_s3_bucket.artifact.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_object" "bundle" {
  bucket = aws_s3_bucket.artifact.id
  key = "releases/${data.archive_file.bundle.output_sha256}.zip"
  source = data.archive_file.bundle.output_path
  source_hash = data.archive_file.bundle.output_sha256
  server_side_encryption = "AES256"
  depends_on = [aws_s3_bucket_public_access_block.artifact]
}
resource "aws_security_group" "monitor" {
  name_prefix = "${var.name}-"
  vpc_id = aws_vpc.main.id
  ingress {
    protocol = "tcp"
    from_port = 22
    to_port = 22
    cidr_blocks = [var.admin_cidr]
  }
  # [추가] 지정한 IP(와이파이 공인 IP 등)에서만 대시보드(gateway 8080) 접속 허용
  dynamic "ingress" {
    for_each = length(var.dashboard_cidrs) > 0 ? [1] : []
    content {
      description = "Dashboard (gateway 8080, Basic Auth)"
      protocol    = "tcp"
      from_port   = 8080
      to_port     = 8080
      cidr_blocks = var.dashboard_cidrs
    }
  }
  egress {
    protocol = "-1"
    from_port = 0
    to_port = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_iam_role" "monitor" {
  name_prefix = "${var.name}-"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }] })
}
resource "aws_iam_role_policy" "monitor" {
  role = aws_iam_role.monitor.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"], Resource = aws_sqs_queue.log_buffer.arn },
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = aws_s3_object.bundle.arn },
      # Grafana CloudWatch 대시보드(Logs Insights) 조회용
      { Effect = "Allow", Action = ["logs:StartQuery", "logs:GetLogGroupFields"], Resource = [
          "arn:aws:logs:${var.region}:*:log-group:/automovitsec/*",
          "arn:aws:logs:${var.region}:*:log-group:/automovitsec/*:*"
      ] },
      { Effect = "Allow", Action = ["logs:GetQueryResults", "logs:StopQuery", "logs:DescribeLogGroups"], Resource = "*" },
      # EC2 인프라 대시보드(CloudWatch 메트릭) 조회용 - enable_cloudwatch_datasource와 무관하게 항상 적용
      { Effect = "Allow", Action = ["cloudwatch:ListMetrics", "cloudwatch:GetMetricData", "cloudwatch:GetMetricStatistics", "ec2:DescribeInstances", "ec2:DescribeRegions", "ec2:DescribeTags", "tag:GetResources"], Resource = "*" },
      # [변경] Bedrock: 추론 프로필 조회 / 프로필 호출 / 프로필을 통한 기반 모델 호출
      {
        Sid      = "ReadConfiguredInferenceProfiles"
        Effect   = "Allow"
        Action   = "bedrock:GetInferenceProfile"
        Resource = local.bedrock_profile_arns
      },
      {
        Sid      = "InvokeConfiguredInferenceProfiles"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = local.bedrock_profile_arns
      },
      {
        Sid       = "InvokeBackingModelsThroughConfiguredProfiles"
        Effect    = "Allow"
        Action    = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource  = local.bedrock_foundation_arns
        Condition = { StringEquals = { "bedrock:InferenceProfileArn" = local.bedrock_profile_arns } }
      }
    ], var.sqs_kms_key_arn == null ? [] : [
      { Effect = "Allow", Action = ["kms:Decrypt"], Resource = var.sqs_kms_key_arn }
    ])
  })
}
resource "aws_iam_role_policy_attachment" "monitor" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
  ])

  role       = aws_iam_role.monitor.name
  policy_arn = each.value
}
resource "aws_iam_instance_profile" "monitor" {
  name_prefix = "${var.name}-"
  role = aws_iam_role.monitor.name
}
resource "aws_ebs_volume" "data" {
  availability_zone = aws_subnet.public[0].availability_zone
  size = var.data_disk_gb
  type = "gp3"
  encrypted = true
  tags = { Name = "${var.name}-data" }
  lifecycle { prevent_destroy = true }
}
resource "aws_instance" "monitor" {
  ami = coalesce(var.ami_id, data.aws_ami.ubuntu.id)
  instance_type = var.instance_type
  subnet_id = aws_subnet.public[0].id
  vpc_security_group_ids = [aws_security_group.monitor.id]
  associate_public_ip_address = true
  key_name = var.key_name
  iam_instance_profile = aws_iam_instance_profile.monitor.name
  metadata_options {
    http_endpoint = "enabled"
    http_tokens = "required"
    # Docker bridge 내부의 Worker·Agent가 IAM 역할 자격증명을 읽도록 2로 설정한다.
    http_put_response_hop_limit = 2
  }
  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted = true
  }
  credit_specification { cpu_credits = "standard" }
  # [변경] 번들이 바뀌어도 인스턴스를 교체하지 않는다. 배포는 terraform_data.deploy가 담당
  user_data_replace_on_change = false
  user_data = templatefile("${path.module}/bootstrap.sh.tftpl", {
    bucket          = aws_s3_bucket.artifact.id
    datasource_b64  = local.datasource_b64
    key             = aws_s3_object.bundle.key
    volume_serial   = replace(aws_ebs_volume.data.id, "-", "")
    runtime_env_b64 = local.runtime_env_b64
    contactpoint_b64 = local.contactpoint_b64
    policy_b64 = local.policy_b64
  })
  tags = { Name = var.name }
  depends_on = [aws_iam_role_policy.monitor, aws_s3_object.bundle, aws_route_table_association.public]
  lifecycle {
    # [추가] user_data는 최초 부팅용. 변경돼도 중지·재시작·교체하지 않는다
    ignore_changes = [user_data]
  }
}
resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id = aws_ebs_volume.data.id
  instance_id = aws_instance.monitor.id
  stop_instance_before_detaching = true
}

# [추가] 번들(또는 데이터소스·.env 설정)이 바뀌면 실행 중인 인스턴스에 SSM으로 재배포
resource "terraform_data" "deploy" {
  triggers_replace = [
    aws_s3_object.bundle.key,
    local.datasource_b64,
    local.runtime_env_b64,
    local.contactpoint_b64,
    local.policy_b64,
  ]

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      CMD_ID=$(aws ssm send-command --region ${var.region} \
        --instance-ids ${aws_instance.monitor.id} \
        --document-name AWS-RunShellScript \
        --comment "monitoring deploy" \
        --parameters '${jsonencode({ commands = local.deploy_commands, executionTimeout = ["1800"] })}' \
        --query Command.CommandId --output text)
      echo "SSM command: $CMD_ID"
      # aws ssm wait는 약 100초 후 포기하므로 docker build 시간을 고려해 직접 최대 15분 대기
      for i in $(seq 1 90); do
        sleep 10
        STATUS=$(aws ssm get-command-invocation --region ${var.region} --command-id "$CMD_ID" \
          --instance-id ${aws_instance.monitor.id} --query Status --output text 2>/dev/null || echo Pending)
        case "$STATUS" in
          Success) echo "배포 완료"; exit 0 ;;
          Pending|InProgress|Delayed) ;;
          *)
            echo "배포 실패: $STATUS"
            aws ssm get-command-invocation --region ${var.region} --command-id "$CMD_ID" \
              --instance-id ${aws_instance.monitor.id} --query StandardErrorContent --output text
            exit 1 ;;
        esac
      done
      echo "배포 대기 시간 초과"; exit 1
    EOT
  }

  depends_on = [aws_iam_role_policy.monitor, aws_volume_attachment.data]
}
