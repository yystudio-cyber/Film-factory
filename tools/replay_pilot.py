"""Run the same synthetic nine-beat fixture in a clean Python process per revision."""
import importlib.util
import os
import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import argparse
parser=argparse.ArgumentParser(description="用合成色板、测试音和回环模型替身复跑指定工厂版本")
parser.add_argument("repository",type=Path)
parser.add_argument("new_output",type=Path)
args=parser.parse_args()
repo=args.repository.resolve(strict=True)
out=args.new_output.resolve()
benchmark=Path(__file__).resolve().parents[1]/"factory"/"benchmark.py"
if out.exists() or out.with_suffix(".json").exists():
    parser.error("输出目录与同名 JSON 均须为新路径")
if not out.parent.is_dir():
    parser.error("请指定现有父目录")
sys.path[:0]=[str(repo),str(repo/"tests")]
from factory import engine
from factory.store import Store
from test_pilot_queue import FullPilotQueue

spec=importlib.util.spec_from_file_location("factory.ab_benchmark",benchmark)
bench=importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)
publish=Store.publish
rng=random.Random(4279)
def measured(store,jid,*args):
    complete=publish(store,jid,*args)
    if complete:
        metrics=bench.export(store.root,jid,"fixture","pilot")
        bench.safe_output(out.with_suffix(".json"),metrics)
    return complete

os.environ["FILM_TEST_PILOT_EVIDENCE_DIR"]=str(out)
with patch.object(Store,"publish",measured), patch.object(engine.secrets,"randbelow",rng.randrange):
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(FullPilotQueue))
sys.exit(0 if result.wasSuccessful() else 1)
