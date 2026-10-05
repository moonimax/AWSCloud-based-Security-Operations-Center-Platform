#!/usr/bin/env bash
# ── 시나리오 5: SQL 인젝션 ────────────────────────────────
#   attack_type = sqli  →  Grafana 규칙 R4
#   ⚠️ 자동차단 규칙! 성공하면 출발지 IP(내 공인 IP)가 2분간 차단됨.
#      차단되면 그동안 ALB 접속 불가. 다음 차단테스트 전 2분 대기.
# ---------------------------------------------------------
ALB="http://automovitsec-alb-840700719.ap-northeast-2.elb.amazonaws.com"

send() { code=$(curl -s -o /dev/null -w "%{http_code}" "$@"); echo "  HTTP $code"; }

echo "[R4] SQL 인젝션 3건 전송 → attack_type=sqli (⚠️ 자동차단)"
send -G "$ALB/rest/products/search" --data-urlencode "q=' OR 1=1--"
send -G "$ALB/rest/products/search" --data-urlencode "q=')) UNION SELECT username,password FROM Users--"
send -G "$ALB/rest/products/search" --data-urlencode "q=1 OR sleep(3)"
echo "완료. 30~60초 뒤 Slack에 'R4 SQLi' 알림 + 차단 확인. 2분 뒤 자동해제."
