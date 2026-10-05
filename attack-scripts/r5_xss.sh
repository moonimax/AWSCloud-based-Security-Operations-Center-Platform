#!/usr/bin/env bash
# ── 시나리오 6: XSS (크로스사이트 스크립팅) ──────────────
#   attack_type = xss  →  Grafana 규칙 R5
#   동작: 알림만 (차단 안 함). 1건만 있어도 발생.
# ---------------------------------------------------------
ALB="http://automovitsec-alb-840700719.ap-northeast-2.elb.amazonaws.com"

send() { code=$(curl -s -o /dev/null -w "%{http_code}" "$@"); echo "  HTTP $code"; }

echo "[R5] XSS 3건 전송 → attack_type=xss (알림만)"
send -G "$ALB/rest/products/search" --data-urlencode "q=<script>alert(1)</script>"
send -G "$ALB/rest/products/search" --data-urlencode "q=<img src=x onerror=alert(1)>"
send -G "$ALB/rest/products/search" --data-urlencode "q=<iframe src=javascript:alert(1)>"
echo "완료. 30~60초 뒤 Slack에 'R5 XSS' 알림(차단 없음) 확인."
