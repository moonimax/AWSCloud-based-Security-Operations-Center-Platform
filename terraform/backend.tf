# =============================================================================
# 원격 상태(tfstate) 저장소 — 팀원과 GitHub Actions가 같은 상태를 공유하기 위해 필요
#
# 버킷 이름은 계정 정보가 코드에 남지 않도록 "부분 구성(partial configuration)"으로
# init 시점에 주입합니다.
#
# 사용 방법
#  1) S3 버킷을 콘솔/CLI로 먼저 1회 생성 (버전 관리 + 퍼블릭 액세스 차단 켜기)
#  2) 로컬: cp backend.hcl.example backend.hcl 후 버킷 이름 입력 (backend.hcl은 커밋 금지)
#           terraform init -backend-config=backend.hcl
#  3) CI  : GitHub Secret TF_STATE_BUCKET 에 버킷 이름 등록 (infra-deploy.yml이 주입)
#
# use_lockfile(S3 자체 잠금)은 Terraform 1.10 이상에서 동작합니다.
# 더 낮은 버전을 쓴다면 DynamoDB 테이블 잠금(dynamodb_table)을 사용하세요.
# =============================================================================

terraform {
  backend "s3" {
    key          = "infra/terraform.tfstate"
    region       = "ap-northeast-2"
    encrypt      = true
    use_lockfile = true
  }
}
