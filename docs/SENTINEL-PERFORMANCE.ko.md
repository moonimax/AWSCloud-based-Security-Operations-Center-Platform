# Sentinel 조회·분석 성능 개선 패치

> 아래 명령의 `scripts/`, `tests/`, `runtime/` 경로는 `monitoring-integrated/` 폴더 기준입니다.

기존 monitoring-grafana-import / folder-tabs / week-ui + analysis-csv 기반의 교체 파일입니다.
이 패치는 데이터 삭제, 보존 기간 축소, IAM 변경, 인증 해제를 수행하지 않습니다.

## 변경 파일
- runtime/agent/app.py: 검색은 조회 기록을 만들고 즉시 반환; 집계 진행 조회와 원문 페이지 API 추가. Loki 전체 응답 16MiB·동시 요청 2개 제한.
- query_engine.py (신규): 시간 단위 집계·5분 캐시·15분 조회 기록·100~500건 페이지·동일 timestamp 경계 처리.
- analyzer.py: 검색 집계 재사용, 표본 그룹화, 3개 핵심 발견, 상세 설명, 소유자별 분석 캐시, 단계 및 시간 기록.
- analysis.js: 진행 단계·소요 시간·캐시 여부·같은 표본 상세 설명·CSV.
- performance.js (신규): 24시간 이상 갱신 중지, 절대 시각 고정, 수동 조회, 원문 페이지 및 집계 진행 표시.
- catalog.py: 패널의 장기 조회 준비 여부 제공.
- index.html: 기존 일주일/5분 UI에 위 JS 연결, 상단 탭·챗봇 너비 조절 포함.
- planner.py: 테스트/참고용 일주일 호환본. 설치 스크립트는 기존 planner.py를 덮어쓰지 않음.
- scripts/patch_dashboards.py: 기존 Grafana JSON 레이아웃/UID를 유지하고 조회 간격 설정. JSON별 백업 생성.

## EC2 적용
아래는 ZIP을 풀어 sentinel-performance 폴더에 들어온 상태입니다.

현재 직접 수정한 UI를 유지하는 기본 적용:
```bash
sudo python3 scripts/install.py /opt/monitoring/runtime --keep-ui
```
이 옵션은 현재 index.html에 performance.js만 삽입합니다. 기존 상단 탭, 챗봇 리사이즈, 문구를 보존합니다.
이번 패키지의 전체 index.html을 사용하려면 --keep-ui 없이 실행하세요. 이 버전에는 상단 탭·챗봇 최대 50% 리사이즈가 포함되어 있습니다.
백업은 runtime/backup-performance-날짜 폴더에 생깁니다.

기존 planner가 일주일을 허용하는지 확인:
```bash
cd /opt/monitoring/runtime
sudo docker compose exec -T agent python -c "from planner import validate; print(validate({'minutes':10080}))"
```
아직 기존 컨테이너가 돌아가는 시점의 검사입니다. 실패하면 기존 planner.py의 기간 검증과 모델 안내를 10080분으로 올리세요. 자체 수정이 없는 경우에만 동봉 planner.py를 사용하세요.

Grafana JSON 패치: 실제 호스트 경로를 먼저 확인합니다.
```bash
sudo docker inspect "$(sudo docker compose ps -q grafana)" --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
sudo docker inspect "$(sudo docker compose ps -q agent)" --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
```
기본 경로라면 ZIP 디렉터리로 돌아와 실행:
```bash
sudo python3 scripts/patch_dashboards.py /opt/monitoring/runtime/grafana/dashboards /opt/monitoring/runtime/data/dashboards
```
DATA_ROOT가 다르면 출력된 실제 dashboards 디렉터리로 바꿉니다. 두 서비스를 같은 JSON 파일에 연결해야 합니다.
JSON 백업은 .json.backup-날짜 확장자로 생성되어 자동 목록에서 제외됩니다.

```bash
cd /opt/monitoring/runtime
sudo docker compose up -d --build --no-deps agent
sudo docker compose restart grafana
sudo docker compose logs --tail=80 agent grafana
```
브라우저 Ctrl+Shift+R. 적용에는 Terraform apply가 필요 없습니다. 영구 배포를 위해 저장소/배포 번들에도 같은 파일을 반영하세요.
새 API는 기존 gateway의 location / -> agent 라우팅으로 동작합니다. X-WEBAUTH-USER가 계속 전달되어야 합니다. IAM/.env/모델 ID는 변경하지 않습니다.

