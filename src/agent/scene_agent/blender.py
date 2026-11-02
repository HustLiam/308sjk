"""Bounded geometry-only Python candidates executed by Blender's own Python.

Candidate code can only call the GeometryAPI, not arbitrary bpy/file/network APIs.
The trusted worker performs bpy/bmesh/mathutils operations and all exports. This
is a separate process/directory, not a claim of an OS security sandbox.
"""
import ast
import os
from pathlib import Path
import subprocess

from .common import SceneError, confined, read_json, write_json

METHODS={'box','cylinder','mesh','difference','body','clearance_probe'}
ALLOWED=(ast.Module,ast.FunctionDef,ast.arguments,ast.arg,ast.Assign,ast.Expr,ast.Return,
         ast.For,ast.If,ast.Compare,ast.BinOp,ast.UnaryOp,ast.Subscript,ast.Dict,ast.List,
         ast.Tuple,ast.Name,ast.Call,ast.Constant,ast.Load,ast.Store,ast.Attribute,ast.keyword,
         ast.Add,ast.Sub,ast.Mult,ast.Div,ast.USub,ast.UAdd,ast.Eq,ast.NotEq,ast.Lt,ast.LtE,
         ast.Gt,ast.GtE,ast.And,ast.Or,ast.BoolOp,ast.Not,ast.Pass)


def validate_script(source):
    if len(source)>64000:raise SceneError('Geometry script too large')
    try:tree=ast.parse(source)
    except SyntaxError as exc:raise SceneError('Geometry script syntax: '+str(exc)) from exc
    if len(tree.body)!=1 or not isinstance(tree.body[0],ast.FunctionDef) or tree.body[0].name!='build':
        raise SceneError('Candidate must define only build(params, api)')
    function=tree.body[0]
    if ([a.arg for a in function.args.args]!=['params','api'] or function.decorator_list or function.args.defaults
            or function.args.kwarg or function.args.vararg or function.args.posonlyargs or function.args.kwonlyargs
            or function.returns or any(a.annotation for a in function.args.args)):
        raise SceneError('Only build(params, api) is allowed')
    if any(isinstance(n,ast.FunctionDef) and n is not function for n in ast.walk(tree)):
        raise SceneError('Nested functions are not permitted')
    for node in ast.walk(tree):
        if not isinstance(node,ALLOWED):raise SceneError('Unsupported geometry Python syntax '+type(node).__name__)
        if isinstance(node,ast.Name) and (node.id.startswith('_') or node.id in ('eval','exec','open','globals','locals','getattr','setattr','type','object','compile','__import__')):
            raise SceneError('Forbidden geometry identifier '+node.id)
        if isinstance(node,ast.Attribute) and not (isinstance(node.value,ast.Name) and node.value.id=='api' and node.attr in METHODS):
            raise SceneError('Only bounded GeometryAPI attribute calls are allowed')
        if isinstance(node,ast.Call) and not (isinstance(node.func,ast.Attribute) or (isinstance(node.func,ast.Name) and node.func.id in ('range','min','max','abs'))):
            raise SceneError('Forbidden geometry function call')
        if isinstance(node,ast.Assign) and any(not isinstance(t,ast.Name) or t.id in ('params','api','build') for t in node.targets):
            raise SceneError('Candidate may assign local names only')
    return True


def run_blender(source,parameters,directory, *, executable='E:/Blender/blender.exe',timeout_s=120):
    validate_script(source)
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    script=directory/'build.py';script.write_text(source,encoding='utf-8')
    write_json(directory/'parameters.json',parameters)
    exe=Path(executable)
    if not exe.is_file():raise SceneError('Blender executable missing: '+str(exe))
    worker=Path(__file__).with_name('blender_worker.py')
    command=[str(exe),'--background','--factory-startup','--disable-autoexec','--python-exit-code','23',
             '--python',str(worker),'--','--script',str(script),'--parameters',str(directory/'parameters.json'),'--out',str(directory)]
    # Do not pass model API keys or project dotenv into the candidate process.
    environment={k:v for k,v in os.environ.items() if k.upper() in {
        'SYSTEMROOT','WINDIR','SYSTEMDRIVE','TEMP','TMP','PATH','USERPROFILE','APPDATA','LOCALAPPDATA','PROGRAMDATA'}}
    try:
        with (directory/'blender.log').open('w',encoding='utf-8') as log:
            result=subprocess.run(command,cwd=directory,env=environment,stdout=log,stderr=subprocess.STDOUT,timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:raise SceneError('Blender candidate timed out') from exc
    write_json(directory/'invocation.json',{'command':command,'returncode':result.returncode,'timeout_s':timeout_s})
    if result.returncode:
        raise SceneError('Blender candidate failed; see '+str(directory/'blender.log'),
                         details={'blender_log_tail':(directory/'blender.log').read_text(encoding='utf-8',errors='replace')[-4096:]})
    manifest=read_json(directory/'component_manifest.json')
    if not manifest.get('bodies') or not (directory/'component.blend').is_file() or not (directory/'preview.png').is_file():
        raise SceneError('Blender export is incomplete')
    for b in manifest['bodies']:
        if not confined(directory,b['mesh']).is_file():raise SceneError('Missing rigid-body mesh')
    return manifest
