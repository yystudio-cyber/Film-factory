"""Qualify the configured workstation and optionally follow one real factory job."""
import csv
import hashlib
import json
import math
import os
import re
import secrets
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import graphs, media
from .common import assets, canvas, disk_budget, frame_count, read, request, write
from .director import Director
from .lock import exclusive
from .store import Store


def add_arguments(commands):
    p = commands.add_parser("station-accept", help="检验服务、实际节点与绑定，可跟踪一条实机验收短片")
    p.add_argument("--report", type=Path, help="另存 JSON 验收报告")
    p.add_argument("--render", action="store_true", help="提交或跟踪 6 秒正片验收任务")
    p.add_argument("--first", help="资产目录内的 I2V 首帧文件名")
    p.add_argument("--last", help="资产目录内的 FLF 尾帧文件名")
    p.add_argument("--run-id", help="验收批次唯一编号，同一编号复用已提交任务")
    p.add_argument("--port", type=int, default=8766, help="当前工厂控制台端口")
    p.add_argument("--wait-minutes", type=float, default=90, help="等待已提交任务的分钟数，0 只检查一次")


def _fingerprint(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _file_hash(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _widget(spec):
    if not isinstance(spec, list) or not spec:
        return None, {}
    return spec[0], spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}


def _literal_error(spec, value, role, cfg):
    kind, options = _widget(spec)
    choices = kind if isinstance(kind, list) else options.get("options") if kind == "COMBO" else None
    if role in {"first", "last"}:
        # Actual image is uploaded under a unique name immediately before submission.
        if choices is not None or kind in ("STRING", "COMBO", "*"):
            return None
        return "图像文件名须绑定到上传文件选择器或字符串字段"
    if role in {"seconds", "frames", "seed", "fps", "width", "height", "half_width", "half_height"}:
        if kind not in ("INT", "FLOAT", "*"):
            return f"数值绑定指向 {kind}"
        w, h = canvas(cfg)
        values = {"seconds":list(range(2,9)), "frames":[frame_count(24*s) for s in range(2,9)], "seed":[0, 2**31-1],
                  "fps":[24], "width":[w], "height":[h], "half_width":[w//2], "half_height":[h//2]}[role]
    elif role in {"prompt", "negative", "prefix"}:
        return None if kind in ("STRING", "*") else f"文本绑定指向 {kind}"
    else:
        if choices is not None:
            return None if value in choices else f"当前安装清单缺少选项 {str(value)[:160]}"
        if kind in ("INT", "FLOAT") and (type(value) not in (int,float) or
                                           (kind == "INT" and type(value) != int)):
            return f"固定值 {value!r} 不是 {kind}"
        values = [value] if type(value) in (int,float) else []
    for v in values:
        if v < options.get("min",-math.inf) or v > options.get("max",math.inf):
            return f"数值 {v} 超出节点范围 [{options.get('min')}, {options.get('max')}]"
    return None


# Follow just the video/latent payload. Following all dependencies can mistake
# an audio sampler or a reference-image upscaler for a second video stage.
_VIDEO_INPUT = {
    "SaveVideo":"video", "VHS_VideoCombine":"images", "CreateVideo":"images",
    "VAEDecode":"samples", "VAEDecodeTiled":"samples", "LTXVSeparateAVLatent":"av_latent",
    "LTXVConcatAVLatent":"video_latent", "LTXVImgToVideoInplace":"latent",
    "LTXVAddGuide":"latent", "LTXVCropGuides":"latent", "LTXVLatentUpsampler":"samples",
    "KSampler":"latent_image", "KSamplerAdvanced":"latent_image",
    "SamplerCustom":"latent_image", "SamplerCustomAdvanced":"latent_image",
    "ImageScale":"image", "ImageScaleBy":"image", "ImageCrop":"image",
}
_SAMPLERS = {"KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced"}


def _parent(node, field, graph):
    value = node.get("inputs", {}).get(field)
    if isinstance(value, list) and len(value)==2 and isinstance(value[0], str) and value[0] in graph:
        return value[0]
    return None


def _steps(node, graph):
    inputs = node["inputs"]
    # Advanced KSampler can restrict the active step interval.
    if node["class_type"] == "KSamplerAdvanced":
        values = [inputs.get(k) for k in ("steps", "start_at_step", "end_at_step")]
        if all(type(v) is int for v in values):
            steps, start, end = values
            return max(0, min(steps,end)-start)
        return None
    if type(inputs.get("steps")) is int:
        return inputs["steps"]
    schedule = graph.get(_parent(node,"sigmas",graph),{})
    if schedule.get("class_type")=="ManualSigmas":
        raw = schedule["inputs"].get("sigmas")
        if isinstance(raw,str):
            try:
                values = [float(n.strip()) for n in raw.split(",")]
                return len(values)-1 if all(math.isfinite(n) for n in values) else None
            except ValueError:
                return None
    return None


def _stage_settings(sampler, graph):
    model_source = sampler
    if sampler["class_type"]=="SamplerCustomAdvanced":
        guider = graph.get(_parent(sampler,"guider",graph),{})
        model_source = guider if guider.get("class_type") in {"CFGGuider","BasicGuider"} else {}
    cfg = model_source.get("inputs",{}).get("cfg")
    settings = {"cfg":cfg if type(cfg) in (int,float) else None, "distilled_loras":[]}
    key = _parent(model_source,"model",graph)
    seen = set()
    standard_loaders = {"LoraLoader","LoraLoaderModelOnly"}
    model_passes = {"ModelSamplingSD3","ModelSamplingDiscrete","ModelSamplingFlux","ModelSamplingContinuousEDM"}
    while key and key not in seen:
        seen.add(key)
        node = graph[key]
        if node["class_type"] in standard_loaders:
            name = node["inputs"].get("lora_name")
            if isinstance(name,str) and "ltx" in name.lower() and "distill" in name.lower():
                strength = node["inputs"].get("strength_model")
                settings["distilled_loras"].append({"node":key,"model":name,
                                                    "strength":strength if type(strength) in (int,float) else None})
        elif node["class_type"] not in model_passes:
            break
        key = _parent(node,"model",graph)
    return settings


def calibration(graph, output, errors, warnings):
    trace, seen = [], set()
    key = output
    while key and key not in seen:
        seen.add(key)
        node = graph[key]
        trace.append((key,node["class_type"]))
        field = _VIDEO_INPUT.get(node["class_type"])
        key = _parent(node,field,graph) if field else None
    # The ordering in the evidence follows the direction of video generation.
    path = list(reversed(trace))
    stages = [(key,kind) for key,kind in path if kind in _SAMPLERS or kind=="LTXVLatentUpsampler"]
    sequence = ["upscale" if kind=="LTXVLatentUpsampler" else "sample" for key,kind in stages]
    starts_at_source = path[0][1] in {"EmptyLTXVLatentVideo","LTXVImgToVideo"}
    result = {"status":"unverified","video_path":[{"id":key,"type":kind} for key,kind in path]}
    if sequence==["sample","upscale","sample"]:
        result["status"]="two_stages_observed"
        first, up, second = [key for key,kind in stages]
        steps = [_steps(graph[key],graph) for key in (first,second)]
        result["steps"]=steps
        settings = [_stage_settings(graph[key],graph) for key in (first,second)]
        result["stage_settings"] = settings
        for stage, setup in enumerate(settings,1):
            if setup["cfg"] is None:
                warnings.append(f"第 {stage} 阶段未能从标准采样器/引导器读取有效 CFG")
            elif setup["cfg"]!=1:
                errors.append(f"第 {stage} 阶段 CFG={setup['cfg']}，预期 1")
            if not setup["distilled_loras"] or any(lora["strength"] is None for lora in setup["distilled_loras"]):
                warnings.append(f"第 {stage} 阶段未能从标准模型路径确认蒸馏 LoRA 强度")
            for lora in setup["distilled_loras"]:
                if lora["strength"] is not None and lora["strength"]!=0.5:
                    errors.append(f"第 {stage} 阶段 {lora['node']} 蒸馏 LoRA 强度为 {lora['strength']}，预期 0.5")
        for actual, expected in zip(steps,(8,3)):
            if actual is None:
                warnings.append("两阶段已定位；自定义或动态调度器的有效步数需核对")
            elif actual!=expected:
                errors.append(f"采样器实际步数 {actual} 与工作站标定 {expected} 不符")
        loader = graph.get(_parent(graph[up],"upscale_model",graph),{})
        model = loader.get("inputs",{}).get("model_name")
        factor = re.search(r"(?:-|_)x(\d+(?:\.\d+)?)(?:[-_.]|$)",model,re.I) if isinstance(model,str) else None
        if factor:
            result["upscale_factor_in_model_name"] = float(factor[1])
            if float(factor[1])!=2:
                errors.append(f"空间放大模型标记为 ×{factor[1]}，工作站标定为 ×2")
        else:
            warnings.append("空间放大已定位；显式 ×2 模型名未识别")
    elif starts_at_source:
        result["status"]="different_pipeline_observed"
        errors.append(f"视频输出回溯至初始 LTX latent，观察到 {sequence}；正片需要采样、潜空间放大、再采样")
    else:
        warnings.append(f"视频路径在 {path[0][0]} {path[0][1]} 终止，无法确认双阶段；请核对本地图")
    return result


def registry_fields(declared, inputs):
    """Read flat legacy fields and selected Comfy V3 dynamic fields from live object_info."""
    required = dict(declared.get("required",{}))
    optional = dict(declared.get("optional",{}))
    for name,spec in list({**required,**optional}.items()):
        kind,meta = _widget(spec)
        if kind=="COMFY_DYNAMICCOMBO_V3":
            options=meta.get("options",[])
            selected=next((o for o in options if o.get("key")==inputs.get(name)),None)
            if selected is None:
                raise ValueError(f"{name} 的 resize/动态选项未匹配现场 schema")
            for target,bucket in ((required,"required"),(optional,"optional")):
                target.update({name+"."+field:definition for field,definition in
                               selected["inputs"].get(bucket,{}).items()})
        elif kind=="COMFY_AUTOGROW_V3":
            template=meta["template"]
            fields=template.get("names")
            if fields is None:
                fields=[template["prefix"]+str(i) for i in range(template["max"])]
            definition=template["input"]
            is_required=bool(definition.get("required"))
            widget=next(iter((definition.get("required") or definition["optional"]).values()))
            required.pop(name,None)
            optional.pop(name,None)
            for i,key in enumerate(fields):
                bucket=required if is_required and i<template["min"] else optional
                bucket[name+"."+key]=widget
    return required,{**required,**optional}


def inspect_route(cfg, route, registry):
    profile = graphs.load_profile(cfg,route)
    graph = profile["graph"]
    errors, warnings = [], []
    targets = {target:role for role,fields in profile["bindings"].items() for target in fields}
    deps = {key:[] for key in graph}
    for key, node in graph.items():
        kind = node["class_type"]
        info = registry.get(kind)
        if not isinstance(info, dict):
            errors.append(f"{key} 缺少已安装节点 {kind}")
            continue
        declared = info.get("input",{})
        inputs = node["inputs"]
        required,definitions = registry_fields(declared,inputs)
        if key == profile["output"] and not info.get("output_node"):
            errors.append(f"{key} {kind} 没有声明为输出节点")
        for name in sorted(required.keys() - inputs.keys()):
            errors.append(f"{key}.{name} 缺少必填输入")
        for name, value in inputs.items():
            target = key+"."+name
            spec = definitions.get(name)
            if spec is None:
                # Custom variable-arity nodes may accept extra inputs not enumerated in object_info.
                (errors if target in targets else warnings).append(f"{target} 未在节点公开 schema 中声明")
            if isinstance(value,list) and len(value)==2 and isinstance(value[0],str) and type(value[1]) is int:
                parent, slot = value
                if parent not in graph:
                    errors.append(f"{target} 的上游 {parent} 不存在")
                    continue
                deps[key].append(parent)
                outputs = registry.get(graph[parent]["class_type"],{}).get("output",[])
                if slot < 0 or slot >= len(outputs):
                    errors.append(f"{target} 的上游输出槽 {parent}[{slot}] 无效")
                else:
                    source_type = outputs[slot]
                    target_type, _ = _widget(spec)
                    builtin = {"IMAGE","MASK","LATENT","MODEL","CONDITIONING","CLIP","VAE",
                               "VIDEO","AUDIO","STRING","BOOLEAN","INT","FLOAT"}
                    if (isinstance(target_type,str) and isinstance(source_type,str)
                            and source_type in builtin and target_type in builtin and source_type!=target_type
                            and not (source_type=="INT" and target_type=="FLOAT")):
                        errors.append(f"{target} 连接类型不符，{source_type} → {target_type}")
            elif spec is not None:
                err = _literal_error(spec,value,targets.get(target),cfg)
                if err:
                    errors.append(f"{target} {err}")
    reachable, visiting = set(), set()

    def visit(key):
        if key in visiting:
            errors.append(f"输出依赖中存在环，节点 {key}")
            return
        if key in reachable:
            return
        visiting.add(key)
        for parent in deps[key]:
            visit(parent)
        visiting.remove(key)
        reachable.add(key)

    visit(profile["output"])
    for target, role in targets.items():
        if target.split(".",1)[0] not in reachable:
            errors.append(f"{role}: {target} 不参与最终输出")
    stages = calibration(graph, profile["output"], errors, warnings)
    return {"calibration":stages, "passed":not errors,"nodes":len(graph),"output":profile["output"],"bindings":profile["bindings"],
            "sha256":_fingerprint(profile),"errors":errors,"warnings":warnings}


def _local(cfg):
    from PIL import ImageFont
    disk_budget(cfg,6)
    ImageFont.truetype(cfg["font"],18)
    with tempfile.TemporaryDirectory(dir=cfg["data"]) as tmp:
        sample = Path(tmp)/"codec.mp4"
        media.ff("-f","lavfi","-i","color=c=gray:s=128x64:r=24",
                 "-f","lavfi","-i","sine=f=350:r=48000","-t","0.5",
                 "-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-ac","2",sample)
        media.technical(sample,dict(cfg,width=128,height=64),12,master=True)
    if (cfg["width"],cfg["height"]) != (1920,1080):
        raise ValueError("本验收配置要求 1920×1080 的正片交付画幅")
    return {"master":[1920,1080],"fps":24,"codec_probe":"H.264/AAC"}


def _comfy(cfg):
    stats = request(cfg["comfy"].rstrip("/")+"/system_stats",timeout=10)
    devices = stats.get("devices") if isinstance(stats,dict) else []
    if not devices:
        raise ValueError("ComfyUI 未返回实际执行设备")
    if not any(d.get("type")=="cuda" for d in devices):
        raise ValueError("ComfyUI 当前未报告 CUDA 设备，请检查 GPU1 的启动参数")
    flags = stats.get("system",{}).get("argv",[])
    expected = ("--lowvram","--disable-smart-memory","--disable-pinned-memory")
    warnings = [f"命令行没有显示 {flag}" for flag in expected if flag not in flags]
    placement_errors = []
    if flags and cfg.get("station_contract"):
        if warnings:
            placement_errors.extend(warnings)
        device = flags[flags.index("--default-device")+1:][:1] if "--default-device" in flags else []
        if device != ["1"] and "--default-device=1" not in flags:
            placement_errors.append("当前 ComfyUI 启动参数未指定任务书中的物理 GPU1")
    return {"placement_errors":placement_errors,"devices":[{k:d.get(k) for k in ("name","type","index","vram_total","vram_free")} for d in devices],
            "warnings":warnings}


def _gpu_placement(cfg):
    result = {"devices":[], "services":{}, "warnings":[], "errors":[]}
    def rows(*argv):
        output = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=8, check=True)
        return list(csv.reader(output.stdout.splitlines(),skipinitialspace=True))

    try:
        gpus = rows("nvidia-smi", "--query-gpu=index,uuid,name,memory.total,memory.used",
                    "--format=csv,noheader,nounits")
        ids = {}
        for index, uid, name, total, used in gpus:
            ids[uid] = int(index)
            result["devices"].append({"index":int(index),"name":name,"total_mib":int(total),"used_mib":int(used)})
        configured = {g["index"]:g for g in result["devices"]}
        for i in (0,1):
            if i not in configured:
                result["errors"].append(f"驱动清单缺少物理 GPU{i}")
            elif configured[i]["total_mib"] < 11264:
                result["errors"].append(f"GPU{i} 只有 {configured[i]['total_mib']} MiB，与 12 GB 卡配置不符")
        if os.name!="nt":
            result["warnings"].append("服务监听进程到物理卡的核对需在 Windows 工作站运行")
            return result
        process_rows = rows("nvidia-smi","--query-compute-apps=pid,gpu_uuid","--format=csv,noheader,nounits")
        cards = {}
        for row in process_rows:
            if len(row)==2 and row[0].strip().isdigit() and row[1].strip() in ids:
                cards.setdefault(int(row[0]),set()).add(ids[row[1].strip()])
        tcp = subprocess.run(["netstat","-ano","-p","tcp"],capture_output=True,timeout=8,check=True).stdout
        listeners = {}
        for line in tcp.decode(errors="replace").splitlines():
            fields = line.split()
            if len(fields)==5 and fields[0]=="TCP" and fields[2].endswith(":0") and fields[-1].isdigit():
                listeners.setdefault(fields[1].rsplit(":",1)[-1],set()).add(int(fields[-1]))
        for service, expected in (("comfy",1),("ace",0)):
            url = urlsplit(cfg[service])
            pids = listeners.get(str(url.port or (443 if url.scheme=="https" else 80)),set())
            if url.hostname not in {"127.0.0.1","localhost","::1"} or len(pids)!=1:
                result["warnings"].append(f"{service} 的本地监听进程无法唯一确定")
                continue
            pid = next(iter(pids))
            observed = sorted(cards.get(pid,()))
            result["services"][service] = {"listening_pid":pid,"gpu_indices":observed,"expected":expected}
            if len(observed)==1 and observed[0]!=expected:
                result["errors"].append(f"{service} 监听进程实际在 GPU{observed[0]} 建立 CUDA 上下文，预期 GPU{expected}")
            elif observed!=[expected]:
                result["warnings"].append(f"{service} 当前可能未载入模型、使用子进程或存在多卡上下文；无法确认单卡分配")
    except (OSError, subprocess.SubprocessError, ValueError) as err:
        result["warnings"].append(f"驱动或进程映射不可用，物理卡分配待核对：{err}")
    return result


def _ace(cfg):
    health = request(cfg["ace"].rstrip("/")+"/health",timeout=10)
    if not isinstance(health,dict) or health.get("code",200)!=200 or health.get("error"):
        raise ValueError("ACE-Step 健康检查未通过")
    data = health.get("data",health)
    if not isinstance(data,dict) or str(data.get("status","")).lower() not in {"ok","healthy","ready","up"}:
        raise ValueError("ACE-Step 没有返回健康状态")
    return {"status":data["status"],"service":data.get("service")}


def _text(director):
    nonce = secrets.token_hex(5)
    reply = director.ask('Copy the supplied nonce exactly. Return {"nonce":"..."}',{"nonce":nonce},tokens=256)
    if reply.get("nonce") != nonce:
        raise ValueError("文本模型未正确返回校验值")
    return {"model":director.endpoint(False)[1]}


def preflight(cfg):
    director = Director(dict(cfg,allow_hosted_models=cfg.get('allow_hosted_station_tests',False)))
    checks = {}
    registry = {}
    try:
        registry = request(cfg["comfy"].rstrip("/")+"/object_info",timeout=30)
        if not isinstance(registry,dict) or not registry:
            raise ValueError("ComfyUI 节点清单为空")
        checks["object_info"] = {"passed":True,"count":len(registry)}
    except Exception as err:
        checks["object_info"] = {"passed":False,"error":str(err)}
    actions = {"media":lambda:_local(cfg), "comfy":lambda:_comfy(cfg), "ace":lambda:_ace(cfg),
               "text":lambda:_text(director),"vision":director.check_vision,"gpu_placement":lambda:_gpu_placement(cfg)}
    for name,action in actions.items():
        try:
            checks[name] = {"passed":True,"evidence":action()}
            if name == "comfy" and checks[name]["evidence"].get("placement_errors"):
                checks[name]["passed"] = False
            if name == "gpu_placement":
                checks[name]["passed"] = not checks[name]["evidence"]["errors"]
            if name == "vision":
                checks[name]["evidence"] = {"model":director.endpoint(True)[1],"image_probe":"two panels"}
        except Exception as err:
            checks[name] = {"passed":False,"error":str(err)}
    routes = {}
    for route in graphs.ROUTES:
        try:
            routes[route] = inspect_route(cfg,route,registry)
        except Exception as err:
            routes[route] = {"passed":False,"errors":[str(err)]}
    return {"passed":all(item["passed"] for item in [*checks.values(),*routes.values()]),
            "checks":checks,"routes":routes}


def _brief(first,last):
    return {"title":"工作站三路实机验收","script":"【Clip 1｜00:00-00:02（2s）】建立场景，保留轻微环境声。\n"
            "【Clip 2｜00:02-00:04（2s）】从指定首图展示一个微小动作，延续环境声。\n"
            "【Clip 3｜00:04-00:06（2s）】承接上一镜的采用画面，平稳衔接指定尾图。全片轻柔纯器乐配乐。",
            "requirements":f"严格三镜，每镜2秒。S01 全新文生画面，首尾帧都留空。"
            f"S02 用图片 {first} 作为 start，end 留空。S03 用 previous 作为 start，用 {last} 作为 end。"
            "依据参考图描述安排真实场景与微小动作，不添加对白、旁白、标题或字幕。",
            "seconds":6,"mode":"auto","music":True}


def isolated_brief(cfg, first, last, hashes):
    """Freeze exactly the two requested image files without captions or adjacent assets."""
    import shutil
    from .studio_import import _commit_bundle
    names = [first,last]
    if first == last or not isinstance(hashes,list) or len(hashes) != 2:
        raise ValueError("需要两张不同的已选图片及其指纹")
    manifest = {"schema":"station-test-v1",
                "assets":[dict(name=n,sha256=h,caption="") for n,h in zip(names,hashes)]}
    root = cfg["data"]/"studio-imports"
    root.mkdir(exist_ok=True)
    key = _fingerprint(manifest)
    spec = dict(_brief(first,last),studio_bundle=key,station_test=True,studio_links={
        "1":{"start":"","end":""},"2":{"start":first,"end":""},"3":{"start":"previous","end":last}})
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        stage = Path(tmp)/"bundle"
        dest = stage/"assets"
        dest.mkdir(parents=True)
        for name,digest in zip(names,hashes):
            if not isinstance(name,str) or Path(name).name!=name or Path(name).suffix.lower() not in {".png",".jpg",".jpeg"}:
                raise ValueError("验收图片名称无效")
            src = (cfg["assets"]/name).resolve(strict=True)
            if src.parent != cfg["assets"].resolve():
                raise ValueError("验收图片超出素材目录")
            shutil.copyfile(src,dest/name)
            if _file_hash(dest/name)!=digest:
                raise ValueError("验收控制台与提交端的参考图内容不一致")
        write(stage/"manifest.json",manifest)
        write(stage/"brief.json",spec)
        _commit_bundle(stage,root/key,manifest)
    return spec


def verify_render(cfg, jid, first, last, bound_routes=None):
    folder = cfg["data"]/jid
    delivery = Store(cfg).delivery_dir(jid)
    if delivery is None:
        raise ValueError("任务尚无正式交付版本")
    audit = read(delivery/"acceptance.json")
    verdicts = audit["source_verdicts"]
    if (not audit["editorial"]["passed"] or not audit["visual"]["passed"] or not verdicts
            or not all(v["passed"] for v in verdicts)):
        raise ValueError("缺少已通过的实际画面或剧情总审")
    timeline = read(delivery/"timeline.json")
    if len(timeline)!=3:
        raise ValueError("导演制作的镜头数与三路验收要求不符")
    wanted = [("T2V","",""),("I2V",first,""),("FLF","previous",last)]
    shots = []
    for i,(shot,(route,begin,end)) in enumerate(zip(timeline,wanted),1):
        if (shot["shot"],shot["seconds"],shot["frames"],shot["start"],shot["end"])!=(i,2,48,begin,end):
            raise ValueError(f"S{i:02} 实际时长或首尾帧与 {route} 验收要求不符")
        cut = (folder/shot["file"]).resolve(strict=True)
        if not cut.is_relative_to((folder/"takes").resolve()):
            raise ValueError("采用镜头文件位于任务目录以外")
        take = cut.parent
        design = read(take/"shot.json")
        if i==3 and design.get("previous_code")!=timeline[1]["code"]:
            raise ValueError("FLF 首帧记录未指向已采用的第二镜")
        profile = graphs.load_profile(cfg,route)
        if bound_routes and _fingerprint(profile)!=bound_routes[route]["sha256"]:
            raise ValueError(f"{route} 正片图在实机验收期间发生变化")
        ticket = read(take/"remote.json")
        if (not ticket.get("prompt_id") or ticket["output"]!=profile["output"]
                or ticket.get("base")!=cfg["comfy"].rstrip("/")):
            raise ValueError(f"S{i:02} 缺少当前工作流回执")
        if not read(take/"review.json")["passed"]:
            raise ValueError(f"S{i:02} 未通过候选审片")
        graph = read(take/"graph.json")
        width, height = canvas(cfg)
        expected = {"seconds":2, "frames":49, "prompt":design["prompt"], "seed":design["seed"],
                    "negative":graphs.NEGATIVE, "fps":24, "width":width, "height":height,
                    "half_width":width//2, "half_height":height//2}
        for role,targets in profile["bindings"].items():
            for target in targets:
                node,field = target.split(".",1)
                value = graph[node]["inputs"][field]
                if role in expected and value!=expected[role]:
                    raise ValueError(f"S{i:02} 的 {role} 与采用的镜头设计不一致")
                if role in {"first","last"} and (not isinstance(value,str) or
                                                  Path(value).suffix.lower() not in {".png",".jpg",".jpeg"}):
                    raise ValueError(f"S{i:02} 实际提交的参考图名无效")
                graph[node]["inputs"][field] = profile["graph"][node]["inputs"][field]
        if graph!=profile["graph"]:
            raise ValueError(f"S{i:02} 使用的实际图与当前 {route} 配置不一致")
        source_info = read(take/"technical.json")
        if cfg.get("station_contract") and (source_info["frames"] != 49 or source_info["fps"] != 24 or
                [source_info["width"],source_info["height"]] != [1920,1088] or not source_info["audio"]):
            raise ValueError(f"S{i:02} 原生媒体不符合 49 帧/24fps/1920×1088/含音频要求")
        shots.append({"source":source_info,"route":route,"code":shot["code"],"prompt_id":ticket["prompt_id"]})
    # Older published releases only froze the score itself.
    ticket_file = delivery/"score-ticket.json"
    ready_file = delivery/"score-ready.json"
    score = read(ticket_file if ticket_file.exists() else folder/"score-ticket.json")
    ready = read(ready_file if ready_file.exists() else folder/"score-ready.json")
    if (not score.get("task_id") or not (delivery/"score.wav").is_file()
            or not (delivery/"score.wav").stat().st_size or ready.get("seconds")!=6):
        raise ValueError("缺少实际配乐文件或 ACE-Step 回执")
    facts = media.technical(delivery/"film.mp4",cfg,144,master=True)
    return {"shots":shots,"master":facts,"source_checks":len(verdicts),
            "music_task_id":score["task_id"],"score_receipts_frozen":ticket_file.is_file() and ready_file.is_file(),"film":str(delivery/"film.mp4")}


def _capture_stage_gpu(cfg, jid, folder, store):
    if store is None:
        return
    for active in store.stages(jid):
        if active["state"]=="running" and active["actor"].startswith(("ComfyUI","ACE-Step")):
            _capture_one_gpu(cfg,folder,active)


def _capture_one_gpu(cfg,folder,active):
    stage = "music" if active["actor"].startswith("ACE-Step") else f"shot-{active['shot']}"
    path = folder/(stage+".json")
    if path.exists():
        previous = read(path)
        if len((previous.get("mapping") or {}).get("gpu_indices",[]))==1 or time.time()-previous.get("epoch",0)<30:
            return
    service = "ace" if stage=="music" else "comfy"
    result = _gpu_placement(cfg)
    write(path,{"time":datetime.now(timezone.utc).isoformat(),"epoch":time.time(),"stage":active["title"],
                "service":service,"mapping":result["services"].get(service),
                "devices":result["devices"], "warnings":result["warnings"]})


def _scope(routes, telemetry):
    unverified = []
    pipeline = {}
    for name in graphs.ROUTES:
        evidence = routes.get(name,{}).get("calibration",{})
        status = evidence.get("status","unverified")
        pipeline[name] = {"status":status,"steps":evidence.get("steps"),
                          "upscale_factor_in_model_name":evidence.get("upscale_factor_in_model_name"),
                          "stage_settings":evidence.get("stage_settings",[])}
        if status!="two_stages_observed" or evidence.get("steps")!=[8,3] or evidence.get("upscale_factor_in_model_name")!=2:
            unverified.append(f"{name} 两阶段、8+3 步及 ×2 空间倍率的完整静态证据")
        settings = pipeline[name]["stage_settings"]
        if len(settings)!=2 or any(s["cfg"]!=1 or not s["distilled_loras"] or
                                   any(l["strength"]!=0.5 for l in s["distilled_loras"]) for s in settings):
            unverified.append(f"{name} 两个采样阶段的 CFG 与蒸馏 LoRA 实际强度")
    samples = {}
    mismatches = []
    for stage in ("shot-1","shot-2","shot-3","music"):
        file = telemetry/(stage+".json")
        item = read(file) if file.is_file() else {}
        mapping = item.get("mapping") or {}
        cards = mapping.get("gpu_indices",[])
        expected = 0 if stage=="music" else 1
        samples[stage] = item if item else {"status":"not_sampled"}
        if len(cards)==1 and cards!=[expected]:
            mismatches.append(f"{stage} 在阶段执行时观察到 GPU{cards[0]}，预期 GPU{expected}")
        elif cards!=[expected]:
            unverified.append(f"{stage} 执行期间服务 PID 与物理 GPU{expected} 的唯一映射")
    return {"video_paths":pipeline,"gpu_samples":samples,"unverified":unverified,
            "mismatches":mismatches,"profile_evidence":"partial" if unverified or mismatches else "observed"}


def watch(cfg, args, report, save):
    if not args.run_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,56}",args.run_id):
        raise ValueError("--render 需要 1–56 字符的 --run-id，可用字母、数字、下划线和减号")
    if not math.isfinite(args.wait_minutes) or not 0 <= args.wait_minutes <= 1440 or not 1 <= args.port <= 65535:
        raise ValueError("端口范围是 1–65535，等待时间范围是 0–1440 分钟")
    files = assets(cfg)
    if not args.first or args.first==args.last or any(n not in files for n in (args.first,args.last)):
        raise ValueError("--first 与 --last 需要资产目录内两张不同的参考图")
    base = f"http://127.0.0.1:{args.port}"
    identity = {"asset_scope_version":1,"first":args.first,"last":args.last,"first_hash":_file_hash(files[args.first]),
                "last_hash":_file_hash(files[args.last]),"routes":{k:v["sha256"] for k,v in report["preflight"]["routes"].items()},
                "service_digest":_fingerprint({k:cfg[k] for k in ("comfy","ace")}),"models":{k:report["preflight"]["checks"][k]["evidence"]["model"]
                                                                          for k in ("text","vision")}}
    folder = cfg["data"]/"station-acceptance"/"runs"
    folder.mkdir(parents=True, exist_ok=True)
    record = folder/(args.run_id+".json")
    # Keep the registration lock short. Concurrent observers can follow the same job.
    with exclusive(record.with_suffix(".lock")):
        saved = read(record) if record.exists() else {"identity":identity}
        if saved["identity"]!=identity:
            raise ValueError("此 run-id 对应的参考图、工作流或服务配置不同，请选择新编号")
        write(record,saved)
        if not saved.get("job"):
            ids = request(base+"/api/station-test",{"token":"station-accept-"+args.run_id,
                          "first":args.first,"last":args.last,"hashes":[identity["first_hash"],identity["last_hash"]]},timeout=20)["ids"]
            if len(ids)!=1 or not re.fullmatch(r"[a-f0-9]{32}",ids[0]):
                raise ValueError("控制台返回的任务编号异常")
            saved["job"] = ids[0]
            write(record,saved)
    jid = saved["job"]
    report["job"] = jid
    report["monitor"] = base+"/monitor?job="+jid
    telemetry = folder/"telemetry"/args.run_id
    store = Store(cfg) if os.name=="nt" and (cfg["data"]/"factory.sqlite3").is_file() else None
    deadline = time.monotonic()+args.wait_minutes*60
    while True:
        job = request(base+"/api/jobs/"+jid,timeout=6)
        state = job["state"]
        report.update(status="monitoring",job_state=state,detail=job["detail"])
        if state=="running":
            _capture_stage_gpu(cfg,jid,telemetry,store)
        save()
        if state=="done":
            routes = report["preflight"]["routes"]
            report["render"] = verify_render(cfg,jid,args.first,args.last,routes)
            evidence = _scope(routes,telemetry)
            report["qualification"] = evidence
            if cfg.get("station_contract") and evidence["unverified"]:
                raise ValueError("实机证据待补："+"；".join(evidence["unverified"]))
            if evidence["mismatches"]:
                raise ValueError("；".join(evidence["mismatches"]))
            report["status"] = "render_passed"
            return 0
        if state not in {"running","queued"}:
            raise ValueError("实机任务暂停或失败：" + str(job["detail"]))
        remaining = deadline-time.monotonic()
        if remaining<=0:
            report["status"] = "pending"
            return 2
        time.sleep(min(5,remaining))


def run_cli(cfg, args):
    stamp = datetime.now(timezone.utc)
    location = args.report or cfg["data"]/"station-acceptance"/(stamp.strftime("%Y%m%dT%H%M%S%fZ")+".json")
    report = {"started":stamp.isoformat(),"mode":"live_render" if args.render else "preflight","status":"checking"}
    def save():
        write(location,report)
    save()
    try:
        report["preflight"] = preflight(cfg)
        if not report["preflight"]["passed"]:
            report["status"] = "preflight_failed"
            return 1
        if args.render:
            return watch(cfg,args,report,save)
        report["status"] = "preflight_passed"
        return 0
    except Exception as err:
        report.update(status="failed",error=str(err))
        return 1
    finally:
        report['preflight_passed'] = report.get('preflight',{}).get('passed',False)
        report['render_passed'] = report['status'] == 'render_passed'
        report["updated"] = datetime.now(timezone.utc).isoformat()
        save()
        print(json.dumps({"status":report["status"],"report":str(location),"job":report.get("job"),
                          "error":report.get("error")},ensure_ascii=False))
