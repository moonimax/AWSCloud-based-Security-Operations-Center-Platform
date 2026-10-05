# 검증 기록

2026-09-18 로컬 검증 결과:

- Python unittest 21개 통과: snake/camel 입력 호환, 충돌 거부, timestamp 검사, ID 유지/생성, 단위 변환, 원문 보존, HTTP 204만 삭제, 실패/네트워크 오류 시 미삭제, 필터 범위·주입 방지, Bedrock 응답 검증(모의), HTTP 수동 검색/출처 검증/백엔드 실패.
- 모든 Python AST, YAML/JSON 파싱 통과.
- Terraform .tf 파일 HCL 파싱 통과. 이는 provider 스키마 검증이 아닙니다.
- Terraform 치환 부분을 대체한 bootstrap 스크립트 bash -n 통과.
- 프런트엔드 JavaScript node --check 통과.

미수행: Docker Compose 실제 기동·이미지 pull, Loki 실제 LogQL 실행, Grafana 화면/임베딩/URL 변수 적용, terraform init/validate/plan/apply, 실제 AWS 자원 생성·EBS 교체 복구, Bedrock 실제 호출.
이 환경에는 Docker와 Terraform CLI가 없으므로 전체 배포 성공을 보장하지 않습니다. README 점검 순서대로 배포 환경에서 검증하세요.

이미지 버전과 API 설정 참고:
- Grafana 다운로드: https://grafana.com/grafana/download/
- Loki 3.7 릴리스: https://grafana.com/docs/loki/latest/release-notes/v3-7/
- Bedrock Converse: https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html
