"""Local production console for a calibrated LTX Studio workstation."""
import argparse
import json

from factory import acceptance, drafts, studio_import
from factory.common import config
from factory.graphs import ROUTES, bind, inspect_graph
from factory.setup import doctor
from factory.web import serve


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config",default="config.local.json")
    cmd = p.add_subparsers(dest="command",required=True)
    web = cmd.add_parser("serve")
    web.add_argument("--port",type=int,default=8766)
    cmd.add_parser("doctor")
    cmd.add_parser("verify-install",help="离线检查发行包字节，不启动服务")
    verify=cmd.add_parser("verify-bundle",help="离线核验导出的成片目录，无需调用任何服务")
    verify.add_argument("directory")
    cmd.add_parser("counter-info")
    manifest = cmd.add_parser("scan-clips")
    manifest.add_argument("output")
    pilot = cmd.add_parser("accept-pilot")
    pilot.add_argument("job")
    pilot.add_argument("--report",required=True)
    cmd.add_parser("startup-check")
    acceptance.add_arguments(cmd)
    studio_import.add_arguments(cmd)
    compare = cmd.add_parser("compare-directors")
    compare.add_argument("brief")
    compare.add_argument("output")
    legacy = cmd.add_parser("import-clips")
    legacy.add_argument("manifest")
    edit = cmd.add_parser("export-edit")
    edit.add_argument("job")
    edit.add_argument("output")
    inspect = cmd.add_parser("inspect")
    inspect.add_argument("file")
    register = cmd.add_parser("bind")
    register.add_argument("route",choices=ROUTES)
    register.add_argument("graph")
    register.add_argument("mapping")
    register.add_argument("--draft",action="store_true",help="绑定半分辨率、单阶段草稿工作流")
    args = p.parse_args()
    if args.command=="verify-install":
        from pathlib import Path
        from factory.integrity import verify_install
        print(json.dumps(verify_install(Path(__file__).resolve().parent),indent=2))
        return
    if args.command=="verify-bundle":
        from pathlib import Path
        from factory.integrity import verify_bundle
        print(json.dumps(verify_bundle(Path(args.directory)),ensure_ascii=False,indent=2))
        return
    if args.command == "inspect":
        return inspect_graph(args.file)
    if args.command == "import-studio" and args.inspect:
        print(json.dumps(studio_import.run(None,args),ensure_ascii=False,indent=2))
        return
    cfg = config(args.config)
    if args.command == "serve":
        serve(cfg,args.port)
    elif args.command == "station-accept":
        raise SystemExit(acceptance.run_cli(cfg,args))
    elif args.command == "import-studio":
        print(json.dumps(studio_import.run(cfg,args),ensure_ascii=False,indent=2))
    elif args.command == "compare-directors":
        from factory.common import read
        from factory.comparison import compare
        from factory.web import specification
        compare(cfg,specification(read(args.brief)),args.output)
    elif args.command == "export-edit":
        from factory.editor import export_edit
        from factory.store import Store
        export_edit(Store(cfg), args.job, args.output)
    elif args.command == "import-clips":
        from factory.archive import import_clips
        from factory.store import Store
        import_clips(Store(cfg), args.manifest)
    elif args.command == "accept-pilot":
        from factory.pilot import qualify
        from factory.common import write
        try:
            result = qualify(cfg,args.job)
        except Exception as err:
            result = {"passed":False,"reason":str(err)}
        write(args.report,result)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        raise SystemExit(0 if result["passed"] else 1)
    elif args.command == "counter-info":
        from factory.common import read
        from pathlib import Path
        doc = read(Path(cfg["studio_root"])/"clip_code_counter.json")
        print(json.dumps({k:v for k,v in doc.items() if type(v) is int} if isinstance(doc,dict) else doc if type(doc) is int else {},indent=2))
    elif args.command == "scan-clips":
        from factory.archive import scan
        from factory.common import write
        from pathlib import Path
        if Path(args.output).exists():
            raise ValueError("请指定未使用的清单文件名")
        write(args.output,scan(cfg))
    elif args.command == "startup-check":
        import socket
        from urllib.parse import urlsplit
        for name in ("comfy","ace"):
            address = urlsplit(cfg[name])
            try:
                with socket.create_connection((address.hostname,address.port),timeout=3):
                    print(name,"已连接")
            except OSError:
                print(name,"未连接，请检查原服务")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1",8766))
    elif args.command == "doctor":
        checks = doctor(cfg)
        print(json.dumps(checks,ensure_ascii=False,indent=2))
        ok = all(checks.get(k) is True for k in ("ComfyUI","ACE-Step",*ROUTES)) and all(
            checks.get(k) for k in ("ffmpeg","ffprobe","Pillow","font"))
        raise SystemExit(0 if ok else 1)
    else:
        bind(drafts.settings(cfg) if args.draft else cfg,args.route,args.graph,args.mapping)


if __name__ == "__main__":
    main()
