"""Bind named inputs to a copy of an already calibrated local ComfyUI API graph."""
import copy
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

from .common import canvas, frame_count, read, request, write

ROUTES = {"T2V": set(), "I2V": {"first"}, "FLF": {"first", "last"}}
CORE = {"prompt", "frames", "seed"}
OPTIONAL = {"negative", "width", "height", "half_width", "half_height", "fps", "prefix"}
NEGATIVE = ("stiff motion, morphing, flickering, warped hands, duplicate person, "
            "jump cut, watermark, subtitles, text overlay, low quality")


def unpack(data):
    graph = data.get("prompt", data)
    if not isinstance(graph, dict) or not graph or not all(
        isinstance(n, dict) and "class_type" in n and isinstance(n.get("inputs"), dict)
        for n in graph.values()
    ):
        raise ValueError("需要 ComfyUI 导出的 API 格式 JSON（节点含 class_type 和 inputs）")
    return graph


def validate(profile, route):
    graph = unpack(profile["graph"])
    fields = profile["bindings"]
    required = CORE | ROUTES[route]
    if not required <= fields.keys() or fields.keys() - required - OPTIONAL:
        raise ValueError(f"{route} bindings require {sorted(required)}; verify field names")
    seen = set()
    for key, targets in fields.items():
        if not isinstance(targets, list) or not targets:
            raise ValueError(f"Missing bindings for {key}")
        for target in targets:
            node, field = target.rsplit(".", 1)
            value = graph[node]["inputs"][field]
            if target in seen or isinstance(value, (list, dict)):
                raise ValueError(f"{target} is duplicated or linked; bind the upstream literal")
            seen.add(target)
    if profile["output"] not in graph:
        raise ValueError("Output node not in graph")
    return profile


def inspect_graph(file):
    graph = unpack(read(file))
    for node, item in graph.items():
        values = {k: str(v)[:85] for k, v in item["inputs"].items() if not isinstance(v, list)}
        print(node, item["class_type"], item.get("_meta", {}).get("title", ""), json.dumps(values, ensure_ascii=False))


def load_profile(cfg, route):
    file = cfg["workflows"] / (route + ".json")
    if not file.is_file():
        raise ValueError(f"缺少 {file}。请先运行 bind 导入 {route} 工作流")
    return validate(read(file), route)


def bind(cfg, route, graphfile, mapfile):
    profile = read(mapfile)
    profile["graph"] = unpack(read(graphfile))
    write(cfg["workflows"] / (route + ".json"), validate(profile, route))


def upload(base, path):
    name = uuid.uuid4().hex + path.suffix.lower()
    boundary = uuid.uuid4().hex
    payload = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"'
               '\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
    result = request(base + "/upload/image", payload + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode(),
                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return "/".join(filter(None, [result.get("subfolder"), result["name"]]))


def reconcile(base, client_id):
    # Match a unique caller nonce, never the latest filename or somebody else's job.
    history = request(base+"/history?max_items=128")
    matches = [pid for pid,item in history.items() if len(item.get("prompt",[]))>3 and
               item["prompt"][3].get("client_id")==client_id]
    if not matches:
        queue = request(base+"/queue")
        matches = [row[1] for key in ("queue_running","queue_pending") for row in queue.get(key,[])
                   if len(row)>3 and row[3].get("client_id")==client_id]
    if len(matches)!=1:
        raise RuntimeError(f"提交回执不确定，请核对 ComfyUI 历史或队列（client_id={client_id}）。"
                           "该次继续不会重复投递；确认需要新任务后，可显式修改该镜。")
    return matches[0]


def render(cfg, shot, first, last, seed, directory, checkpoint):
    base = cfg["comfy"].rstrip("/")
    prepared = directory/"source-ready.json"
    if prepared.exists():
        path = directory/read(prepared)["file"]
        if path.is_file():
            return path
    receipt_file = directory/"remote.json"
    if receipt_file.exists():
        receipt = read(receipt_file)
        if receipt.get("base",base) != base:
            raise ValueError("此镜已提交至其他 ComfyUI 地址，请核对原服务")
        pid = receipt.get("prompt_id") or reconcile(base,receipt["client_id"])
        receipt["prompt_id"] = pid
        write(receipt_file,receipt)
        return collect(cfg,base,pid,receipt["output"],directory,checkpoint)

    route = "FLF" if last else "I2V" if first else "T2V"
    profile = load_profile(cfg, route)
    base = cfg["comfy"].rstrip("/")
    w, h = canvas(cfg)
    graph = copy.deepcopy(profile["graph"])
    values = dict(prompt=shot["prompt"], negative=NEGATIVE, frames=frame_count(shot["frames"]),
                  seed=seed, width=w, height=h, half_width=w//2, half_height=h//2,
                  fps=24, prefix="film_factory/" + directory.parent.parent.name + "/" + directory.name)
    for key, path in (("first", first), ("last", last)):
        if path:
            values[key] = upload(base, path)
    for key, targets in profile["bindings"].items():
        for target in targets:
            node, field = target.rsplit(".", 1)
            graph[node]["inputs"][field] = values[key]
    write(directory / "graph.json", graph)
    checkpoint()
    client_id = uuid.uuid4().hex
    receipt = {"client_id":client_id,"base":base,"output":profile["output"]}
    write(receipt_file,receipt)
    try:
        ticket = request(base+"/prompt",{"prompt":graph,"client_id":client_id})
    except RuntimeError as err:
        if str(err).startswith("HTTP 400:"):
            write(directory/"remote-error.json",{"error":str(err)})
        raise
    receipt["prompt_id"] = ticket["prompt_id"]
    write(receipt_file,receipt)
    return collect(cfg,base,ticket["prompt_id"],profile["output"],directory,checkpoint)


def collect(cfg,base,pid,output_node,directory,checkpoint):
    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        checkpoint()
        item = request(base + "/history/" + pid).get(pid)
        if item:
            if item.get("status", {}).get("status_str") == "error":
                write(directory/"remote-error.json",{"status":item["status"]})
                raise RuntimeError("ComfyUI execution failed: " + str(item["status"].get("messages", []))[-1200:])
            output = item.get("outputs", {}).get(output_node, {})
            videos = [v for group in output.values() if isinstance(group, list) for v in group
                      if isinstance(v, dict) and Path(v.get("filename", "")).suffix.lower() in
                      {".mp4", ".webm", ".mov", ".mkv"} and v.get("type", "output") == "output"]
            if len(videos) != 1:
                raise ValueError("绑定的最终输出节点应返回唯一视频，实际为 " + str(len(videos)))
            ref = videos[0]
            path = directory / ("source" + Path(ref["filename"]).suffix.lower())
            request(base + "/view?" + urlencode({k: ref[k] for k in ("filename", "subfolder", "type") if k in ref}),
                    timeout=300, dest=path)
            write(directory/"source-ready.json",{"file":path.name})
            return path
        time.sleep(cfg.get("poll_seconds", 2))
    raise TimeoutError(f"ComfyUI timeout; inspect prompt {pid} before restarting")
