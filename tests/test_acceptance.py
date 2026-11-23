import argparse
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from factory import acceptance as station
from factory.common import read, write


class RouteInspection(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cfg = {"workflows":Path(tmp.name),"width":1920,"height":1080}
        self.graph = {"1":{"class_type":"Generate","inputs":{
            "text":"prompt","seed":0,"length":49,"model":"ltx","first":"old.jpg","last":"old.png"}},
            "2":{"class_type":"SaveVideo","inputs":{"video":["1",0]}}}
        self.bindings = {"prompt":["1.text"],"seed":["1.seed"],"frames":["1.length"],
                         "first":["1.first"],"last":["1.last"]}
        self.registry = {"Generate":{"input":{"required":{"text":["STRING"],"seed":["INT",{"min":0}],
                           "length":["INT",{"min":1,"max":193}],"model":[["ltx"]],"first":[["other.png"]],
                           "last":["COMBO",{"options":["other.jpg"]}]}},"output":["VIDEO"]},
                         "SaveVideo":{"input":{"required":{"video":["VIDEO"]}},"output":[],"output_node":True}}

    def inspect(self):
        write(self.cfg["workflows"]/"FLF.json",{"graph":self.graph,"bindings":self.bindings,"output":"2"})
        return station.inspect_route(self.cfg,"FLF",self.registry)

    def test_real_schema_and_dynamic_upload(self):
        result = self.inspect()
        self.assertTrue(result["passed"],result)
        self.assertEqual(result["nodes"],2)
        self.assertEqual(len(result["sha256"]),64)

    def test_missing_model_broken_wire_and_bad_frame_range(self):
        self.registry["Generate"]["input"]["required"]["length"][1]["max"] = 160
        self.registry["Generate"]["input"]["required"]["model"] = ["COMBO",{"options":["other"]}]
        self.graph["2"]["inputs"]["video"] = ["1",2]
        result = self.inspect()
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["errors"]),3,result["errors"])

    def test_disconnected_roles_unknown_nodes_and_bad_output(self):
        self.graph["2"]["inputs"]["video"] = ["3",0]
        self.graph["3"] = {"class_type":"Uninstalled","inputs":{}}
        self.registry["SaveVideo"]["output_node"] = False
        result = self.inspect()
        self.assertFalse(result["passed"])
        self.assertTrue(any("参与最终输出" in e for e in result["errors"]))
        self.assertTrue(any("Uninstalled" in e for e in result["errors"]))
        self.assertTrue(any("声明为输出" in e for e in result["errors"]))

    def test_video_path_proves_stage_order_and_flags_draft_or_opaque_nodes(self):
        def node(kind,**inputs):
            return {"class_type":kind,"inputs":inputs}
        graph = {"1":node("EmptyLTXVLatentVideo"),"2":node("KSampler",steps=8,latent_image=["1",0]),
                 "3":node("LatentUpscaleModelLoader",model_name="ltx-2.3-spatial-upscaler-x2-1.1.safetensors"),
                 "4":node("LTXVLatentUpsampler",samples=["2",0],upscale_model=["3",0]),
                 "5":node("ManualSigmas",sigmas="0.85, 0.725, 0.422, 0.0"),
                 "6":node("SamplerCustomAdvanced",latent_image=["4",0],sigmas=["5",0]),
                 "7":node("VAEDecode",samples=["6",0]),"8":node("VHS_VideoCombine",images=["7",0])}
        errors,warnings = [],[]
        result = station.calibration(graph,"8",errors,warnings)
        self.assertEqual((result["steps"],result["status"]),([8,3],"two_stages_observed"))
        self.assertEqual(errors,[])
        graph["9"] = node("LoraLoaderModelOnly",lora_name="ltx-2.3-distilled.safetensors",strength_model=0.7)
        graph["10"] = node("CFGGuider",cfg=1.5,model=["9",0])
        graph["6"]["inputs"]["guider"]=["10",0]
        station.calibration(graph,"8",errors,warnings)
        self.assertTrue(any("CFG=1.5" in e for e in errors))
        self.assertTrue(any("强度为 0.7" in e for e in errors))
        graph["2"]["inputs"]["steps"]=4
        station.calibration(graph,"8",errors,warnings)
        self.assertTrue(any("步数" in e for e in errors))
        graph["7"]["inputs"]["samples"]=["2",0]
        self.assertEqual(station.calibration(graph,"8",errors,warnings)["status"],"different_pipeline_observed")
        self.assertTrue(any("初始 LTX" in e for e in errors))
        graph["7"]["class_type"]="PrivateLatentMovieNode"
        errors.clear()
        self.assertEqual(station.calibration(graph,"8",errors,warnings)["status"],"unverified")
        self.assertFalse(errors)
        self.assertTrue(warnings)

    def test_bad_link_type(self):
        self.registry["Generate"]["output"] = ["AUDIO"]
        result = self.inspect()
        self.assertFalse(result["passed"])
        self.assertIn("连接类型不符",result["errors"][0])


