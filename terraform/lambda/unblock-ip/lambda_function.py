"""자동 해제 Lambda — EventBridge rate(1 minute) 로 주기 실행.

동작: SSM `{SSM_PREFIX}/blocklist/*` 를 훑어, expire_at 지난 IP만
      NACL DENY 규칙 삭제 → **삭제 성공한 경우에만** SSM 기록 삭제.
      (NACL 삭제 실패 시 SSM 유지 → 다음 1분 스윕에서 재시도)

⚠️ 영구차단 없음: 모든 기록은 expire_at 을 가지며 만료되면 반드시 풀린다.
   block Lambda 가 쓴 기록 형식: {"blocked_at":int, "expire_at":int, "entries":{nacl_id: rule_number}}

환경변수:
  SSM_PREFIX   차단 기록 경로 접두사 (block Lambda 와 동일하게, 예: "/automovitsec")
"""
import os
import json
import time
import logging

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger()
log.setLevel(logging.INFO)

ec2 = boto3.client("ec2")
ssm = boto3.client("ssm")


def _event(**fields):
    """대시보드(CloudWatch Logs Insights)용 구조화 이벤트 로그 한 줄.
    block Lambda 와 같은 형식(type="soc_event")으로 해제 이벤트를 남긴다."""
    try:
        print(json.dumps({"type": "soc_event", **fields}, ensure_ascii=False))
    except Exception:
        pass


def _delete_entries(entries):
    """기록된 NACL DENY 규칙들을 삭제. 이미 없으면 성공 취급.
    하나라도 (NotFound 아닌) 실패가 있으면 False → 호출측이 SSM 기록을 남겨 재시도."""
    ok = True
    for nacl_id, num in (entries or {}).items():
        try:
            ec2.delete_network_acl_entry(NetworkAclId=nacl_id, RuleNumber=int(num), Egress=False)
            log.info("NACL 규칙 삭제: %s #%s", nacl_id, num)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code == "InvalidNetworkAclEntry.NotFound":
                log.info("이미 삭제된 규칙(성공 취급): %s #%s", nacl_id, num)
                continue
            log.error("NACL 규칙 삭제 실패(%s): %s #%s", code, nacl_id, num)
            ok = False
    return ok


def _process_one(name, rec, now):
    if int(rec.get("expire_at", 0)) > now:
        return {"name": name, "status": "not_expired"}
    # 먼저 NACL 규칙 삭제 → 성공했을 때만 SSM 기록 삭제 (고아 규칙 방지)
    if _delete_entries(rec.get("entries", {})):
        ssm.delete_parameter(Name=name)
        _event(event="unblock", action="unblocked", ip=name.rsplit("/", 1)[-1],
               entries=rec.get("entries"))
        return {"name": name, "status": "unblocked"}
    return {"name": name, "status": "nacl_delete_failed_kept"}


def handler(event, context):
    now = int(time.time())
    prefix = os.environ["SSM_PREFIX"]
    path = f"{prefix}/blocklist/"
    results = []
    paginator = ssm.get_paginator("get_parameters_by_path")
    for page in paginator.paginate(Path=path, Recursive=True):
        for p in page.get("Parameters", []):
            try:
                rec = json.loads(p["Value"])
            except ValueError:
                results.append({"name": p["Name"], "status": "bad_record"})
                continue
            try:
                results.append(_process_one(p["Name"], rec, now))
            except Exception as e:
                log.exception("해제 처리 오류: %s", p["Name"])
                results.append({"name": p["Name"], "status": "error", "detail": str(e)})

    unblocked = sum(1 for r in results if r["status"] == "unblocked")
    log.info("스윕 완료: 검사 %d건, 해제 %d건", len(results), unblocked)
    return {"checked": len(results), "unblocked": unblocked, "results": results}
