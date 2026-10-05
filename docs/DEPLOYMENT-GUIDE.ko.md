# 배포 가이드 (Terraform 통합 구성)

Terraform 실행 위치는 프로젝트 루트의 `terraform/` 한 곳입니다. 웹 인프라와 관제 서버를 하나의 구성으로 관리합니다.

## 통합 내용

| 파일/설정 | 내용 |
|---|---|
| terraform/main.tf | VPC·서브넷·라우팅·보안 그룹, 단일 AWS provider |
| terraform/ec2_target.tf | 웹 ASG·ALB |
| terraform/monitoring_infra.tf, multilog.tf | CloudWatch·Lambda·SQS·ALB/VPC 로그 수집 |
| terraform/monitoring_server.tf | 관제 EC2·EBS·IAM·배포용 S3 |
| terraform/monitoring_variables.tf, monitoring_outputs.tf | 관제 설정과 접속 출력 |
| terraform/bootstrap.sh.tftpl | 관제 EC2 초기 설치 |
| monitoring-integrated/runtime | 기존 Grafana·Loki·Worker·Nova Micro 검색 앱 |

관제 서버는 `aws_vpc.main`, `aws_subnet.public[0]`, `aws_sqs_queue.log_buffer`를 직접 참조합니다. VPC ID·서브넷 ID·큐 URL을 수동으로 옮기지 않습니다. 관제 서버가 인터넷 경로 생성 전에 초기 설치를 시작하지 않도록 퍼블릭 라우팅 연결에 의존성을 추가했습니다.

리전은 기존 루트 변수 `region`으로 통일했습니다. AWS provider는 기존 관제 구성에 맞춰 `~> 6.0`, Terraform은 `>= 1.6.0, < 2.0.0`입니다. 이전 루트 provider 5.x에서 올라가는 경우 계획을 검토해야 합니다. 원격 상태의 S3 use_lockfile을 사용한다면 Terraform 1.10 이상을 사용하세요.

## A. 아직 배포하지 않은 새 환경

### 1. 준비

Windows: AWS CLI v2, Terraform, SSH 클라이언트. 로컬 Docker 미리보기를 함께 쓸 때만 Docker Desktop과 Python이 필요합니다. `start_nova.ps1 -PrepareOnly`는 Python/Docker를 호출하지 않습니다.

동일 AWS 리전에 EC2 키페어를 만들고 개인키 .pem을 보관합니다. AWS 프로필에는 인프라 생성 권한(EC2·IAM/PassRole·S3·Lambda·SQS·CloudWatch·SSM·ELB 등)이 필요합니다. Bedrock 호출만 가능한 계정으로는 전체 인프라를 만들 수 없습니다.

이번 apply는 관제 서버뿐 아니라 VPC·NAT Gateway·ALB·웹 ASG도 생성합니다. 관제 서버만 배포하던 이전 방식보다 생성 범위가 넓습니다.

### 2. AI 설정 생성

압축을 푼 프로젝트 루트에서 PowerShell:

```powershell
$env:AWS_PROFILE = "monitoring"
aws sts get-caller-identity
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\monitoring-integrated\scripts\start_nova.ps1 -AwsProfile monitoring -Region ap-northeast-2 -PrepareOnly
Copy-Item .\monitoring-integrated\runtime\nova-bedrock.tfvars.json .\terraform\nova.auto.tfvars.json
```

SSO 계정은 필요 시 먼저 `aws sso login --profile monitoring`을 실행합니다. 추론 프로필 조회에는 bedrock:GetInferenceProfile 권한이 필요합니다. 이 단계는 모델 호출 없이 실제 프로필/목적지 ARN을 조회하며 IAM을 변경하지 않습니다. EC2의 InvokeModel 권한은 Terraform이 생성합니다.

생성한 JSON에는 `region`, `bedrock_model_id`, `bedrock_resource_arns`가 들어 있습니다. 같은 계정/리전에서 배포하세요. `nova.auto.tfvars.json`은 terraform.tfvars보다 우선합니다. 서울 외 리전은 azs도 일치하도록 수정해야 합니다.

### 3. 단일 설정 파일 작성

```powershell
cd terraform
Copy-Item .\terraform.tfvars.example .\terraform.tfvars
notepad .\terraform.tfvars
```

필수 변경:

```hcl
key_name   = "서울-리전에-만든-실제-키페어-이름"
admin_cidr = "본인공인IPv4/32"
```

예제 IP는 실제 IP로 교체합니다. `name`은 관제 서버 이름, `project_name`은 전체 인프라 접두사입니다. `enable_flow_logs = true`로 VPC 로그를 수집합니다. ALB 로그 수집은 활성화되어 있습니다. 기본 웹 앱은 nginx 이미지이며 실제 팀 앱을 실행하려면 기존 앱 배포 절차에 맞춰 `app_image`를 설정하세요. ECR 사용 시 웹 EC2의 이미지 pull 권한도 확인합니다.

### 4. 계획 확인 및 생성

```powershell
# 원격 상태(S3)를 쓰는 경우: Copy-Item .\backend.hcl.example .\backend.hcl 후 버킷 이름 입력
terraform init -backend-config=backend.hcl
terraform validate
terraform plan -out=unified.tfplan
terraform apply unified.tfplan
terraform output
```