class StationQualification(unittest.TestCase):
    def test_local_preflight_exercises_actual_master_codec(self):
        with tempfile.TemporaryDirectory() as tmp:
            font = os.environ.get("FILM_TEST_FONT") or (
                "C:/Windows/Fonts/arial.ttf" if os.name=="nt" else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
            cfg = dict(data=Path(tmp),font=font,width=1920,height=1080,min_free_gib=0)
            self.assertEqual(station._local(cfg)["master"],[1920,1080])

    def test_comfy_requires_cuda_and_ace_checks_business_status(self):
        cfg = dict(ace="http://ace",comfy="http://comfy")
        with patch.object(station,"request",return_value={"data":{"status":"ok"},"code":200}):
            self.assertEqual(station._ace(cfg)["status"],"ok")
        with patch.object(station,"request",return_value={"code":200,"data":{"status":"loading"}}):
            with self.assertRaisesRegex(ValueError,"健康"):
                station._ace(cfg)
        with patch.object(station,"request",return_value={"devices":[{"type":"cpu"}]}):
            with self.assertRaisesRegex(ValueError,"CUDA"):
                station._comfy(cfg)
        with patch.object(station,"request",return_value={"devices":[{"name":"RTX 3060","type":"cuda"}]}):
            self.assertEqual(station._comfy(cfg)["devices"][0]["name"],"RTX 3060")

    def test_gpu_placement_detects_swapped_devices_and_unknown_listener(self):
        cfg = {"comfy":"http://127.0.0.1:8188","ace":"http://127.0.0.1:8002"}
        inventory = "0, UUID0, RTX 3060, 12288, 6400\n1, UUID1, RTX 3060, 12288, 9000"
        processes = "111, UUID0\n333, UUID0"
        sockets = b"TCP 127.0.0.1:8188 0.0.0.0:0 LISTENING 111\nTCP 127.0.0.1:8002 0.0.0.0:0 LISTENING 222"
        results = [SimpleNamespace(stdout=x) for x in (inventory,processes,sockets)]
        with patch.object(station.os,"name","nt"),patch.object(station.subprocess,"run",side_effect=results):
            actual = station._gpu_placement(cfg)
        self.assertEqual(actual["services"]["comfy"]["gpu_indices"],[0])
        self.assertTrue(any("GPU0" in e for e in actual["errors"]))
        self.assertTrue(any("ace" in e for e in actual["warnings"]))

    def test_parser(self):
        parser = argparse.ArgumentParser()
        station.add_arguments(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["station-accept"])
        self.assertFalse(args.render)
        self.assertEqual(args.port,8766)

    def test_preflight_failure_persists_report_before_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"result.json"
            args=SimpleNamespace(report=path,render=True)
            with patch.object(station,"preflight",return_value={"passed":False}),patch.object(station,"watch") as watch:
                self.assertEqual(station.run_cli({"data":Path(tmp)},args),1)
                watch.assert_not_called()
                self.assertEqual(read(path)["status"],"preflight_failed")

    def test_wait_timeout_resubmission_is_idempotent_and_asset_change_invalidates_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/"a.png").write_bytes(b"test-a")
            (folder/"b.png").write_bytes(b"test-b")
            cfg=dict(data=folder,assets=folder,comfy="http://local",ace="http://local")
            args=SimpleNamespace(run_id="demo",first="a.png",last="b.png",port=8766,wait_minutes=0)
            report={"preflight":{"routes":{},"checks":{k:{"evidence":{"model":k}} for k in ("text","vision")}}}
            submitted=[]
            def server(url,body=None,**kwargs):
                if url.endswith("/api/station-test"):
                    submitted.append(body)
                    return {"ids":["a"*32]}
                return {"state":"queued","detail":"queued"}
            with patch.object(station,"request",side_effect=server):
                self.assertEqual(station.watch(cfg,args,report,lambda:None),2)
                self.assertEqual(station.watch(cfg,args,report,lambda:None),2)
                self.assertEqual(len(submitted),1)
                self.assertEqual(report["status"],"pending")
                (folder/"a.png").write_bytes(b"updated")
                with self.assertRaisesRegex(ValueError,"配置不同"):
                    station.watch(cfg,args,report,lambda:None)

    def test_normal_factory_three_route_movie_and_receipts(self):
        import test_factory
        test = test_factory.Environment()
        test.setUp()
        self.addCleanup(test.doCleanups)
        test.reject_first = False
        first, last = "first.png", "anchor.png"
        (test.cfg["assets"]/first).write_bytes((test.cfg["assets"]/last).read_bytes())
        (test.cfg["assets"]/"private-client.png").write_bytes(b"UNSELECTED")
        (test.cfg["assets"]/"private-client.txt").write_text("NEVER_SHARE_OTHER_CAPTION")
        (test.cfg["assets"]/"first.txt").write_text("NEVER_SHARE_SELECTED_SIDECAR")
        # Only inference is substituted. The actual input and delivery media use station geometry.
        from factory import media
        test.cfg.update(width=1920,height=1080)
        media.ff("-f","lavfi","-i","smptebars=size=1920x1088:rate=24",
                 "-f","lavfi","-i","sine=f=450:r=48000","-t",str(49/24),
                 "-c:v","libx264","-threads:v","2","-preset","ultrafast","-c:a","aac",
                 test.cfg["data"].parent/"fixture.mp4")
        port = int(test.url.rsplit(":",1)[1])
        args = SimpleNamespace(run_id="sample",first=first,last=last,port=port,wait_minutes=1)
        report={"preflight":{"routes":{},"checks":{k:{"evidence":{"model":k}} for k in ("text","vision")}}}
        worker = threading.Thread(target=test.engine.worker,daemon=True)
        worker.start()
        try:
            status = station.watch(test.cfg,args,report,lambda:None)
        finally:
            test.engine.shutdown.set()
            worker.join(5)
        self.assertEqual(status,0)
        wire = json.dumps([body for body,_,_ in test.calls])
        self.assertNotIn("private-client",wire)
        self.assertNotIn("NEVER_SHARE",wire)
        self.assertFalse(any(b"UNSELECTED" in upload for upload in test.uploaded))
        self.assertEqual([x["route"] for x in report["render"]["shots"]],["T2V","I2V","FLF"])
        self.assertEqual(report["qualification"]["profile_evidence"],"partial")
        self.assertTrue(report["qualification"]["unverified"])
        jid=report["job"]
        timeline=test.engine.store.delivery_dir(jid)/"timeline.json"
        rows=read(timeline)
        self.assertEqual([x["source"]["frames"] for x in report["render"]["shots"]],[49,49,49])
        self.assertEqual([report["render"]["master"][k] for k in ("width","height","frames")],[1920,1080,144])
        evidence_dir=os.getenv("FILM_TEST_EVIDENCE_DIR")
        if evidence_dir:
            import shutil
            output=Path(evidence_dir)
            output.mkdir(parents=True,exist_ok=False)
            shutil.copyfile(timeline.parent/"film.mp4",output/"synthetic-6s.mp4")
            write(output/"evidence.json",{"kind":"http_fixtures_real_media","routes":report["render"]["shots"],
                  "master":report["render"]["master"],"asset_isolation_checked":True})
        rows[1]["start"]="previous"
        write(timeline,rows)
        with self.assertRaisesRegex(ValueError,"I2V"):
            station.verify_render(test.cfg,jid,first,last)
