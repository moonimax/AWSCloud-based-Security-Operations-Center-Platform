#!/usr/bin/env bash
# ── 시나리오 7: 경로순회 (Path Traversal) ────────────────
#   attack_type = path_traversal  →  Grafana 규칙 R6
#   동작: 알림만 (차단 안 함). 1건만 있어도 발생.
#
#   ⚠️ 주의: curl --path-as-is "$ALB/../../etc/passwd" 형태는
#      ALB가 400으로 막아서(서버에 안 닿음) 탐지 안 됨.
#      아래처럼 경로에 /etc/passwd 를 직접 넣거나, 쿼리로 ../ 를
#      전달하는 방식만 실제로 통과·탐지됨(검증 완료).
# ---------------------------------------------------------
ALB="http://automovitsec-alb-840700719.ap-northeast-2.elb.amazonaws.com"

send() { code=$(curl -s -o /dev/null -w "%{http_code}" "$@"); echo "  HTTP $code"; }

echo "[R6] 경로순회 3건 전송 → attack_type=path_traversal (알림만)"
send "$ALB/etc/passwd"
send -G "$ALB/ftp" --data-urlencode "doc=../../../etc/passwd"
send -G "$ALB/ftp" --data-urlencode "file=../../../../proc/self/environ"
echo "완료. 30~60초 뒤 Slack에 'R6 경로순회' 알림(차단 없음) 확인."
