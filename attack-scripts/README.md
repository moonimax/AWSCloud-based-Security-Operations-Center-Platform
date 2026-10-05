# 공격 시연 스크립트 (탐지·차단 검증용)

타겟 = OWASP Juice Shop (automovitsec-alb). 각 파일이 시나리오 1개.
Git Bash에서 실행: `bash r4_sqli.sh`

| 파일 | 시나리오 | 규칙 | 동작 |
|---|---|---|---|
| `r2_sensitive_file.sh` | 민감파일(.env 등) | R2 | 알림만 |
| `r5_xss.sh` | XSS | R5 | 알림만 |
| `r6_path_traversal.sh` | 경로순회 | R6 | 알림만 |
| `r3_bruteforce.sh` | 무차별 대입(로그인 401 ×12) | R3 | ⚠️ 자동차단 |
| `r4_sqli.sh` | SQL 인젝션 | R4 | ⚠️ 자동차단 |

## 주의
- **순서:** 알림 전용(R2·R5·R6) 먼저 → 차단(R3·R4) 나중. 차단되면 출발지 IP가 2분간 막혀 그동안 공격이 안 나감. 차단 테스트 사이 2분 대기.
- **반드시 ALB로.** `localhost:8080`(관제)로 쏘면 로그가 SQS로 안 가서 탐지 안 됨.
- 탐지 확인: 전송 30~60초 뒤 Slack/Grafana에서 해당 규칙 알림 확인.

## 검증된 함정 (원본 명령에서 수정한 부분)
- R3 로그인은 **POST+JSON**이어야 401. GET(`-G`)이면 500이라 안 걸림.
- 경로순회 `curl --path-as-is "$ALB/../../etc/passwd"`는 ALB가 400으로 막음(서버 미도달). 경로에 `/etc/passwd` 직접 넣거나 쿼리로 `../` 전달해야 통과·탐지됨.