새 환경인데 계획에 destroy가 나타나면 상태/backend 선택을 확인하세요. apply 완료와 EC2 내부 Docker 설치 완료는 다릅니다.

### 5. 관제 서버 확인

```powershell
$monitorIp = terraform output -raw public_ip
ssh -i "C:\Keys\monitoring.pem" ubuntu@$monitorIp
```

EC2에서:

```bash
sudo cloud-init status --wait
sudo systemctl status monitoring --no-pager
cd /opt/monitoring/runtime
sudo docker compose ps
sudo docker compose logs --tail 100 worker agent gateway
sudo cat /srv/monitoring-data/secrets/credentials.txt
sudo docker compose exec agent python check_bedrock.py
```

마지막 명령은 유료 Bedrock 호출 1회입니다. 실패 시 `sudo tail -n 100 /var/log/cloud-init-output.log`와 `sudo journalctl -u monitoring -n 100 --no-pager`를 확인합니다.

별도 로컬 PowerShell에서:

```powershell
ssh -i "C:\Keys\monitoring.pem" -N -L 18080:127.0.0.1:8080 ubuntu@EC2공인IP
```

http://localhost:18080 접속. EC2에서 확인한 operator 비밀번호를 사용합니다. 8080 인바운드 개방은 필요 없습니다. `terraform output -raw alb_url`은 웹 서비스 주소이며 Sentinel 주소가 아닙니다. 웹 요청 후 worker 성공 로그와 대시보드 데이터를 확인하세요. ALB/VPC 로그는 전달 지연이 있습니다.

## B. 이미 AWS에 배포한 환경: 상태 이전 먼저

**폴더 통합은 두 개의 terraform.tfstate를 자동 합치지 않습니다. 기존 배포가 있다면 새 폴더에서 곧바로 apply하지 마세요.**

1. 두 기존 구성의 자동 배포를 중지하고 같은 큐를 소비하는 별도 worker의 중복 실행도 확인합니다.
2. 기존 루트와 관제 구성의 `terraform state pull` 출력을 각각 별도 파일로 백업합니다. PowerShell 저장 인코딩은 UTF-8로 사용합니다. state에는 민감한 값이 있을 수 있으므로 ZIP/Git에 넣지 않습니다.
3. 통합 `/terraform`은 **기존 루트 state/backend**를 이어받습니다. 새 빈 상태로 기존 VPC 등을 재생성하지 않습니다.
4. 기존 관제 구성의 관리 리소스를 통합 state로 이전합니다. 두 state를 JSON 텍스트로 이어붙이지 않습니다. 원격 backend는 로컬 복제 상태에서 `terraform state mv -state=... -state-out=...` 방식 또는 각 리소스의 공식 import ID에 따른 import를 검토하세요. 원격 state push는 serial/lineage와 동시 실행을 확인한 뒤 수행해야 하며 `-force`로 덮어쓰지 않습니다. 이전이 끝난 뒤 기존 관제 state가 동일 리소스를 다시 관리하지 않게 정리합니다.
5. 이전 관제 구성의 관리 리소스 주소는 통합 구성에서도 그대로 유지했으므로 이름 변경용 moved 블록은 필요 없습니다. 단, 서로 다른 state 간 이전 자체는 별도로 필요합니다. data 블록과 출력값은 관리 리소스처럼 import하지 않습니다.
6. `name`, key_name, admin_cidr, instance_type, data_disk_gb, 기존 Bedrock 설정을 보존합니다. ami_id에 현재 관제 EC2 AMI를 고정하면 최신 AMI 조회에 의한 뜻하지 않은 교체를 피할 수 있습니다. 기존 관제 EBS의 AZ가 통합 구성의 첫 퍼블릭 서브넷 AZ와 일치하는지도 확인합니다.
7. 기존 root provider 5.x 잠금 파일은 백업 후 `terraform init -upgrade`로 6.x에 맞추고, **plan에서 삭제·교체 내역을 검토한 뒤** apply합니다. SQS/VPC 참조 대상이 기존과 같은지 확인합니다.

기존 관제 데이터 EBS에는 `prevent_destroy`가 설정되어 있습니다. 삭제 차단을 해제해 오류를 없애는 방식으로 이전하지 마세요. 기존 배포의 state/리소스 ID를 제공받지 않았으므로 자동 상태 이전은 이 작업에서 수행하지 않았습니다.

## 로컬 실행 및 남은 기능

로컬은 기존 `monitoring-integrated/scripts/start_nova.ps1`을 그대로 사용합니다. IP 전체 집계, 복수 로그 종류 선택, AI 이상 로그 분석 기능은 이번 폴더 통합에 추가하지 않았습니다.

관제 EBS는 기본 40GB, Loki 보관은 7일입니다. 기존 상위 `.github` 워크플로는 이번 작업에서 수정하지 않았습니다. CI를 쓸 경우 통합 tfvars와 기존 state/backend를 제공하도록 별도 설정해야 합니다.

검증 결과와 제한은 [`VALIDATION-UNIFIED.md`](VALIDATION-UNIFIED.md)를 참고하세요. 기존 문서의 분리 Terraform 배포 지침보다 이 문서가 우선합니다.
