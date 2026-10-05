"""Bounded snapshot queries. Single process, single Loki tenant per deployment."""
import base64
import copy
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from planner import compile_query

NS = 10**9
TTL = 900
CHUNK = 3600
TIE_LIMIT = 4000  # below common Loki 5000 line cap
LOCK = threading.RLock()
SNAPSHOTS = OrderedDict()
COUNTS = OrderedDict()
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='count')
SECRET = secrets.token_bytes(32)


def stable(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def get_snapshot(sid, owner):
    with LOCK:
        snap = SNAPSHOTS.get(sid)
        if not snap or snap['owner'] != owner or time.time()-snap['created'] > TTL:
            raise ValueError('조회 기록이 만료됐습니다. 새로 조회하세요.')
        return snap


def snapshot(plan, owner, loki_get, fresh=False):
    now = time.time()
    with LOCK:
        for sid, s in list(SNAPSHOTS.items()):
            if now-s['created'] > TTL and s['state'] != 'running': del SNAPSHOTS[sid]
        if not fresh:
            for s in reversed(list(SNAPSHOTS.values())):
                if s['owner'] == owner and s['plan'] == plan and now-s['created'] < 300 and s['state'] != 'error':
                    return s
        if len(SNAPSHOTS) >= 32:
            removable = next((k for k,s in SNAPSHOTS.items() if s['state'] != 'running'), None)
            if removable is None: raise RuntimeError('조회 작업이 많습니다. 잠시 후 다시 시도하세요.')
            del SNAPSHOTS[removable]
        if sum(s['state']=='running' for s in SNAPSHOTS.values()) >= 2:
            raise RuntimeError('집계 중입니다. 기존 조회를 이용하거나 완료 후 다시 시도하세요.')
        end = int(now)
        s = {'id':uuid.uuid4().hex, 'owner':owner, 'plan':copy.deepcopy(plan),
             'query':compile_query(plan), 'start':end-plan['minutes']*60, 'end':end,
             'created':now, 'state':'running', 'total':None, 'chunks':[], 'done_chunks':0,
             'event':threading.Event()}
        # Split at wall-clock hour boundaries for reusable historical count chunks.
        lo = s['start']
        while lo < end:
            hi = min(end, (lo//CHUNK+1)*CHUNK)
            s['chunks'].append((lo,hi)); lo=hi
        SNAPSHOTS[s['id']] = s
        POOL.submit(count_snapshot, s, loki_get, fresh)
        return s


def count_snapshot(s, loki_get, fresh):
    began = time.monotonic(); values=[]
    try:
        for lo,hi in s['chunks']:
            if time.monotonic()-began > 600: raise TimeoutError('집계 제한 시간 초과')
            key = (s['owner'],s['query'],lo,hi)
            with LOCK:
                item = COUNTS.get(key)
                cached = item is not None and time.time()-item[0]<300 and not fresh
            if cached: value=item[1]
            else:
                data=loki_get('/loki/api/v1/query', {'query':f'sum(count_over_time({s["query"]}[{hi-lo}s]))', 'time':str(hi*NS)})
                value=int(sum(float(x['value'][1]) for x in data.get('result',[])))
                with LOCK:
                    COUNTS[key]=(time.time(),value); COUNTS.move_to_end(key)
                    while len(COUNTS)>2048: COUNTS.popitem(last=False)
            values.append({'start_unix':lo,'end_unix':hi,'count':value})
            with LOCK: s['done_chunks']=len(values)
        with LOCK:
            s.update(state='done',total=sum(x['count'] for x in values),buckets=values,
                     collect_ms=round((time.monotonic()-began)*1000))
    except Exception as exc:
        with LOCK: s.update(state='error',error='집계 실패: '+type(exc).__name__)
    finally: s['event'].set()


def public(s):
    with LOCK:
        return {'snapshot_id':s['id'],'plan':s['plan'],'query':s['query'],
                'start_unix':s['start'],'end_unix':s['end'],'state':s['state'],
                'total':s['total'],'done_chunks':s['done_chunks'],'total_chunks':len(s['chunks']),
                'error':s.get('error'),'cache_age_seconds':round(time.time()-s['created'])}


def encode(s, end, skip):
    payload=stable([s['id'],end,skip]).encode()
    return base64.urlsafe_b64encode(hmac.digest(SECRET,payload,'sha256')+payload).decode()


def decode(s, cursor):
    if not cursor: return s['end']*NS,0
    try:
        raw=base64.urlsafe_b64decode(cursor)
        if not hmac.compare_digest(raw[:32],hmac.digest(SECRET,raw[32:],'sha256')): raise ValueError()
        sid,end,skip=json.loads(raw[32:])
        if sid!=s['id'] or type(end)!=int or type(skip)!=int or not s['start']*NS < end <= s['end']*NS or not 0<=skip<TIE_LIMIT: raise ValueError()
        return end,skip
    except Exception: raise ValueError('더 보기 위치가 잘못됐습니다. 새로 조회하세요.') from None


def rows_query(s, loki_get, lo, hi, limit):
    data=loki_get('/loki/api/v1/query_range',{'query':s['query'],'start':str(lo),'end':str(hi+1),'limit':limit,'direction':'backward'})
    rows=[]
    for stream in data.get('result',[]):
        labels=stream.get('stream',{})
        for ts,line in stream.get('values',[]):
            if lo<=int(ts)<=hi:
                rows.append({'timestamp_ns':ts,'server':labels.get('server',''),'labels':labels,'line':line})
    return sorted(rows,key=lambda x:(-int(x['timestamp_ns']),stable(x['labels']),x['line']))


def exact(s,loki_get,ts):
    # Loki accepts equal timestamps as bounds; using ts+1 and filtering also works
    # with backends which require start < end.
    rows=rows_query(s,loki_get,ts,ts,TIE_LIMIT+1)
    rows=[r for r in rows if int(r['timestamp_ns'])==ts]
    if len(rows)>=TIE_LIMIT: raise ValueError('동일 시각 로그가 너무 많습니다. 서버·로그 종류를 좁혀주세요. 누락을 막기 위해 페이지 이동을 중단했습니다.')
    return rows


def page(s,loki_get,size=100,cursor=None,scan_budget=2):
    if type(size)!=int or not 100<=size<=500: raise ValueError('페이지 크기는 100~500입니다.')
    end,skip=decode(s,cursor); start=s['start']*NS+1; out=[]; scanned=0
    if skip:
        tied=exact(s,loki_get,end); remaining=tied[skip:]
        out.extend(remaining[:size])
        if len(remaining)>size: return page_result(s,out,encode(s,end,skip+size),0)
        end-=1
    while len(out)<size and end>=start and scanned<scan_budget:
        lo=max(start,end-CHUNK*NS+1); need=size-len(out)
        batch=rows_query(s,loki_get,lo,end,need+1); scanned+=1
        if len(batch)>need:
            boundary=int(batch[need-1]['timestamp_ns'])
            newer=[r for r in batch if int(r['timestamp_ns'])>boundary]
            tied=exact(s,loki_get,boundary)
            take=need-len(newer)
            out.extend(newer+tied[:take])
            if len(tied)>take: token=encode(s,boundary,take)
            elif boundary>start: token=encode(s,boundary-1,0)
            else: token=None
            return page_result(s,out,token,scanned)
        out.extend(batch); end=lo-1
    token=encode(s,end,0) if end>=start else None
    return page_result(s,out,token,scanned)


def page_result(s,rows,cursor,scanned):
    # Bound response memory and label truncation explicitly; count remains exact.
    for r in rows:
        r['truncated']=len(r['line'])>16000
        r['line']=r['line'][:16000]
    return {**public(s),'rows':rows,'next_cursor':cursor,'scanned_hours':scanned,
            'partial_page':bool(cursor) and not rows}


def evidence(s,loki_get,progress):
    began=time.monotonic()
    while not s['event'].wait(.5):
        progress('로그 조회',public(s))
        if time.monotonic()-began>620: raise TimeoutError('집계를 기다리는 시간이 초과됐습니다.')
    if s['state']!='done': raise RuntimeError(s.get('error','집계 실패'))
    logs=[]; span=s['end']-s['start']
    # Samples from each sixth; query only the last hour of each sixth to bound scan cost.
    for i in range(6):
        hi=s['start']+span*(i+1)//6
        lo=max(s['start']+span*i//6, hi-CHUNK)
        for row in rows_query(s,loki_get,lo*NS+1,hi*NS,10):
            logs.append({'id':f'L{len(logs)+1}','timestamp_ns':row['timestamp_ns'],
                         'labels':row['labels'],'line':row['line'][:800],
                         'truncated':len(row['line'])>800})
        progress('로그 조회',{'sample_sections':i+1,'sample_sections_total':6})
    return {'plan':s['plan'],'start_unix':s['start'],'end_unix':s['end'],'total':s['total'],
            'sample_count':len(logs),'logs':logs,'buckets':s['buckets'],
            'snapshot_id':s['id'],'sampling':'기간 6등분, 각 구간 끝 최대 1시간에서 최근 10건, 최대 60건·원문당 800자. 전체 원문 분석 아님. 희소·과거 사건이 표본에서 빠질 수 있음.'}
