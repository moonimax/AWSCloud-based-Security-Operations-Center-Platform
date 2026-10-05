import json,sys,threading,unittest
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime/agent'))
import app
class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def post(self,data,origin=None):
        headers={'Content-Type':'application/json'}
        if origin:headers['Origin']=origin
        return urlopen(Request(self.base+'/api/search',data=json.dumps(data).encode(),headers=headers))
    def test_manual_filter(self):
        with patch.object(app,'search',return_value={'total':1}) as search:
            result=json.load(self.post({'plan':{'status':'500'}}))
            self.assertEqual(result['engine'],'manual');self.assertEqual(search.call_args.args[0]['status'],'500')
    def test_origin_rejected(self):
        with self.assertRaises(HTTPError) as ctx:self.post({'plan':{}},'https://untrusted.example')
        self.assertEqual(ctx.exception.code,403)
    def test_extra_plan_field_rejected(self):
        with self.assertRaises(HTTPError) as ctx:self.post({'plan':{'url':'http://metadata/'}})
        self.assertEqual(ctx.exception.code,400)
    def test_backend_failure_not_success(self):
        with patch.object(app,'search',side_effect=TimeoutError()):
            with self.assertRaises(HTTPError) as ctx:self.post({'plan':{}})
            self.assertEqual(ctx.exception.code,502)
    def test_health(self):self.assertEqual(json.load(urlopen(self.base+'/health'))['status'],'ok')
if __name__=='__main__':unittest.main()
