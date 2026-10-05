# [가이드라인] Git 브랜치 동기화 및 코드 유실 방지 수칙

본 문서는 브랜치 미동기화로 인해 기존 코드가 누락되거나 배포 장애가 발생하는 문제를 방지하기 위한 표준 개발 규칙입니다.

---

## 1. 핵심 원칙: "작업 전후 무조건 최신 `main` 동기화"

1. **오래된 브랜치에서 작업하지 않는다:** 항상 최신 `main`을 기반으로 새 브랜치를 생성합니다.
2. **머지/PR 직전에는 반드시 최신 `main`을 내 브랜치로 가져와 충돌을 해결한다:** 내 브랜치에서 먼저 충돌을 해결하고 검증해야 `main` 브랜치가 깨지지 않습니다.
3. **`main` 브랜치 직접 Push는 원칙적으로 금지한다:** 모든 작업은 기능 브랜치 + PR(Pull Request) 단계를 거칩니다.

---

## 2. 표준 기능 개발 라이프사이클 (Work-flow)

### [1단계] 작업 시작 전 최신 `main` 기반 브랜치 생성

```bash
# 1. main 브랜치로 이동 및 최신 변경 사항 pull
git checkout main
git pull origin main

# 2. 최신 main을 기반으로 기능 브랜치 생성 및 이동
git checkout -b feature/issue-name
```

---

### [2단계] 작업 중 지속적인 최신 `main` 동기화

팀원이 `main`에 코드를 반영했다면, 내 작업 도중에도 수시로 최신 내용을 내 브랜치로 가져와야 합니다.

```bash
# 1. 작업 중이던 변경 사항을 임시 저장하거나 커밋
git add .
git commit -m "WIP: 작업 진행 중"
# (커밋 대신 임시 보관하려면: git stash)

# 2. 원격 저장소 최신 정보 fetch
git fetch origin main

# 3. 최신 main 변경분을 현재 브랜치로 병합
git merge origin/main
# (커밋 히스토리를 한 줄로 유지하려면: git rebase origin/main)

# 4. 임시 저장(stash)했던 경우 복원
# git stash pop
```

---

### [3단계] PR 생성 전 최종 동기화 및 로컬 사전 검증

작업을 완료한 후, 원격에 올리기 전에 반드시 최신 `main`과 합치고 로컬 검증을 마쳐야 합니다.

```bash
# 1. 작업 내용 최종 커밋
git add .
git commit -m "feat: 구현 기능 요약"

# 2. 최신 main을 다시 한번 내 브랜치로 merge
git fetch origin main
git merge origin/main

# 3. 만약 충돌(Conflict)이 발생한 경우:
# - 에디터(VS Code 등)에서 충돌 코드 정리 (<<<<<<<, =======, >>>>>>> 삭제)
# - 충돌 해결 후 staging 및 merge 커밋 생성:
git add .
git commit -m "fix: resolve merge conflicts with main"

# 4. 로컬 사전 검증 실행 (Terraform 및 Docker 문법 오류 방지)
cd terraform
terraform validate
terraform plan

cd ../monitoring-integrated/runtime
docker compose config

# 5. 모든 검증을 통과하면 원격 브랜치로 푸시
git push origin feature/issue-name
```

---

## 3. GitHub 리포지토리 보호 설정 (Admin 권장)

실수를 시스템적으로 방지하기 위해 저장소 Settings -> Branches -> Branch protection rules에 다음 설정을 적용합니다:

```text
[규칙 설정 가이드]
1. Branch name pattern: main
2. 체크할 항목:
   - [x] Require a pull request before merging
     - [x] Require approvals (최소 1명 승인)
   - [x] Require status checks to pass before merging
     - [x] Require branches to be up to date before merging (필수: 최신 main 미반영 시 머지 불가)
     - 검색창에서 통과해야 할 CI Job 등록 (예: terraform-validate, docker-lint)
   - [x] Do not allow bypassing the above settings (관리자도 예외 없이 적용)
```

---

## 4. Terraform & IaC 프로젝트 방어적 코드 작성

파일 경로를 하드코딩하면 파일 삭제 시 배포가 즉시 깨집니다. 동적 탐색(`fileset`)을 기본으로 사용합니다.

```hcl
# [개선 예시] 하드코딩 대신 fileset으로 실제 존재하는 파일만 자동 패키징
locals {
  runtime_files = concat(
    ["compose.yaml", "gateway/default.conf", "loki/config.yaml"],
    [for f in fileset("${path.module}/../monitoring-integrated/runtime/agent", "**") : "agent/${f}"],
    [for f in fileset("${path.module}/../monitoring-integrated/runtime/worker", "**") : "worker/${f}"],
    [for f in fileset("${path.module}/../monitoring-integrated/runtime/grafana/provisioning", "**") : "grafana/provisioning/${f}"],
    [for f in fileset("${path.module}/../monitoring-integrated/runtime/grafana/dashboards", "*.json") : "grafana/dashboards/${f}"]
  )
}
```

---

## 5. 비상 복구 명령어 모음 치트시트

```bash
# 1. 꼬인 작업 브랜치를 버리고 최신 main으로 완전히 새로 시작하고 싶을 때
git checkout main
git pull origin main
git branch -D feature/issue-name
git checkout -b feature/issue-name

# 2. 실수로 삭제된 파일을 과거 커밋에서 1개만 복구하고 싶을 때
# git log --all --full-history -- "<파일경로>" 로 커밋 해시 확인 후 실행
git checkout <커밋해시> -- <복구할파일경로>

# 3. 진행 중인 merge를 취소하고 이전 상태로 되돌리고 싶을 때
git merge --abort

# 4. 진행 중인 rebase를 취소하고 싶을 때
git rebase --abort

# 5. 로컬 작업분을 원격 main 상태로 강제 일치시키고 싶을 때 (주의: 로컬 작업 날아감)
git fetch origin main
git reset --hard origin/main
```