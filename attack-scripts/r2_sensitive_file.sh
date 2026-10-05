#!/usr/bin/env bash
# ── 시나리오 2: 민감파일 접근 ─────────────────────────────
#   attack_type = sensitive_file  →  Grafana 규칙 R2
#   동작: 알림만 (차단 안 함). 1건만 있어도 발생.
# ---------------------------------------------------------
ALB="http://automovitsec-alb-840700719.ap-northeast-2.elb.amazonaws.com"

send() { code=$(curl -s -o /dev/null -w "%{http_code}" "$@"); echo "  HTTP $code  ($*)"; }

echo "[R2] 민감파일 접근 3건 전송 → attack_type=sensitive_file (알림만)"
send "$ALB/.env"
send "$ALB/.git/config"
send "$ALB/.aws/credentials"
echo "완료. 30~60초 뒤 Slack에 'R2 민감파일' 알림(차단 없음) 확인."
