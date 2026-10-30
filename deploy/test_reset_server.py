"""HTTP guard tests only; real restore acceptance is a separate deployment step."""
import importlib.util,json,os,tempfile,threading,unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from pathlib import Path

class ResetHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        os.environ['RESET_STATE_DIR']=cls.tmp.name
        spec=importlib.util.spec_from_file_location('reset_portal',Path(__file__).with_name('reset_server.py'))
        cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)
        cls.calls=[]
        cls.module.job=lambda:cls.calls.append('fixed-reset-job')
        cls.server=cls.module.Server(('127.0.0.1',0),cls.module.Handler)
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()
        cls.url='http://127.0.0.1:%s'%cls.server.server_port
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.tmp.cleanup()
    def setUp(self):
        self.module.state.update(running=False,phase='ready',last_started=0,last_result=None)
        self.calls.clear()
    def request(self,path,body=None,headers=None):
        req=Request(self.url+path,data=body,headers=headers or {})
        try:
            response=urlopen(req,timeout=5)
        except HTTPError as e:response=e
        return response.status,response.read()
    def headers(self):return {'Origin':self.module.ORIGIN,'Content-Type':'application/json','X-Reset-Token':self.module.TOKEN}
    def test_get_status_never_resets(self):
        self.assertEqual(self.request('/demo/api/status')[0],200)
        self.assertFalse(self.calls)
        self.assertEqual(self.request('/demo/api/reset')[0],404)
    def test_cross_origin_and_missing_token_are_rejected(self):
        for h in ({},dict(self.headers(),Origin='https://evil.invalid')):
            self.assertEqual(self.request('/demo/api/reset',b'{"confirm":"RESET_DEMO"}',h)[0],403)
        self.assertFalse(self.calls)
    def test_explicit_confirmation_and_fixed_payload(self):
        for body in (b'{}',b'{"confirm":"RESET_DEMO","database":"other"}',b'not json'):
            self.assertEqual(self.request('/demo/api/reset',body,self.headers())[0],400)
        self.assertFalse(self.calls)
    def test_one_job_and_cooldown(self):
        body=b'{"confirm":"RESET_DEMO"}'
        self.assertEqual(self.request('/demo/api/reset',body,self.headers())[0],202)
        self.assertEqual(self.request('/demo/api/reset',body,self.headers())[0],409)
        self.module.state['running']=False
        self.assertEqual(self.request('/demo/api/reset',body,self.headers())[0],429)
        self.assertEqual(self.calls,['fixed-reset-job'])

if __name__=='__main__':unittest.main()
