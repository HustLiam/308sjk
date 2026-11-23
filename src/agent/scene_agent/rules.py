"""Immutable candidate bundles and human approval records bound to exact bytes."""
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re
import shutil

from .common import SceneError, canonical, confined, digest, expression, read_json, write_json

RULE_FORMAT='308sjk.component_rule/0.1-draft'
NAME=re.compile(r'^[A-Za-z][A-Za-z0-9_.-]{0,95}$')
BEHAVIORS={'none','conveyor','binary_prismatic','ray','source','sink','indicator','panel','rigid','joint_position','robot_control'}


def implementation_hashes():
    root=Path(__file__).resolve().parents[3]
    files=['runtime/device_scene.py','runtime/device_robot.py','runtime/device_plc_link.py','src/agent/scene_agent/device_ir.py',
           'src/agent/scene_agent/common.py','src/agent/scene_agent/blender.py',
           'src/agent/scene_agent/blender_worker.py','src/agent/scene_agent/validation.py',
           'src/agent/scene_agent/rules.py','src/agent/scene_agent/simulation_contract.py',
           'src/agent/scene_agent/simulation_tests.py','src/agent/scene_agent/mechanics.py','src/agent/scene_agent/robot_io.py','src/agent/scene_agent/test_safety.py',
           'src/agent/scene_agent/verification.py','src/agent/scene_agent/fixed_tests.py','src/agent/scene_agent/plc_test.py']
    return {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}


class ApprovalRequired(SceneError):
    def __init__(self, missing):
        super().__init__('Missing approved component rules: '+', '.join(missing),'component_model',{'missing':missing})


