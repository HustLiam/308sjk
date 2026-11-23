"""Read-only approval recovery and version allocation; never grants approval."""
import copy
from pathlib import Path

from .common import SceneError, digest, read_json
from .rules import ApprovalRequired, bundle_files, implementation_hashes, review_digest, validate_rule


class TaskApprovedLibrary:
    """Resolve this task's reviewed versions, never a newer matching library rule."""
    def __init__(self,library,task):
        self.library=library;self.task=task;self.pins={}
        for item in task.get('component_resolution',[]):
            if item.get('origin')!='approved_library':continue
            prior=self.pins.get(item['type'])
            if prior and prior['rule_sha256']!=item['rule_sha256']:
                raise SceneError('任务中同类型组件的审批版本不一致：'+item['type'])
            self.pins[item['type']]=item
        self.pins.update({item['type']:item for item in task.get('candidates',[])})

    def resolve(self,kind,parameters=None):
        pin=self.pins.get(kind)
        if pin is None:
            if self.task.get('force_regenerate'):raise ApprovalRequired([kind])
            return self.library.resolve(kind,parameters)
        if pin.get('directory'):
            candidate=Path(pin['directory']).resolve()
            if not any(candidate.is_relative_to(self.library.root/group) for group in ('candidates','approved')):
                raise SceneError('组件不属于当前任务库')
            recovered=existing_approval(self.library,candidate)
            folder=Path(recovered['directory']) if recovered else candidate
            if not folder.is_relative_to(self.library.root/'approved'):raise ApprovalRequired([kind])
            expected_review=(recovered or pin).get('review_sha256')
            folders=[folder]
        else:
            expected_review=pin.get('review_sha256')
            folders=[p.parent for p in (self.library.root/'approved'/kind/pin['version']).glob('*/approval.json')]
        for folder in folders:
            rule=read_json(folder/'rule.json');approval=read_json(folder/'approval.json')
            if digest(rule)!=pin['rule_sha256']:continue
            if (rule['component_type']!=kind or approval.get('rule_sha256')!=pin['rule_sha256']
                    or (expected_review and approval.get('review_sha256')!=expected_review)
                    or approval.get('review_sha256')!=review_digest(folder)):
                raise SceneError('任务锁定的已审批组件内容发生变化：'+kind)
            report=read_json(folder/'validation.json')
            if report.get('implementation_sha256')!=implementation_hashes():
                raise SceneError('已审批组件的建模实现发生变化，需要重新检查并审批新版本：'+kind)
            if (report.get('status')!='passed' or rule.get('unresolved') or not report.get('checks')
                    or not all(c.get('passed') is True for c in report['checks'])):
                raise SceneError('任务锁定的组件未通过检查：'+kind)
            validate_rule(rule,parameters)
            return {'rule':rule,'directory':str(folder),'approval':approval}
        raise ApprovalRequired([kind])


def existing_approval(library,directory):
    """Recover an exact human-approved snapshot after report wording changed.

    Only scope/model_requirements descriptions may differ. Rules, assets,
    checks and implementation hashes must still match; failed evidence is never
    replaced by an older passing report.
    """
    path=Path(directory).resolve()
    if not path.is_relative_to(library.root/'candidates'):return None
    if not (path/'validation.json').is_file():return None
    rule=read_json(path/'rule.json')
    approvals=sorted((library.root/'approved'/rule['component_type']/rule['version']).glob('*/approval.json'))
    if not approvals:return None
    report=read_json(path/'validation.json')
    files=bundle_files(path);implementation=implementation_hashes()
    if (rule.get('unresolved') or report.get('status')!='passed' or not report.get('checks')
            or not all(c.get('passed') is True for c in report['checks'])
            or report.get('files')!=files or report.get('rule_sha256')!=digest(rule)
            or report.get('implementation_sha256')!=implementation):return None
    evidence=lambda r:{k:v for k,v in r.items() if k not in ('scope','model_requirements')}
    for approval_path in approvals:
        folder=approval_path.parent;approval=read_json(approval_path)
        approved_report=read_json(folder/'validation.json')
        if (approval.get('rule_sha256')!=digest(rule) or read_json(folder/'rule.json')!=rule
                or bundle_files(folder)!=files or evidence(approved_report)!=evidence(report)
                or approval.get('review_sha256')!=review_digest(folder)):continue
        return {'directory':str(folder),'review_sha256':approval['review_sha256'],
                'rule_sha256':digest(rule),'validation_status':'passed','approval':'approved'}
    return None


def candidate_version(library,rule, *, force_new=False):
    """Allocate a unique candidate version, retaining all modeling content."""
    if not force_new:
        copies=list((library.root/'approved'/rule['component_type']/rule['version']).glob('*/rule.json'))
        if not any(digest(read_json(p))!=digest(rule) for p in copies):return rule,None
    versions=[tuple(map(int,rule['version'].split('.')))]
    occupied=[]
    for group in ('approved','candidates'):
        for folder in (library.root/group/rule['component_type']).glob('*'):
            try:version=tuple(map(int,folder.name.split('.')))
            except ValueError:continue
            if len(version)==3:versions.append(version);occupied.append(version)
    if force_new and (not occupied or versions[0]>max(occupied)):return rule,None
    major,minor,patch=max(versions);revised=copy.deepcopy(rule)
    revised['version']=f'{major}.{minor}.{patch+1}'
    return revised,{'component_type':rule['component_type'],'from_version':rule['version'],
                    'to_version':revised['version'],'reason':'full regeneration requires a new candidate directory' if force_new else 'approved rule version is immutable',
                    'original_rule_sha256':digest(rule),'candidate_rule_sha256':digest(revised)}
