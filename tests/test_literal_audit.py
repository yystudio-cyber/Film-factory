import unittest
from unittest.mock import Mock

from factory import story


class LiteralAudit(unittest.TestCase):
    def audit(self, locks, shots, editorial=True):
        ledger = [{"id":"剧001", "text":"原文", "checks":[
            {"shot":n,"channel":ch,"literal":line} for n,ch,line in locks]}]
        timeline = [dict(s,shot=i,code=f"C{i:03}") for i,s in enumerate(shots,1)]
        director = Mock()
        director.ask.return_value = {"rows":[{"id":"剧001","passed":editorial,"notes":"文字编辑审片结论"}]}
        return story.audit_edit(director,ledger,timeline,{"passed":True})[0]

    @staticmethod
    def edited(note, origin="user", **fields):
        return dict(fields, changes=[note], revision_history=[{"note":note,"origin":origin}])

    def test_title_caption_narration_mismatch_vetoes_editor_pass(self):
        for channel in ("title","caption","narration"):
            for adopted in ("","苏州夜班","杭州夜斑"):
                with self.subTest(channel=channel,adopted=adopted):
                    row = self.audit([(1,channel,"杭州夜班")],[{channel:adopted}])
                    self.assertFalse(row["passed"])
                    self.assertEqual(row["repair_shot"], 1)
                    self.assertEqual(row["literal_checks"][0]["adopted"], adopted)

    def test_dialogue_needs_authored_line_and_matching_actual_transcription(self):
        locked = [(1,"dialogue","开机，出发！")]
        self.assertTrue(self.audit(locked,[{"dialogue":"开机，出发！","transcript":"开机出发"}])["passed"])
        for transcript in ("","开机出差","开，出发"):
            self.assertFalse(self.audit(locked,[{"dialogue":"开机，出发！","transcript":transcript}])["passed"])
        self.assertFalse(self.audit(locked,[{"dialogue":"开机，出发。","transcript":"开机出发"}])["passed"])
        row = self.audit([(1,"dialogue","We can")],[{"dialogue":"We can","transcript":"we cannot"}])
        self.assertFalse(row["passed"])

    def test_explicit_human_revision_is_bound_to_the_adopted_shot_and_literal(self):
        note = "将标题从“杭州夜班”改为“杭州晨班”"
        locks = [(1,"title","杭州夜班"),(2,"title","苏州")]
        adopted = [self.edited(note,title="杭州晨班"),{"title":"苏洲"}]
        row = self.audit(locks,adopted)
        self.assertFalse(row["passed"])
        self.assertEqual(row["repair_shot"],2)
        first,second = row["literal_checks"]
        self.assertEqual(first["expected"],"杭州晨班")
        self.assertEqual(first["user_revisions"],[note])
        self.assertTrue(first["passed"])
        self.assertFalse(second["passed"])
        adopted[0]["title"] = "杭州辰班"
        self.assertEqual(self.audit(locks,adopted)["repair_shot"],1)

    def test_nontextual_or_unattributed_note_cannot_unlock_a_line(self):
        locked = [(1,"caption","锁定")]
        for note,origin in [
            ("字幕颜色改为“金色”","user"),("字幕改为“金色”","editorial"),
            ("字幕改为“金色”","legacy"),("不要把字幕改为“金色”","user"),
            ("将字幕框背景改为“金色”","user"),("删除字幕下的装饰","user"),
            ("将标题改为“金色”","user"),("把字幕“另一项”改为“金色”","user"),
        ]:
            with self.subTest(note=note,origin=origin):
                row = self.audit(locked,[self.edited(note,origin,caption="金色")])
                self.assertFalse(row["passed"])
                self.assertEqual(row["literal_checks"][0]["rule"],"source")
        self.assertFalse(self.audit(locked,[{"caption":"金色","changes":["字幕改为“金色”"]}])["passed"])

    def test_replacing_one_of_two_literals_does_not_unlock_the_other(self):
        locks = [(1,"title","AA"),(1,"title","BB")]
        cut = self.edited('Change the title from "AA" to "CC"',title="CC BD")
        row = self.audit(locks,[cut])
        self.assertEqual([r["passed"] for r in row["literal_checks"]],[True,False])

    def test_explicit_deletion_or_rewording_only_opens_named_channel(self):
        locks = [(1,"narration","旧台词")]
        self.assertTrue(self.audit(locks,[self.edited("删除旁白",narration="")])["passed"])
        self.assertFalse(self.audit(locks,[self.edited("删除旁白",narration="旧台词")])["passed"])
        revised = self.edited("精简旁白",narration="短句")
        self.assertTrue(self.audit(locks,[revised])["passed"])
        self.assertEqual(self.audit(locks,[revised])["literal_checks"][0]["rule"],"editor")
        self.assertFalse(self.audit(locks,[revised],editorial=False)["passed"])
        self.assertFalse(self.audit(locks,[self.edited("精简旁白",narration="")])["passed"])

    def test_verbatim_unquoted_form_and_repeated_revisions(self):
        cut = self.edited("字幕改为：乙",caption="丙")
        cut["revision_history"].append({"origin":"user","note":"把字幕“乙”改成“丙”"})
        row = self.audit([(1,"caption","甲")],[cut])
        self.assertTrue(row["passed"])
        self.assertEqual(row["literal_checks"][0]["expected"],"丙")
