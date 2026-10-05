import base64
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime/agent'))
sys.path.insert(0,str(ROOT/'scripts'))
import planner
from password_hash import apr1

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

worker=load(ROOT/'runtime/worker/worker.py','multi_worker')
ALB='http 2026-09-21T01:30:15Z app/demo/123 198.51.100.10:456 10.0.1.5:80 0.001 0.002 0.001 403 - 100 200 "GET http://example.test:80/admin?q=1 HTTP/1.1" "test agent" - - arn:demo trace'
FLOW='2 123456789012 eni-test 198.51.100.10 10.0.1.5 50000 443 6 1 60 1789954214 1789954215 REJECT OK'

class MultiLogTests(unittest.TestCase):
    def normalize(self,kind,raw):
        payload=worker.normalize(json.dumps({'logGroup':'/test','logStream':'test','timestamp':1789954215000,'log_type':kind,'message':raw}))
        stream=payload['streams'][0]
        return stream['stream'],json.loads(stream['values'][0][1])
    def test_alb(self):
        labels,row=self.normalize('alb',ALB)
        self.assertEqual(labels['job'],'aws-alb')
        self.assertEqual(row['status'],403);self.assertEqual(row['target_status_code'],'-')
        self.assertEqual(row['path'],'/admin');self.assertEqual(row['raw_message'],ALB)
    def test_flow_not_http(self):
        labels,row=self.normalize('vpc_flow',FLOW)
        self.assertEqual(labels['job'],'aws-vpc_flow')
        self.assertEqual(row['action'],'REJECT');self.assertNotIn('status',row)
    def test_flow_nodata(self):
        _,row=self.normalize('vpc_flow','2 123456789012 eni-test - - - - - - - 100 101 - NODATA')
        self.assertEqual(row['log_status'],'NODATA')
    def test_alb_ipv6(self):
        _,row=self.normalize('alb',ALB.replace('198.51.100.10:456','[2001:db8::1]:456'))
        self.assertEqual(row['ip'],'2001:db8::1')
    def test_invalid_retained(self):
        sqs=Mock()
        body=json.dumps({'logGroup':'g','logStream':'s','timestamp':1,'log_type':'vpc_flow','message':'bad'})
        self.assertEqual(worker.deliver({'Body':body},sqs,'q','url'),'invalid')
        sqs.delete_message.assert_not_called()
    def test_search(self):
        p=planner.keyword_plan('최근 30분 VPC 차단')
        self.assertEqual(p['action'],'REJECT')
        self.assertIn('job="aws-vpc_flow"',planner.compile_query(p))
    def test_impossible_filters(self):
        for p in [{'log_type':'vpc_flow','status':'403'},{'log_type':'alb','action':'REJECT'},{'log_type':'unknown'}]:
            with self.assertRaises(ValueError):planner.validate(p)
    def test_apr1_reference(self):
        import shutil
        if not shutil.which('openssl'):self.skipTest('OpenSSL reference unavailable')
        for password in ['test','random-1234567890-abcdef']:
            expected=subprocess.check_output(['openssl','passwd','-apr1','-salt','12345678',password],text=True).strip()
            self.assertEqual(apr1(password,'12345678'),expected)

class ForwarderTests(unittest.TestCase):
    def setUp(self):
        self.sqs=Mock();fake=types.ModuleType('boto3');fake.client=Mock(return_value=self.sqs)
        with patch.dict(sys.modules,{'boto3':fake}),patch.dict(os.environ,{'QUEUE_URL':'queue'}):
            self.module=load(ROOT.parent/'terraform/lambda/cw-logs-to-sqs/lambda_function.py','forwarder')
    def event(self,data):return {'awslogs':{'data':base64.b64encode(gzip.compress(json.dumps(data).encode())).decode()}}
    def test_control(self):
        self.module.lambda_handler(self.event({'messageType':'CONTROL_MESSAGE'}),None)
        self.sqs.send_message.assert_not_called()
    def test_source_and_id(self):
        self.module.lambda_handler(self.event({'messageType':'DATA_MESSAGE','logGroup':'/x/vpc-flow-logs','logStream':'s','logEvents':[{'id':'e','timestamp':123,'message':FLOW}]}),None)
        body=json.loads(self.sqs.send_message.call_args.kwargs['MessageBody'])
        self.assertEqual(body['event_id'],'e');self.assertEqual(body['log_type'],'vpc_flow')
    def test_failure_propagates(self):
        self.sqs.send_message.side_effect=RuntimeError('down')
        with self.assertRaises(RuntimeError):
            self.module.lambda_handler(self.event({'logGroup':'g','logStream':'s','logEvents':[{'id':'e','timestamp':123,'message':'{}'}]}),None)

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.s3=Mock();self.logs=Mock();self.logs.put_log_events.return_value={}
        fake=types.ModuleType('boto3');fake.client=lambda name:self.s3 if name=='s3' else self.logs
        with patch.dict(sys.modules,{'boto3':fake}):
            self.module=load(ROOT.parent/'terraform/lambda/alb-to-cloudwatch/lambda_function.py','bridge')
        self.s3.get_object.return_value={'Body':io.BytesIO(gzip.compress((ALB+'\n').encode()))}
        self.event={'Records':[{'s3':{'bucket':{'name':'bucket'},'object':{'key':'alb/a.log.gz'}}}]}
    def test_bridge(self):
        with patch.dict(os.environ,{'LOG_GROUP':'group','LOG_STREAM':'stream'}):self.module.lambda_handler(self.event,None)
        self.assertEqual(self.logs.put_log_events.call_args.kwargs['logEvents'][0]['message'],ALB)
    def test_rejected_not_success(self):
        self.logs.put_log_events.return_value={'rejectedLogEventsInfo':{'tooOldLogEventEndIndex':0}}
        with patch.dict(os.environ,{'LOG_GROUP':'group','LOG_STREAM':'stream'}),self.assertRaises(RuntimeError):
            self.module.lambda_handler(self.event,None)

if __name__=='__main__':unittest.main()
