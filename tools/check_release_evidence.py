"""Check the recorded outcomes from the shipped fictional experiments, without services."""
import argparse
import json
from pathlib import Path


def check(root):
    def load(path):return json.loads((root/path).read_text(encoding="utf8"))
    def require(value,message):
        if not value:raise ValueError(message)
    phases={"before":("clean","unknown","lost","changed_cut","alias"),
            "after":("clean","unknown","lost","changed_cut","alias"),
            "hold-before":("incomplete","ambiguous","changed_tail","lost_json","published_damage"),
            "hold-after":("incomplete","ambiguous","changed_tail","lost_json","published_damage")}
    result={key:{case:load(f"workflow/{key}/{case}.json") for case in cases} for key,cases in phases.items()}
    expected=result["before"]["clean"]["movie"]["sha256"]
    for phase in ("after","hold-after"):
        for case,row in result[phase].items():
            runs=row["runs"]
            require(runs[-1]["delivered"] and runs[-1]["submissions"]==3,case+" failed to recover uniquely")
            require(row["movie"]["sha256"]==expected,case+" recovered different movie")
            if case in ("changed_cut","changed_tail","unknown","incomplete","ambiguous","lost_json"):
                require(not runs[0]["delivered"] and runs[0]["submissions"]==1,case+" failed to withhold")
            if case=="lost":require(len(runs)==1,"unique lost POST response needed an intervention")
            if case=="ambiguous":require([r["submissions"] for r in runs]==[1,1,3],"ambiguous retry not held")
    require(result["before"]["unknown"]["runs"][0]["delivered"],"missing baseline API symptom")
    require(result["before"]["changed_cut"]["movie"]["sha256"]!=expected,"missing baseline wrong movie")
    require(not result["before"]["alias"]["canonical_unchanged"] and result["after"]["alias"]["canonical_unchanged"],"aliases")
    require(result["hold-before"]["lost_json"]["runs"][-1]["submissions"]==4,"missing baseline duplicate")
    require(result["hold-before"]["published_damage"]["corrupt_download_offered"] and
            not result["hold-after"]["published_damage"]["corrupt_download_offered"] and
            result["hold-after"]["published_damage"]["restored_download_matches"],"published corruption")
    counts={}
    for label in ("before","after","hold-before","hold-after"):
        rows=load("motion/"+label+".json")
        misses=sum(r["harmful"] and not r["fixture_rejected"] for r in rows)
        false_rejects=sum(not r["harmful"] and r["fixture_rejected"] for r in rows)
        require(len(rows)==5 and misses==(2 if label.endswith("before") else 0) and false_rejects==0,"motion "+label)
        counts[label]={"harmful_missed":misses,"legal_rejected":false_rejects}
    return {"verified":True,"workflow_runs":sum(map(len,result.values())),"motion_fixture":counts}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence",type=Path)
    print(json.dumps(check(parser.parse_args().evidence),indent=2))
