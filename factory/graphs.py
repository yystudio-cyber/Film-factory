"""Bind named inputs to a copy of an already calibrated local ComfyUI API graph."""
import copy
import hashlib
import math
import json
import time
import uuid
import urllib.error

from . import dispatch
from pathlib import Path
from urllib.parse import urlencode

from .common import canvas, frame_count, read, request, write

ROUTES = {"T2V": set(), "I2V": {"first"}, "FLF": {"first", "last"}}
CORE = {"prompt", "seed"}
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
    timing = fields.keys() & {"frames", "seconds"}
    if len(timing) != 1:
        raise ValueError("工作流须绑定 frames 或上游 seconds，二选一")
    required = CORE | ROUTES[route] | timing
    if not required <= fields.keys() or fields.keys() - required - OPTIONAL:
        raise ValueError(f"{route} bindings require {sorted(required)}; verify field names")
    seen = set()
    for key, targets in fields.items():
        if not isinstance(targets, list) or not targets:
            raise ValueError(f"Missing bindings for {key}")
        for target in targets:
            node, field = target.split(".", 1)
            value = graph[node]["inputs"][field]
            if target in seen or isinstance(value, (list, dict)):
                raise ValueError(f"{target} is duplicated or linked; bind the upstream literal")
            seen.add(target)
    if profile["output"] not in graph:
        raise ValueError("Output node not in graph")
    return profile


def check_native_profile(profile,route,size):
    """Verify the native AV output and reference geometry from the exported graph."""
    graph = profile["graph"]
    if not {"fps","width","height"} <= profile["bindings"].keys() or "negative" in profile["bindings"]:
        raise ValueError("工作站图必须绑定 fps、width、height 并保留原有负向提示词")
    bindings = {target:role for role,targets in profile["bindings"].items() for target in targets}
    def role(node,field):
        key = node+"."+field
        value = graph[node]["inputs"].get(field)
        if isinstance(value,list) and len(value)==2 and str(value[0]) in graph:
            parent = str(value[0])
            if graph[parent]["class_type"] in {"PrimitiveInt","PrimitiveFloat","PrimitiveNode"}:
                return bindings.get(parent+".value",graph[parent]["inputs"].get("value"))
        return bindings.get(key,value)
    visited = set()
    pending = [profile["output"]]
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        pending.extend(str(v[0]) for v in graph[key]["inputs"].values()
                       if isinstance(v,list) and len(v)==2 and str(v[0]) in graph)
    components = {k:graph[k] for k in visited}
    clocks = []
    for node,part in components.items():
        for field in ("fps","frame_rate"):
            if field in part["inputs"]:
                clocks.append(role(node,field))
    if not clocks or any(clock not in ("fps",24) for clock in clocks):
        raise ValueError("最终输出链上的全部 fps/frame_rate 必须原生为 24 或来自已绑定的 fps")
    if not any(part["class_type"] in {"CreateVideo","VHS_VideoCombine"} and
               isinstance(part["inputs"].get("audio"),list) for part in components.values()):
        raise ValueError("最终视频输出链缺少音频连接")
    if "seconds" in profile["bindings"]:
        formulas = [k for k,n in components.items() if n["class_type"]=="ComfyMathExpression"
                    and "".join(str(n["inputs"].get("expression","")).split()) in {"a*b+1","b*a+1"}]
        if not any(sorted(str(role(k,f)) for f in graph[k]["inputs"] if f.rsplit(".",1)[-1] in {"a","b"})
                   == ["fps","seconds"] for k in formulas):
            raise ValueError("现场导出文件中未确认秒数与 fps 驱动的 a*b+1 表达式")
    if route=="FLF":
        for k,node in components.items():
            if node["class_type"]=="ResizeImageMaskNode":
                for dim,value in zip(("width","height"),size):
                    fields = [f for f in node["inputs"] if f.rsplit(".",1)[-1]==dim]
                    if len(fields)!=1 or role(k,fields[0]) not in (dim,value):
                        raise ValueError(f"FLF {k} 缺少可核验的 {dim}={value}，请在工作流副本补齐后重新导出")


