"""Scene authoring CLI; component approval is explicit and never an LLM tool."""
import argparse
import json
from pathlib import Path
import sys

from .common import SceneError, read_json, write_json
from .device_ir import parse_device_ir
from .properties import apply_property_patch as apply_patch
from .rules import ComponentLibrary
from .workflow import prepare, build_task, check_task, review_pack


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',default='E:/chagpt/scene_component_library')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('import-package',help='按 AML 外部资源引用导入并装配，不调用 Agent')
    p.add_argument('--aml',required=True);p.add_argument('--out',required=True)
    p.add_argument('--aliases',help='显式资源路径修正 JSON；不修改源文件')
    p=sub.add_parser('prepare');source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--aml');source.add_argument('--device-ir');p.add_argument('--request',required=True)
    p.add_argument('--out',required=True);p.add_argument('--candidate-rules');p.add_argument('--offline',action='store_true')
    p.add_argument('--blender',default='E:/Blender/blender.exe')
    p.add_argument('--verification-profile',choices=['fixed_model_io/1','legacy'],default='fixed_model_io/1')
    p=sub.add_parser('build');p.add_argument('--task',required=True);p.add_argument('--candidate-validation',action='store_true')
    p.add_argument('--blender',default='E:/Blender/blender.exe')
    p=sub.add_parser('check');p.add_argument('--task',required=True);p.add_argument('--candidate-validation',action='store_true')
    p.add_argument('--freeze',action='store_true')
    p.add_argument('--plc-host');p.add_argument('--plc-port',type=int,default=502)
    p=sub.add_parser('review');p.add_argument('--task',required=True)
    p=sub.add_parser('tests');p.add_argument('--task',required=True);p.add_argument('--test-id',action='append')
    p.add_argument('--candidate-validation',action='store_true')
    p.add_argument('--plc-host');p.add_argument('--plc-port',type=int,default=502)
    p=sub.add_parser('run');p.add_argument('--scene',required=True);p.add_argument('--host',required=True)
    p.add_argument('--port',type=int,default=502);p.add_argument('--prog-id',type=int,required=True)
    p.add_argument('--seconds',type=float,default=30);p.add_argument('--schedule')
    p=sub.add_parser('approve');p.add_argument('--candidate',required=True);p.add_argument('--reviewer',required=True)
    p.add_argument('--review-sha256',required=True);p.add_argument('--note',required=True)
    p=sub.add_parser('edit');p.add_argument('--task',required=True);p.add_argument('--out',required=True)
    p.add_argument('--patch');p.add_argument('--request');p.add_argument('--editable-id',action='append',default=[])
    args=parser.parse_args(argv);library=ComponentLibrary(args.library)
    try:
        if args.command=='import-package':
            from .package_import import import_package
            report=import_package(args.aml,args.out,aliases=read_json(args.aliases) if args.aliases else None,
                                  progress=lambda message:print(message,flush=True))
            result={k:report[k] for k in ('status','counts','issues','gaps','simulation_acceptance')}
            if not report['assembly']['compiled']:
                result['assembly']=report['assembly']
                print(json.dumps(result,ensure_ascii=False,indent=2));return 2
        elif args.command=='prepare':
            candidates=read_json(args.candidate_rules)['rules'] if args.candidate_rules else None
            author=None
            if not args.offline and candidates is None:
                from ..config import get_api_key
                from ..client import BigModelClient
                from .author import SceneGenerationAgent
                if get_api_key():author=SceneGenerationAgent(BigModelClient(get_api_key()))
            result=prepare(read_json(args.device_ir) if args.device_ir else args.aml,args.request,args.out,library,candidate_rules=candidates,author=author,blender=args.blender,verification_profile=args.verification_profile)
        elif args.command=='build':result=build_task(args.task,library,isolated_candidates=args.candidate_validation,blender=args.blender)
        elif args.command=='check':
            result=check_task(args.task,isolated_candidates=args.candidate_validation,freeze=args.freeze,
                plc_config={'host':args.plc_host,'port':args.plc_port} if args.plc_host else None)
            if result['status']!='passed':print(json.dumps(result,ensure_ascii=False,indent=2));return 2
        elif args.command=='review':result=review_pack(args.task)
        elif args.command=='tests':
            from .simulation_tests import run_test_plan
            task=Path(args.task);scene=task/('candidate_scene' if args.candidate_validation else 'scene')
            from .workflow import test_task
            result=test_task(task,isolated_candidates=args.candidate_validation,selected=args.test_id,
                plc_config={'host':args.plc_host,'port':args.plc_port} if args.plc_host else None)
            if result['status']!='passed':
                print(json.dumps(result,ensure_ascii=False,indent=2));return 2
        elif args.command=='run':
            from runtime.device_plc_link import run_frozen_scene
            result=run_frozen_scene(args.scene,args.host,args.port,args.prog_id,args.seconds,read_json(args.schedule) if args.schedule else None)
        elif args.command=='approve':
            result={'status':'approved','directory':str(library.approve(args.candidate,reviewer=args.reviewer,
                    expected_review_sha256=args.review_sha256,note=args.note))}
        else:
            task=Path(args.task);out=Path(args.out)
            if out.exists():raise SceneError('Edit output must be a new task directory')
            ir=read_json(task/'device_ir.json')
            if args.patch:
                patch=read_json(args.patch);revised=apply_patch(ir,patch)
            else:
                from ..config import get_api_key
                from ..client import BigModelClient
                from .author import SceneGenerationAgent
                if not args.request or not args.editable_id:raise SceneError('Natural-language edit requires request and explicit editable IDs')
                if not get_api_key():raise SceneError('Model key required for natural-language edit')
                revised,patch=SceneGenerationAgent(BigModelClient(get_api_key())).local_edit(ir,args.request,args.editable_id,out/'model_calls')
            write_json(out/'device_ir.json',revised);write_json(out/'patch.json',patch)
            result={**read_json(task/'task.json'),'status':'edited_requires_revalidation','library':str(library.root),
                    'parent_task':str(task),'model_frozen':False,'plc_acceptance':'not_run'}
            result.pop('scene',None)
            write_json(out/'task.json',result)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (SceneError,ValueError,KeyError,OSError,RuntimeError) as exc:
        print(json.dumps({'status':'blocked','layer':getattr(exc,'layer','component_model'),'error':str(exc),
                          'details':getattr(exc,'details',{}),'plc_generation':'unchanged','plc_acceptance':'not_run'},ensure_ascii=False,indent=2))
        return 2


if __name__=='__main__':
    try:sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:pass
    raise SystemExit(main())