def validate_rule(rule, parameters=None):
    required={'format','component_type','version','model','parameter_bounds','assumptions','unresolved'}
    if set(rule)-required-{'blender_script','provenance','description'} or required-set(rule):
        raise SceneError('Component rule fields: missing='+str(sorted(required-set(rule)))+
                         '; unexpected='+str(sorted(set(rule)-required-{'blender_script','provenance','description'})))
    if rule['format']!=RULE_FORMAT or not NAME.fullmatch(rule['component_type']) or not re.fullmatch(r'\d+\.\d+\.\d+',rule['version']):
        raise SceneError('Invalid rule identity/version')
    model=rule['model']
    if set(model)-{'geoms','behavior','interfaces','physical_parameters','blender_bodies','bodies','joints','equalities'}:
        raise SceneError('Unknown modeling rule fields')
    if model.get('behavior','none') not in BEHAVIORS:raise SceneError('Unimplemented physical behavior kernel')
    names=set()
    if not isinstance(rule['assumptions'],list) or not isinstance(rule['unresolved'],list):
        raise SceneError('Assumptions and unresolved must be lists')
    bodies={'root'};joints=set()
    for b in model.get('bodies',[]):
        if set(b)-{'name','parent','position_m','rpy_rad'} or not NAME.fullmatch(b.get('name','')):
            raise SceneError('Invalid internal body')
        if b['name'] in bodies or b.get('parent','root') not in bodies:raise SceneError('Invalid internal body order/tree')
        bodies.add(b['name'])
    for j in model.get('joints',[]):
        if set(j)-{'name','body','type','pos','axis','range','damping'} or not NAME.fullmatch(j.get('name','')):
            raise SceneError('Invalid internal joint')
        if j['name'] in joints or j['body'] not in bodies or j['type'] not in ('slide','hinge'):
            raise SceneError('Invalid internal joint reference/type')
        joints.add(j['name'])
    for eq in model.get('equalities',[]):
        allowed={'name','type','joint1','joint2','polycoef'} if eq.get('type')=='joint' else {'name','type','body1','body2','anchor'}
        if set(eq)-allowed or not NAME.fullmatch(eq.get('name','')):raise SceneError('Invalid equality fields')
        if eq.get('type')=='joint':
            if eq.get('joint1') not in joints or eq.get('joint2') not in joints:raise SceneError('Unknown equality joint')
        elif eq.get('type') in ('connect','weld'):
            if eq.get('body1') not in bodies or eq.get('body2') not in bodies:raise SceneError('Unknown equality body')
        else:raise SceneError('Unsupported equality type')
    for f in model.get('interfaces',[]):
        if set(f)-{'name','position_m','rpy_rad'} or not NAME.fullmatch(f.get('name','')):
            raise SceneError('Mounting frame must have name, position_m, optional rpy_rad ONLY; got '+str(sorted(f)))
    for g in model.get('geoms',[])+model.get('blender_bodies',[]):
        if set(g)-{'name','type','size','pos','rpy_rad','rgba','mass','density','friction','collision','mesh','body','priority'}:
            raise SceneError('Unknown geometry fields')
        if not NAME.fullmatch(g.get('name','')) or g['name'] in names:raise SceneError('Duplicate/invalid geom name')
        names.add(g['name'])
        if g.get('type') not in ('box','sphere','cylinder','capsule','ellipsoid','mesh'):raise SceneError('Unsupported geometry primitive')
        if g.get('body','root') not in bodies:raise SceneError('Geometry body not defined')
    for name,bounds in rule['parameter_bounds'].items():
        if (not isinstance(bounds,list) or len(bounds)!=2
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in bounds)
                or bounds[0]>bounds[1]):raise SceneError('Invalid parameter bounds '+name)
        if parameters is not None and (name not in parameters or type(parameters[name]) not in (int,float) or not bounds[0]<=parameters[name]<=bounds[1]):
            raise SceneError('Parameter outside approved domain: '+name,'device_description')
    if parameters is not None:
        evaluated=expression(model,parameters)
        from .common import vector
        for f in evaluated.get('interfaces',[]):
            vector(f.get('position_m',[0,0,0]));vector(f.get('rpy_rad',[0,0,0]))
        for b in evaluated.get('bodies',[]):
            vector(b.get('position_m',[0,0,0]));vector(b.get('rpy_rad',[0,0,0]))
        for j in evaluated.get('joints',[]):
            axis=vector(j.get('axis',[0,0,1]));vector(j.get('pos',[0,0,0]))
            if axis.dot(axis)<1e-20 or len(j['range'])!=2 or not j['range'][0]<j['range'][1]:raise SceneError('Invalid internal joint axis/range')
        for g in evaluated.get('geoms',[])+evaluated.get('blender_bodies',[]):
            if g['type']!='mesh' and (not g.get('size') or any(type(n) not in (int,float) or n<=0 for n in g['size'])):
                raise SceneError('Nonpositive geometry size')
            if g['type']!='mesh' and len(g['size'])!={'box':3,'sphere':1,'cylinder':2,'capsule':2,'ellipsoid':3}[g['type']]:
                raise SceneError('Wrong geometry size dimension')
            vector(g.get('pos',[0,0,0]));vector(g.get('rpy_rad',[0,0,0]))
            for key in ('mass','density'):
                if key in g and (type(g[key]) not in (int,float) or not math.isfinite(g[key]) or g[key]<=0):
                    raise SceneError('Invalid physical '+key)
        return evaluated
    return rule


