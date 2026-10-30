#!/usr/bin/env python3
"""Small public demo landing/reset service. Python 3.6+, standard library only.

It cannot choose databases or commands. The only privileged operation is a
root-owned, no-argument reset script permitted by a dedicated sudoers entry.
"""
import hmac,json,os,secrets,subprocess,threading,time
from http.server import BaseHTTPRequestHandler,HTTPServer
from socketserver import ThreadingMixIn
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
STATE=Path(os.environ.get('RESET_STATE_DIR','/var/lib/inspection-reset'))
ORIGIN=os.environ.get('DEMO_ORIGIN','https://odoo.maomaokingdom.top').rstrip('/')
COOLDOWN=300
TOKEN=secrets.token_urlsafe(32)
LOCK=threading.Lock()
STATE.mkdir(parents=True,exist_ok=True)
state_file=STATE/'status.json'
state={'running':False,'phase':'ready','last_started':0,'last_result':None}
if state_file.exists():
    state.update(json.loads(state_file.read_text()))
    if state.get('running'):
        state.update(running=False,phase='interrupted',last_result='服务曾重启，请联系维护者确认。')

def persist():
    temp=STATE/'status.tmp';temp.write_text(json.dumps(state));temp.replace(state_file)

def job():
    result='重置未完成，请联系维护者；不会向浏览器暴露内部日志。'
    try:
        with (STATE/'reset.log').open('w') as logfile:
            process=subprocess.Popen(['sudo','-n',str(ROOT/'deploy/reset.sh')],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True)
            for line in process.stdout:
                logfile.write(line);logfile.flush()
                if line.startswith('PHASE '):
                    with LOCK:
                        state['phase']=line.strip()[6:];persist()
            code=process.wait()
        if code==0:result='已恢复初始数据，请重新登录。'
        with LOCK:
            state.update(running=False,phase='ready' if code==0 else 'failed',last_result=result);persist()
    except Exception:
        with LOCK:
            state.update(running=False,phase='failed',last_result=result);persist()

class Handler(BaseHTTPRequestHandler):
    server_version='DemoPortal'
    sys_version=''
    def log_message(self,fmt,*args):
        # Do not log cookies, tokens, body data or account credentials.
        print('%s %s'%(self.address_string(),fmt%args),flush=True)
    def send(self,code,data,kind='application/json; charset=utf-8'):
        if isinstance(data,dict):data=json.dumps(data,ensure_ascii=False).encode()
        if isinstance(data,str):data=data.encode()
        self.send_response(code)
        self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY');self.end_headers()
        if self.command!='HEAD':self.wfile.write(data)
    def do_HEAD(self):
        self.do_GET()
    def do_GET(self):
        if self.path in ('/demo','/demo/'):
            return self.send(200,(ROOT/'deploy/demo.html').read_bytes(),'text/html; charset=utf-8')
        if self.path=='/demo/api/status':
            with LOCK:
                data=dict(state)
            data['retry_after']=max(0,int(COOLDOWN-(time.time()-data['last_started'])))
            data['csrf_token']=TOKEN
            return self.send(200,data)
        return self.send(404,{'error':'Not found'})
    def do_POST(self):
        if self.path!='/demo/api/reset':return self.send(404,{'error':'Not found'})
        if self.headers.get('Origin')!=ORIGIN or not hmac.compare_digest(self.headers.get('X-Reset-Token',''),TOKEN):
            return self.send(403,{'error':'请从本站演示入口操作。'})
        if self.headers.get('Content-Type')!='application/json':return self.send(415,{'error':'Invalid content type'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=256:raise ValueError()
            data=json.loads(self.rfile.read(length))
            if data!={'confirm':'RESET_DEMO'}:raise ValueError()
        except (ValueError,TypeError):return self.send(400,{'error':'缺少重置确认。'})
        with LOCK:
            if state['running']:return self.send(409,{'error':'正在重置，请等待完成。'})
            if time.time()-state['last_started']<COOLDOWN:return self.send(429,{'error':'重置冷却中，请稍后再试。'})
            state.update(running=True,phase='Preparing a clean database',last_started=time.time(),last_result=None);persist()
        threading.Thread(target=job,daemon=True).start()
        return self.send(202,{'accepted':True})

class Server(ThreadingMixIn,HTTPServer):
    daemon_threads=True
    def get_request(self):
        connection,address=super().get_request();connection.settimeout(10);return connection,address

if __name__=='__main__':
    os.umask(0o077)
    Server((os.environ.get('RESET_BIND_IP','172.17.0.1'),8092),Handler).serve_forever()
