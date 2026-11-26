"""Real loopback HTTPS upstream. No request payload/header is logged."""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl
import threading
import time

import httpx

CERTS=Path(__file__).parent/"fixtures"/"tls"


class State:
    def __init__(self,scenario):
        self.scenario=scenario
        self.count=0
        self.bodies=[]
        self.received=threading.Event()
        self.lock=threading.Lock()
        self.redirect_url=None


@contextmanager
def upstream(scenario="ok"):
    state=State(scenario)

    class Handler(BaseHTTPRequestHandler):
        protocol_version="HTTP/1.1"

        def log_message(self,*args):
            pass

        def do_POST(self):
            body=self.rfile.read(int(self.headers.get("content-length",0)))
            with state.lock:
                state.count+=1
                state.bodies.append(json.loads(body))
                state.received.set()  # Receipt is recorded BEFORE any delay.
            if scenario=="timeout":
                time.sleep(2)
            headers={"x-request-id":"fixture-request-123"}
            status=200
            response={"model":"meta/llama-3.3-70b-instruct","choices":[{"index":0,"finish_reason":"stop",
                "message":{"role":"assistant","content":"READY"}}],
                "usage":{"prompt_tokens":12,"completion_tokens":1,"total_tokens":13}}
            if scenario=="no_usage":
                response.pop("usage")
            elif scenario=="bad_usage":
                response["usage"]={"prompt_tokens":True,"completion_tokens":1,"total_tokens":2}
            elif scenario=="wrong_model":
                response["model"]="different/model"
            elif scenario=="missing_model":
                response.pop("model")
            elif scenario=="bad_body":
                response={"unexpected":"body"}
            elif scenario=="invalid_json":
                response=None
            elif scenario in {"401","403","429","500"}:
                status=int(scenario)
                response={"error":{"message":"RAW-ERROR-SECRET-PROMPT","code":"insufficient_quota"}}
            elif scenario=="redirect":
                status=307
                headers["location"]=state.redirect_url or "https://second-host.invalid/receive"
                response={}
            elif scenario=="pending":
                status=202
            elif scenario=="secret_echo":
                response["choices"][0]["message"]["content"]="fixture-secret-token"
                headers["x-request-id"]="fixture-secret-token"
            payload=json.dumps(response).encode() if response is not None else b"invalid-json"
            if scenario=="large":
                payload=b"x"*(1024*1024+1)
            self.send_response(status)
            self.send_header("content-type","application/json")
            for key,value in headers.items():
                self.send_header(key,value)
            self.send_header("content-length",str(len(payload)))
            self.send_header("connection","close")
            self.end_headers()
            self.close_connection=True
            try:
                if scenario=="slow_drip":
                    for index in range(0,len(payload),10):
                        self.wfile.write(payload[index:index+10]);self.wfile.flush();time.sleep(0.08)
                else:
                    self.wfile.write(payload)
            except (BrokenPipeError,ConnectionResetError,ssl.SSLError):
                pass

    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    server.daemon_threads=True
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CERTS/"test-cert.pem",CERTS/"test-key.pem")
    server.socket=context.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,kwargs={"poll_interval":0.01},daemon=True)
    thread.start()
    state.url=f"https://127.0.0.1:{server.server_port}/v1/chat/completions"
    try:
        yield state
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


class LoopbackTransport(httpx.AsyncBaseTransport):
    """Test-only remapping AFTER adapter validates the exact production target.

    Uses HTTPX's real TLS transport, with the test certificate trusted explicitly.
    Neither production CLI nor deployment config can select this transport.
    """
    def __init__(self,url):
        self.url=httpx.URL(url)
        context=ssl.create_default_context(cafile=str(CERTS/"test-cert.pem"))
        self.inner=httpx.AsyncHTTPTransport(verify=context,retries=0)

    async def handle_async_request(self,request):
        request.url=self.url
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()
