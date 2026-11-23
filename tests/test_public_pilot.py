import tempfile
import unittest
from pathlib import Path

from factory.common import write
from factory.pilot import qualify


class PublicPilotProfile(unittest.TestCase):
    def test_qualifier_requires_exact_configured_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job, bundle = "b" * 32, "a" * 64
            write(root / job / "brief.json", {"studio_bundle": bundle, "seconds": 60})
            write(root / "studio-imports" / bundle / "manifest.json", {"project_id": "fixture-garden"})
            config = {"data": root, "station_contract": True}
            with self.assertRaisesRegex(ValueError, "pilot_project_id"):
                qualify(config, job)
            with self.assertRaisesRegex(ValueError, "项目来源"):
                qualify(dict(config, pilot_project_id="another-project"), job)
            with self.assertRaisesRegex(ValueError, "尚无已发布"):
                qualify(dict(config, pilot_project_id="fixture-garden"), job)
