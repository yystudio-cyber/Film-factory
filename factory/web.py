import json
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import graphs, setup
from .common import assets, read, write
from .engine import Factory
from .store import Store


def specification(body):
    spec = {k:body.get(k, "") for k in ("title","script","requirements","seconds","mode","music")}
    if not all(isinstance(spec[k],str) and spec[k].strip() for k in ("script","requirements","title")):
        raise ValueError("需要片名、剧本和制作要求")
    if len(spec["script"])+len(spec["requirements"]) > 100000:
        raise ValueError("剧本与要求总长度上限为十万字符")
    if type(spec["seconds"]) is not int or not 2 <= spec["seconds"] <= 600:
        raise ValueError("目标时长为 2–600 整秒")
    if spec["mode"] not in {"auto","review"} or type(spec["music"]) is not bool:
        raise ValueError("无效的生产模式或配乐设置")
    return spec


def handler(cfg, store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, data, status=200):
            raw = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control","no-store")
            self.end_headers()
            self.wfile.write(raw)

        def file(self, path):
            size = path.stat().st_size
            start, end = 0, size-1
            partial = self.headers.get("Range")
            if partial:
                match = re.fullmatch(r"bytes=(\d+)-(\d*)", partial)
                if not match:
                    return self.send_error(416)
                start, end = int(match[1]), min(end, int(match[2]) if match[2] else end)
                if start > end:
                    return self.send_error(416)
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(end-start+1))
            self.end_headers()
            with path.open("rb") as src:
                src.seek(start)
                remaining=end-start+1
                while remaining:
                    chunk=src.read(min(remaining,1024*1024))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining-=len(chunk)

        def job_view(self, jid):
            job = store.job(jid)
            folder = store.root / jid
            job["spec"] = json.loads(job["spec"])
            job["plan"] = read(folder/"plan.json") if (folder/"plan.json").is_file() else None
            job["events"] = store.history(jid)
            job["takes"] = [{k:v for k,v in t.items() if k != "path"} for t in store.takes(jid)]
            return job

        def do_GET(self):
            try:
                path = urlsplit(self.path).path
                if path in {"/","/setup"}:
                    return self.file(Path(__file__).with_name("index.html" if path == "/" else "setup.html"))
                if path == "/api/jobs":
                    return self.respond([{k:(json.loads(v)["title"] if k=="spec" else v) for k,v in j.items()}
                                        for j in store.jobs()])
                if path == "/api/assets":
                    return self.respond(list(assets(cfg)))
                if path == "/api/doctor":
                    return self.respond(setup.doctor(cfg))
                if path == "/api/history":
                    return self.respond(setup.history(cfg))
                if path.startswith("/api/history/"):
                    return self.respond(setup.historical_graph(cfg, path.split("/")[-1]))
                m = re.fullmatch(r"/api/jobs/([0-9a-f]{32})(?:/(film.mp4|acceptance.json|timeline.json|C(\d+).mp4))?",path)
                if m:
                    jid, artifact, code = m.groups()
                    if not artifact:
                        return self.respond(self.job_view(jid))
                    if code:
                        take = next((x for x in store.takes(jid) if x["code"]==int(code)),None)
                        if take:
                            return self.file(Path(take["path"])/"cut.mp4")
                    elif store.job(jid)["state"]=="done":
                        return self.file(store.root/jid/artifact)
                self.send_error(404)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as err:
                self.respond({"error":str(err)},400)

        def do_POST(self):
            local_hosts = {f"localhost:{self.server.server_port}",f"127.0.0.1:{self.server.server_port}"}
            origin = self.headers.get("Origin")
            if self.headers.get("Host") not in local_hosts or (origin and urlsplit(origin).netloc not in local_hosts):
                return self.send_error(403)
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 16_000_000 or self.headers.get_content_type()!="application/json":
                    return self.send_error(400)
                body = json.loads(self.rfile.read(length))
                if self.path == "/api/jobs":
                    return self.respond({"id":store.add(specification(body))},201)
                if self.path == "/api/inspect":
                    return self.respond(setup.fields(graphs.unpack(body),cfg))
                if self.path == "/api/bind":
                    route = body["route"]
                    if route not in graphs.ROUTES:
                        raise ValueError("无效工作流")
                    profile = graphs.validate(body["profile"],route)
                    write(cfg["workflows"]/(route+".json"),profile)
                    return self.respond({"saved":route})
                m = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/(stop|resume|approve|revise|batch)", self.path)
                if m:
                    store.command(m[1],m[2],**body)
                    return self.respond({"ok":True})
                self.send_error(404)
            except Exception as err:
                self.respond({"error":str(err)},400)
    return Handler


def serve(cfg, port):
    from .lock import exclusive
    with exclusive(cfg["data"]/"worker.lock"):
        store = Store(cfg)
        http = ThreadingHTTPServer(("127.0.0.1",port),handler(cfg,store))
        store.recover()
        engine = Factory(cfg,store)
        thread = threading.Thread(target=engine.worker,daemon=True)
        thread.start()
        print(f"Film Factory: http://127.0.0.1:{http.server_port}",flush=True)
        try:
            http.serve_forever()
        finally:
            engine.shutdown.set()
            http.server_close()