def inspect_graph(file):
    graph = unpack(read(file))
    for node, item in graph.items():
        values = {k: str(v)[:85] for k, v in item["inputs"].items() if not isinstance(v, list)}
        links = {k:v for k,v in item['inputs'].items() if isinstance(v,list)}
        print(node, item["class_type"], item.get("_meta", {}).get("title", ""), json.dumps({'literal': values, 'links': links}, ensure_ascii=False))


def load_profile(cfg, route):
    file = cfg["workflows"] / (route + ".json")
    if not file.is_file():
        raise ValueError(f"缺少 {file}。请先运行 bind 导入 {route} 工作流")
    profile = validate(read(file), route)
    if cfg.get("station_contract"):
        check_native_profile(profile,route,canvas(cfg))
    return profile


def bind(cfg, route, graphfile, mapfile):
    profile = read(mapfile)
    profile["graph"] = unpack(read(graphfile))
    validate(profile,route)
    if cfg.get("station_contract"):
        check_native_profile(profile,route,canvas(cfg))
    write(cfg["workflows"]/(route+".json"),profile)


def upload(base, path):
    name = uuid.uuid4().hex + path.suffix.lower()
    boundary = uuid.uuid4().hex
    payload = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"'
               '\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
    result = request(base + "/upload/image", payload + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode(),
                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return "/".join(filter(None, [result.get("subfolder"), result["name"]]))


def reconcile(base, client_id):
    # A prompt can move from queue to history during sampling. Union observations by ID.
    queue=request(base+"/queue")
    history=request(base+"/history?max_items=128")
    if not isinstance(queue,dict) or not {"queue_running","queue_pending"}<=queue.keys() or not isinstance(history,dict):
        raise RuntimeError("提交回执不确定：历史或队列返回格式异常，未重复投递")
    matches=set()
    def observe(row,pid):
        if isinstance(row,(list,tuple)) and len(row)>3 and isinstance(row[3],dict) and row[3].get("client_id")==client_id:
            if not isinstance(pid,str) or not pid.strip():
                raise RuntimeError("提交回执不确定：匹配项没有有效任务号")
            matches.add(pid)
    for key in ("queue_running","queue_pending"):
        rows=queue.get(key,[])
        if not isinstance(rows,list):
            raise RuntimeError("提交回执不确定：队列结构异常")
        for row in rows:
            observe(row,row[1] if isinstance(row,list) and len(row)>1 else None)
    for pid,item in history.items():
        if isinstance(item,dict):
            observe(item.get("prompt"),pid)
    if len(matches)!=1:
        raise RuntimeError("提交回执不确定，请核对 ComfyUI 历史及队列；未重复投递")
    return matches.pop()


def source_hash(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file,"sha256").hexdigest()


def execution_clock(history, submitted):
    times={}
    for item in history.get("status",{}).get("messages",[]):
        if not isinstance(item,(list,tuple)) or len(item)!=2 or not isinstance(item[1],dict):
            continue
        stamp=item[1].get("timestamp")
        if type(stamp) in (int,float) and math.isfinite(stamp) and stamp>0:
            times[item[0]]=stamp/1000
    a,b=times.get("execution_start"),times.get("execution_success")
    result={}
    if a is not None and b is not None and b>=a:
        result["remote_execution_seconds"]=round(b-a,3)
    if a is not None and submitted is not None and a>=submitted:
        result["submit_to_start_seconds"]=round(a-submitted,3)
    return result


def render(cfg, shot, first, last, seed, directory, checkpoint):
    base = cfg["comfy"].rstrip("/")
    prepared = directory/"source-ready.json"
    if prepared.exists():
        ready=read(prepared)
        path=directory/ready["file"]
        if path.parent!=directory or path.is_symlink():
            raise ValueError("已有素材缓存路径异常")
        if path.is_file() and ready.get("terminal_verified") is True and ready.get("sha256") and source_hash(path)==ready["sha256"]:
            return path
        if dispatch.saved(directory) is None:
            raise ValueError("素材缓存无法验证且没有远端回执；请核对，尚未重新投递")
        # Verification failed or the legacy receipt predates hashes. Re-fetch only via the existing ticket.
    receipt = dispatch.saved(directory)
    if receipt is not None:
        if receipt.get("base",base) != base:
            raise ValueError("此镜已提交至其他 ComfyUI 地址，请核对原服务")
        pid = receipt.get("prompt_id") or reconcile(base,receipt["client_id"])
        receipt["prompt_id"] = pid
        dispatch.save(directory,receipt)
        return collect(cfg,base,pid,receipt["output"],directory,checkpoint)

    if (directory/"graph.json").exists():
        if not (directory/"not-submitted.json").exists():
            raise ValueError("发现已有渲染图，但投递凭据缺失；须核对远端，未重复提交")
    route = "FLF" if last else "I2V" if first else "T2V"
    if shot["frames"] % 24 or not 48 <= shot["frames"] <= 192:
        raise ValueError("单镜只支持 2–8 整秒")
    profile = load_profile(cfg, route)
    base = cfg["comfy"].rstrip("/")
    w, h = canvas(cfg)
    graph = copy.deepcopy(profile["graph"])
    values = dict(prompt=shot["prompt"], negative=NEGATIVE, frames=frame_count(shot["frames"]),
                  seconds=shot['frames']//24, seed=seed, width=w, height=h, half_width=w//2, half_height=h//2,
                  fps=24, prefix="film_factory/" + directory.parent.parent.name + "/" + directory.name)
    for key, path in (("first", first), ("last", last)):
        if path:
            values[key] = upload(base, path)
    for key, targets in profile["bindings"].items():
        for target in targets:
            node, field = target.split(".", 1)
            graph[node]["inputs"][field] = values[key]
    write(directory/"not-submitted.json",{"status":"preparing"})
    write(directory / "graph.json", graph)
    checkpoint()
    if cfg.get("station_contract"):
        from .ownership import queue_state
        if queue_state(cfg):
            raise ValueError("8188 正在执行或等待其他任务，未提交新渲染；队列清空后显式继续")
    client_id = uuid.uuid4().hex
    receipt = {"client_id":client_id,"base":base,"output":profile["output"],"submitted_epoch":time.time()}
    checkpoint()
    dispatch.save(directory,receipt)
    (directory/"not-submitted.json").unlink(missing_ok=True)
    try:
        ticket = request(base+"/prompt",{"prompt":graph,"client_id":client_id})
    except RuntimeError as err:
        if str(err).startswith("HTTP 400:"):
            write(directory/"remote-error.json",{"category":"invalid_workflow"})
        raise
    except (ConnectionError,TimeoutError,urllib.error.URLError):
        ticket = None
    checkpoint()
    pid=ticket.get("prompt_id") if isinstance(ticket,dict) else None
    if not isinstance(pid,str) or not pid.strip():
        pid=reconcile(base,client_id)
    receipt["prompt_id"]=pid
    dispatch.save(directory,receipt)
    return collect(cfg,base,pid,profile["output"],directory,checkpoint)


def collect(cfg,base,pid,output_node,directory,checkpoint):
    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        checkpoint()
        item = request(base + "/history/" + pid).get(pid)
        if item:
            status=item.get("status") if isinstance(item,dict) else None
            if not isinstance(status,dict):
                raise ValueError("远端历史缺少终态证据；保留原回执")
            messages=status.get("messages",[])
            if not isinstance(messages,list):
                raise ValueError("远端事件列表无法确认；保留原回执")
            events={r[0] for r in messages if isinstance(r,(list,tuple)) and len(r)==2 and isinstance(r[0],str)}
            if status.get("status_str")=="error" or events & {"execution_error","execution_interrupted"}:
                error_text=" ".join(str(r[1].get(k,"")) for r in messages
                    if isinstance(r,(list,tuple)) and len(r)==2 and isinstance(r[1],dict)
                    for k in ("exception_type","exception_message")).lower()
                category=("out_of_memory" if "out of memory" in error_text or "outofmemory" in error_text
                          else "interrupted" if "execution_interrupted" in events else "node_error")
                write(directory/"remote-error.json",{"category":category})
                raise RuntimeError("ComfyUI execution failed: "+category)
            if status.get("status_str")!="success" or status.get("completed") is not True:
                raise ValueError("远端状态未确认成功；保留原回执，等待核对")
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
            receipt=read(directory/"remote.json")
            write(directory/"source-ready.json",{"file":path.name,"sha256":source_hash(path),"terminal_verified":True,
                  "timing":execution_clock(item,receipt.get("submitted_epoch"))})
            return path
        time.sleep(cfg.get("poll_seconds", 2))
    raise TimeoutError(f"ComfyUI timeout; inspect prompt {pid} before restarting")