def bundle_files(directory):
    directory=Path(directory).resolve();result={}
    for p in sorted(directory.rglob('*')):
        if p.is_file() and p.name not in ('validation.json','approval.json','review.json'):
            if p.is_symlink():raise SceneError('Symbolic links not allowed in component bundles')
            result[p.relative_to(directory).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def review_digest(directory):
    directory=Path(directory)
    return digest({'files':bundle_files(directory),'validation':read_json(directory/'validation.json')})


class ComponentLibrary:
    def __init__(self,root):self.root=Path(root).resolve()

    def stage(self,rule, *, origin='agent'):
        validate_rule(rule)
        for approved in (self.root/'approved'/rule['component_type']/rule['version']).glob('*/rule.json'):
            if digest(read_json(approved))!=digest(rule):
                raise SceneError('Approved rule versions are immutable; increment the version')
        key=digest(rule)
        path=confined(self.root,'candidates/'+rule['component_type']+'/'+rule['version']+'/'+key)
        if not path.exists():
            write_json(path/'rule.json',rule)
            write_json(path/'origin.json',{'origin':origin,'rule_sha256':key,'created_at':datetime.now(timezone.utc).isoformat()})
        elif read_json(path/'rule.json')!=rule:raise SceneError('Candidate content-address collision')
        return path

    def submit_validation(self,path,report):
        path=Path(path).resolve()
        if not path.is_relative_to(self.root/'candidates'):raise SceneError('Validation target is not an isolated candidate')
        report={**report,'rule_sha256':digest(read_json(path/'rule.json')),'files':bundle_files(path),
                'implementation_sha256':implementation_hashes()}
        write_json(path/'validation.json',report)
        value=review_digest(path)
        write_json(path/'review.json',{'review_sha256':value,'status':'awaiting_human_review',
                    'validation_status':report['status'],'candidate':str(path)})
        return value

    def approve(self,path, *, reviewer, expected_review_sha256, note):
        """Human-facing command only. Never exposed among the agent's tools."""
        path=Path(path).resolve()
        if not path.is_relative_to(self.root/'candidates'):raise SceneError('Only candidates can be approved')
        if not reviewer.strip() or not note.strip():raise SceneError('Reviewer and decision note required')
        current=review_digest(path)
        report=read_json(path/'validation.json');rule=read_json(path/'rule.json')
        if current!=expected_review_sha256 or report.get('files')!=bundle_files(path) or report.get('rule_sha256')!=digest(rule):
            raise SceneError('Review is stale: candidate, assets or checks changed')
        if report.get('implementation_sha256')!=implementation_hashes():raise SceneError('Model implementation changed since validation; revalidate before approval')
        if report.get('status')!='passed' or rule.get('unresolved') or not report.get('checks') or not all(c.get('passed') is True for c in report['checks']):
            raise SceneError('Unresolved/failed candidate cannot be approved')
        for existing in (self.root/'approved'/rule['component_type']/rule['version']).glob('*/approval.json'):
            if existing.parent.name!=current:
                raise SceneError('Approved rule and asset versions are immutable; increment the version')
        target=confined(self.root,'approved/'+rule['component_type']+'/'+rule['version']+'/'+current)
        if target.exists():raise SceneError('This exact approval already exists')
        shutil.copytree(path,target)
        write_json(target/'approval.json',{'reviewer':reviewer,'note':note,'review_sha256':current,
                    'approved_at':datetime.now(timezone.utc).isoformat(),'rule_sha256':digest(rule)})
        return target

    def resolve(self,kind,parameters=None):
        if not NAME.fullmatch(kind):raise SceneError('Invalid component type')
        matches=[]
        for p in (self.root/'approved'/kind).glob('*/*/approval.json'):
            folder=p.parent;approval=read_json(p)
            if approval.get('review_sha256')!=review_digest(folder):raise SceneError('Approved bundle was modified: '+str(folder))
            rule=read_json(folder/'rule.json')
            if read_json(folder/'validation.json').get('implementation_sha256')!=implementation_hashes():
                raise SceneError('Approved modeling implementation changed; a new candidate review is required')
            if approval.get('rule_sha256')!=digest(rule):raise SceneError('Approved rule changed')
            try:validate_rule(rule,parameters)
            except SceneError:continue
            matches.append((tuple(map(int,rule['version'].split('.'))),folder,rule))
        if not matches:raise ApprovalRequired([kind])
        _,folder,rule=max(matches,key=lambda v:v[0])
        return {'rule':rule,'directory':str(folder),'approval':read_json(folder/'approval.json')}
