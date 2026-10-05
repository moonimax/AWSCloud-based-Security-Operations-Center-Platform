<#
.SYNOPSIS
  실습 인프라 전체 정리 스크립트 (단일 Terraform 루트: terraform/)

.DESCRIPTION
  terraform/ 한 곳에서 관제 서버와 기반 인프라(VPC, ALB, 웹서버, SQS, Lambda ...)를
  함께 관리하므로, Terraform이 의존성 순서대로 삭제합니다.

    1) terraform destroy  (관제 로그 디스크는 기본 보존)
    2) 남은 과금 리소스 확인 (NAT, 실행 중 EC2, ALB)

  원격 상태를 쓰는 경우 terraform/backend.hcl 이 있으면 init 시 자동으로 사용합니다.

.PARAMETER DeleteMonitoringData
  관제 서버의 로그 디스크(prevent_destroy 보호)까지 삭제합니다.
  지정하지 않으면 실행 중에 물어봅니다. 보존하면 다음 apply 때 같은 디스크를 다시 사용합니다.

.PARAMETER Yes
  시작 전 확인 질문을 건너뜁니다.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\destroy-all.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\destroy-all.ps1 -DeleteMonitoringData
#>
param(
    [switch]$DeleteMonitoringData,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
$Root      = Split-Path -Parent $PSScriptRoot
$BaseDir   = Join-Path $Root 'terraform'
$DiskFile  = Join-Path $BaseDir 'monitoring_server.tf'
$DiskAddr  = 'aws_ebs_volume.data'
$Timer     = [System.Diagnostics.Stopwatch]::StartNew()

function Write-Step($msg) { Write-Host "`n==== $msg ====" -ForegroundColor Cyan }
function Fail($msg)       { Write-Host "[중단] $msg" -ForegroundColor Red; exit 1 }

function Get-StateList {
    $items = & terraform state list 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $items) { return @() }
    return @($items)
}

function Initialize-Terraform {
    if (-not (Test-Path '.terraform')) {
        Write-Host 'terraform init 실행 중...'
        $initArgs = @('init', '-input=false')
        if (Test-Path 'backend.hcl') { $initArgs += '-backend-config=backend.hcl' }
        & terraform @initArgs | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "terraform init 실패: $(Get-Location) (원격 상태를 쓴다면 backend.hcl 을 확인하세요)" }
    }
}

# -----------------------------------------------------------------------------
# 0. 사전 확인
# -----------------------------------------------------------------------------
foreach ($cmd in 'terraform', 'aws') {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { Fail "$cmd 명령을 찾을 수 없습니다." }
}
if (-not (Test-Path $BaseDir)) { Fail "폴더가 없습니다: $BaseDir" }

$identity = & aws sts get-caller-identity --query '[Account,Arn]' --output text 2>$null
if ($LASTEXITCODE -ne 0) { Fail 'AWS 로그인 정보를 확인할 수 없습니다. aws configure 를 먼저 확인하세요.' }
$account, $arn = $identity -split '\s+'

Write-Step '정리 대상 확인'
Write-Host "AWS 계정 : $account"
Write-Host "사용자   : $arn"

if (-not $Yes) {
    $answer = Read-Host "위 계정의 실습 인프라를 모두 삭제합니다. 계속하려면 계정 번호($account)를 입력하세요"
    if ($answer -ne $account) { Fail '입력이 일치하지 않아 취소했습니다.' }
}

# -----------------------------------------------------------------------------
# 1. 인프라 삭제 (terraform/)
# -----------------------------------------------------------------------------
Write-Step '1/2 인프라 정리'
Push-Location $BaseDir
try {
    Initialize-Terraform
    $managed = @(Get-StateList | Where-Object { $_ -notlike 'data.*' })

    if ($managed.Count -eq 0) {
        Write-Host '관리 중인 리소스가 없습니다. 건너뜁니다.'
    }
    else {
        $hasDataDisk = $managed -contains $DiskAddr
        $deleteDisk  = $DeleteMonitoringData.IsPresent

        if ($hasDataDisk -and -not $DeleteMonitoringData -and -not $Yes) {
            $reply = Read-Host '관제 로그 디스크도 삭제할까요? 보존하면 소액의 EBS 요금이 계속 나옵니다 [y/N]'
            $deleteDisk = ($reply -match '^(y|yes)$')
        }

        if (-not $hasDataDisk -or $deleteDisk) {
            # prevent_destroy 보호를 잠깐 해제했다가, 성공·실패와 상관없이 원래 파일로 복원
            $utf8     = New-Object System.Text.UTF8Encoding $false
            $original = [System.IO.File]::ReadAllText($DiskFile, $utf8)
            try {
                $patched = $original -replace 'prevent_destroy\s*=\s*true', 'prevent_destroy = false'
                [System.IO.File]::WriteAllText($DiskFile, $patched, $utf8)
                & terraform destroy -auto-approve -input=false
                if ($LASTEXITCODE -ne 0) { Fail 'terraform destroy 실패. 위 오류 메시지를 확인하세요.' }
            }
            finally {
                [System.IO.File]::WriteAllText($DiskFile, $original, $utf8)
                Write-Host 'monitoring_server.tf 보호 설정을 원래대로 복원했습니다.'
            }
        }
        else {
            # 로그 디스크만 남기고 나머지 전부 삭제 (다음 apply 때 같은 디스크 재사용)
            # for_each/count 인덱스(["alb"], [0])는 PowerShell 인자 따옴표 문제를 피하려고 리소스 단위로 묶는다.
            $targets = $managed |
                ForEach-Object { $_ -replace '\[.*\]$', '' } |
                Where-Object { $_ -ne $DiskAddr } |
                Sort-Object -Unique |
                ForEach-Object { "-target=$_" }
            & terraform destroy -auto-approve -input=false @targets
            if ($LASTEXITCODE -ne 0) { Fail 'terraform destroy 실패. 위 오류 메시지를 확인하세요.' }
            Write-Host '로그 디스크는 보존했습니다. 완전히 지우려면 -DeleteMonitoringData 옵션으로 다시 실행하세요.' -ForegroundColor Yellow
        }
    }
}
finally { Pop-Location }

# -----------------------------------------------------------------------------
# 2. 과금 리소스가 남았는지 확인
# -----------------------------------------------------------------------------
Write-Step '2/2 남은 과금 리소스 확인'
$nat = & aws ec2 describe-nat-gateways --filter 'Name=state,Values=pending,available' --query 'length(NatGateways)' --output text
$ec2 = & aws ec2 describe-instances --filters 'Name=instance-state-name,Values=pending,running,stopping,stopped' --query 'length(Reservations[].Instances[])' --output text
$alb = & aws elbv2 describe-load-balancers --query 'length(LoadBalancers)' --output text

Write-Host "NAT Gateway   : $nat 개"
Write-Host "EC2 인스턴스  : $ec2 개"
Write-Host "로드 밸런서   : $alb 개"

$Timer.Stop()
if ("$nat$ec2$alb" -eq '000') {
    Write-Host ("`n정리 완료 ({0:mm}분 {0:ss}초)" -f $Timer.Elapsed) -ForegroundColor Green
}
else {
    Write-Host ("`n남은 리소스가 있습니다. 다른 사람이 만든 것인지 콘솔에서 확인하세요. ({0:mm}분 {0:ss}초)" -f $Timer.Elapsed) -ForegroundColor Yellow
}
