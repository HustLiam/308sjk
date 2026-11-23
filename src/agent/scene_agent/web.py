"""Persistent scene-authoring jobs for the existing local workbench.

Only this human-facing adapter exposes component approval. Neither the model
author nor its repair loop receives approval, PLC generation or deployment tools.
"""
import base64
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import threading
import time
import uuid

from .common import SceneError, confined, digest, read_json, write_json
from .device_ir import parse_device_ir, validate_device_ir, values, facts_digest
from .properties import apply_property_patch, interface_properties
from .rules import ComponentLibrary, validate_rule, review_digest, implementation_hashes
from .workflow import prepare, build_task, check_task, component_report
from .approval_state import existing_approval, TaskApprovedLibrary
from .inventory import component_inventory
from ..generation_control import GenerationControl, GenerationCancelled
from .requirements import DEFAULT_REQUEST, MODEL_REQUIREMENTS

IDENT=re.compile(r'[0-9a-f]{32}\Z')
DEFAULT_APPROVAL_NOTE='用户已确认核对当前组件版本的参数、建模近似、预览与检查报告；未填写补充说明。'


class SceneWebError(SceneError):
    def __init__(self,message,status=400,layer='device_description'):
        super().__init__(message,layer);self.status=status


class SceneWorkbench:
    def __init__(self,root,author_factory=None,*,verification_profile='fixed_model_io/1'):
        if verification_profile not in ('fixed_model_io/1','legacy'):raise ValueError('Unknown verification profile')
        self.verification_profile=verification_profile
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.library=ComponentLibrary(self.root/'component_library')
        self.author_factory=author_factory or self._default_author
        self.lock=threading.RLock();self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='device-scene')
        self.active=None;self.control=None;self.geometry_cache={};self.joint_previews={};self.mesh_buffers={}
        self.assembly_previews={}
        for p in self.root.glob('tasks/*/web.json'):
            record=read_json(p)
            if record.get('busy'):
                record.update(busy=False,phase='interrupted',error={'layer':'component_model','message':'服务重启，上次操作已中断；原产物保留，可重新构建。'})
                record['revision']+=1;write_json(p,record)

    @staticmethod
    def _default_author(control):
        from ..client import BigModelClient
        from ..config import get_api_key
        from .author import SceneGenerationAgent
        if not get_api_key():raise SceneWebError('尚未配置模型，请配置后重试。')
        client=BigModelClient(get_api_key());client.control=control
        return SceneGenerationAgent(client)

    def close(self):
        with self.lock:
            if self.control:self.control.request()
        self.executor.shutdown(wait=True)
        for _, preview in self.joint_previews.values():
            preview.close()
        self.joint_previews.clear()
        self.assembly_previews.clear()
        self.mesh_buffers.clear()

    def directory(self,ident):
        if not isinstance(ident,str) or not IDENT.fullmatch(ident):raise SceneWebError('场景任务不存在',404)
        return confined(self.root,'tasks/'+ident)

    def _record(self,ident):
        path=self.directory(ident)/'web.json'
        if not path.is_file():raise SceneWebError('场景任务不存在',404)
        return read_json(path)

    def _save(self,record):
        record['revision']+=1;record['updated_at']=time.time()
        path=self.directory(record['id'])/'web.json';temporary=path.with_suffix('.tmp')
        write_json(temporary,record);temporary.replace(path)

    def _update(self,ident,**fields):
        with self.lock:
            record=self._record(ident);record.update(fields);self._save(record)

    def _log(self,ident,message):
        with self.lock:
            if self.control:self.control.check()
            record=self._record(ident);record['logs'].append({'time':time.time(),'message':message})
            record['logs']=record['logs'][-800:];record['stage']=message;self._save(record)

    def assembly_publisher(self, ident):
        from .assembly_preview import AssemblyPreview
        preview = AssemblyPreview(ident)
        with self.lock:
            self.assembly_previews = {ident: preview}
        def publish(report, nodes, meshes):
            if self.control:self.control.check()
            preview.publish(report, nodes, meshes)
        return publish

    def assembly_view(self, ident):
        with self.lock:
            self._record(ident)
            preview = self.assembly_previews.get(ident)
            result = preview.read() if preview else None
            if result is None:raise SceneWebError('装配预览尚未就绪',404)
            return result

    def assembly_mesh(self, ident, token, mesh):
        with self.lock:
            self._record(ident)
            preview = self.assembly_previews.get(ident)
            if preview is None:raise SceneWebError('装配预览已释放',404)
            try:return preview.mesh(token, mesh)
            except KeyError:raise SceneWebError('装配预览版本已过期',409)

    def _editable(self,ident,revision):
        record=self._record(ident)
        if record.get('resource_package'):
            raise SceneWebError('资源包场景请通过 AML 和源资源修改后重新导入；当前尚不能执行生成、冻结或 IO 测试',409)
        if type(revision) is not int or record['revision']!=revision:raise SceneWebError('场景版本已更新，请刷新后再操作',409)
        if self.active or record['busy']:raise SceneWebError('设备场景正在处理，请等待完成或停止当前任务',409)
        return record

    def _start(self,record,phase,function):
        if self.active:raise SceneWebError('另一个设备场景正在处理，请稍后重试',409)
        ident=record['id'];self.active=ident;self.control=GenerationControl()
        record.update(busy=True,phase=phase,stage='等待执行',error=None,started_at=time.time(),finished_at=None)
        self._save(record)
        self.executor.submit(self._work,ident,function)

    def _work(self,ident,function):
        phase='ready';error=None
        try:
            function();self.control.check()
        except GenerationCancelled:
            phase='cancelled';error={'layer':'component_model','message':'已停止，候选与检查记录保留。'}
        except Exception as exc:
            phase='failed';error={'layer':getattr(exc,'layer','component_model'),'message':str(exc),
                                  'details':getattr(exc,'details',{})}
            write_json(self.directory(ident)/'operation_error.json',error)
        finally:
            with self.lock:
                record=self._record(ident);record.update(busy=False,phase=phase,error=error,finished_at=time.time())
                self._save(record);self.active=None;self.control=None

    def upload(self,data):
        """Parse a new source and inventory all types for fresh authoring; no LLM."""
        if data.get('source_task') or not data.get('aml'):raise SceneWebError('请上传新的 AML 文件')
        return self.create(data,parse_only=True)

    def import_package(self,data):
        from .package_import import import_package
        from .resource_workflow import persist_source, restore_bindings
        path=None;content=None;source_record=None
        if data.get('source_task'):
            source_record=self._record(data['source_task'])
            if not source_record.get('resource_package'):raise SceneWebError('请选择资源装配任务')
            source_report=read_json(self.directory(source_record['id'])/source_record['scene_kind']/'package_manifest.json')
            path=self.directory(source_record['id'])/'package_source'/Path(source_report['source']['file']).name
            if not path.is_file():
                path=self.directory(source_record['id'])/source_record['scene_kind']/'source'/Path(source_report['source']['file']).name
        elif data.get('aml'):
            upload=data['aml'];name=Path(str(upload.get('name','')).replace('\\','/')).name
            if not name.lower().endswith('.aml'):raise SceneWebError('请选择 AML 文件')
            try:content=base64.b64decode(upload.get('data',''),validate=True)
            except (ValueError,TypeError) as exc:raise SceneWebError('AML 上传数据无效') from exc
            if not content or len(content)>2*1024*1024:raise SceneWebError('AML 文件须非空且不超过2 MiB')
        else:
            path=Path(str(data.get('path',''))).expanduser().resolve()
        if path is not None and (not path.is_file() or path.suffix.lower()!='.aml'):raise SceneWebError('请输入后端主机上的 AML 文件完整路径，关联资源须保留在同一目录中')
        aliases=data.get('aliases') or {}
        if not isinstance(aliases,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in aliases.items()):
            raise SceneWebError('资源路径修正必须是字符串映射')
        with self.lock:
            if self.active:raise SceneWebError('另一个设备场景正在处理，请稍后重试',409)
            ident=uuid.uuid4().hex;folder=self.directory(ident);folder.mkdir(parents=True)
            if content is not None:
                source=folder/'upload_source';source.mkdir();path=source/name;path.write_bytes(content)
            if source_record:
                aliases=source_record.get('resource_aliases') or {
                    r['uri'].split('#')[0].removeprefix('file:///'):r['alias']
                    for r in source_report.get('references',[]) if r.get('alias')}
            (folder/'input.aml').write_bytes(path.read_bytes())
            record={'id':ident,'revision':0,'name':path.name,'created_at':time.time(),'parent':None,
                    'request':'按 AML 引用导入组件并装配','method':'resource_import','resource_package':True,
                    'busy':False,'phase':'draft','logs':[],'scene_kind':'imported_scene','error':None,'preview_revision':0,
                    'resource_aliases':aliases,'generation_policy':'resource_first_user_selected_missing_geometry'}
            write_json(folder/'web.json',record)
            def run():
                result=import_package(path,folder/'imported_scene',aliases=aliases,progress=lambda message:self._log(ident,message),
                                      preview=self.assembly_publisher(ident))
                persist_source(folder,folder/'imported_scene')
                choices=restore_bindings(self,folder,result)
                if choices:
                    name='imported_scene_bound'
                    result=import_package(folder/'package_source'/path.name,folder/name,aliases=aliases,resolutions=choices,
                                          progress=lambda message:self._log(ident,message),preview=self.assembly_publisher(ident))
                    write_json(folder/'resource_choices.json',choices)
                    self._update(ident,scene_kind=name)
                self._update(ident,preview_revision=1)
                if result.get('assembly',{}).get('compiled') and not result['assembly'].get('actual_geoms',0):
                    self._log(ident,'AML 已解析，但未加载三维几何；请通过原始 AML 完整路径导入关联资源，或在检查结果中补充模型')
                else:
                    self._log(ident,'资源导入完成：'+result['status']+'；缺项请查看检查结果')
            self._start(record,'importing',run)
            return self.state(ident)

    def resolve_resource(self,data):
        from .resource_workflow import resolve
        return resolve(self,data)

    def create(self,data, *, parse_only=False):
        source_ir=None;source_parent=None
        request=str(data.get('request') or (DEFAULT_REQUEST if parse_only else '')).strip()
        if not request or len(request)>12000:raise SceneWebError('请输入设备场景需求（最多12000字）')
        method=data.get('method','agent')
        if method!='agent' or data.get('example'):raise SceneWebError('请上传 AML，由 Agent 生成缺失组件；参考场景入口已移除')
        if data.get('source_task') and data.get('aml'):raise SceneWebError('新上传的 AML 不能同时复用旧任务设备快照')
        if data.get('source_task'):
            source=self._record(data['source_task']);filename=source['name']
            content=(self.directory(source['id'])/'input.aml').read_bytes()
            source_ir=read_json(self.directory(source['id'])/'device_ir.json')
            source_parent=source['id']
        else:
            upload=data.get('aml') or {};filename=str(upload.get('name',''))
            if not filename.lower().endswith('.aml'):raise SceneWebError('请选择 AML 文件')
            try:content=base64.b64decode(upload.get('data',''),validate=True)
            except (ValueError,TypeError) as exc:raise SceneWebError('AML 上传数据无效') from exc
            if not content or len(content)>2*1024*1024:raise SceneWebError('AML 文件须非空且不超过2 MiB')
        with self.lock:
            if self.active:raise SceneWebError('另一个设备场景正在处理，请稍后重试',409)
            ident=uuid.uuid4().hex;folder=self.directory(ident);folder.mkdir(parents=True)
            (folder/'input.aml').write_bytes(content)
            try:
                # Reparse the exact source bytes on every new generation. Only
                # explicit, recorded local edits may overlay that source.
                ir=parse_device_ir(folder/'input.aml')
                if source_ir is not None and source_ir.get('changes'):
                    if source_ir['source']['sha256']!=ir['source']['sha256']:raise SceneWebError('原 AML 与局部修改的设备来源不一致')
                    ir=validate_device_ir(copy.deepcopy(source_ir))
            except Exception:
                # Invalid uploads never become visible generation jobs.
                (folder/'input.aml').unlink();folder.rmdir();raise
            write_json(folder/'device_ir.json',ir)
            record={'id':ident,'revision':0,'name':Path(filename.replace('\\','/')).name,'created_at':time.time(),
                'parent':source_parent,'request':request,'method':method,'busy':False,'phase':'draft','logs':[],
                'scene_kind':'candidate_scene','error':None,'preview_revision':0,
                'parse_only':parse_only,'source_sha256':ir['source']['sha256'],
                'generation_policy':'full_regeneration_no_reuse'}
            write_json(folder/'web.json',record)
            if parse_only:
                def inspect_source():
                    prepare(ir,request,folder,self.library,force_regenerate=True,verification_profile=self.verification_profile,progress=lambda message:self._log(ident,message))
                    self._log(ident,'AML 已重新解析；全部组件等待 Agent 重新生成规则、几何与装配')
                self._start(record,'parsing',inspect_source)
            else:self._start(record,'generating',lambda:self._generate(ident,ir))
            return self.state(ident)

    def _generate(self,ident,ir,reusable=None):
        record=self._record(ident);folder=self.directory(ident)
        fresh=record.get('generation_policy')=='full_regeneration_no_reuse'
        self._log(ident,'完整重生成：重新解析 AML，由 Agent 生成全部规则；不使用旧候选、已审批模型或场景'
                  if fresh else '重新解析 AML 并核对 IO；逐项查询已审批组件库，不复用旧任务候选或场景'
                  if not reusable else '核对局部修改与 AML IO；重新验证未改动组件规则')
        factory=self.author_factory;control=self.control
        class LazyAuthor:
            instance=None
            @staticmethod
            def verification_parameters(ir):
                from .verification import parameters
                return parameters(ir)
            def check_cancelled(self):control.check()
            def get(self):
                if self.instance is None:self.instance=factory(control)
                return self.instance
            def draft_rules(self,*args):return self.get().draft_rules(*args)
            def repair_candidate(self,*args):return self.get().repair_candidate(*args)
            def draft_test_sequences(self,ir,plan,evidence):
                method=getattr(self.get(),'draft_test_sequences',None)
                return method(ir,plan,evidence) if method else plan
            def draft_collision_free_tests(self,ir,metadata,xml,plan,evidence):
                method=getattr(self.get(),'draft_collision_free_tests',None)
                return method(ir,metadata,xml,plan,evidence) if method else self.draft_test_sequences(ir,plan,evidence)
        author=LazyAuthor()
        task=prepare(ir,record['request'],folder,self.library,author=author,
            reusable_candidates=reusable,force_regenerate=fresh,verification_profile=self.verification_profile,progress=lambda message:self._log(ident,message))
        self.control.check()
        if task['pending']:
            self._log(ident,'设备描述存在待确认项，请先修订 AML');return
        if any(c['validation_status']!='passed' for c in task['candidates']):
            if not all(self.component_state(ident,c['type'])['geometry_ready'] for c in task['candidates']):
                self._log(ident,'部分组件未通过几何检查；候选、失败报告和修复历史已保存');return
            self._log(ident,'组件几何检查通过但仍有未解决问题，仅构建静态候选预览')
        self._build(ident)

    def _build(self,ident,formal=False):
        folder=self.directory(ident);self._log(ident,'正在对齐安装接口并构建 MuJoCo 模型')
        task=read_json(folder/'task.json')
        states=[self.component_state(ident,c['type']) for c in task.get('candidates',[])]
        if not formal and not all(c['geometry_ready'] for c in states):raise SceneWebError('候选几何检查失败或版本已过期，请先重查组件',409,'component_model')
        geometry_only=not formal and any(c['validation_status']!='passed' for c in states)
        build_task(folder,self.library,isolated_candidates=not formal,geometry_only=geometry_only)
        kind='scene' if formal else 'candidate_scene'
        self._update(ident,scene_kind=kind,preview_revision=self._record(ident)['preview_revision']+1)
        if geometry_only:
            self._log(ident,'候选几何与运动副已生成；组件行为仍有未解决问题，未运行整机动态检查、未冻结、未运行 PLC')
            return
        self._log(ident,'装配已构建，正在运行独立设备行为检查')
        report=check_task(folder,isolated_candidates=not formal,capture_replay=True)
        self._log(ident,'独立设备检查'+('通过' if report['status']=='passed' else '未通过')+'；PLC 验收尚未运行')

    def operate(self,ident,revision,action):
        with self.lock:
            record=self._editable(ident,revision)
            if action=='freeze' and not self.state(ident)['all_approved']:
                raise SceneWebError('请先逐项审批全部组件，再冻结设备模型',409,'component_model')
            if action=='freeze' and not self.state(ident)['can_build']:
                raise SceneWebError('当前组件检查未通过，请先修复并重新检查组件',409,'component_model')
            if action=='check' and self.state(ident)['geometry_only']:
                raise SceneWebError('当前仅为候选几何预览；组件行为尚未通过检查，不能进行整机验收',409,'component_model')
            if action not in ('build','check','freeze','revalidate'):raise SceneWebError('未知场景操作')
            def run():
                folder=self.directory(ident)
                if action=='revalidate':
                    self._revalidate(ident)
                elif action=='freeze':
                    self._build(ident,formal=True);self.control.check()
                    from runtime.device_scene import DeviceRuntime
                    kind=folder/'scene';meta=read_json(kind/'scene.physical.json')
                    DeviceRuntime(meta,(kind/'scene.xml').read_text(encoding='utf-8')).freeze(kind,read_json(kind/'behavior_checks.json'))
                    task=read_json(folder/'task.json');task['scene'].update(model_frozen=True,status='model_frozen');write_json(folder/'task.json',task)
                    self._log(ident,'设备模型已冻结，可用于后续 PLC 验收；本次没有部署或运行 PLC')
                elif action=='build':self._build(ident)
                else:
                    self._log(ident,'正在重新运行独立设备行为检查')
                    report=check_task(folder,isolated_candidates=record['scene_kind']=='candidate_scene',capture_replay=True)
                    self._log(ident,'设备行为检查'+('通过' if report['status']=='passed' else '未通过'))
                self._update(ident,preview_revision=self._record(ident)['preview_revision']+1)
            self._start(record,action,run);return self.state(ident)

    def run_tests(self,data):
        ident=data.get('task')
        with self.lock:
            record=self._editable(ident,data.get('revision'));state=self.state(ident)
            if not state['scene_ready'] or state['geometry_only'] or not state['can_build']:
                raise SceneWebError('请先生成可执行且检查有效的设备模型',409,'simulation_test')
            selected=data.get('test_ids')
            available={m['id'] for m in state['test_modules']}
            if (not isinstance(selected,list) or not selected or any(not isinstance(s,str) or s not in available for s in selected)
                    or len(set(selected))!=len(selected)):
                raise SceneWebError('请选择当前设备的有效测试模块',400,'simulation_test')
            def run():
                from .workflow import test_task
                folder=self.directory(ident);scene=folder/record['scene_kind']
                self._log(ident,'正在运行所选 '+str(len(selected))+' 个测试模块；每项使用独立模型状态')
                report=test_task(folder,isolated_candidates=record['scene_kind']=='candidate_scene',selected=selected,cancelled=self.control.check)
                self._log(ident,'所选测试'+('通过' if report['status']=='passed' else '存在失败或未覆盖项')+'；结果与各模块回放已保存')
            self._start(record,'testing',run);return self.state(ident)

    def plc_test(self,data):
        """Connect an already deployed, identity-checked isolated PLC harness."""
        ident=data.get('task')
        with self.lock:
            record=self._editable(ident,data.get('revision'));folder=self.directory(ident)
            from .verification import enabled
            if not enabled(read_json(folder/'device_ir.json')):raise SceneWebError('此任务使用旧校验配置，请重新生成场景',409)
            if not self.state(ident)['scene_ready']:raise SceneWebError('请先构建隔离模型',409)
            config={'host':data.get('host'),'port':data.get('port')}
            if not isinstance(config['host'],str) or not config['host'].strip() or type(config['port']) is not int or not 1<=config['port']<=65535:
                raise SceneWebError('请输入隔离 OpenPLC 的主机和端口')
            write_json(folder/'plc_test_connection.json',config)
            def run():
                self._log(ident,'正在核对 OpenPLC 测试程序身份并运行固定结构与双向 IO 校验')
                report=check_task(folder,isolated_candidates=record['scene_kind']=='candidate_scene',plc_config=config,cancelled=self.control.check)
                self._log(ident,'固定模型与 IO 校验：'+report['status'])
            self._start(record,'testing',run);return self.state(ident)

    def _revalidate(self,ident):
        from .validation import validate_candidate
        from .author import SceneGenerationAgent
        folder=self.directory(ident);task=read_json(folder/'task.json');ir=read_json(folder/'device_ir.json')
        for item in task['candidates']:
            resolved,path=self._component(ident,item['type']);rule=read_json(path/'rule.json')
            approved=self.component_state(ident,item['type'])['approved']
            # Approved reports/assets are immutable. Run repeat checks in a
            # separate candidate library and retain the original approval token.
            validation_library=self.library;validation_path=path
            if approved:
                validation_library=ComponentLibrary(folder/'rechecks'/uuid.uuid4().hex/item['type'])
                validation_path=validation_library.stage(rule,origin='read_only_approved_recheck')
            self._log(ident,'重新检查组件：'+item['type']);reports=[]
            for component in ir['components']:
                if component['type']==item['type']:
                    try:
                        SceneGenerationAgent._validate_candidate(rule,[SceneGenerationAgent._component_summary(component,ir)])
                        report,_=validate_candidate(validation_library,validation_path,values(component))
                    except (SceneError,ValueError,KeyError,OSError) as exc:
                        report={'status':'failed','checks':[{'name':'candidate_build','passed':False,'detail':str(exc)}],
                                'parameters':values(component),'unresolved':[str(exc)],'layer':getattr(exc,'layer','component_model')}
                    reports.append(report)
            combined=component_report(reports)
            write_json(folder/'rechecks'/(item['type']+'.json'),combined)
            if approved:
                item.update(resolved)
                item['revalidation_status']=combined['status']
            else:
                item['review_sha256']=self.library.submit_validation(path,combined)
                item['validation_status']=combined['status']
            write_json(folder/'task.json',task)
        self._log(ident,'组件复查完成，请核对当前预览和审批版本')

    def stop(self,ident):
        with self.lock:
            record=self._record(ident)
            if self.active!=ident or not self.control:raise SceneWebError('该任务当前没有运行中的操作',409)
            self.control.request();record.update(phase='stopping',stage='正在停止；当前几何导出结束后保留结果并退出')
            self._save(record);return self.state(ident)

    def edit(self,data):
        ident=data.get('task');request=str(data.get('request') or '').strip();scope=data.get('editable_ids',[])
        if not request or len(request)>12000 or not isinstance(scope,list) or not scope:raise SceneWebError('请选择要修改的组件，并填写修改要求')
        with self.lock:
            parent=self._editable(ident,data.get('revision'));original=self.directory(ident);ir=read_json(original/'device_ir.json')
            if not set(scope).issubset({c['id'] for c in ir['components']}):raise SceneWebError('修改范围包含未知组件')
            child=uuid.uuid4().hex;folder=self.directory(child);folder.mkdir(parents=True)
            record={**copy.deepcopy(parent),'id':child,'parent':ident,'revision':0,'created_at':time.time(),
                'phase':'editing','logs':[],'request':request,'scene_kind':'candidate_scene','preview_revision':0,
                'generation_policy':'local_edit_preserve_unaffected'}
            write_json(folder/'web.json',record);(folder/'input.aml').write_bytes((original/'input.aml').read_bytes())
            def run():
                self._log(child,'只修改已选组件，其他设备事实和 AML IO 保持不变')
                if data.get('patch') is not None:
                    patch=data['patch']
                    if set(patch.get('editable_ids',[]))!=set(scope):raise SceneWebError('补丁修改范围与已选组件不一致')
                    revised=apply_property_patch(ir,patch)
                else:
                    author=self.author_factory(self.control)
                    revised,patch=author.local_edit(ir,request,scope,folder/'model_calls'/'edit')
                write_json(folder/'patch.json',patch);write_json(folder/'device_ir.json',revised)
                task=read_json(original/'task.json') if (original/'task.json').exists() else {}
                self._generate(child,revised,task.get('candidates',[]))
            self._start(record,'editing',run);return self.state(child)

    def approve(self,data):
        ident=data.get('task')
        if data.get('confirmed') is not True:raise SceneWebError('请先核对并确认此组件的参数、近似和检查报告')
        with self.lock:
            record=self._editable(ident,data.get('revision'));folder=self.directory(ident);task=read_json(folder/'task.json')
            item,_=self._component(ident,data.get('component_type'))
            if item['review_sha256']!=data.get('review_sha256'):raise SceneWebError('审批版本已过期，请重新打开组件详情',409)
            if self.component_state(ident,item['type'])['approved']:return self.state(ident)
            self.library.approve(item['directory'],reviewer=str(data.get('reviewer','')),
                expected_review_sha256=item['review_sha256'],note=str(data.get('note') or '').strip() or DEFAULT_APPROVAL_NOTE)
            record['logs'].append({'time':time.time(),'message':'人工已批准组件 '+item['type']+' 的当前版本'})
            self._save(record);return self.state(ident)

    def _component(self,ident,kind):
        folder=self.directory(ident);task=read_json(folder/'task.json')
        item=next((c for c in task.get('candidates',[]) if c['type']==kind),None)
        if not item:raise SceneWebError('组件候选不存在',404)
        candidate=Path(item['directory']).resolve()
        if not any(candidate.is_relative_to(self.library.root/group) for group in ('candidates','approved')):
            raise SceneWebError('组件不属于当前任务库',403)
        snapshot=existing_approval(self.library,candidate)
        if snapshot:item={**item,**snapshot};candidate=Path(snapshot['directory'])
        return item,candidate

    def component_state(self,ident,kind):
        with self.lock:
            item,path=self._component(ident,kind);rule=read_json(path/'rule.json')
            report=read_json(path/'validation.json') if (path/'validation.json').exists() else None
            current=bool(report and report.get('implementation_sha256')==implementation_hashes()
                         and review_digest(path)==item['review_sha256'])
            approved=False
            if current:
                for p in (self.library.root/'approved'/kind/rule['version']).glob('*/approval.json'):
                    approval=read_json(p)
                    if approval.get('review_sha256')==item['review_sha256'] and review_digest(p.parent)==item['review_sha256']:
                        approved=True
            recheck=self.directory(ident)/'rechecks'/(kind+'.json')
            return {**item,'version':rule['version'],'rule':rule,'validation':report,'review_current':current,
                    'last_revalidation':read_json(recheck) if recheck.is_file() else None,
                    'geometry_ready':bool(current and item.get('revalidation_status')!='failed' and report.get('checks') and all(c.get('passed') is True for c in report['checks'])),
                    'approved':approved,'can_approve':current and not approved and report['status']=='passed' and not rule['unresolved'],
                    'has_preview':(path/'preview.png').exists() or (path/'geometry/preview.png').exists()}

    def listing(self):
        with self.lock:
            records=[read_json(p) for p in self.root.glob('tasks/*/web.json')]
            return {'active':self.active,'tasks':[{k:r.get(k) for k in ('id','name','phase','created_at','parent','request','busy','revision')}
                    for r in sorted(records,key=lambda r:r['created_at'],reverse=True)],'default_request':DEFAULT_REQUEST}

    def delete_history(self,data):
        """Remove reviewed task caches; component bundles and PLC sessions remain."""
        import shutil
        ids=data.get('task_ids');versions=data.get('revisions')
        if (not isinstance(ids,list) or not ids or len(ids)>1000 or any(not isinstance(i,str) or not IDENT.fullmatch(i) for i in ids)
                or len(ids)!=len(set(ids)) or not isinstance(versions,dict) or set(versions)!=set(ids)):
            raise SceneWebError('请选择要删除的场景记录及其当前版本')
        with self.lock:
            if self.active:raise SceneWebError('场景正在处理，请结束当前操作后再清理历史',409)
            paths=[]
            for ident in ids:
                self._editable(ident,versions[ident]);path=self.directory(ident)
                if path.parent!=(self.root/'tasks').resolve() or path.name!=ident:
                    raise SceneWebError('场景目录不属于当前任务',403)
                paths.append((ident,path))
            staging=confined(self.root,'.history-trash/'+uuid.uuid4().hex);staging.mkdir(parents=True)
            moved=[]
            try:
                for ident,path in paths:
                    path.rename(staging/ident);moved.append((ident,path))
            except OSError:
                for ident,path in reversed(moved):(staging/ident).rename(path)
                staging.rmdir();raise
            self.geometry_cache={k:v for k,v in self.geometry_cache.items() if k[0] not in ids}
            for ident in ids:
                if ident in self.joint_previews:self.joint_previews[ident][1].close()
            self.joint_previews={k:v for k,v in self.joint_previews.items() if k not in ids}
            self.mesh_buffers={k:v for k,v in self.mesh_buffers.items() if k[0] not in ids}
            reset=self.root/'cache_reset.json'
            retired=set(read_json(reset).get('retired_tasks',[])) if reset.is_file() else set()
            write_json(reset,{'retired_tasks':sorted(retired|set(ids)),'updated_at':time.time(),'source':'user_selected_history_cleanup'})
            cache_error=None
            try:shutil.rmtree(staging)
            except OSError as exc:cache_error=str(exc)
            return {'retired_tasks':ids,'cache_status':'pending' if cache_error else 'cleared',
                    'cache_error':cache_error,'listing':self.listing()}

    def state(self,ident):
        with self.lock:
            record=self._record(ident);folder=self.directory(ident)
            if record.get('resource_package'):
                from .package_web import state
                cached=self.joint_previews.get(ident)
                preview=cached[1] if cached and cached[0]==self._preview_signature(folder/record['scene_kind']) else None
                result=state(copy.deepcopy(record),folder,bool(self.active and self.active!=ident),preview)
                from .resource_resolution import ResourceLibrary
                result['resource_library']=ResourceLibrary(self.root/'resource_library').entries()
                assembly=self.assembly_previews.get(ident)
                if assembly and assembly.read():
                    result['assembly_preview']={'token':assembly.token,'revision':assembly.revision}
                return result
            result={**copy.deepcopy(record),'occupied':bool(self.active and self.active!=ident),'plc_acceptance':'not_run',
                    'model_requirements':copy.deepcopy(MODEL_REQUIREMENTS)}
            ir=read_json(folder/'device_ir.json') if (folder/'device_ir.json').exists() else None
            from .requirements import for_device
            result['model_requirements']=copy.deepcopy(for_device(ir or {}))
            task=read_json(folder/'task.json') if (folder/'task.json').exists() else None
            result.update(device_ir=ir,candidates=[],reports={},scene_ready=False,all_approved=False,model_frozen=False)
            result['interface_properties']=interface_properties(ir) if ir else {}
            result['component_counts']=component_inventory(ir) if ir else {'leaf':0,'composite':0,'total':0,'joints':0,'leaf_ids':[]}
            result['source_sha256']=ir['source']['sha256'] if ir else None
            result['device_ir_sha256']=facts_digest(ir) if ir else None
            if task:
                result['task_status']=task['status'];result['pending']=task.get('pending',[])
                result['component_resolution']=task.get('component_resolution',[])
                result['missing_types']=task.get('missing_types',[])
                result['candidates']=[self.component_state(ident,c['type']) for c in task.get('candidates',[])]
                # Components already resolved from approved rules are not candidates.
                approved_types={c['type'] for c in result['candidates'] if c['approved']}
                all_approved=not any(not c['approved'] for c in result['candidates']) and not (set(task.get('missing_types',[]))-approved_types)
                if ir and all_approved:
                    try:
                        approved_library=TaskApprovedLibrary(self.library,task)
                        for c in ir['components']:
                            if c['id'] not in {j['id'] for j in ir['joints']}:approved_library.resolve(c['type'],values(c))
                    except SceneError:all_approved=False
                result['all_approved']=all_approved and not task.get('pending') and bool(ir)
            scene=folder/record['scene_kind']
            result['scene_ready']=(scene/'scene.physical.json').exists() and (scene/'scene.xml').exists()
            result['can_build']=bool(task and task['status'] in ('awaiting_component_approval','approved_components_available')
                                     and not task.get('pending') and all(c['geometry_ready'] for c in result['candidates']))
            result['geometry_only']=bool(task and task.get('scene',{}).get('geometry_only') and record['scene_kind']=='candidate_scene')
            result['model_xml_sha256']=read_json(scene/'scene.physical.json')['model_xml_sha256'] if result['scene_ready'] else None
            for key in ('assembly','behavior'):
                path=scene/(key+'_checks.json')
                if path.exists():result['reports'][key]=read_json(path)
            result['model_frozen']=(scene/'model.freeze.json').is_file() and record['scene_kind']=='scene'
            result['has_replay']=(scene/'behavior_replay.json').exists()
            plan=read_json(folder/'simulation_tests.json') if (folder/'simulation_tests.json').exists() else None
            result['test_modules']=[];result['test_plan']=plan
            checker_sha=None
            if plan:
                import hashlib
                from . import simulation_tests
                checker_sha=simulation_tests.checker_digest()
                statuses={}
                for prefix in ('','selected_tests/'):
                    path=scene/prefix/'simulation_test_results.json'
                    if not path.is_file() or not result['scene_ready'] or result['geometry_only']:continue
                    report=read_json(path);meta=read_json(scene/'scene.physical.json')
                    from .simulation_tests import FORMAT
                    if (report.get('format')!=FORMAT or report.get('scene_sha256')!=result['model_xml_sha256']
                            or report.get('device_ir_sha256')!=facts_digest(ir) or report.get('definition_sha256')!=digest(meta)
                            or report.get('plan_sha256')!=digest(plan) or report.get('checker_sha256')!=checker_sha):continue
                    for module in report['modules']:
                        statuses[module['id']]={**module,'replay':prefix+module['replay'] if module.get('replay') else None}
                for module in plan['modules']:
                    result['test_modules'].append({**module,'status':'not_run','replay':None,**statuses.get(module['id'],{}),
                        'available':result['scene_ready'] and not result['geometry_only'] and result['can_build']})
            result['test_plan_sha256']=digest(plan) if plan else None
            result['plc_test_connection']=read_json(folder/'plc_test_connection.json') if (folder/'plc_test_connection.json').exists() else None
            result['scene_definition_sha256']=digest(read_json(scene/'scene.physical.json')) if result['scene_ready'] else None
            safety=read_json(folder/'test_safety.json') if (folder/'test_safety.json').exists() else None
            result['test_safety']=safety if safety and result['scene_ready'] and safety.get('model_xml_sha256')==result['model_xml_sha256'] and safety.get('plan_sha256')==result['test_plan_sha256'] and safety.get('checker_sha256')==checker_sha else None
            result['has_preview']=(scene/'preview.png').exists()
            result['files']=[name for name in ('input.aml','device_ir.json','task.json','patch.json','simulation_tests.json','robot_io_rules.json','test_safety.json','verification_parameters.json','io_test.xml','plc_test_contract.json',
                record['scene_kind']+'/scene.xml',record['scene_kind']+'/scene.physical.json',record['scene_kind']+'/io_map.json',
                record['scene_kind']+'/assembly_checks.json',record['scene_kind']+'/behavior_checks.json',record['scene_kind']+'/model.freeze.json',
                record['scene_kind']+'/simulation_test_results.json',record['scene_kind']+'/selected_tests/simulation_test_results.json') if (folder/name).exists()]
            return result

    def file(self,ident,relative,component_type=None):
        if component_type:
            _,root=self._component(ident,component_type)
            if relative=='preview.png':relative='geometry/preview.png' if (root/'geometry/preview.png').exists() else relative
            allowed=relative in {'rule.json','validation.json','preview.png','geometry/preview.png','geometry/component.blend','geometry/component_manifest.json'}
            allowed=allowed or bool(re.fullmatch(r'geometry/bodies/[A-Za-z][A-Za-z0-9_-]{0,63}\.obj',relative or ''))
        else:
            root=self.directory(ident)
            allowed=relative in {'input.aml','device_ir.json','task.json','patch.json','simulation_tests.json','robot_io_rules.json','test_safety.json','verification_parameters.json','io_test.xml','plc_test_contract.json'} or bool(re.fullmatch(
                r'(?:candidate_scene|scene)/(?:scene\.xml|scene\.physical\.json|io_map\.json|assembly_checks\.json|behavior_checks\.json|behavior_replay\.json|model\.freeze\.json|preview\.png|validated_preview\.png|(?:selected_tests/)?(?:simulation_test_results\.json|test_replays/test_[a-f0-9]{20}\.json))',relative or ''))
            if self._record(ident).get('resource_package'):
                from .package_web import FILES
                allowed=relative=='input.aml' or relative in {self._record(ident)['scene_kind']+'/'+f for f in FILES}
        if not allowed:raise SceneWebError('此产物不可下载',403)
        path=confined(root,relative)
        if not path.is_file():raise SceneWebError('产物尚未生成',404)
        return path

    def bundle(self,ident):
        """MJCF downloads include referenced meshes, checks and editable sources."""
        import io
        import zipfile
        with self.lock:
            record=self._record(ident)
            if record['busy']:raise SceneWebError('请等待当前场景操作完成后再下载完整模型包',409)
            folder=self.directory(ident);scene=folder/record['scene_kind'];files={}
            for name in ('input.aml','device_ir.json','task.json','patch.json','simulation_tests.json','robot_io_rules.json','test_safety.json','verification_parameters.json','io_test.xml','plc_test_contract.json'):
                if (folder/name).is_file():files[name]=folder/name
            if not (scene/'scene.xml').is_file():raise SceneWebError('场景尚未构建',404)
            extensions={'.xml','.json','.obj','.blend','.py','.png','.log'}
            if record.get('resource_package'):extensions.update({'.dae','.aml','.xsd'})
            for path in scene.rglob('*'):
                if path.is_file() and path.suffix in extensions:
                    safe=confined(folder,path.relative_to(folder))
                    files[path.relative_to(folder).as_posix()]=safe
            limit=1024 if record.get('resource_package') else 256
            if sum(p.stat().st_size for p in files.values())>limit*1024*1024:
                raise SceneWebError('模型包超过'+str(limit)+' MiB，请使用分项下载')
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
                for name,path in files.items():archive.write(path,name)
                archive.writestr('README.txt',(
                    'AML resource assembly preview\nOpen '+record['scene_kind']+'/scene.xml with meshes/ intact.\n'
                    'Original AML/DAE/XSD/logic snapshots and mapping reports are included.\n'
                    'Web display simplification is not applied to these meshes.\n'
                    'Collision, IO and installation gaps remain; no simulation acceptance or freeze is granted.\n'
                    if record.get('resource_package') else
                    'Device scene model package\nOpen '+record['scene_kind']+'/scene.xml in MuJoCo with its assets directory intact.\n'
                    'This package includes actual generated geometry and model checks. PLC acceptance is not implied.\n'
                    'For DeviceRuntime, rebuild from DeviceIR on the target machine before freezing: manifest paths retain original provenance.\n'))
            return stream.getvalue()

    def _preview_signature(self, folder):
        """Invalidate runtime indices when the model or its source ownership changes."""
        from .plc_preview import profile_signature
        return (profile_signature(),) + tuple((name, p.stat().st_mtime_ns, p.stat().st_size) for name in
                     ('scene.xml', 'assembly_checks.json', 'body_mapping.json', 'package_manifest.json', 'scene.physical.json')
                     if (p := folder/name).is_file())

    def joint_preview(self, ident, client, components, version, *, command=None, metadata=False):
        with self.lock:
            record = self._record(ident)
            if record.get('busy') or self.active:
                raise SceneWebError('模型正在处理，请等待完成后再控制关节', 409)
            folder = self.directory(ident)/record['scene_kind']
            cached = self.joint_previews.get(ident)
            if not cached or cached[0] != self._preview_signature(folder) or cached[1].version != version:
                if cached and cached[0] != self._preview_signature(folder):cached[1].close()
                raise SceneWebError('模型版本已变化，请重新加载关节预览', 409)
            preview = cached[1]
            if command is not None:
                return preview.command(client, components, command.get('action'), command.get('targets'), command.get('duration', 2))
            return preview.snapshot(client, components, metadata=metadata)

    def agent_command(self, data):
        with self.lock:
            ident = data.get('task')
            record = self._record(ident)
            folder = self.directory(ident)/record['scene_kind']
            cached = self.joint_previews.get(ident)
            if self.active or record.get('busy'):
                raise SceneWebError('模型正在处理，请等待完成',409)
            if not cached or cached[0] != self._preview_signature(folder) or cached[1].version != data.get('version'):
                raise SceneWebError('模型版本已变化，请刷新场景后重试',409)
            if not cached[1].agent:
                raise SceneWebError('当前场景没有可用的 Agent 控制器')
            return cached[1].agent.start(data.get('client'), data.get('request'))

    def mesh_asset(self,ident,version,mesh):
        with self.lock:
            record=self._record(ident)
            cached=self.joint_previews.get(ident)
            if not cached or cached[1].version!=version or cached[0]!=self._preview_signature(self.directory(ident)/record['scene_kind']):
                raise SceneWebError('模型版本已变化，请重新加载',409)
            data=self.mesh_buffers.get((ident,version,mesh))
            if data is None:raise SceneWebError('网格不存在',404)
            return data

    def geometry(self,ident):
        """Use the actual compiled MuJoCo model, including exported mesh triangles."""
        from runtime.device_scene import DeviceRuntime
        import mujoco
        import numpy as np
        with self.lock:
            record=self._record(ident);folder=self.directory(ident)/record['scene_kind']
            signature=self._preview_signature(folder)
            if record.get('resource_package'):
                from .package_web import model
                meta=read_json(folder/'assembly_checks.json')
                key=(ident,meta['model_xml_sha256'],digest(meta),signature)
                if key in self.geometry_cache:return self.geometry_cache[key]
                m,d,owners,meta=model(self.directory(ident),record['scene_kind'])
            else:
                meta=read_json(folder/'scene.physical.json');xml=(folder/'scene.xml').read_text(encoding='utf-8')
                key=(ident,meta['model_xml_sha256'],digest(meta),signature)
                if key in self.geometry_cache:return self.geometry_cache[key]
                runtime=DeviceRuntime(meta,xml);m,d=runtime.model,runtime.data
                owners={c['body']:cid for cid,c in meta['components'].items()}
                owners.update({p['body']:p['product_id'] for p in meta['products']})
            geoms=[];positions=[];preview_assets=None;preview_quality=None
            if record.get('resource_package'):
                from .package_preview import assets
                preview_assets,preview_quality=assets(m)
            visual_mesh_bodies={int(m.geom_bodyid[i]) for i in range(m.ngeom)
                if int(m.geom_type[i])==int(mujoco.mjtGeom.mjGEOM_MESH) and not m.geom_contype[i] and not m.geom_conaffinity[i]}
            for i in range(m.ngeom):
                g={'type':int(m.geom_type[i]),'size':m.geom_size[i].tolist(),'color':m.geom_rgba[i].tolist()}
                body=int(m.geom_bodyid[i]);owner=None
                while body:
                    owner=owners.get(mujoco.mj_id2name(m,mujoco.mjtObj.mjOBJ_BODY,body))
                    if owner:break
                    body=int(m.body_parentid[body])
                g['component_id']=owner
                g['collision_proxy']=int(m.geom_bodyid[i]) in visual_mesh_bodies and bool(m.geom_contype[i] or m.geom_conaffinity[i])
                g['visual_mesh']=int(m.geom_bodyid[i]) in visual_mesh_bodies and g['type']==int(mujoco.mjtGeom.mjGEOM_MESH)
                if g['type']==int(mujoco.mjtGeom.mjGEOM_MESH):
                    mesh=int(m.geom_dataid[i]);start=int(m.mesh_vertadr[mesh]);count=int(m.mesh_vertnum[mesh])
                    verts=m.mesh_vert[start:start+count];fstart=int(m.mesh_faceadr[mesh]);fcount=int(m.mesh_facenum[mesh])
                    if preview_assets is not None:g['mesh_asset']=str(mesh)
                    else:
                        if fcount>200000:raise SceneWebError('网格超过当前预览预算，请查看 Blender 预览',400,'component_model')
                        g['vertices']=verts[m.mesh_face[fstart:fstart+fcount]].reshape(-1).tolist()
                geoms.append(g)
                name=mujoco.mj_id2name(m,mujoco.mjtObj.mjOBJ_GEOM,i) or ''
                if g['type']!=0 and not name.startswith('item_'):
                    if record.get('resource_package'):
                        rotation=d.geom_xmat[i].reshape(3,3)
                        center=d.geom_xpos[i]+rotation@m.geom_aabb[i,:3]
                        half=np.abs(rotation)@m.geom_aabb[i,3:]
                        positions.extend([(center-half).tolist(),(center+half).tolist()])
                    else:positions.append(d.geom_xpos[i].tolist())
            p=np.asarray(positions or [[0,0,0]],dtype=float);low=p.min(axis=0);high=p.max(axis=0)
            from .mechanics import describe_mechanism, body_transforms
            ir_path=self.directory(ident)/'device_ir.json'
            ir=read_json(ir_path) if ir_path.is_file() else None
            result={'center':((low+high)/2).tolist(),'span':max(float(np.max(high-low)),.4),
                'geoms':geoms,'transforms':[list(map(float,p))+list(map(float,r.ravel())) for p,r in zip(d.geom_xpos,d.geom_xmat)],
                'mechanism':describe_mechanism(ir,m) if ir else {'frames':[],'joints':[]},'body_transforms':body_transforms(d),
                'source':'mujoco_compiled_device_model','candidate':meta['candidate']}
            if preview_assets is not None:result.update(mesh_assets=preview_assets,preview_quality=preview_quality)
            from .joint_preview import JointPreview
            from .plc_preview import load_profile
            if record.get('resource_package'):
                components=read_json(folder/'package_manifest.json')['components']
                records=read_json(folder/'assembly_checks.json').get('joint_records', [])
            else:
                components=(ir or {}).get('components', [])
                records=[]
            for _, old_preview in self.joint_previews.values():old_preview.close()
            preview=JointPreview(m,d,owners,components,records,include_bodies=not record.get('resource_package'),
                                 plc_config=load_profile() if record.get('resource_package') else None)
            from .agent_control import AgentControl
            preview.agent=AgentControl(preview,folder,owners,records,self.author_factory)
            self.joint_previews={ident:(signature,preview)}
            result['joint_preview_version']=preview.version
            result['component_children']=preview.mechanical_children
            self.mesh_buffers={}
            if preview_assets is not None:
                for mesh,item in preview_assets.items():
                    payload=item.pop('data')
                    self.mesh_buffers[(ident,preview.version,mesh)]=payload
                    item['byte_length']=len(payload)
                    item['url']=f'/api/device-scene/mesh?task={ident}&version={preview.version}&mesh={mesh}'
            self.geometry_cache={key:result}
            self.assembly_previews.pop(ident,None)
            return result
