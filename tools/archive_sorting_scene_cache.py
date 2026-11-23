"""Archive only sorting-example scenes and exclusive rules; retain shared assets.

Dry run by default. Stop the device-scene service before using --apply.
PLC sessions, programs and acceptance records are outside this tool's scope.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def confined(root,path):
    resolved=path.resolve()
    if not resolved.is_relative_to(root) or resolved==root or path.is_symlink():
        raise ValueError('Refusing path outside the device-scene cache: '+str(path))
    return resolved


def tree_hashes(root,paths):
    result={}
    for folder in paths:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                confined(root,path)
                result[path.relative_to(root).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def plan(root):
    root=Path(root).resolve()
    if root.name!='device-scenes':raise ValueError('Root must be the explicitly named device-scenes directory')
    records=[]
    for folder in sorted((root/'tasks').glob('*')):
        if not folder.is_dir():continue
        confined(root,folder)
        record=read(folder/'web.json');ir=read(folder/'device_ir.json');task=read(folder/'task.json')
        if record.get('busy'):raise ValueError('A scene task is still busy: '+folder.name)
        types={c['type'] for c in ir.get('components',[])}|{c['type'] for c in task.get('candidates',[])}
        sorting=(ir.get('station')=='FactoryForgeSortingCell'
                 or record.get('name')=='factoryforge_sorting_reference.aml')
        records.append((folder,sorting,types))
    sorting_types=set().union(*(types for _,sorting,types in records if sorting))
    other_types=set().union(*(types for _,sorting,types in records if not sorting))
    exclusive=sorting_types-other_types;paths=[folder for folder,sorting,_ in records if sorting]
    protected=[folder for folder,sorting,_ in records if not sorting]
    for area in ('candidates','approved'):
        for folder in sorted((root/'component_library'/area).glob('*')):
            if not folder.is_dir():continue
            confined(root,folder)
            (paths if folder.name in exclusive else protected).append(folder)
    return {'root':str(root),'retired_tasks':[folder.name for folder,sorting,_ in records if sorting],
            'preserved_tasks':[folder.name for folder,sorting,_ in records if not sorting],
            'shared_types':sorted(sorting_types&other_types),
            'archived_types':sorted({folder.name for folder in paths if folder.parent.name in ('candidates','approved')}),
            'paths':[p.relative_to(root).as_posix() for p in paths],
            'protected_paths':[p.relative_to(root).as_posix() for p in protected]}


def archive(root):
    summary=plan(root);root=Path(summary['root'])
    if not summary['retired_tasks']:return {**summary,'status':'nothing_to_archive'}
    # Check every source and destination before any move, then keep reversible
    # moves within the explicitly selected cache directory on the same volume.
    stamp=datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S-%f')
    target=confined(root,root/'archive'/('sorting-reset-'+stamp))
    moves=[(confined(root,root/relative),confined(root,target/relative)) for relative in summary['paths']]
    if any(dst.exists() for _,dst in moves):raise ValueError('Archive destination already exists')
    protected=[root/relative for relative in summary['protected_paths']]
    before=tree_hashes(root,protected);completed=[]
    previous=read(root/'cache_reset.json')
    try:
        for src,dst in moves:
            dst.parent.mkdir(parents=True,exist_ok=True);src.rename(dst);completed.append((src,dst))
        if tree_hashes(root,protected)!=before:raise ValueError('Preserved device data changed; rolling back archive')
        report={**summary,'status':'archived','archive':str(target),'preserved_sha256':before}
        (target/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        reset={'retired_tasks':sorted(set(previous.get('retired_tasks',[]))|set(summary['retired_tasks'])),
               'last_archive':str(target),'preserved_shared_types':summary['shared_types']}
        temporary=root/'cache_reset.tmp'
        temporary.write_text(json.dumps(reset,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        temporary.replace(root/'cache_reset.json')
    except Exception:
        for src,dst in reversed(completed):
            src.parent.mkdir(parents=True,exist_ok=True);dst.rename(src)
        raise
    return {key:value for key,value in report.items() if key!='preserved_sha256'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    print(json.dumps(archive(args.root) if args.apply else plan(args.root),ensure_ascii=False,indent=2))
