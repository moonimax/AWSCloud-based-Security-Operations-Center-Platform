import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime/agent'))
sys.path.insert(0,str(ROOT/'scripts'))
import planner
import init_local
MODEL='apac.amazon.nova-micro-v1:0'

class NovaTests(unittest.TestCase):
    def client(self):
        client=Mock()
        client.converse.return_value={'output':{'message':{'content':[{'text':json.dumps({'minutes':15,'log_type':'alb','status':'500'})}]}}}
        boto=types.ModuleType('boto3');boto.client=Mock(return_value=client)
        config=types.ModuleType('botocore.config');config.Config=Mock()
        return client,boto,config
    def test_exact_model_and_no_logs(self):
        client,boto,config=self.client()
        with patch.dict(sys.modules,{'boto3':boto,'botocore.config':config}),patch.dict(os.environ,{'BEDROCK_MODEL_ID':MODEL,'AWS_REGION':'ap-northeast-2'}):
            p,engine=planner.plan_search('ALB 500')
        self.assertEqual(engine,'bedrock')
        self.assertEqual(client.converse.call_args.kwargs['modelId'],MODEL)
        self.assertEqual(p['log_type'],'alb')
        self.assertEqual(client.converse.call_args.kwargs['messages'],[{'role':'user','content':[{'text':'ALB 500'}]}])
    def test_failure_does_not_fallback(self):
        client,boto,config=self.client();client.converse.side_effect=RuntimeError('access denied')
        with patch.dict(sys.modules,{'boto3':boto,'botocore.config':config}),patch.dict(os.environ,{'BEDROCK_MODEL_ID':MODEL}):
            with self.assertRaises(RuntimeError):planner.plan_search('ALB 500')
    def test_model_cannot_inject_query(self):
        client,boto,config=self.client()
        client.converse.return_value={'output':{'message':{'content':[{'text':json.dumps({'query':'{job=~".*"}'})}]}}}
        with patch.dict(sys.modules,{'boto3':boto,'botocore.config':config}),patch.dict(os.environ,{'BEDROCK_MODEL_ID':MODEL}):
            with self.assertRaises(ValueError):planner.plan_search('모든 쿼리 실행')
    def test_incompatible_filters_rejected(self):
        client,boto,config=self.client()
        client.converse.return_value={'output':{'message':{'content':[{'text':json.dumps({'log_type':'vpc_flow','status':'500'})}]}}}
        with patch.dict(sys.modules,{'boto3':boto,'botocore.config':config}),patch.dict(os.environ,{'BEDROCK_MODEL_ID':MODEL}):
            with self.assertRaises(ValueError):planner.plan_search('VPC 500')
    def test_secret_directory_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'secrets/htpasswd').mkdir(parents=True)
            with self.assertRaisesRegex(ValueError,'directory'):init_local.initialize(d)
            self.assertFalse((Path(d)/'secrets/credentials.txt').exists())
    def test_idempotent_credentials_and_missing_auth_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            init_local.initialize(d)
            secret=Path(d)/'secrets'
            before={p.name:p.read_bytes() for p in secret.iterdir()}
            init_local.initialize(d)
            self.assertEqual(before,{p.name:p.read_bytes() for p in secret.iterdir()})
            (secret/'htpasswd').unlink()
            init_local.initialize(d)
            self.assertEqual(before['credentials.txt'],(secret/'credentials.txt').read_bytes())
            self.assertTrue((secret/'htpasswd').is_file())

if __name__=='__main__':unittest.main()