## Windows 로컬
```powershell
python scripts\install.py C:\프로젝트\runtime --keep-ui
python scripts\patch_dashboards.py C:\프로젝트\runtime\grafana\dashboards C:\프로젝트\runtime\data\dashboards
cd C:\프로젝트\runtime
docker compose up -d --build --no-deps agent
docker compose restart grafana
```
Compose 경로/파일명이 다른 경우 기존에 사용하던 -f 옵션을 유지하세요.

## 실제 동작
1. 조건 입력 후 보내기: 시간 범위를 고정한 조회 기록 생성. 같은 사용자·조건은 최대 5분간 재사용.
2. 원문은 기본 100건. 100/200/500 선택 후 이전 로그 더 보기. 새 페이지가 이전 표시를 대체하므로 DOM에 무한 누적되지 않음.
3. 원문 한 요청은 최대 2개 시간 구간을 조사. 희소 로그에서는 100건 미만/0건이어도 더 보기가 남을 수 있음.
4. 전체 건수는 최대 1시간의 인접 구간으로 나눠 순차 집계. 한 Agent의 집계 작업자는 1개, 대기 포함 최대 2개 조회. 7일은 경계에 따라 168~169개 집계 요청.
5. 집계가 실패하면 부분 합을 전체 건수로 표시하지 않음. UI의 진행 구간과 오류 확인 후 조건을 줄이거나 새로 조회.
6. 현재 조건 새로 조회는 조회 기록 및 해당 집계 캐시를 우회. 일반 검색은 5분 캐시 사용. 지연 수집 데이터는 새로 조회 필요.
7. 24시간 이상은 Grafana 자동 갱신 꺼짐. 패널 시간은 절대 시각 고정. 같은 조회 기록을 쓰는 원문·분석은 기준 시각 표시.
8. 간격: 6시간 미만 1분 / 6~24시간 미만 5분 / 1~7일 미만 15분 / 7일 1시간.
9. 장기간 Grafana 원문/통계/미설정 패널은 자동 로드하지 않음. 상단 분할 집계와 근거 목록을 사용하거나 해당 패널을 24시간 미만으로 축소. 그래프만 느슨하게 만들고 원문·통계 패널이 전체 로그를 다시 읽는 우회 부하를 방지.
10. 전체 건수에 모델을 호출하지 않음. 기본 분석은 최대 3개 발견, 같은 표본 상세 설명은 최대 8개. 상세 설명은 별도 모델 호출이며 더 넓은 로그를 수집하는 기능은 아님.

## 분석 표본과 한계
기간을 6등분하고 각 구간 마지막 최대 1시간의 최근 10건을 읽습니다. 최대 60건, 한 원문 800자.
긴 기간에서 전체 구간을 다시 스캔하지 않으므로 희소하거나 오래된 공격 로그가 표본에서 제외될 수 있습니다.
전체 집계는 선택 범위 전체의 건수지만 분석 내용은 표본 기반입니다. '공격 없음'이나 전체 IP 순위의 근거로 사용하면 안 됩니다.
동일 JSON의 time/timestamp/@timestamp/request_id/trace_id를 그룹 비교에서 제외하되 IP·경로·상태 코드는 유지합니다.
대표 최대 30그룹을 모델에 전달하며 제외 그룹 수와 표본 내 발생 횟수를 표시합니다. 대표 원문과 근거 ID는 보존합니다.
분석 캐시는 사용자·조건·질문·모델·조회 기록 ID(기준 시각)·상세 모드·스키마 버전으로 구분합니다.
동일 작업은 실행 중/완료 상태 모두 재사용하며 CSV 다운로드는 모델 재호출 없음. 실패 결과는 재사용하지 않습니다.
새 분석은 30초 간격, 동시에 1개. 완료 후 15분/최대 32작업 메모리 보관, 재시작 시 소멸.

