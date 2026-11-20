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
        self.calls = []
        self.missing = False
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
                owner.calls.append((body,self.headers.get("Authorization"),self.path))
                content = body["messages"][1]["content"]
                data=json.loads(content if isinstance(content,str) else content[0]["text"])
                if "Read the two colored panels" in instruction:
                    import base64,io
                    from PIL import Image,ImageColor
                    image=Image.open(io.BytesIO(base64.b64decode(content[1]["image_url"]["url"].split(",")[1])))
                    lookup={ImageColor.getrgb(n):n for n in ("red","green","blue","yellow")}
                    value={"colors":[lookup[image.getpixel((x,32))] for x in (16,96)]}
                elif "Act as script supervisor" in instruction:
                    value={"rows":[{"id":u["id"],"covered":not owner.missing,"notes":"覆盖说明",
                                    "checks":[{"shot":i,"expect":"工位镜头","literal":"","channel":"visual"} for i in (1,2,3)]}
                                   for u in data["units"]]}
                elif "Evaluate each source unit" in instruction:
                    value={"rows":[{"id":u["id"],"passed":True,"notes":"可见工位","repair_shot":None} for u in data["units"]]}
                elif "You direct" in instruction:
                    value = plan
                elif "Update this shot" in instruction:
                    value = {"shot":dict(data["shot"],beat="lower camera")}
                elif "English LTX" in instruction:
                    value = {"prompt":"A camera travels through a bright and busy workshop with two desks. "*8
                                     +" At two seconds it stops. Sound: a quiet hum."}
                elif "Judge the candidate" in instruction:
                    owner.reviews.append(body)
                    ok = (len(owner.reviews)>1 or not owner.reject_first) and not owner.reject_all
                    value = {"scores":{k:5 if ok else 2 for k in
                             ("story","composition","identity","continuity","artifacts")},
                             "checks":[{"index":i+1,"result":"seen","evidence":"desk"} for i in range(len(data["shot"]["obligations"]))],
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

    def test_atomic_batch_and_coverage_gate(self):
        row = dict(title="短片",script="工位",seconds=6,requirements="片尾",music=False)
        payload={"token":"test-batch-unique","films":[row,{**row,"title":"第二部"}]}
        with self.assertRaisesRegex(RuntimeError,"400"):
            request(self.url+"/api/batches",{**payload,"films":[row,{**row,"seconds":-1}]})
        self.assertEqual(self.store.jobs(),[])
        ids=request(self.url+"/api/batches",payload)["ids"]
        self.assertEqual(request(self.url+"/api/batches",payload)["ids"],ids)
        self.missing=True
        with self.assertRaisesRegex(ValueError,"剧情核对失败"):
            self.execute(ids[0])
        self.assertEqual(len(self.submitted),0)
        self.store.status(ids[0],"failed","coverage")
        self.assertEqual(self.store.claim(),ids[1])

    def test_dual_provider_and_1080_monitor(self):
        self.reject_first=False
        self.cfg.update(text_provider="deepseek",ds_base=self.cfg["llm"]+"/ds",
                        ds_model="ds-v4.1-flash",width=1920,height=1080)
        root=self.cfg["data"].parent
        media.ff("-f","lavfi","-i","testsrc2=size=1920x1088:rate=24",
                 "-f","lavfi","-i","sine=f=400:r=48000","-t",str(49/24),
                 "-c:v","libx264","-preset","ultrafast","-threads","4","-c:a","aac",root/"fixture.mp4")
        with patch.dict(os.environ,{"FILM_DS_KEY":"text-only-test"}):
            jid=self.new()
            self.execute(jid)
        self.assertTrue(any(k=="Bearer text-only-test" for _,k,_ in self.calls))
        for body,key,path in self.calls:
            content = body["messages"][1]["content"]
            if path.startswith("/ds/"):
                self.assertEqual(key,"Bearer text-only-test")
                self.assertEqual(body["model"],"ds-v4.1-flash")
                self.assertIsInstance(content,str)
            else:
                self.assertEqual(key,"Bearer test")
                self.assertTrue(any(b["type"]=="image_url" for b in content))
        view=request(self.url+"/api/monitor/"+jid+"?shot=2")
        self.assertEqual(view["state"],"done")
        self.assertEqual((view["technical"]["width"],view["technical"]["height"],view["technical"]["frames"]),
                         (1920,1080,144))
        self.assertTrue(view["source_verdicts"])
        self.assertTrue(view["attempts"][0]["seam"])
        self.assertTrue(view["stages"])
        a=view["attempts"][0]["id"]
        image=root/"boundary.jpg"
        request(self.url+f"/api/frames/{jid}/{a}/seam",dest=image)
        self.assertTrue(image.read_bytes().startswith(b"\xff\xd8"))

    def test_post_receipt_loss_recovers_existing_render(self):
        from factory.common import request as original
        self.reject_first=False
        jid=self.new()
        calls=[]
        nonce=[]
        def lose_receipt(url,body=None,**kwargs):
            if url.endswith("/prompt") and not calls:
                calls.append(original(url,body,**kwargs))
                nonce.append(body["client_id"])
                raise ConnectionError("response lost after acceptance")
            if url.endswith("/history?max_items=128") and nonce:
                return {"1":{"prompt":[0,"1",{},{"client_id":nonce[0]}]}}
            return original(url,body,**kwargs)
        with patch("factory.graphs.request",side_effect=lose_receipt):
            with self.assertRaisesRegex(ConnectionError,"response lost"):
                self.execute(jid)
            self.store.status(jid,"failed","response lost")
            self.store.command(jid,"resume")
            self.execute(jid)
        self.assertEqual(len(self.submitted),3)
        self.assertEqual(self.store.job(jid)["state"],"done")

    def test_low_disk_no_inference(self):
        from collections import namedtuple
        jid=self.new()
        usage=namedtuple("Usage","total used free")(1000,999,1)
        with patch("factory.common.shutil.disk_usage",return_value=usage):
            with self.assertRaisesRegex(ValueError,"工作盘"):
                self.execute(jid)
        self.assertFalse(self.calls)
        self.assertFalse(self.submitted)

    def test_ambiguous_remote_does_not_requeue(self):
        with patch("factory.graphs.request",return_value={}) as service:
            with self.assertRaisesRegex(RuntimeError,"回执不确定"):
                graphs.reconcile("http://fixture","no-match")
        self.assertEqual(service.call_count,2)
        self.assertTrue(all(len(c.args)==1 for c in service.call_args_list))

    def test_reference_snapshot_survives_original_change(self):
        from factory.references import snapshot
        f = self.cfg["assets"]/"anchor.png"
        b = f.read_bytes()
        folder = self.cfg["data"]
        result = snapshot(folder,{"anchor.png":f},["anchor.png"])
        f.write_bytes(b"changed")
        self.assertEqual(snapshot(folder,{},["anchor.png"])["anchor.png"].read_bytes(),b)
        self.assertNotEqual(result["anchor.png"],f)

    def test_worker_continues_after_failed_film(self):
        import time
        first, second = self.new(), self.new()
        real = self.engine.produce
        def one_bad(jid):
            if jid==first:
                raise ValueError("first failed")
            real(jid)
        self.reject_first=False
        with patch.object(self.engine,"produce",side_effect=one_bad):
            thread=threading.Thread(target=self.engine.worker,daemon=True)
            thread.start()
            try:
                end=time.monotonic()+60
                while self.store.job(second)["state"] not in {"done","failed"} and time.monotonic()<end:
                    time.sleep(.1)
                self.assertEqual(self.store.job(first)["state"],"failed")
                self.assertEqual(self.store.job(second)["state"],"done")
            finally:
                self.engine.shutdown.set()
                thread.join(5)

    def test_exact_clock(self):
        self.assertEqual([frame_count(i*24) for i in (2,4,6,8)],[49,97,145,193])


if __name__=="__main__":
    unittest.main()
