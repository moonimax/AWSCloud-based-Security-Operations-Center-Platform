import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen
from planner import DEFAULT, validate, plan_search, compile_query, dashboard_url
import analyzer
import query_engine as qe
try:
    import catalog
except ModuleNotFoundError as exc:
    if exc.name != 'catalog':raise
    catalog = None
try:
    import designs
except ModuleNotFoundError as exc:
    if exc.name != 'designs':raise
    designs = None

logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
LOKI=os.environ.get('LOKI_URL','http://loki:3100')
SLOTS=threading.BoundedSemaphore(2)

LOKI_SLOTS=threading.BoundedSemaphore(2)

def loki_get(path,params):
    if not LOKI_SLOTS.acquire(timeout=30): raise TimeoutError('Loki 조회 대기 초과')
    try:
        with urlopen(LOKI+path+'?'+urlencode(params),timeout=8) as r:
            raw=r.read(16*1024*1024+1)
        if len(raw)>16*1024*1024: raise RuntimeError('Loki 응답 크기 초과')
        data=json.loads(raw)
        if data.get('status')!='success': raise RuntimeError('Loki 조회 실패')
        return data['data']
    finally: LOKI_SLOTS.release()

class Handler(BaseHTTPRequestHandler):
    def send(self,status,data,ctype='application/json'):
        body=data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',ctype+'; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if analyzer.handle_get(self):return
        path=urlsplit(self.path).path
        if path.startswith('/api/snapshot/'):
            try:
                user=self.headers.get('X-WEBAUTH-USER','').strip()
                if not user:return self.send(401,{'error':'gateway의 내부 사용자 헤더가 필요합니다.'})
                return self.send(200,qe.public(qe.get_snapshot(path.rsplit('/',1)[-1],user)))
            except ValueError as exc:return self.send(404,{'error':str(exc)})
        if path=='/api/dashboards':
            return self.send(200,catalog.list_dashboards()) if catalog else self.send(404,{'error':'catalog.py가 없는 버전입니다.'})
        if path=='/api/design' and designs:
            user=self.headers.get('X-WEBAUTH-USER','')
            allowed=bool(user) and user in os.environ.get('DESIGN_IMPORT_USERS','operator').split(',')
            return self.send(200,{'design':designs.current(),'can_import':allowed})
        if path=='/':
            html=Path(__file__).with_name('index.html').read_bytes()
            if b'/analysis.js' not in html:
                html=html.replace(b'</body>',b'<script src="/analysis.js"></script></body>',1)
            return self.send(200,html,'text/html')
        if path=='/performance.js':return self.send(200,Path(__file__).with_name('performance.js').read_bytes(),'application/javascript')
        if path=='/api/config':return self.send(200,{'engine':'bedrock' if os.environ.get('BEDROCK_MODEL_ID') else 'keyword','model_id':os.environ.get('BEDROCK_MODEL_ID',''),'analysis_model_id':os.environ.get('BEDROCK_ANALYSIS_MODEL_ID',''),'default_url':dashboard_url(DEFAULT)})
        if path=='/health':return self.send(200,{'status':'ok'})
        return self.send(404,{'error':'찾을 수 없습니다.'})
    def do_POST(self):
        if analyzer.handle_post(self,loki_get):return
        if self.path not in ('/api/search','/api/count','/api/logs','/api/design'):return self.send(404,{'error':'찾을 수 없습니다.'})
        origin=self.headers.get('Origin')
        if origin and (urlsplit(origin).netloc!=self.headers.get('Host') or urlsplit(origin).scheme not in ('http','https')):
            return self.send(403,{'error':'요청 출처를 확인하세요.'})
        if not SLOTS.acquire(blocking=False):return self.send(429,{'error':'조회 중입니다. 잠시 후 다시 시도하세요.'})
        try:
            is_design=self.path=='/api/design'
            if is_design:
                if not designs:return self.send(404,{'error':'디자인 업로드가 없는 버전입니다.'})
                user=self.headers.get('X-WEBAUTH-USER','')
                if not user or user not in os.environ.get('DESIGN_IMPORT_USERS','operator').split(','):
                    return self.send(403,{'error':'디자인 가져오기 권한이 없습니다.'})
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=(1048576 if is_design else 4096) or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('요청 크기 또는 Content-Type을 확인하세요.')
            body=json.loads(self.rfile.read(size))
            if not isinstance(body,dict):raise ValueError('요청 형식 오류')
            if is_design:
                d,warnings=designs.normalize(body.get('dashboard'))
                if body.get('apply') is True:
                    return self.send(202,{'design':designs.save(d),'warnings':warnings,'state':'saved_pending_grafana'})
                return self.send(200,{'design':designs.metadata(d),'warnings':warnings,'state':'validated'})
            user=self.headers.get('X-WEBAUTH-USER','').strip()
            if not user:return self.send(401,{'error':'gateway의 내부 사용자 헤더가 필요합니다.'})
            if self.path=='/api/logs':
                snap=qe.get_snapshot(body['snapshot_id'],user)
                return self.send(200,qe.page(snap,loki_get,body.get('page_size',100),body.get('cursor')))
            if self.path=='/api/count':
                snap=qe.snapshot(validate(body['plan']),user,loki_get)
                return self.send(200,qe.public(snap))
            if 'plan' in body:plan=validate(body['plan']);engine='manual'
            else:
                text=body.get('text','')
                if not isinstance(text,str) or not 1<=len(text.strip())<=500:raise ValueError('검색어는 1~500자입니다.')
                plan,engine=plan_search(text)
            snap=qe.snapshot(plan,user,loki_get,body.get('fresh') is True)
            result={**qe.public(snap),'rows':[],'engine':engine,'dashboard_url':dashboard_url(plan),
                    'summary':'조회 조건을 적용했습니다. 전체 건수는 분할 집계 중이며 원문은 페이지별로 조회합니다.'}
            # 사용자 질문·원본 로그 대신 검증된 범위와 결과 건수만 감사 기록한다.
            logging.info('search engine=%s minutes=%s count=%s',engine,plan['minutes'],result['total'])
            return self.send(200,result)
        except (ValueError,TypeError,KeyError) as exc:return self.send(400,{'error':str(exc) if isinstance(exc,ValueError) else '요청 형식을 확인하세요.'})
        except Exception as exc:
            logging.exception('search failed')
            return self.send(502,{'error':'AI 또는 로그 저장소 연결을 확인하세요. 검색 실패 시 기존 대시보드를 유지합니다.'})
        finally:SLOTS.release()
    def log_message(self,*args):pass

if __name__=='__main__':ThreadingHTTPServer(('0.0.0.0',8000),Handler).serve_forever()
