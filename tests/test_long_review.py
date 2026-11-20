import unittest
from unittest.mock import Mock

from factory.director import Director


class MasterReview(unittest.TestCase):
    def setUp(self):
        self.director = Director({})
        self.spec = {"script":"第一场原文。\n角色说：“不要改写。”", "requirements":"保持原台词"}
        self.plan = [{"shot":i, "observations":f"画面 {i}", "changes":[]} for i in range(1, 99)]
        self.plan[48]["changes"] = ["S49 必须夕照"]
        self.boards = ["sheet" + str(i) for i in range(1,8)]

    @staticmethod
    def verdict(*args, **kwargs):
        return {"passed":True, "notes":"原文事件已核对", "boundary_notes":"人物离开上一场，下一场换景"}

    def test_every_shot_once_and_prior_boundary_sheet_with_original_script(self):
        self.director.ask = Mock(side_effect=self.verdict)
        result = self.director.final_review(self.spec, self.plan, self.boards)
        self.assertTrue(result["passed"])
        first, second, last, summary = self.director.ask.call_args_list
        self.assertEqual([c.args[1]["shot_range"] for c in (first,second,last)], [[1,48],[49,96],[97,98]])
        self.assertEqual([c.args[2] for c in (first,second,last)],
                         [self.boards[:3],self.boards[2:6],self.boards[5:7]])
        assigned = [s["shot"] for c in (first,second,last) for s in c.args[1]["accepted_timeline"]]
        self.assertEqual(assigned, list(range(1,99)))
        self.assertEqual(second.args[1]["previous_shot"], self.plan[47])
        self.assertEqual(second.args[1]["image_order"][0],
                         {"first_shot":33, "last_shot":48, "role":"previous context"})
        for call in (first,second,last,summary):
            self.assertEqual(call.args[1]["brief"], self.spec)
        self.assertEqual(len(summary.args), 2)  # text route for the final fold
        self.assertEqual(summary.args[1]["revisions"], [{"shot":49,"changes":["S49 必须夕照"]}])
        self.assertEqual([s["last_shot"] for s in result["segments"]], [48,96,98])

    def test_failure_is_retained_even_if_later_segments_pass(self):
        failed = {"passed":False, "notes":"S49 场景错", "boundary_notes":"接不住上一段", "repair_shot":49}
        self.director.ask = Mock(side_effect=[self.verdict(), failed, self.verdict()])
        result = self.director.final_review(self.spec, self.plan, self.boards)
        self.assertFalse(result["passed"])
        self.assertEqual(result["repair_shot"],49)
        self.assertIn("S49 场景错",result["notes"])
        self.assertEqual(self.director.ask.call_count,3)
        self.assertTrue(result["segments"][2]["passed"])

    def test_global_narrative_verdict_can_reject_despite_all_local_passes(self):
        self.director.ask = Mock(side_effect=[self.verdict()]*3 + [
            {"passed":False, "notes":"结尾缺少呼应", "repair_shot":98}])
        result = self.director.final_review(self.spec, self.plan, self.boards)
        self.assertFalse(result["passed"])
        self.assertTrue(all(s["passed"] for s in result["segments"]))
        self.assertEqual(result["repair_shot"],98)

    def test_short_movie_needs_one_visual_review(self):
        self.director.ask = Mock(return_value={"passed":True, "notes":"已核对"})
        result = self.director.final_review(self.spec, self.plan[:16], self.boards[:1])
        self.assertTrue(result["passed"])
        self.assertEqual(self.director.ask.call_count,1)

    def test_rejects_out_of_range_fix_and_missing_boundary_evidence(self):
        for result in (
            {"passed":False, "notes":"错位", "boundary_notes":"接点", "repair_shot":48},
            {"passed":True, "notes":"审片通过", "boundary_notes":["不合法"]},
            {"passed":False, "notes":" ", "boundary_notes":"接点"},
        ):
            with self.subTest(result=result):
                self.director.ask = Mock(side_effect=[self.verdict(),result])
                with self.assertRaises(ValueError):
                    self.director.final_review(self.spec, self.plan, self.boards)

    def test_cannot_pass_without_contact_sheet_for_every_shot(self):
        self.director.ask = Mock()
        for boards in ([], self.boards[:6], self.boards + ["extra"]):
            with self.subTest(boards=boards), self.assertRaises(ValueError):
                self.director.final_review(self.spec, self.plan, boards)
        self.director.ask.assert_not_called()
