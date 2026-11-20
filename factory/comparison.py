"""Compare the text-director assignments on the actual workstation's script."""
import time

from . import archive, story
from .common import write
from .director import Director
from .store import Store


def compare(cfg, spec, output):
    refs = archive.references(Store(cfg),spec["script"],spec["requirements"])
    candidates = []
    # Same input, catalogue, timing constraints and validators. Reviewers do not see model names.
    for provider in ("qwen","deepseek"):
        actor = Director(dict(cfg,text_provider=provider))
        t = time.monotonic()
        row = {"provider":provider}
        try:
            _,row["model"],_,_ = actor.endpoint(False)
            plan = actor.plan(spec,refs)
            row.update(plan=plan,seconds=sum(s["seconds"] for s in plan["shots"]))
        except Exception as err:
            row["error"] = str(err)
        row["elapsed_seconds"] = round(time.monotonic()-t,2)
        candidates.append(row)
    for row in candidates:
        if "plan" not in row:
            continue
        row["reviews"] = {}
        for provider in ("qwen","deepseek"):
            try:
                ledger = story.coverage(Director(dict(cfg,text_provider=provider)),spec,row["plan"])
                row["reviews"][provider] = {"covered":sum(r["covered"] for r in ledger),
                                            "total":len(ledger),"rows":ledger}
            except Exception as err:
                row["reviews"][provider] = {"error":str(err)}
    write(output,{"brief":spec,"candidates":candidates})
