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
    elif args.command == "doctor":
        print(json.dumps(doctor(cfg),ensure_ascii=False,indent=2))
    else:
        bind(drafts.settings(cfg) if args.draft else cfg,args.route,args.graph,args.mapping)


if __name__ == "__main__":
    main()
