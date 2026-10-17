"""Loopback-only browser panel for the independent text and vision APIs."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler
import uuid
import webbrowser

from api_profiles import Profiles, ProfileError, Conflict, session_headers
from project_setup import reply_options, save_reply_options

ROOT = Path(__file__).resolve().parent
# Fixed 128x128 PNG; connection tests never read screenshots or chat history.
TEST_IMAGE = ('iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAIAAABMXPacAAABW0lEQVR4nO3OQQ0AMAgAMSTOvxkmo48jqYDOvD2SH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH7R9aofE3jEleG8AAAAASUVORK5CYII=')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url,code,'Redirect refused',headers,fp)


def server_error_detail(error, profile):
    """Read bounded JSON error fields only; never expose raw bodies or headers."""
    try:
        raw = error.read(65537)
        if not isinstance(raw, bytes) or len(raw) > 65536:
            return ''
        data = json.loads(raw)
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return ''
    finally:
        error.close()
    if not isinstance(data, dict):
        return ''
    value = data.get('error', data)
    if isinstance(value, str):
        value = {'message': value}
    if not isinstance(value, dict):
        return ''

    def clean(text):
        key = profile.get('api_key', '')
        if key:
            text = text.replace(key, '[密钥已隐藏]')
        text = text.replace(TEST_IMAGE, '[图片数据已隐藏]')
        text = re.sub(r'data:image/[^\s,;]+;base64,[A-Za-z0-9+/=_-]*',
                      '[图片数据已隐藏]', text, flags=re.I)
        text = re.sub(r'\bBearer\s+[^\s,"\'<>]+', 'Bearer [已隐藏]', text, flags=re.I)
        text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[密钥已隐藏]', text, flags=re.I)
        text = re.sub(r'[A-Za-z0-9+/_=-]{80,}', '[长数据已隐藏]', text)
        text = ''.join(c if c.isprintable() else ' ' for c in text)
        text = ' '.join(text.split())
        return text[:400] + ('…' if len(text) > 400 else '')

    fields = []
    for name in ('message', 'type', 'code', 'param'):
        item = value.get(name)
        if isinstance(item, (str, int, float)) and not isinstance(item, bool):
            item = clean(str(item))
            if item:
                fields.append(name + '=' + item)
    return '；'.join(fields)[:900]


def probe_connection(channel, profile, opener=None):
    content = '请只回复 OK。'
    if channel=='vision':
        content = [dict(type='text',text='请简单描述这张测试图片。'),
                   dict(type='image_url',image_url=dict(url='data:image/png;base64,'+TEST_IMAGE))]
    body = dict(model=profile['model'],stream=False,max_tokens=min(512,profile['max_tokens']),
                messages=[dict(role='user',content=content)])
    base = profile['base_url'].rstrip('/')
    url = base if base.endswith('/chat/completions') else base+'/chat/completions'
    headers = {'Authorization':'Bearer '+profile['api_key'],'Content-Type':'application/json',
               'User-Agent':'wechat-api-switcher/1.0'}
    headers.update(session_headers(profile,url,uuid.uuid4()))
    request = Request(url,data=json.dumps(body,ensure_ascii=False).encode('utf-8'),method='POST',headers=headers)
    started = time.monotonic()
    try:
        with (opener or build_opener(NoRedirect)).open(request,timeout=min(45,profile['timeout_seconds'])) as response:
            data = response.read(2*1024*1024+1)
            if len(data)>2*1024*1024:
                raise ProfileError('接口响应过大，请检查地址。')
            result = json.loads(data)
            value = result['choices'][0]['message']['content']
            if isinstance(value,list):
                value = ''.join(item.get('text','') for item in value if item.get('type')=='text')
            if not isinstance(value,str) or not value.strip():
                raise ProfileError('接口已响应，但没有返回有效正文。')
    except HTTPError as exc:
        hints = {401:'请检查密钥。',403:'请检查账号或模型权限。',404:'请检查接口路径和模型名称。',
                 429:'请求受限，请稍后重试。'}
        detail = server_error_detail(exc, profile)
        message = '接口返回 HTTP %d。' % exc.code
        message += ('服务端错误：' + detail) if detail else hints.get(exc.code,'服务端未提供可显示的 JSON 错误详情，请检查接口设置。')
        raise ProfileError(message) from None
    except (URLError,TimeoutError,OSError):
        raise ProfileError('连接失败或超时，请检查网络、证书和接口地址。') from None
    except (ValueError,KeyError,IndexError,TypeError):
        raise ProfileError('接口响应不符合当前 Chat Completions 格式。') from None
    return dict(ok=True,milliseconds=round((time.monotonic()-started)*1000),
                message='图片测试请求已返回有效内容。' if channel=='vision' else '文字测试请求已返回有效内容。')


def make_server(manager, port=0, token=None, probe=probe_connection):
    token = token or secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # No URLs, tokens, request bodies or credentials in terminal logs.

        def send(self, status, value, html=False):
            payload = value if html else json.dumps(value,ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(payload)

        def allowed(self, auth=True):
            host = '127.0.0.1:%d' % self.server.server_port
            origin = self.headers.get('Origin')
            if self.headers.get('Host')!=host or (origin and origin!='http://'+host):
                self.send(403,dict(error='访问来源不受支持。'))
                return False
            if auth and not secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):
                self.send(403,dict(error='请通过启动工具打开配置面板。'))
                return False
            return True

        def do_GET(self):
            if not self.allowed(auth=self.path!='/'):
                return
            try:
                if self.path=='/':
                    self.send(200,(ROOT/'api_switcher.html').read_bytes(),html=True)
                elif self.path=='/api/state':
                    self.send(200,manager.public())
                elif self.path=='/api/reply-settings':
                    self.send(200,reply_options(manager.root))
                else:
                    self.send(404,dict(error='页面不存在。'))
            except ProfileError as exc:
                self.send(400,dict(error=str(exc)))
            except Exception:
                self.send(500,dict(error='读取配置失败，请检查文件权限。'))

        def do_POST(self):
            if not self.allowed():
                return
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 65536 or self.headers.get('Content-Type')!='application/json':
                    self.send(400,dict(error='请求格式或长度无效。'))
                    return
                command = json.loads(self.rfile.read(length))
                if not isinstance(command,dict):
                    raise ProfileError('请求格式无效。')
                if self.path=='/api/save':
                    self.send(200,manager.mutate(command))
                elif self.path=='/api/test':
                    profile = manager.test_candidate(command)
                    self.send(200,probe(command['channel'],profile))
                elif self.path=='/api/reply-settings':
                    try:
                        with manager.writing():
                            value = save_reply_options(manager.root, command)
                    except ValueError as exc:
                        raise ProfileError(str(exc)) from None
                    self.send(200,value)
                elif self.path=='/api/shutdown':
                    self.send(200,dict(ok=True))
                    threading.Thread(target=self.server.shutdown,daemon=True).start()
                else:
                    self.send(404,dict(error='操作不存在。'))
            except Conflict as exc:
                self.send(409,dict(error=str(exc)))
            except ProfileError as exc:
                self.send(400,dict(error=str(exc)))
            except (ValueError,TypeError):
                self.send(400,dict(error='请求格式无效。'))
            except Exception:
                self.send(500,dict(error='操作未完成，请检查配置文件权限。'))

    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads = True
    return server,token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,default=ROOT)
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--no-browser',action='store_true')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('port 超出范围。')
    manager = Profiles(args.project)
    manager.ensure()
    server,token = make_server(manager,args.port)
    url = 'http://127.0.0.1:%d/#token=%s' % (server.server_port,token)
    print('API 配置面板：'+url,flush=True)
    print('切换从下一次请求生效；关闭面板不停止微信回复。终端按 Ctrl+C 退出。',flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__=='__main__':
    try:
        main()
    except ProfileError as exc:
        print('无法打开配置面板：'+str(exc))
        raise SystemExit(1)
