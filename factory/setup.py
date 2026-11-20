import re
import shutil
from pathlib import Path

from .common import canvas, request
from .graphs import ROUTES, load_profile, unpack


def doctor(cfg):
    checks = {}
    for tool in ("ffmpeg", "ffprobe"):
        checks[tool] = shutil.which(tool) or False
    import os
    checks["FILM_LLM_KEY"] = bool(os.environ.get("FILM_LLM_KEY"))
    try:
        import PIL
        checks["Pillow"] = PIL.__version__
    except ImportError:
        checks["Pillow"] = False
    for name, url in (("ComfyUI", cfg["comfy"]+"/system_stats"), ("ACE-Step", cfg["ace"]+"/health")):
        try:
            request(url, timeout=4)
            checks[name] = True
        except Exception as err:
            checks[name] = str(err)
    for route in ROUTES:
        try:
            load_profile(cfg, route)
            checks[route] = True
        except Exception as err:
            checks[route] = str(err)
    checks["font"] = Path(cfg["font"]).is_file()
    return checks


def history(cfg):
    rows = request(cfg["comfy"]+"/history?max_items=25", timeout=15)
    return [{"id": k, "outputs": [item.get("filename", "") for channel in v.get("outputs", {}).values()
              for group in channel.values() if isinstance(group, list) for item in group if isinstance(item, dict)
              and item.get("filename", "").lower().endswith((".mp4",".mov",".webm",".mkv"))]}
            for k, v in reversed(list(rows.items()))]


def historical_graph(cfg, pid):
    if not re.fullmatch(r"[\w-]{1,64}", pid):
        raise ValueError("Invalid prompt ID")
    return unpack(request(cfg["comfy"]+"/history/"+pid)[pid]["prompt"][2])


def fields(graph, cfg=None):
    # Follow semantic consumers of primitive/scheduler value nodes. Only propose a
    # role when the inputs agree; the operator confirms the mapping before saving.
    refs = {}
    for node in graph.values():
        for field, src in node.get("inputs", {}).items():
            if isinstance(src, list) and len(src)==2:
                refs.setdefault(str(src[0]),set()).add(field.lower())
    w,h=canvas(cfg) if cfg else (1920,1088)
    rows = []
    outputs = []
    for node, n in unpack(graph).items():
        title = n.get("_meta", {}).get("title", n["class_type"])
        kind = n["class_type"].lower()
        if "savevideo" in kind or "videocombine" in kind or "saveanimated" in kind:
            outputs.append(node)
        for field, value in n["inputs"].items():
            if isinstance(value, (list, dict)):
                continue
            suggested = ""
            if field in {"seed","noise_seed"}:
                suggested = "seed"
            elif field in {"length","frames_number","num_frames"} and ("latent" in kind or "video" in kind):
                suggested = "frames"
            elif field in {"fps","frame_rate"}:
                suggested = "fps"
            elif field == "text" and ("negative" in title.lower() or "负向" in title):
                suggested = "negative"
            elif field == "text" and ("positive" in title.lower() or "正向" in title):
                suggested = "prompt"
            elif field == "filename_prefix" and node in outputs:
                suggested = "prefix"
            consumers = refs.get(node, set())
            if field in {"value","text"}:
                if consumers and consumers <= {"length","frames_number","frame_count","num_frames"}:
                    suggested = "frames"
                elif consumers and consumers <= {"frame_rate","fps"}:
                    suggested = "fps"
                elif "negative" in consumers and "positive" not in consumers:
                    suggested = "negative"
                elif "positive" in consumers and "negative" not in consumers:
                    suggested = "prompt"
            for dim, full in (("width",w),("height",h)):
                if field == dim or (field == "value" and consumers == {dim}):
                    if value == full:
                        suggested=dim
                    elif value == full//2:
                        suggested="half_"+dim
            rows.append({"target":node+"."+field, "label":title, "value":str(value)[:110], "suggest":suggested})
    return {"rows":rows, "outputs":outputs}
