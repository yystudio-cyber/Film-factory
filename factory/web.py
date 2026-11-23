import json
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import drafts, graphs, monitor, setup, storyboard
from .common import assets, read, write
from .engine import Factory
from .store import Store
from .station import Station


def specification(body):
    if not isinstance(body, dict):
        raise ValueError("影片条目应为 JSON 对象")
    spec = {k:body.get(k, "") for k in ("title","script","requirements","seconds","mode","music")}
    if not spec["title"] and isinstance(spec["script"],str) and spec["script"].strip():
        spec["title"] = spec["script"].strip().splitlines()[0][:64]
    if not all(isinstance(spec[k],str) and spec[k].strip() for k in ("script","requirements","title")):
        raise ValueError("需要片名、剧本和制作要求")
    if len(spec["script"])+len(spec["requirements"]) > 100000:
        raise ValueError("剧本与要求总长度上限为十万字符")
    if spec["seconds"] in (None,""):
        from .timecode import requested_seconds
        spec["seconds"]=requested_seconds(spec["script"],spec["requirements"])
    if type(spec["seconds"]) is not int or not 2 <= spec["seconds"] <= 600:
        raise ValueError("目标时长为 2–600 整秒")
    from .timecode import breakdown
    breakdown(spec["script"],spec["seconds"],spec["requirements"])
    if spec["mode"] not in {"auto","review"} or type(spec["music"]) is not bool:
        raise ValueError("无效的生产模式或配乐设置")
    if type(body.get("render_drafts",False)) is not bool:
        raise ValueError("动态草稿选项须为布尔值")
    if body.get("draft_policy","all") not in {"all","risk"}:
        raise ValueError("草稿策略仅支持 all 或 risk")
    if body.get("render_drafts"):
        spec["render_drafts"] = True
        if body.get("draft_policy")=="risk":
            spec["draft_policy"] = "risk"
    brief = body.get("production_brief",{})
    if not isinstance(brief,dict) or brief.keys()-{"audience","objective","channel"} or any(
            not isinstance(v,str) or len(v)>600 for v in brief.values()):
        raise ValueError("制片简报接受受众、目的与渠道，每项限 600 字")
    brief = {k:v.strip() for k,v in brief.items() if v.strip()}
    if brief:
        spec["production_brief"] = brief
    return spec


def handler(cfg, store):
    station = Station(cfg)
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
            takes = store.takes(jid)
            job["takes"] = [{k:v for k,v in t.items() if k != "path"} for t in takes]
            designs = storyboard.current_designs(store, jid, job["plan"] or {}, storyboard.eligible_versions(takes))
            job["shot_summaries"] = [{"beat": shot.get("beat", ""), "seconds": shot["seconds"]} for shot, _ in designs]
            return job

        def do_GET(self):
            try:
                path = urlsplit(self.path).path
                if path in {"/","/setup"}:
                    return self.file(Path(__file__).with_name("index.html" if path == "/" else "setup.html"))
                if path in {"/monitor","/monitor.js","/monitor.css"}:
                    name = "monitor.html" if path == "/monitor" else path[1:]
                    return self.file(Path(__file__).with_name(name))
                if path in {"/storyboard","/storyboard.js","/storyboard.css"}:
                    return self.file(Path(__file__).with_name("storyboard.html" if path=="/storyboard" else path[1:]))
                match = re.fullmatch(r"/api/storyboard/([a-f0-9]{32})(?:/refs/([a-f0-9]{64}\.(?:png|jpe?g)))?",path)
                if match:
                    if not match[2]:
                        return self.respond(storyboard.snapshot(store,match[1]))
                    ref = storyboard.reference_file(store,match[1],match[2])
                    return self.file(ref) if ref else self.send_error(404)
                match = re.fullmatch(r"/api/drafts/([a-f0-9]{32})/(S\d{2,3}-[a-f0-9]{12}\.mp4)",path)
                if match:
                    video = drafts.video(store.root/match[1],match[2])
                    return self.file(video) if video else self.send_error(404)
                match = re.fullmatch(r"/api/monitor/([a-f0-9]{32})",path)
                if match:
                    selected = parse_qs(urlsplit(self.path).query).get("shot")
                    return self.respond(monitor.snapshot(store,match[1],int(selected[0]) if selected else None))
                match = re.fullmatch(r"/api/frames/([a-f0-9]{32})/(S[0-9]{2,3}-V[12]-[0-9a-f]{10})/([0-4]|seam)",path)
                if match:
                    frame = store.root/match[1]/"takes"/match[2]/("seam.jpg" if match[3]=="seam" else f"frame-{match[3]}.jpg")
                    if frame.is_file():
                        return self.file(frame)
                    return self.send_error(404)
                if path == "/api/jobs":
                    return self.respond([{k:(json.loads(v)["title"] if k=="spec" else v) for k,v in j.items()}
                                        for j in store.jobs()])
                if path == "/api/assets":
                    return self.respond(list(assets(cfg)))
                if path == "/api/station":
                    return self.respond(station.read())
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
                    else:
                        release,record = store.delivery_record(jid)
                        if release:
                            if record:
                                from .integrity import check
                                check(release,record,(artifact,))
                            return self.file(release/artifact)
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
                if self.path == "/api/station-test":
                    from .acceptance import isolated_brief
                    token = body["token"]
                    if not isinstance(token,str) or not re.fullmatch(r"station-accept-[A-Za-z0-9_-]{1,56}",token):
                        raise ValueError("无效的实机验收编号")
                    spec = isolated_brief(cfg, body["first"], body["last"],body["hashes"])
                    return self.respond({"ids": store.add_many([spec],token)},201)
                if self.path == "/api/batches":
                    rows = body.get("films")
                    if not isinstance(rows,list) or not 1<=len(rows)<=50:
                        raise ValueError("每批需要 1–50 部影片")
                    token = body.get("token")
                    if not isinstance(token,str) or not re.fullmatch(r"[a-zA-Z0-9-]{12,80}",token):
                        raise ValueError("无效的批次提交编号")
                    specs = [specification({"mode":"auto","music":True,**row}) for row in rows]
                    return self.respond({"ids":store.add_many(specs,token)},201)
                if self.path == "/api/jobs":
                    return self.respond({"id":store.add(specification(body))},201)
                if self.path == "/api/inspect":
                    return self.respond(setup.fields(graphs.unpack(body),cfg))
                if self.path == "/api/bind":
                    route = body["route"]
                    if route not in graphs.ROUTES:
                        raise ValueError("无效工作流")
                    profile = graphs.validate(body["profile"],route)
                    tier = body.get("tier","master")
                    if tier not in {"master","draft"}:
                        raise ValueError("请选择正片或动态草稿")
                    selected = drafts.settings(cfg) if tier=="draft" else cfg
                    if selected.get("station_contract"):
                        from .common import canvas
                        graphs.check_native_profile(profile,route,canvas(selected))
                    write(selected["workflows"]/(route+".json"),profile)
                    return self.respond({"saved":route,"tier":tier})
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
        if cfg.get("station_contract"):
            print("工作站资源："+json.dumps(Station(cfg).read(),ensure_ascii=False),flush=True)
        engine = Factory(cfg,store)
        thread = threading.Thread(target=engine.worker,daemon=True)
        thread.start()
        print(f"Film Factory: http://127.0.0.1:{http.server_port}",flush=True)
        try:
            http.serve_forever()
        finally:
            engine.shutdown.set()
            http.server_close()
            thread.join()
            engine.owner.close()
