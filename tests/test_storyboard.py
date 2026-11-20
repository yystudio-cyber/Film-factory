import hashlib
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from factory import monitor, storyboard
from factory.common import write
from factory.store import Store
from factory.web import handler


class StoryboardTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store({"data": Path(tmp.name)})
        self.jid = self.store.add({"title":"夜研","seconds":6,"music":True,"script":"进入办公室",
                                   "requirements":"硬切","mode":"review"})
        self.folder = self.store.root / self.jid
        self.plan = {"shots":[{"beat":"隧道","seconds":2,"frames":48,"start":"","end":""},
                              {"beat":"办公室","seconds":4,"frames":96,"start":"previous","end":"office.jpg",
                               "anchors":["lead.png"]}],
                     "crew":{"1":{"camera":{"framing":"全景"},"sound":{"bridge":"隧道轰鸣"}},
                             "2":{"camera":{"framing":"旧版近景"}}},
                     "coverage":[{"id":"剧001","text":"进入办公室",
                                  "checks":[{"shot":2,"expect":"看到办公区","channel":"visual"}]}]}

    def take(self, number):
        name = f"S{number:02}-V1-deadbeef0{number}"
        directory = self.folder/"takes"/name
        directory.mkdir(parents=True,exist_ok=True)
        (directory/"cut.mp4").write_bytes(b"cut")
        (directory/"frame-4.jpg").write_bytes(b"last")
        return self.store.register(self.jid, number, directory)

    def test_empty_and_proposed_timeline(self):
        empty=storyboard.snapshot(self.store,self.jid)
        self.assertEqual(empty["shots"],[])
        write(self.folder/"proposed-plan.json",self.plan)
        result=storyboard.snapshot(self.store,self.jid)
        self.assertFalse(result["ready"])
        self.assertEqual(result["frames"],144)
        self.assertEqual([(s["start_frame"],s["end_frame"]) for s in result["shots"]],[(0,48),(48,144)])
        self.assertEqual(result["shots"][0]["references"],[])
        self.assertEqual(result["shots"][1]["movies"],[])
        self.assertEqual(result["shots"][1]["references"][0]["url"],None)
        self.assertFalse(result["master"])

    def test_adoption_reference_freeze_and_revision(self):
        write(self.folder/"plan.json",self.plan)
        key=hashlib.sha256(b"image").hexdigest()+".jpg"
        (self.folder/"references").mkdir()
        (self.folder/"references"/key).write_bytes(b"image")
        write(self.folder/"reference-index.json",{"office.jpg":{"file":key}})
        self.assertEqual(self.store.claim(),self.jid)
        first=self.take(1)
        first_dir=self.folder/"takes"/"S01-V1-deadbeef01"
        (first_dir/"frame-2.jpg").write_bytes(b"identity")
        write(first_dir/"review.json",{"subjects":[{"name":"小程","frame":2}]})
        self.assertFalse(self.store.advance(self.jid,1,first))
        self.store.command(self.jid,"approve",code=first)
        self.assertEqual(self.store.claim(),self.jid)
        second=self.take(2)
        write(self.folder/"takes"/"S02-V1-deadbeef02"/"shot.json",
              dict(self.plan["shots"][1],beat="办公室修订",title="到站",
                   cast_sources={"小程":f"C{first:03}"},
                   departments={"camera":{"framing":"修订近景"},"sound":{"bridge":"回声渐弱"}}))
        self.store.advance(self.jid,2,second)
        result=storyboard.snapshot(self.store,self.jid)
        a,b=result["shots"]
        self.assertEqual((a["movies"][0]["kind"],b["movies"][0]["kind"]),("adopted","candidate"))
        self.assertEqual(b["beat"],"办公室修订")
        self.assertEqual(b["title"],"到站")
        self.assertEqual(a["departments"]["camera"]["framing"],"全景")
        self.assertEqual(b["departments"]["camera"]["framing"],"修订近景")
        self.assertEqual(b["departments"]["sound"]["bridge"],"回声渐弱")
        self.assertEqual(result["gate"],2)
        self.assertEqual(b["checks"][0]["original"],"进入办公室")
        self.assertEqual(b["references"][0]["url"],f"/api/frames/{self.jid}/S01-V1-deadbeef01/4")
        self.assertEqual(b["references"][1]["url"],f"/api/storyboard/{self.jid}/refs/{key}")
        self.assertEqual(b["references"][-1],{"role":"角色指认帧","name":f"小程 · C{first:03} · 抽帧 3/5",
                                             "url":f"/api/frames/{self.jid}/{first_dir.name}/2"})
        self.assertEqual(monitor.snapshot(self.store,self.jid,2)["shots"][1]["beat"],"办公室修订")
        self.assertEqual(storyboard.reference_file(self.store,self.jid,key),self.folder/"references"/key)
        self.assertIsNone(storyboard.reference_file(self.store,self.jid,"0"*64+".jpg"))
        self.assertIsNone(storyboard.reference_file(self.store,self.jid,"../../private.jpg"))

    def test_alternate_media_retains_its_own_post_and_cast_material(self):
        self.test_adoption_reference_freeze_and_revision()
        self.store.command(self.jid,"approve",code=2)
        self.store.claim()
        folder=self.folder/"takes"/"S02-V2-deadbeef04"
        folder.mkdir()
        (folder/"cut.mp4").write_bytes(b"alt")
        write(folder/"shot.json",dict(self.plan["shots"][1],beat="备选构图",title="另一种定版"))
        code=self.store.register(self.jid,2,folder)
        shot=storyboard.snapshot(self.store,self.jid)["shots"][1]
        selected,alternate=shot["movies"]
        self.assertEqual((selected["kind"],alternate["kind"],alternate["code"]),("adopted","candidate",code))
        self.assertEqual(shot["title"],"到站")
        self.assertEqual(alternate["design"]["title"],"另一种定版")
        self.assertFalse(any(r["role"]=="角色指认帧" for r in alternate["design"]["references"]))

    def test_draft_is_distinct_from_approved_or_master(self):
        write(self.folder/"plan.json",self.plan)
        path=self.folder/"drafts"/"S02-0123456789ab"
        path.mkdir(parents=True)
        (path/"preview.mp4").touch()
        write(path/"meta.json",{"created_ns":time.time_ns(),"filename":path.name+".mp4",
                                "width":960,"height":540,"status":"退回"})
        result=storyboard.snapshot(self.store,self.jid)
        item=result["shots"][1]["movies"][0]
        self.assertEqual(item["kind"],"draft")
        self.assertEqual(item["status"],"退回")
        self.assertIn("退回",item["label"])
        self.assertIsNone(result["master"])
        self.assertEqual(item["url"],f"/api/drafts/{self.jid}/{path.name}.mp4")

    def test_upstream_sql_edit_invalidates_older_downstream_draft(self):
        write(self.folder/"plan.json",self.plan)
        self.assertEqual(self.store.claim(),self.jid)
        self.store.command(self.jid,"revise",shot=1,note="改为窗外")
        self.store.revisions(self.jid)
        epoch=self.store.edit_epochs(self.jid)[1]
        write(self.folder/"revised-S01.json",{"notes":["改为窗外"],
              "shot":dict(self.plan["shots"][0],beat="窗外")})
        path=self.folder/"drafts"/"S02-0123456789ab"
        path.mkdir(parents=True)
        (path/"preview.mp4").touch()
        meta={"created_ns":time.time_ns(),"filename":path.name+".mp4",
              "width":960,"height":540,"status":"通过"}
        write(path/"meta.json",meta)
        result=storyboard.snapshot(self.store,self.jid)
        self.assertEqual(result["shots"][0]["beat"],"窗外")
        self.assertEqual(result["shots"][1]["movies"],[])
        write(path/"meta.json",dict(meta,edit_epoch=epoch))
        result=storyboard.snapshot(self.store,self.jid)
        self.assertEqual(result["shots"][1]["movies"][0]["kind"],"draft")

    def test_live_web_entry_and_frozen_reference_range(self):
        key=hashlib.sha256(b"reference").hexdigest()+".jpg"
        (self.folder/"references").mkdir()
        (self.folder/"references"/key).write_bytes(b"reference")
        write(self.folder/"reference-index.json",{"office.jpg":{"file":key}})
        write(self.folder/"plan.json",self.plan)
        cfg={"data":self.store.root,"assets":self.store.root,"comfy":"http://127.0.0.1:1"}
        with ThreadingHTTPServer(("127.0.0.1",0),handler(cfg,self.store)) as server:
            url=f"http://127.0.0.1:{server.server_port}"
            worker=threading.Thread(target=server.serve_forever,daemon=True)
            worker.start()
            try:
                for route, token in [("/","renderDraft"),("/storyboard","departments"),
                                     ("/storyboard.js","selectedSource"),("/storyboard.css","#screen")]:
                    with urlopen(url+route) as response:
                        self.assertIn(token,response.read().decode())
                with urlopen(url+"/api/storyboard/"+self.jid) as response:
                    board=json.load(response)
                frozen=board["shots"][1]["references"][1]["url"]
                with urlopen(Request(url+frozen,headers={"Range":"bytes=0-2"})) as response:
                    self.assertEqual((response.status,response.read()),(206,b"ref"))
                body=dict(json.loads(self.store.job(self.jid)["spec"]),render_drafts=True,
                          production_brief={"audience":" 工程师 ","channel":" "})
                req=Request(url+"/api/jobs",data=json.dumps(body).encode(),
                            headers={"Content-Type":"application/json"})
                with urlopen(req) as response:
                    created=json.load(response)["id"]
                stored=json.loads(self.store.job(created)["spec"])
                self.assertEqual(stored["production_brief"],{"audience":"工程师"})
                self.assertTrue(stored["render_drafts"])
            finally:
                server.shutdown()
                worker.join()


if __name__ == "__main__":
    unittest.main()
