import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime/agent'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import unittest, time, threading, json, copy, re
import query_engine as q
import analyzer
from planner import DEFAULT
from patch_dashboards import patch

class Tests(unittest.TestCase):
    def snap(self,rows=None):
        return {'id':'a'*32,'owner':'operator','plan':dict(DEFAULT),'query':'{job="test"}',
                'start':0,'end':7200,'created':time.time(),'state':'done','total':0,
                'done_chunks':2,'chunks':[(0,3600),(3600,7200)],'event':threading.Event()}
    def fake(self,rows):
        def call(path,p):
            if path.endswith('/query'):
                hi=int(p['time']);duration=int(re.search(r'\[(\d+)s\]',p['query'])[1])*q.NS
                n=sum(hi-duration<int(r[0])<=hi for r in rows)
                return {'result':[{'value':[hi,str(n)]}]}
            selected=[r for r in rows if int(p['start'])<=int(r[0])<int(p['end'])]
            selected=sorted(selected,key=lambda r:-int(r[0]))[:int(p['limit'])]
            return {'result':[{'stream':{'job':'test'},'values':selected}]}
        return call
    def test_pages_ties_no_loss(self):
        rows=[(str(7100*q.NS),'same-'+str(i)) for i in range(235)]+[(str(5000*q.NS-i),'old') for i in range(130)]
        s=self.snap();call=self.fake(rows);out=[];cursor=None
        for _ in range(10):
            d=q.page(s,call,100,cursor);out+=d['rows'];cursor=d['next_cursor']
            if not cursor:break
        self.assertEqual(len(out),len(rows));self.assertEqual(len({(r['timestamp_ns'],r['line']) for r in out}),len(rows))
    def test_range_boundary_count_and_rows(self):
        rows=[(str(n),'x') for n in [0,1,3600*q.NS,3600*q.NS+1,7200*q.NS,7200*q.NS+1]]
        s=self.snap();q.count_snapshot(s,self.fake(rows),True)
        self.assertEqual(s['total'],4)
        self.assertEqual(len(q.page(s,self.fake(rows))['rows']),4)
    def test_cursor_tamper(self):
        s=self.snap();cursor=q.encode(s,2000,1)
        with self.assertRaises(ValueError):q.decode(s,cursor[:-5]+'AAAAA')
        other=self.snap();other['id']='b'*32
        with self.assertRaises(ValueError):q.decode(other,cursor)
    def test_owner_expiry(self):
        s=self.snap();q.SNAPSHOTS[s['id']]=s
        with self.assertRaises(ValueError):q.get_snapshot(s['id'],'someone')
        s['created']-=1000
        with self.assertRaises(ValueError):q.get_snapshot(s['id'],'operator')
        q.SNAPSHOTS.clear()
    def test_sparse_budget(self):
        s=self.snap();s['end']=7*86400
        d=q.page(s,self.fake([]));self.assertEqual(d['scanned_hours'],2);self.assertTrue(d['next_cursor'])
    def test_exact_overflow_not_silent(self):
        s=self.snap()
        with self.assertRaises(ValueError):q.page(s,self.fake([(str(1000),'a')]*q.TIE_LIMIT))
    def test_chunk_cache(self):
        q.COUNTS.clear();s=self.snap();calls=[]
        def call(path,p):calls.append(p);return {'result':[]}
        q.count_snapshot(s,call,False);q.count_snapshot(self.snap(),call,False)
        self.assertEqual(len(calls),2)
        q.count_snapshot(self.snap(),call,True);self.assertEqual(len(calls),4)
        self.assertTrue(all(int(p['time'])>=3600*q.NS for p in calls))
    def test_grouping_preserves_security_fields(self):
        e={'plan':DEFAULT,'start_unix':0,'end_unix':100,'total':1000,'sample_count':3,'sampling':'sample','logs':[]}
        for i,(ts,ip) in enumerate([(1,'1.1.1.1'),(2,'1.1.1.1'),(3,'2.2.2.2')]):
            e['logs'].append({'id':'L'+str(i),'line':json.dumps({'time':ts,'ip':ip,'path':'/admin','status':403}),'labels':{},'truncated':False})
        d=analyzer.compact_evidence(e);self.assertEqual(d['group_count'],2)
        self.assertEqual(d['groups'][0]['sample_occurrences'],2)
        self.assertEqual(d['total'],1000)
    def test_json_patch_preserves_uid_layout_and_idempotent(self):
        d={'uid':'u','panels':[{'id':1,'type':'timeseries','gridPos':{'x':0},'datasource':{'type':'loki'},'targets':[{'expr':'sum(count_over_time({job="a"}[5m]))'}]}]}
        patch(d);once=copy.deepcopy(d);patch(d);self.assertEqual(d,once)
        self.assertEqual(d['uid'],'u');self.assertEqual(d['panels'][0]['gridPos'],{'x':0})
        self.assertIn('${sentinel_interval}',d['panels'][0]['targets'][0]['expr'])
    def test_csv_formula_safety(self):self.assertTrue(analyzer.safe_cell('=1+1').startswith("'"))

