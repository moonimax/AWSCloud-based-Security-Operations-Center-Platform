"""입력 계약 통합, 실패 시 보존, Loki 204 응답 후 SQS 삭제."""
import hashlib
import json
import logging
import os
import random
import signal
import threading
import shlex
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from attack_patterns import classify

log=logging.getLogger('worker')

def parse_source(raw, kind):
    if kind == 'vpc_flow':
        keys='version account_id interface_id srcaddr dstaddr srcport dstport protocol packets bytes start end action log_status'.split()
        fields=raw.split()
        if len(fields)!=14:raise ValueError('VPC default 14-field format required')
        row=dict(zip(keys,fields))
        row.update(ip=row['srcaddr'],event_type='network_flow')
        return row
    if kind == 'alb':
        fields=shlex.split(raw)
        if len(fields)<18:raise ValueError('Invalid ALB access log')
        request=fields[12].split()
        row={'ip':fields[3].rsplit(':',1)[0].strip('[]'),
             'elb_status_code':fields[8],'target_status_code':fields[9],
             'event_type':'alb_access','request':fields[12]}
        if fields[8].isdigit():row['status']=int(fields[8])
        if len(request)>=2:
            row.update(method=request[0],path=urlsplit(request[1]).path,request_uri=request[1])
        return row
    try:
        row=json.loads(raw)
        if not isinstance(row,dict):raise ValueError()
        return row
    except ValueError:return {'event_type':'unparsed'}

def normalize(body):
    e=json.loads(body)
    if not isinstance(e,dict):raise ValueError('이벤트 객체 필요')
    def field(snake,camel):
        if snake in e and camel in e and e[snake]!=e[camel]:raise ValueError('필드 값 충돌')
        value=e.get(snake,e.get(camel))
        if not isinstance(value,str) or not value:raise ValueError('필수 식별자 누락')
        return value
    group=field('log_group','logGroup');stream=field('log_stream','logStream')
    stamp=e.get('timestamp')
    if type(stamp) is not int or stamp<=0:raise ValueError('밀리초 시각 필요')
    raw=e.get('message')
    if not isinstance(raw,str) or not raw:raise ValueError('원본 메시지 필요')
    kind=e.get('log_type') or ('vpc_flow' if group.endswith('/vpc-flow-logs') else 'alb' if group.endswith('/alb/access') else 'nginx')
    if kind not in ('nginx','alb','vpc_flow'):raise ValueError('Unknown log type')
    row=parse_source(raw,kind)
    row['raw_message']=raw
    row['log_type']=kind
    event_id=e.get('event_id',e.get('id')) or hashlib.sha256(json.dumps([group,stream,stamp,raw],ensure_ascii=False).encode()).hexdigest()
    row['event_id']=str(event_id);row['log_group']=group
    row.setdefault('event_type','access');row.setdefault('ip','');row.setdefault('path','')
    if 'request_time' not in row and isinstance(row.get('duration_ms'),(int,float)):
        row['request_time']=row['duration_ms']/1000
    attack_type=classify(row.get('request_uri') or row.get('path',''), row.get('user_agent',''))
    if attack_type=='sqli' and str(row.get('status'))=='500':
        row['success_suspect']=True  # TC-04: SQLi 페이로드 + 500 → 성공 의심
    return {'streams':[{'stream':{'job':'web-access' if kind=='nginx' else 'aws-'+kind,'log_type':kind,'environment':'aws','server':stream,'attack_type':attack_type},
        'values':[[str(stamp*1_000_000),json.dumps(row,ensure_ascii=False)]]}]}

def post(payload,url):
    req=Request(url,data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'},method='POST')
    # urllib 기본 리다이렉트를 사용하지 않도록 별도 opener로 전송한다.
    from urllib.request import build_opener, HTTPRedirectHandler
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    try:
        with build_opener(NoRedirect).open(req,timeout=10) as r:return r.status
    except HTTPError as e:return e.code

def deliver(message,sqs,queue,url,push=post):
    try:payload=normalize(message['Body'])
    except (ValueError,TypeError,KeyError):
        log.error('입력 형식 오류: %s',message.get('MessageId'));return 'invalid'
    status=push(payload,url)
    if status==204:
        sqs.delete_message(QueueUrl=queue,ReceiptHandle=message['ReceiptHandle'])
        log.info('전송·삭제 성공: %s',message['MessageId']);return 'success'
    if status in (401,403,404):return 'configuration_error'
    if status==429 or status>=500:
        n=int(message.get('Attributes',{}).get('ApproximateReceiveCount','1'))
        delay=min(300,2**min(n,8)+random.randint(1,5))
        sqs.change_message_visibility(QueueUrl=queue,ReceiptHandle=message['ReceiptHandle'],VisibilityTimeout=delay)
    log.warning('전송 거부, 메시지 유지: status=%s id=%s',status,message['MessageId'])
    return 'retry'

def main():
    import boto3
    from botocore.config import Config
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    queue=os.environ['QUEUE_URL'];loki=os.environ.get('LOKI_URL','http://loki:3100')
    if not queue:raise SystemExit('QUEUE_URL 설정이 필요합니다.')
    stop=threading.Event()
    for s in (signal.SIGTERM,signal.SIGINT):signal.signal(s,lambda *_:stop.set())
    sqs=boto3.client('sqs',region_name=os.environ.get('AWS_REGION','ap-northeast-2'),
        config=Config(connect_timeout=5,read_timeout=25,retries={'total_max_attempts':2,'mode':'standard'}))
    while not stop.is_set():
        try:
            with urlopen(loki+'/ready',timeout=5) as r:
                if r.status!=200:raise RuntimeError('Loki 준비 대기')
            response=sqs.receive_message(QueueUrl=queue,MaxNumberOfMessages=1,WaitTimeSeconds=20,
                VisibilityTimeout=120,MessageSystemAttributeNames=['ApproximateReceiveCount'])
            for m in response.get('Messages',[]):
                if stop.is_set():break
                outcome=deliver(m,sqs,queue,loki+'/loki/api/v1/push')
                if outcome=='configuration_error':
                    # 자동 재시작으로 실패 메시지를 계속 소비하지 않도록 프로세스는 유지하고 대기한다.
                    log.error('Loki 인증·URL 오류. 수정 후 Worker를 재시작하세요.')
                    stop.wait()
                elif outcome!='success':stop.wait(5)
        except Exception as e:
            log.error('수집 오류: %s, 미삭제 메시지는 유지됩니다.',type(e).__name__);stop.wait(10)

if __name__=='__main__':main()
