import importlib.util,json,os,sys,unittest
from pathlib import Path
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime/agent'))
import planner
spec=importlib.util.spec_from_file_location('worker',ROOT/'runtime/worker/worker.py');worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)

class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.e={'log_group':'group','log_stream':'i-test','timestamp':123456,'event_id':'e1','message':'{"status":200,"duration_ms":50}'}
        self.m={'Body':json.dumps(self.e),'MessageId':'m1','ReceiptHandle':'r1'};self.sqs=Mock()
    def test_camel_and_snake_equivalent(self):
        a=worker.normalize(json.dumps(self.e))
        self.e['logGroup']=self.e.pop('log_group');self.e['logStream']=self.e.pop('log_stream')
        self.assertEqual(a,worker.normalize(json.dumps(self.e)))
    def test_conflicting_fields_rejected(self):
        self.e['logGroup']='other'
        with self.assertRaises(ValueError):worker.normalize(json.dumps(self.e))
    def test_status_204_only_deletes(self):
        for code in [200,204,260,302,400,401,403,404,429,500]:
            with self.subTest(code=code):
                self.sqs.reset_mock();worker.deliver(self.m,self.sqs,'q','url',lambda *_:code)
                self.assertEqual(self.sqs.delete_message.call_count,int(code==204))
    def test_network_exception_no_delete(self):
        with self.assertRaises(TimeoutError):worker.deliver(self.m,self.sqs,'q','url',Mock(side_effect=TimeoutError()))
        self.sqs.delete_message.assert_not_called()
    def test_missing_timestamp_rejected(self):
        del self.e['timestamp']
        with self.assertRaises(ValueError):worker.normalize(json.dumps(self.e))
    def test_unit_and_identity(self):
        s=worker.normalize(self.m['Body'])['streams'][0];row=json.loads(s['values'][0][1])
        self.assertEqual(row['request_time'],.05);self.assertEqual(row['event_id'],'e1')
        self.assertEqual(s['values'][0][0],'123456000000');self.assertEqual(s['stream']['job'],'web-access')
    def test_raw_log_preserved(self):
        self.e['message']='non-json'
        row=json.loads(worker.normalize(json.dumps(self.e))['streams'][0]['values'][0][1])
        self.assertEqual(row['event_type'],'unparsed')
    def test_stable_fallback_id(self):
        del self.e['event_id'];body=json.dumps(self.e)
        self.assertEqual(worker.normalize(body),worker.normalize(body))

class PlannerTests(unittest.TestCase):
    def test_korean_keywords(self):
        p=planner.keyword_plan('최근 30분 web-01의 500 오류')
        self.assertEqual(p['minutes'],30);self.assertEqual(p['server'],'web-01');self.assertEqual(p['status'],'500')
    def test_login_event(self):self.assertEqual(planner.keyword_plan('최근 1시간 로그인 실패')['event'],'login_failed')
    def test_bounds(self):
        for value in [0,1441,True,'30']:
            with self.assertRaises(ValueError):planner.validate({'minutes':value})
    def test_extra_tool_or_query_rejected(self):
        for key in ['query','url','command','tool']:
            with self.assertRaises(ValueError):planner.validate({key:'malicious'})
    def test_injection_rejected(self):
        for payload in [{'server':'x"} | json'},{'status':'500 or 1=1'},{'path':'/" | json'},{'ip':'0.0.0.0/0'}]:
            with self.assertRaises(ValueError):planner.validate(payload)
    def test_unsupported_request_not_silently_all(self):
        with self.assertRaises(ValueError):planner.keyword_plan('모든 보안 설정을 삭제해줘')
    def test_compile_and_family_url(self):
        p=planner.validate({'status':'5xx','ip':'198.51.100.2','path':'/login'})
        self.assertIn('status >= 500',planner.compile_query(p))
        from urllib.parse import urlparse,parse_qs
        args=parse_qs(urlparse(planner.dashboard_url(p)).query)
        self.assertEqual(len(args['var-status']),100);self.assertEqual(args['var-ip'],['198.51.100.2'])
    def test_bedrock_validation_and_no_raw_logs(self):
        client=Mock();client.converse.return_value={'output':{'message':{'content':[{'text':'{"minutes":30,"status":"500"}'}]}}}
        import types
        boto=types.ModuleType('boto3');boto.client=Mock(return_value=client)
        config=types.ModuleType('botocore.config');config.Config=Mock()
        with patch.dict(sys.modules,{'boto3':boto,'botocore.config':config}),patch.dict(os.environ,{'BEDROCK_MODEL_ID':'test'}):
            p,engine=planner.plan_search('최근 30분 500')
            self.assertEqual(engine,'bedrock');self.assertEqual(p['status'],'500')
            self.assertEqual(client.converse.call_args.kwargs['messages'][0]['content'],[{'text':'최근 30분 500'}])
            client.converse.return_value={'output':{'message':{'content':[{'text':'{"query":"arbitrary"}'}]}}}
            with self.assertRaises(ValueError):planner.plan_search('bad')

if __name__=='__main__':unittest.main()
