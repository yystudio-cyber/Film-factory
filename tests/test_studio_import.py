import argparse
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from factory import studio_import
from factory.common import assets, read, write
from factory.store import Store


class TestStudioImport(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.old=self.root/"Studio"
        self.old.mkdir()
        self.cfg={"data": self.root/"factory", "assets":self.root/"shared"}
        for folder in self.cfg.values():
            folder.mkdir()
        self.source=self.old/"legacy.json"
        Image.new("RGB",(32,32),"red").save(self.old/"夜景.png")
        self.project={"project_id":"fixture-project","name":"先导片","script":"木门打开。温室亮灯。","seconds":8,
                      "assets":[{"asset_id":"inputA","path":"夜景.png"}]}
        write(self.source,self.project)
        self.parser=argparse.ArgumentParser()
        studio_import.add_arguments(self.parser.add_subparsers(dest="command",required=True))

    def invoke(self,*extra):
        args=self.parser.parse_args(["import-studio",str(self.source),*map(str,extra)])
        return studio_import.run(self.cfg,args)

    def test_prepare_and_enqueue_idempotently(self):
        (self.old/"夜景.txt").write_text("花园夜景",encoding="utf8")
        mapping=self.old/"mapping.json"
        write(mapping,{"links":[{"shot":"S02","first":"夜景.png"}],"mode":"auto","music":False})
        preview=self.invoke("--inspect")
        self.assertIn("/assets/0/path",[r["pointer"] for r in preview["structure"]])
        prepared=self.invoke("--mapping",mapping)
        self.assertNotIn("job_id",prepared)
        self.assertFalse((self.cfg["data"]/"factory.sqlite3").exists())
        saved=self.invoke("--mapping",mapping,"--enqueue")
        self.assertEqual(saved,self.invoke("--mapping",mapping,"--enqueue"))
        sealed=read(Path(saved["directory"])/"brief.json")
        self.assertEqual(studio_import.project_config(self.cfg,{}),self.cfg)
        scoped=studio_import.project_config(self.cfg,sealed)
        self.assertEqual(set(assets(scoped)),{"夜景.png"})
        self.assertEqual(set(assets(self.cfg)),set())
        (self.old/"夜景.png").unlink()
        self.assertTrue(assets(scoped)["夜景.png"].exists())
        self.assertEqual((scoped["assets"]/"夜景.txt").read_text("utf8"),"花园夜景")
        self.assertEqual(Store(self.cfg).claim(),saved["job_id"])
        plan={"shots":[{"start":"","end":""},{"start":"previous","end":""}]}
        studio_import.apply_links(sealed,plan)
        self.assertEqual(plan["shots"][1]["start"],"夜景.png")
        self.assertFalse(sealed["music"])
        self.assertEqual(sealed["mode"],"auto")

    def test_mapped_nested_input_external_root_plan_captions(self):
        images=self.root/"materials"
        images.mkdir()
        (self.old/"夜景.png").rename(images/"真实文件.png")
        write(self.source,{"items":[{"ID":"studio-3","x/y":{"~body":"保留全部台词。"},
                           "media":[{"uid":"X1","local":"真实文件.png","key":"花园.png"}]}]})
        meta=self.root/"map"
        meta.mkdir()
        write(meta/"captions.json",{"X1":"虚构色板"})
        write(meta/"plan.json",{"id":"fixture-plan","shots":[{"status":"rejected"}]})
        mapping=meta/"bind.json"
        write(mapping,{"project_pointer":"/items/0","fields":{"id":"/ID","script":"/x~1y/~0body","assets":"/media"},
                       "asset_fields":{"id":"/uid","path":"/local","name":"/key"},
                       "title":"剪辑样例","captions_file":"captions.json","plan_file":"plan.json","seconds":6})
        with self.assertRaisesRegex(ValueError,"源文件"):
            self.invoke("--mapping",mapping)
        r=self.invoke("--mapping",mapping,"--root",images)
        b=Path(r["directory"])
        self.assertEqual(read(b/"brief.json")["script"],"保留全部台词。")
        self.assertEqual(read(b/"source-plan.json"),read(meta/"plan.json"))
        self.assertFalse((b/"plan.json").exists())
        self.assertEqual((b/"assets"/"花园.txt").read_text("utf8"),"虚构色板")

    def test_reject_collision_and_missing_clip(self):
        self.project["assets"] += [{"path":"夜景.png"}]
        write(self.source,self.project)
        with self.assertRaisesRegex(ValueError,"冲突"):
            self.invoke("--enqueue")
        self.assertEqual(list((self.cfg["data"]/"studio-imports").iterdir()),[])
        self.project["assets"].pop()
        write(self.source,self.project)
        f=self.old/"map.json"
        write(f,{"links":[{"shot":2,"first":"C006"}]})
        with self.assertRaisesRegex(ValueError,"C006"):
            self.invoke("--mapping",f,"--enqueue")
        self.assertEqual(Store(self.cfg).jobs(),[])

    def test_script_file_tampering_and_link_bounds(self):
        self.project.pop("script")
        write(self.source,self.project)
        (self.old/"s.md").write_text("文字原样\n",encoding="utf-8-sig")
        f=self.old/"mapping.json"
        write(f,{"script_file":"s.md","links":[{"shot":3,"last":"夜景.png"}]})
        r=self.invoke("--mapping",f)
        b=Path(r["directory"])
        spec=read(b/"brief.json")
        self.assertEqual(spec["script"],"文字原样\n")
        with self.assertRaisesRegex(ValueError,"S03"):
            studio_import.apply_links(spec,{"shots":[]})
        with self.assertRaisesRegex(ValueError,"尾帧需要首帧"):
            studio_import.apply_links(spec,{"shots":[{} for _ in range(3)]})
        (b/"assets"/"夜景.png").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError,"损坏"):
            self.invoke("--mapping",f,"--enqueue")
        self.assertFalse((self.cfg["data"]/"factory.sqlite3").exists())

    def test_invalid_traversal_alias(self):
        self.project["assets"][0]["alias"]="../escape.png"
        write(self.source,self.project)
        f=self.old/"mapping.json"
        write(f,{"asset_fields":{"name":"/alias"}})
        with self.assertRaisesRegex(ValueError,"目标素材文件名"):
            self.invoke("--mapping",f,"--enqueue")


if __name__ == "__main__":
    unittest.main()
