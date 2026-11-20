"""Real HTTP adapters and media tools. Only the three inference services are fixtures."""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from factory import graphs, media
from factory.common import frame_count, read, request, write
from factory.engine import Factory
from factory.store import Store
from factory.web import handler


class Environment(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name in ("assets","data","workflows"):
            (root/name).mkdir()
        clip, score = root/"fixture.mp4", root/"fixture.wav"
        media.ff("-f","lavfi","-i","testsrc2=size=320x192:rate=24",
                 "-f","lavfi","-i","sine=f=500:r=48000","-t",str(49/24),
                 "-c:v","libx264","-c:a","aac",clip)
        media.ff("-i",clip,"-frames:v","1",root/"assets"/"anchor.png")
        media.ff("-f","lavfi","-i","sine=f=200:r=48000","-t","6",score)
        self.submitted, self.uploaded, self.reviews = [], [], []
        self.reject_first = True
        self.reject_all = False
        self.reject_final = False
        owner = self
        shot = dict(beat="测试工位",seconds=2,start="",end="",sound="room tone",dialogue="",
                    narration="",title="",overlay="")
        plan = dict(bible="One workshop",music="Piano",shots=[
            shot,dict(shot,start="previous"),dict(shot,start="previous",end="anchor.png",title="END")])
        class Services(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def reply(self,obj):
                data = obj if isinstance(obj,bytes) else json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Length",str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                if self.path == "/upload/image":
                    owner.uploaded.append(raw)
                    return self.reply({"name":"image.png","subfolder":""})
                body = json.loads(raw)
                if self.path == "/prompt":
                    owner.submitted.append(body["prompt"])
                    return self.reply({"prompt_id":str(len(owner.submitted))})
                if self.path == "/release_task":
                    return self.reply({"data":{"task_id":"M1"}})
                if self.path == "/query_result":
                    return self.reply({"data":[{"task_id":"M1","status":1,
                                        "result":json.dumps([{"file":"/v1/audio?path=sample.wav"}])}]})
                instruction = body["messages"][0]["content"]
                if "You direct" in instruction:
                    value = plan
                elif "Update this shot" in instruction:
                    data=json.loads(body["messages"][1]["content"][0]["text"])
                    value = {"shot":dict(data["shot"],beat="lower camera")}
                elif "English LTX" in instruction:
                    value = {"prompt":"A camera travels through a bright and busy workshop with two desks. "*8
                                     +" At two seconds it stops. Sound: a quiet hum."}
                elif "Judge the candidate" in instruction:
                    owner.reviews.append(body)
                    ok = (len(owner.reviews)>1 or not owner.reject_first) and not owner.reject_all
                    value = {"scores":{k:5 if ok else 2 for k in
                             ("story","composition","identity","continuity","artifacts")},
                             "notes":"workshop visible","repair":"show the desk"}
                elif "Audit the actual" in instruction:
                    value = {"passed":not owner.reject_final,"notes":"片序符合要求"}
                else:
                    raise AssertionError(instruction)
                self.reply({"choices":[{"message":{"content":json.dumps(value)}}]})
            def do_GET(self):
                if self.path.startswith("/history/"):
                    pid = self.path.split("/")[-1]
                    return self.reply({pid:{"outputs":{"2":{"videos":[{"filename":"fixture.mp4","type":"output"}]}},
                                             "status":{"status_str":"success"}}})
                if self.path.startswith("/view?"):
                    return self.reply(clip.read_bytes())
                if self.path.startswith("/v1/audio?"):
                    return self.reply(score.read_bytes())
                self.reply({})
        self.srv = self.server(Services)
        base = f"http://127.0.0.1:{self.srv.server_port}"
        font = os.environ.get("FILM_TEST_FONT")
        if not font:
            font = "C:/Windows/Fonts/arial.ttf" if os.name=="nt" else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        self.cfg = dict(data=root/"data",assets=root/"assets",workflows=root/"workflows",width=320,height=180,
                        poll_seconds=0,model="fixture",threshold=4,attempts=2,comfy=base,ace=base,llm=base,font=font)
        for route, extra in graphs.ROUTES.items():
            fields = {"prompt","frames","seed"} | extra
            profile = {"graph":{"1":{"class_type":"Fixture","inputs":{x:0 for x in fields}},
                                "2":{"class_type":"SaveVideo","inputs":{}}},
                       "bindings":{x:["1."+x] for x in fields},"output":"2"}
            write(self.cfg["workflows"]/(route+".json"),profile)
        self.store = Store(self.cfg)
        self.engine = Factory(self.cfg,self.store)
        web = self.server(handler(self.cfg,self.store))
        self.url = f"http://127.0.0.1:{web.server_port}"
        self.key = patch.dict(os.environ,{"FILM_LLM_KEY":"test"})
        self.key.start()
        self.addCleanup(self.key.stop)

    def server(self,handler_type):
        s = ThreadingHTTPServer(("127.0.0.1",0),handler_type)
        threading.Thread(target=s.serve_forever,daemon=True).start()
        self.addCleanup(s.server_close)
        self.addCleanup(s.shutdown)
        return s

    def new(self, mode="auto"):
        return request(self.url+"/api/jobs",{"title":"Integration","script":"三个工位镜头，片尾 END",
                       "requirements":"写实", "seconds":6,"mode":mode,"music":True})["id"]

    def execute(self,jid):
        self.assertEqual(self.store.claim(),jid)
        self.engine.produce(jid)

    def test_full_movie(self):
        jid = self.new()
        self.execute(jid)
        job = request(self.url+"/api/jobs/"+jid)
        self.assertEqual(job["state"],"done")
        self.assertEqual([set(g["1"]["inputs"]) & {"first","last"} for g in self.submitted],
                         [set(),set(),{"first"},{"first","last"}])
        self.assertEqual(len(self.uploaded),3)
        self.assertEqual(len(set(t["code"] for t in job["takes"])),3)
        folder=self.cfg["data"]/jid
        report=read(folder/"acceptance.json")
        self.assertEqual(report["technical"]["frames"],144)
        self.assertEqual((report["technical"]["width"],report["technical"]["height"]),(320,180))
        self.assertEqual(report["technical"]["audio"],"aac")
        self.assertTrue(report["editorial"]["passed"])
        saved=folder/"download.mp4"
        request(self.url+"/api/jobs/"+jid+"/film.mp4",dest=saved)
        self.assertEqual(saved.read_bytes(),(folder/"film.mp4").read_bytes())
        self.assertEqual(len(list((folder/"takes").glob("*/rejected.json"))),1)

    def test_gate_variants_revision_stop_restart_batch(self):
        self.reject_first=False
        jid=self.new("review")
        self.execute(jid)
        self.assertEqual(self.store.job(jid)["state"],"review")
        first=self.store.takes(jid)[0]["code"]
        request(self.url+f"/api/jobs/{jid}/revise",{"shot":1,"note":"镜头降低","variants":2})
        self.execute(jid)
        takes=self.store.takes(jid)
        self.assertFalse(takes[0]["eligible"])
        self.assertEqual(len(takes),3)
        request(self.url+f"/api/jobs/{jid}/approve",{"code":takes[-1]["code"]})
        self.store.recover()
        self.assertEqual(self.store.job(jid)["state"],"cancelled")
        self.assertIsNone(self.store.claim())
        request(self.url+f"/api/jobs/{jid}/resume",{})
        request(self.url+f"/api/jobs/{jid}/batch",{"end":3})
        self.execute(jid)
        self.assertEqual(self.store.job(jid)["state"],"done")
        self.assertNotEqual(first,takes[-1]["code"])

    def test_quality_failure_and_interruption(self):
        self.reject_all=True
        jid=self.new()
        with self.assertRaisesRegex(ValueError,"返工上限"):
            self.execute(jid)
        self.assertEqual(len(self.reviews),2)
        self.assertFalse((self.cfg["data"]/jid/"film.mp4").exists())
        self.store.command(jid,"stop")
        with self.assertRaises(InterruptedError):
            self.engine.check(jid)
        self.assertIsNone(self.store.claim())

    def test_final_rejection_withholds_master(self):
        self.reject_first=False
        self.reject_final=True
        jid=self.new()
        with self.assertRaisesRegex(ValueError, "整片剧本验收未通过"):
            self.execute(jid)
        self.assertFalse((self.cfg["data"]/jid/"film.mp4").exists())
        with self.assertRaisesRegex(RuntimeError,"404"):
            request(self.url+f"/api/jobs/{jid}/film.mp4")

    def test_archive_reserves_deleted_codes(self):
        from factory.archive import import_clips,references
        manifest=self.cfg["data"].parent/"legacy.json"
        write(manifest,{"last_issued":9,"clips":{"C001":"fixture.mp4"}})
        import_clips(self.store,manifest)
        self.assertTrue(references(self.store,"用 C001 的尾帧")["C001"].is_file())
        jid=self.new()
        self.store.claim()
        code=self.store.register(jid,1,self.cfg["data"]/jid/"new")
        self.assertEqual(code,10)

    def test_exact_clock(self):
        self.assertEqual([frame_count(i*24) for i in (2,4,6,8)],[49,97,145,193])


if __name__=="__main__":
    unittest.main()