if __name__=='__main__':unittest.main()

class HTTPTests(unittest.TestCase):
    def test_api_snapshot_analysis_cache_csv_and_auth(self):
        import app
        from unittest.mock import patch as mock
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        from http.server import ThreadingHTTPServer
        calls=[]
        def loki(path,p):
            if path.endswith('/query'):return {'result':[{'value':[int(p['time']), '1']}]}
            calls.append(p)
            return {'result':[{'stream':{'job':'test'},'values':[[str(int(p['end'])-1),'{"ip":"1.2.3.4","status":403}']]}]}
        invoked=[]
        def invoke(model,question,evidence,detail,stage):
            invoked.append(1);stage('결과 검증')
            return {'summary':'표본 확인','findings':[],'limitations':['표본만 분석'],'next_checks':[]},{'inputTokens':10,'outputTokens':10},{'bedrock_ms':1,'validate_ms':1}
        with q.LOCK:q.SNAPSHOTS.clear();q.COUNTS.clear()
        with analyzer.LOCK:analyzer.JOBS.clear()
        server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        def request(path,data=None,user='operator'):
            h={'Content-Type':'application/json'}
            if user:h['X-WEBAUTH-USER']=user
            req=Request('http://127.0.0.1:'+str(server.server_port)+path,data=json.dumps(data).encode() if data is not None else None,headers=h)
            try:
                with urlopen(req,timeout=5) as r:return r.status,r.read()
            except HTTPError as e:return e.code,e.read()
        import os
        try:
            with mock.object(app,'loki_get',loki),mock.object(analyzer,'_invoke',invoke),mock.dict(os.environ,{'BEDROCK_ANALYSIS_MODEL_ID':'test-model'}):
                self.assertEqual(request('/api/search',{'plan':DEFAULT},user='')[0],401)
                code,raw=request('/api/search',{'plan':DEFAULT});self.assertEqual(code,200);s=json.loads(raw)
                code,raw=request('/api/search',{'plan':DEFAULT});self.assertEqual(json.loads(raw)['snapshot_id'],s['snapshot_id'])
                self.assertEqual(request('/api/snapshot/'+s['snapshot_id'],user='other')[0],404)
                code,raw=request('/api/logs',{'snapshot_id':s['snapshot_id'],'page_size':100});self.assertEqual(code,200)
                body={'plan':DEFAULT,'question':'분석','snapshot_id':s['snapshot_id']}
                code,raw=request('/api/analyze',body);self.assertEqual(code,202);job=json.loads(raw)['job_id']
                for _ in range(100):
                    result=json.loads(request('/api/analysis/'+job)[1])
                    if result['state']!='running':break
                    time.sleep(.01)
                self.assertEqual(result['state'],'done')
                code,raw=request('/api/analyze',body);self.assertEqual(code,200);self.assertTrue(json.loads(raw)['cached'])
                self.assertEqual(len(invoked),1)
                code,csv=request('/api/analysis/'+job+'/csv');self.assertEqual(code,200);self.assertTrue(csv.startswith(b'\xef\xbb\xbf'))
                self.assertEqual(request('/api/analysis/'+job,user='other')[0],404)
                self.assertEqual(len(invoked),1)
                self.assertIn('collect_ms',result['timings'])
        finally:server.shutdown();server.server_close()