## 조정 값과 보호 장치
- query_engine.py: CHUNK=3600, 조회 캐시 300초, TTL=900, 원문 페이지 100~500.
- 집계 캐시 최대 2048항목, 조회 기록 최대 32개. 집계는 실행 시작 후 600초 제한, 실패 시 오류 표시.
- app.py: Agent 발행 Loki 요청 최대 동시 2개. 응답당 16MiB 초과 시 실패. 한 원문 표시 16,000자 이후 생략 표시.
- 동일 나노초 로그 4000건 이상에서는 누락을 피하려 페이지 조회를 명시적으로 중단. 필터를 좁히세요.
- maxTokens 기본 2400, 상세 4000. 출력이 잘린 JSON을 정상 결과로 취급하지 않음.
- Agent는 단일 프로세스/단일 Loki tenant 기준. 다중 replica는 Redis 같은 공유 작업/캐시 저장소 필요.
- 사용자 헤더를 모두 operator로 고정했다면 접속자 전체가 같은 사용자 범위와 캐시를 공유합니다.
- 조회 기록은 시간 경계 고정이지 Loki 데이터베이스의 MVCC snapshot이 아닙니다. 지연 삽입/보존 삭제 중에는 페이지 내용과 캐시 건수가 달라질 수 있음.
- Grafana iframe의 직접 Loki 요청에는 Agent의 동시성 제한이 적용되지 않음. 아래 Loki 설정도 검토하세요.

## Loki 자체 동시성 제한 (권장, 기존 블록에 병합)
실제 loki/config.yaml의 기존 querier/limits_config 블록에 아래 값만 병합합니다. 같은 최상위 키를 중복 추가하지 마세요. 보존/스토리지/schema 설정은 유지합니다.
```yaml
querier:
  max_concurrent: 2
limits_config:
  max_query_parallelism: 2
  tsdb_max_query_parallelism: 2
  split_queries_by_interval: 1h
  query_timeout: 60s
```
저사양 단일 Loki에서 시작할 값이며 성능 보장값은 아닙니다. 변경 시 사용 중인 Loki 이미지로 설정 검증 후 적용:
```bash
sudo docker compose run --rm --no-deps loki -config.file=/etc/loki/config.yaml -verify-config=true
sudo docker compose up -d --force-recreate --no-deps loki
```
EC2 OOM/디스크 고갈/CPU credit 문제까지 해결하는 패치는 아닙니다. 운영 부하 측정 후 인스턴스 자원도 검토해야 합니다.

## 검증 및 운영 확인
```bash
python3 -m unittest discover -s tests -v
node tests/test_ui.js
```
Python 모의 Loki/Bedrock+실제 로컬 HTTP 서버 11개 검사 및 JS 동작/구문 검증 완료.
실제 AWS Bedrock·Loki 3.7.7·Grafana 13.2.1 화면/부하 테스트는 미실행.

배포 후 15분 -> 24시간 -> 7일 순서로 확인. 전체 집계 진행, 페이지 누락, 동일 분석 캐시 여부를 확인합니다.
Grafana 패널 Inspect -> Query에서 일주일의 step/min interval이 3600초 이상으로 적용되는지 확인하세요. JSON은 classic 대시보드 기준이며 혼합 데이터소스/커스텀 플러그인/Scene JSON은 자동 최적화하지 않습니다.
기존 추세 패널의 고정 [5m] 등 집계 창은 선택 간격으로 변경합니다. count 그래프의 값은 해당 간격당 건수이므로 기존 5분 값과 직접 비교하지 마세요. 전체 기간 [$__range] 쿼리는 변경하지 않고 장기 자동 표시에서 제외합니다.

```bash
sudo docker stats --no-stream
sudo docker compose logs --since=10m agent
sudo docker inspect "$(sudo docker compose ps -a -q loki)" --format 'OOMKilled={{.State.OOMKilled}} Restarts={{.RestartCount}}'
```
Agent 완료 로그 timings에 collect_ms, bedrock_ms, validate_ms, total_ms와 입출력 토큰이 기록됩니다. 질문/원문은 로그에 기록하지 않습니다.

## 복구
runtime/backup-performance-날짜의 파일을 agent/에 복사합니다. 새 query_engine.py/performance.js는 이전 app/index에서 사용하지 않으면 남아 있어도 됩니다.
JSON별 .backup 파일을 원래 .json 이름으로 복원합니다. agent 재빌드 및 grafana 재시작 후 새로고침하세요.
