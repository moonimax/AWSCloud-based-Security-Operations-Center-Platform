"""자동 차단 Lambda (TC-08) — Grafana webhook → NACL DENY.

호출 흐름: Grafana 알림 규칙(R1/R3 등) firing → webhook(Function URL) → 이 Lambda
동작: 비밀헤더 검증 → firing만 → block=on만 → /32 단일 IPv4 강제 → 화이트리스트 제외
      → 이미 차단이면 스킵 → NACL 슬롯 여유 확인 → 각 NACL에 DENY 추가 → SSM에 기록

⚠️ 안전 원칙: 이 Lambda는 '단일 IP(/32)'만 차단한다. 대역/0.0.0.0/빈값은 무조건 거부.
   해제는 별도 unblock Lambda(1분 타이머)가 만료분만 처리. 영구차단·재범가중치 없음.

환경변수(값은 terraform.tfvars → Lambda env, 비밀은 SSM):
  FUNCTION_SECRET   Grafana webhook Authorization: "Bearer <이 값>" 과 일치해야 동작
  NACL_IDS          쉼표구분. ALB 서브넷들에 연결된 NACL 전부 (예: "acl-aaa,acl-bbb")
  WHITELIST_CIDRS   쉼표구분. 절대 차단 금지 = 인프라 IP만(VPC 내부/NAT/ALB). 팀원(사람) IP는 넣지 않음.
  BLOCK_TTL_MIN     차단 유지(분). 테스트 2 / 시연 5. (해제는 unblock Lambda가 수행)
  SSM_PREFIX        차단 기록 경로 접두사 (예: "/automovitsec")
  RULE_NUMBER_MIN   NACL 규칙번호 풀 하한 (기본 1)   — 복구 절차의 "1~50번 삭제"와 일치
  RULE_NUMBER_MAX   NACL 규칙번호 풀 상한 (기본 50)
  NACL_MAX_RULES    NACL 인바운드 규칙 수 상한 (기본 20, 기존 규칙 포함). 초과 시 차단 거부+Slack 경고
  SLACK_WEBHOOK     (선택) 슬롯 초과 등 운영 경고용
"""
import os
import json
import base64
import hmac
import ipaddress
import time
import logging
import urllib.request

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger()
log.setLevel(logging.INFO)

ec2 = boto3.client("ec2")
ssm = boto3.client("ssm")


def _resp(code, obj):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(obj, ensure_ascii=False)}


