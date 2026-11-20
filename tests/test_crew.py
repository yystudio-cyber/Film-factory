import tempfile
import threading
import unittest
from pathlib import Path

from factory import crew
from factory.common import read, write
from factory.references import cast_anchors


class Departments(unittest.TestCase):
    def test_parallel_contract_cached_per_role_and_revision(self):
        gate = threading.Barrier(2)
        calls = []
        class Model:
            def endpoint(self,_):
                return ("test","director","test",True)
            def ask(self,instruction,payload,**kwargs):
                role = "camera" if "cinematographer" in instruction else "sound"
                calls.append((role,payload))
                gate.wait(timeout=5)
                return {"rows":[{"id":s["id"],**{k:"执行："+s["beat"] for k in crew.ROLES[role][1]}}
                                for s in payload["shots"]]}
        spec = dict(music=True,requirements="片名",production_brief={"audience":"新员工"})
        plan = dict(bible="工作站",coverage=[],shots=[dict(beat="入场"),dict(beat="到站")])
        director = Model()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = crew.prepare(director,spec,plan,root)
            self.assertEqual(first,crew.prepare(director,spec,plan,root))
            self.assertEqual(len(calls),2)
            self.assertEqual(calls[0][1]["production_brief"],spec["production_brief"])
            revised = crew.prepare(director,spec,plan,root,revision=(2,dict(beat="推门",changes=["推门"])))
            self.assertEqual(list(revised),["2"])
            self.assertEqual(revised["2"]["sound"]["bridge"],"执行：推门")
            self.assertEqual(len(calls),4)
            self.assertEqual(calls[-1][1]["preceding"],"入场")
            self.assertEqual(len(list((root/"departments").glob("*.json"))),4)

    def test_bad_department_cannot_unlock_render(self):
        class Model:
            def endpoint(self,_):
                return ("test","director","test",True)
            def ask(self,*args,**kwargs):
                return {"rows":[{"id":77}]}
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError,"镜号"):
            crew.prepare(Model(),dict(music=False,requirements=""),dict(bible="办公室",coverage=[],
                         shots=[dict(beat="进站")]),Path(tmp))

    def test_returning_character_uses_first_selected_recognized_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            selected={}
            for i in (1,2,3):
                folder=root/str(i)
                write(folder/"review.json",{"subjects":[{"name":"小满","frame":2}]} if i!=2 else {})
                (folder/"frame-2.jpg").touch()
                selected[i]={"code":i,"path":str(folder)}
            self.assertEqual(cast_anchors(selected,4,["小满"]),[("小满","C001",root/"1/frame-2.jpg")])
            self.assertEqual(cast_anchors({2:selected[2],3:selected[3]},4,["小满"])[0][1],"C003")
            self.assertEqual(cast_anchors(selected,1,["小满"]),[])


class ShotContract(unittest.TestCase):
    def test_moving_shot_cannot_be_stamped_static(self):
        from factory.director import Director
        from unittest.mock import Mock
        director=Director({})
        phrase="Static camera, framing stays exactly the same, no cuts."
        words="A technician steps across the room beside the wooden table. "*9+"Sound: a chair rolls."
        director.ask=Mock(side_effect=[{"prompt":phrase+" "+words},{"prompt":words}])
        shot=dict(start="reference",camera_motion="moving",dialogue="")
        self.assertEqual(director.prompt(dict(requirements=""),"bible",shot,[]),words)
        self.assertEqual(director.ask.call_count,2)
        director.ask=Mock(side_effect=[{"prompt":words},{"prompt":phrase+" "+words}])
        shot["camera_motion"]="locked"
        self.assertTrue(director.prompt(dict(requirements=""),"bible",shot,[]).startswith(phrase))

    def test_face_anchor_requires_a_visibly_identified_sample(self):
        from factory.director import Director,DIMS
        from unittest.mock import Mock
        director=Director({"threshold":4})
        base={"scores":dict.fromkeys(DIMS,5),"checks":[],"notes":"工位", "repair":"使用中景"}
        shot={"subjects":["小满"],"obligations":[]}
        def judge(frame):
            director.ask=Mock(return_value={**base,"subjects":[{"name":"小满","frame":frame,"evidence":"可见面部特征"}]})
            return director.review(dict(requirements=""),dict(bible=""),shot,[],[])
        self.assertFalse(judge(None)["passed"])
        self.assertTrue(judge(1)["passed"])
        with self.assertRaisesRegex(ValueError,"索引"):
            judge(-1)
