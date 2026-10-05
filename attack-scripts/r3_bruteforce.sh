#!/usr/bin/env bash
# ── 시나리오 3: 무차별 대입 (로그인 실패) ────────────────
#   조건: /rest/user/login + status=401 이 1분간 10건 이상  →  Grafana 규칙 R3
#   ⚠️ 자동차단 규칙! 성공하면 출발지 IP(내 공인 IP)가 2분간 차단됨.
#
#   ⚠️ 반드시 POST + JSON 이어야 401 이 나옴.
#      GET(-G)로 보내면 500 이 나와서 R3 이 안 걸림(검증 완료).
#   기본 12회(임계값 10 초과). 필요하면 아래 N 값 조정.
# ---------------------------------------------------------
ALB="http://automovitsec-alb-840700719.ap-northeast-2.elb.amazonaws.com"
N=12

echo "[R3] 로그인 실패 ${N}건 전송(POST) → status=401 (⚠️ 자동차단, 10건↑ 필요)"
for i in $(seq 1 "$N"); do
  code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$ALB/rest/user/login" \
    -H "Content-Type: application/json" -d "{\"email\":\"a@a.com\",\"password\":\"wrong$i\"}")
  echo "  [$i] HTTP $code"
done
echo "완료. 401 이 ${N}번 찍혔으면 정상. 30~60초 뒤 Slack에 'R3 무차별대입' 알림 + 차단 확인. 2분 뒤 자동해제."