def _slack(msg):
    url = os.environ.get("SLACK_WEBHOOK", "").strip()
    if not url:
        return
    try:
        req = urllib.request.Request(url, data=json.dumps({"text": msg}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        log.warning("slack 경고 전송 실패: %s", e)


def _event(**fields):
    """대시보드(CloudWatch Logs Insights)용 구조화 이벤트 로그 한 줄.
    Loki는 VPC 밖이라 접근 불가 → 이 Lambda의 CloudWatch 로그그룹에 순수 JSON으로 남긴다.
    대시보드는 `filter type="soc_event"` 로 걸러 차단/알림 내역 패널을 만든다."""
    try:
        print(json.dumps({"type": "soc_event", **fields}, ensure_ascii=False))
    except Exception:
        pass


def _to_slash32(ip):
    """단일 IPv4만 허용하고 'ip/32' 반환. 그 외(대역/0.0.0.0/사설위험/빈값)는 None."""
    if not ip or "/" in str(ip):          # CIDR 표기가 들어오면 단일 IP 아님 → 거부
        return None
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return None
    if addr.version != 4:
        return None
    if addr.is_unspecified or addr.is_loopback or addr.is_multicast or addr.is_reserved:
        return None                        # 0.0.0.0, 127.x, 224.x 등 거부
    return f"{addr}/32"


def _is_whitelisted(ip):
    cidrs = [c.strip() for c in os.environ.get("WHITELIST_CIDRS", "").split(",") if c.strip()]
    addr = ipaddress.ip_address(ip)
    for c in cidrs:
        try:
            if addr in ipaddress.ip_network(c, strict=False):
                return True
        except ValueError:
            continue
    return False


def _inbound_entries(nacl_id):
    r = ec2.describe_network_acls(NetworkAclIds=[nacl_id])
    entries = r["NetworkAcls"][0]["Entries"]
    # 기본 규칙(32767, '*')과 egress 제외한 실제 인바운드 규칙
    return [e for e in entries if not e["Egress"] and e["RuleNumber"] != 32767]


def _pick_rule_number(used, lo, hi):
    for n in range(lo, hi + 1):
        if n not in used:
            return n
    return None


def _already_blocked(ip):
    name = f"{os.environ['SSM_PREFIX']}/blocklist/{ip}"
    try:
        ssm.get_parameter(Name=name)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound":
            return False
        raise


def _block_ip(ip):
    """단일 IP를 모든 NACL에 DENY 추가하고 SSM에 기록. 결과 dict 반환."""
    nacl_ids = [n.strip() for n in os.environ["NACL_IDS"].split(",") if n.strip()]
    lo = int(os.environ.get("RULE_NUMBER_MIN", "1"))
    hi = int(os.environ.get("RULE_NUMBER_MAX", "50"))
    max_rules = int(os.environ.get("NACL_MAX_RULES", "20"))
    cidr = f"{ip}/32"

    entries = {}
    for nacl_id in nacl_ids:
        inbound = _inbound_entries(nacl_id)
        if len(inbound) >= max_rules:      # 슬롯 한도 (기존 규칙 포함)
            _slack(f":warning: 차단 실패 — NACL {nacl_id} 인바운드 규칙 {len(inbound)}개로 한도({max_rules}) 초과. IP {ip} 미차단.")
            return {"ip": ip, "status": "refused_slot_full", "nacl": nacl_id}
        used = {e["RuleNumber"] for e in inbound}
        num = _pick_rule_number(used, lo, hi)
        if num is None:
            _slack(f":warning: 차단 실패 — NACL {nacl_id} 규칙번호 풀({lo}~{hi}) 소진. IP {ip} 미차단.")
            return {"ip": ip, "status": "refused_no_rule_number", "nacl": nacl_id}
        ec2.create_network_acl_entry(
            NetworkAclId=nacl_id, RuleNumber=num, Protocol="-1",
            RuleAction="deny", Egress=False, CidrBlock=cidr)
        entries[nacl_id] = num

    now = int(time.time())
    record = {"blocked_at": now,
              "expire_at": now + int(os.environ.get("BLOCK_TTL_MIN", "5")) * 60,
              "entries": entries}
    ssm.put_parameter(Name=f"{os.environ['SSM_PREFIX']}/blocklist/{ip}",
                      Value=json.dumps(record), Type="String", Overwrite=True)
    log.info("차단 완료: %s → %s", ip, entries)
    return {"ip": ip, "status": "blocked", "entries": entries, "expire_at": record["expire_at"]}


def handler(event, context):
    # 1) 비밀헤더 검증 — 일치하지 않으면 아무 것도 안 함
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    expected = "Bearer " + os.environ.get("FUNCTION_SECRET", "")
    if not os.environ.get("FUNCTION_SECRET") or not hmac.compare_digest(headers.get("authorization", ""), expected):
        log.warning("비밀헤더 불일치 — 거부")
        return _resp(403, {"error": "forbidden"})

    # 2) 본문 파싱 (Grafana webhook payload)
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    try:
        payload = json.loads(body)
    except ValueError:
        return _resp(400, {"error": "bad json"})

    ttl = os.environ.get("BLOCK_TTL_MIN", "5")
    results = []
    for alert in payload.get("alerts", []):
        # 3) firing 만 처리, resolved 무시
        if alert.get("status") != "firing":
            continue
        labels = alert.get("labels", {})
        rule = labels.get("rule", "?")
        tc = labels.get("tc", "?")
        ip_raw = labels.get("ip", "")
        # 4) 알림 전용(R2 등, block=off): 차단하지 않고 슬랙만 보냄
        if labels.get("block") != "on":
            _slack(f":warning: [탐지·알림전용] {rule}/{tc} — IP {ip_raw} (차단 안 함)")
            _event(event="alert", action="alert_only", rule=rule, tc=tc, ip=ip_raw, block="off")
            results.append({"ip": ip_raw, "rule": rule, "status": "alert_only"})
            continue
        # 5) /32 단일 IPv4 강제 — 대역/0.0.0.0/빈값 거부
        cidr = _to_slash32(ip_raw)
        if not cidr:
            log.warning("유효하지 않은 IP, 차단 거부: %r", ip_raw)
            results.append({"ip": ip_raw, "status": "rejected_not_single_ipv4"})
            continue
        ip = cidr[:-3]
        # 6) 화이트리스트(인프라 IP) 제외
        if _is_whitelisted(ip):
            results.append({"ip": ip, "status": "skipped_whitelist"})
            continue
        # 7) 이미 차단 중이면 스킵 (재범 가중치 없음 → 연장도 안 함)
        if _already_blocked(ip):
            results.append({"ip": ip, "status": "already_blocked"})
            continue
        # 8) 차단 실행 → 성공 시 슬랙 알림 ("차단하고 슬랙")
        try:
            r = _block_ip(ip)
            results.append(r)
            if r.get("status") == "blocked":
                _slack(f":no_entry: [자동차단] {rule}/{tc} — IP {ip} 차단됨 ({ttl}분 후 자동해제)")
                _event(event="block", action="blocked", rule=rule, tc=tc, ip=ip,
                       expire_at=r.get("expire_at"), entries=r.get("entries"))
            else:
                _event(event="block", action=r.get("status"), rule=rule, tc=tc, ip=ip)
            # 슬롯 초과/번호소진(refused_*)은 _block_ip 안에서 이미 Slack 경고를 보냄
        except Exception as e:
            log.exception("차단 처리 오류: %s", ip)
            results.append({"ip": ip, "status": "error", "detail": str(e)})

    return _resp(200, {"processed": len(results), "results": results})
