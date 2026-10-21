"""每份网页新产物的真实部署/物理验收；失败交回现有编排器修复。"""
import hashlib
import json
from pathlib import Path
import shutil
import re
import xml.etree.ElementTree as ET
import time
from urllib.parse import urlsplit

from .orchestrator import Orchestrator
from .runtime_control import RuntimeControlError, deployment_preflight
from .simulation import build_scene
from .motion import Motion
from .pipeline import PLCGenerator


class VerifiedPLCGenerator(PLCGenerator):
    def __init__(self,*args,device_model=None,initial_candidate=None,initial_feedback=None,resume_validation=False,**kwargs):
        kwargs.setdefault('max_rounds',5)
        super().__init__(*args,**kwargs)
        self.device_model=device_model
        self.initial_candidate,self.initial_feedback=initial_candidate,initial_feedback
        self.resume_validation=resume_validation

    def _finish(self,result,spec):
        if result.get('ok') and self.position_mode and self.seed_xml is None:
            from .interp_completion import finish_interpolators
            xml,changed=finish_interpolators(result['xml'],spec)
            if changed:
                ok,errors=self.gate(xml,spec['io_list'])
                result.update(xml=xml if ok else None,ok=ok,errors=errors)
                self.report('已修正标准插补器的精细目标收尾：'+', '.join(changed)+'；原路点及到位容差保持不变。')
        return result

    def _call(self,*args,**kwargs):
        reply=super()._call(*args,**kwargs)
        from .pipeline import extract_xml
        from .xml_normalization import normalize_pou_terminators
        xml=extract_xml(reply)
        if xml:
            fixed,count=normalize_pou_terminators(xml)
            if count:
                self.report('已移除XML逻辑体重复的POU结束标记%d处，工艺语句不变。'%count)
                return fixed
        return reply

    def generate(self,spec,*args,**kwargs):
        if self.initial_candidate:
            candidate,self.initial_candidate=self.initial_candidate,None
            kwargs.pop('feedback',None)
            if self.resume_validation:
                self._trajectory=kwargs.get('trajectory')
                ok,errors=self.gate(candidate,spec['io_list'])
                if ok:
                    self.report('上次为通信或Runtime中断，直接重新验证同一候选；不调用模型，全部闸门重新执行。')
                    return {'ok':True,'xml':candidate,'rounds':0,'mode':'resume'}
                self.initial_feedback=(self.initial_feedback or '')+'\n重新静态检查：\n'+'\n'.join(errors)
            self.report('沿用同一冻结输入的失败候选继续定向修复，全部闸门重新执行。')
            return self.repair(candidate,spec,self.initial_feedback or '重新核对全部工艺并完成实际编译及物理验收。',**kwargs)
        return self._finish(super().generate(spec,*args,**kwargs),spec)

    def repair(self,previous_xml,spec,*args,**kwargs):
        if getattr(self,'preferred_candidate',None):
            previous_xml,preferred_feedback=self.preferred_candidate
            self.preferred_candidate=None
            current_feedback=args[0] if args else kwargs.get('feedback','')
            combined=('修复基底为此前物理检查通过更多的候选。该基底的失败证据：\n'+preferred_feedback+
                '\n当前轮完整失败证据与归因（若来自被回退候选，仅用于避免重复回退，不代表基底也有同样缺陷）：\n'+str(current_feedback or ''))
            if args:args=(combined,)+args[1:]
            else:kwargs['feedback']=combined
        return self._finish(super().repair(previous_xml,spec,*args,**kwargs),spec)

    def gate(self,xml_text,io_list):
        ok,errors=super().gate(xml_text,io_list)
        if ok:
            ns='{http://www.plcopen.org/xml/tc6_0201}'
            for pou in ET.fromstring(xml_text).findall('.//'+ns+'pou'):
                types={v.get('name','').lower():v.find(ns+'type')[0].tag.rsplit('}',1)[-1]
                    for v in pou.findall('.//'+ns+'interface//'+ns+'variable') if v.find(ns+'type') is not None}
                body=''.join(pou.find('.//'+ns+'ST').itertext()) if pou.find('.//'+ns+'ST') is not None else ''
                code=re.sub(r'\(\*[\s\S]*?\*\)|//[^\n]*','',body)
                if re.search(r'(?im)^\s*(?:FUNCTION_BLOCK|END_FUNCTION_BLOCK|PROGRAM|END_PROGRAM|VAR|VAR_INPUT|VAR_OUTPUT|VAR_IN_OUT|END_VAR)\b',code):
                    errors.append('[structure] '+pou.get('name','')+': XML的ST节点只能含逻辑语句，不得再包含POU或VAR声明/结束标记，转换器会自动生成这些部分。')
                for match in re.finditer(r'\bINT_TO_REAL\(\s*(\w+)(?:\s*[-+]\s*(\w+))?\s*\)',body,re.I):
                    real=[name for name in match.groups() if name and types.get(name.lower())=='REAL']
                    if real:
                        errors.append('[type] '+pou.get('name','')+': '+match[0]+' 的实参含 REAL '+','.join(real)+
                            '，matiec会拒绝。若保持原DRIVE402骨架，应将保存INT Setpoint的sp_prev声明为INT；'
                            '否则对INT操作数分别显式转换后做REAL运算，不能对REAL结果再用INT_TO_REAL。')
        if ok and self.device_model:
            from .consistency_check import consistency_check
            ok,problems=consistency_check(xml_text,io_list,device_model=self.device_model)
            errors += [p for p in problems if not p.startswith('SKIP')]
        return ok and not errors,errors

    def build_messages(self, spec, feedback=None, trajectory=None, test_plan=None):
        messages=super().build_messages(spec,feedback,trajectory,test_plan=test_plan)
        messages[1]['content']+=(
            '\n本轮将实际编译、部署并运行物理仿真。验收包含每个waypoint和真实笔尖接触轨迹，'
            '不是仅检查完成灯或插补器内部Done。轴到位容差来自冻结AML，不允许放宽。'
            '各段必须顺序走完；顶点切换前让物理位置完成跟随，必要时在目标保持至少150ms，'
            '但不得违反原需求时限。不能在目标刚变更的扫描读取旧Done推进；'
            '抬笔应先原地上升、到位后横移，独立轮廓之间不能连笔。'
            '不要修改路点、笔态、原工艺要求或用写反馈变量来伪造到位。'
            '绘图活跃期间每扫描保持三轴go_*_exe为TRUE，不可只在步0启动一次后在单轴Done时清除；'
            '无draw_active变量时，以pl_step<>0表示绘图期间，结束清理限定pl_step=0。\n')
        if self.position_mode:
            from .position_control import position_scales
            scales=position_scales(spec.get('io_list',[]))
            messages[1]['content']+=(
                '\n输出接线检查按直接赋值校验，各轴编码倍率为 '+json.dumps(scales)+'。'
                '直接将 ix_x.pos/ix_y.pos 的 REAL 状态乘当前倍率后 REAL_TO_INT 写给 x_cmd/y_cmd；'
                'Z 使用 ix_z 实际类型的 REAL 位置字段，'
                'INTERP 为 pos、INTERP_Z 为 posz。倍率以当前 AML 为准。'
                '不要先写 *_cmd_i 再复制给 *_cmd。'
                'go_x/go_y/go_z 与 rel_* 的 Velocity/Acceleration/Deceleration 实参必须显式使用'
                '对应 axes.defaults 的数值字面量；路径同步缩放放在其后的 INTERP 输入端。\n')
        return messages


def write_json(path, value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def physical_timing_hint(report):
    rows={row.get('id'):row for row in report.get('details',[])}
    if rows.get('SVG_GEOMETRY',{}).get('pass') is not True:return ''
    if not any(rows.get(key,{}).get('pass') is False for key in ('PATH','SVG_PATH')):return ''
    return ('基于实测：笔迹几何已经通过，缺失的是路点实际到位/抬笔时序，保持已通过的插补器和输出不变。'
        '修复到位稳定计时：pl_dwell等计数器只能在三轴本次Done且实际到位之后才开始累加，'
        '未到位时归零；不能从进入该步起边运动边计数，也不能只把计数上限从8改到15。'
        '删除CASE各步内无条件的pl_dwell累加，在插补调用之后的三轴到位分支内连续保持旧目标至少150ms再推进步号，'
        '每次步号改变清零计数。尤其抬笔必须在原XY位置完成并稳定后，才切换下一路径的XY目标。'
        '实际Modbus/物理跟随有采样延迟，INTERP刚Done并不等于模型已到位。不要放宽容差或改验收。')


def failed_candidate(directory):
    """Reuse context on an explicit retry, never reuse an old delivery or verdict."""
    from .pipeline import extract_xml
    directory=Path(directory)
    directories=[directory];seen={directory}
    for parent in directories:
        source=parent/'repair-source.json'
        if source.is_file() and len(directories)<8:
            task=json.loads(source.read_text(encoding='utf-8')).get('task','')
            ancestor=directory.parent/task
            if re.fullmatch(r'svg_web_[0-9a-f]{32}',task) and ancestor not in seen:
                seen.add(ancestor);directories.append(ancestor)
    candidates=[]
    for iteration in [iteration for parent in directories for iteration in sorted(parent.glob('iter_*'),reverse=True)]:
        path=iteration/'plcopen.xml'
        if not path.is_file():continue
        try:ET.fromstring(path.read_text(encoding='utf-8'))
        except ET.ParseError:continue
        gate=iteration/'gate.json'
        gate_doc=json.loads(gate.read_text(encoding='utf-8')) if gate.is_file() else {}
        errors=gate_doc.get('errors',[])
        from .attribution import AttributionEngine
        advice=AttributionEngine.format_feedback(gate_doc['attribution']) if gate_doc.get('attribution') else ''
        verdict=iteration/'verdict.json'
        physical=json.loads(verdict.read_text(encoding='utf-8')) if verdict.is_file() else {}
        passed=sum(row.get('pass') is True for row in physical.get('details',[]))
        timing=physical_timing_hint(physical)
        if timing:
            errors=[error for error in errors if not error.startswith('排查建议')]
            advice+='\n'+timing
        stage={'generate':0,'xml2st':1,'consistency':2,'scene':3,'deploy':4,'acceptance':5,'all':6}.get(gate_doc.get('gate'),0)
        candidates.append(((stage,passed),path,'\n'.join(errors)+('\n上轮归因（仅供修复参考）：\n'+advice if advice else '')))
    if candidates:
        _,path,feedback=max(candidates,key=lambda value:value[0])
        return path.read_text(encoding='utf-8'),feedback,str(path)
    for path in sorted(directory.glob('iter_*/generation/*.response.json'),reverse=True):
        data=json.loads(path.read_text(encoding='utf-8'));choice=data['choices'][0]
        if choice.get('finish_reason')!='stop':continue
        xml=extract_xml(choice['message'].get('content') or '')
        if xml:
            try:ET.fromstring(xml)
            except ET.ParseError:continue
            feedback='上次未通过静态闸门，重新修复全部检查项，不得绕过编译与物理验收。'
            evidence=path.with_name(path.name.replace('.response.json','.feedback.json'))
            if evidence.is_file():
                record=json.loads(evidence.read_text(encoding='utf-8'))
                feedback+='\n'+'\n'.join(record.get('errors',[]))+'\n'+record.get('advice','')
            return xml,feedback,str(path.relative_to(directory))
    return None


def resumable_environment_failure(source):
    """A manual retry after interrupted validation rechecks the source before using a model."""
    source=Path(source)
    if source.name!='plcopen.xml':return False
    gate=source.parent/'gate.json'
    if gate.is_file():
        record=json.loads(gate.read_text(encoding='utf-8'))
        gates=record.get('gates',{})
        if gates.get('acceptance',{}).get('state')=='env_down':return True
        detail=gates.get('deploy',{}).get('detail')
        if isinstance(detail,dict) and detail.get('status')=='CONFIGURATION_ERROR':return True
    verdict=source.parent/'verdict.json'
    if verdict.is_file():
        error=json.loads(verdict.read_text(encoding='utf-8')).get('error')
        return bool(error and WorkbenchOrchestrator._environment_error(error))
    return False


def validation_result(directory):
    from .task_inputs import load_task_spec
    directory=Path(directory)
    proof=directory/'final/verdict.json'
    if not proof.is_file():
        proof=directory/'verdict.json'
    xml=directory/'final/plcopen.xml'
    if not proof.is_file() or not xml.is_file():
        return {'status':'not_verified'}
    report=json.loads(proof.read_text(encoding='utf-8'))
    scene=directory/'simulation/scene.spec.json'
    mapping=directory/'simulation/io_map.json'
    trace=directory/'physical_trace.json'
    if not trace.is_file():
        trace=directory/'final/physical_trace.json'
    bound=all(path.is_file() and report.get('sha256',{}).get(name)==hashlib.sha256(path.read_bytes()).hexdigest()
              for name,path in [('xml',xml),('scene',scene),('io_map',mapping)])
    physical=json.loads(trace.read_text(encoding='utf-8')) if trace.is_file() else []
    spec_bound=report.get('spec_sha256')==hashlib.sha256(json.dumps(load_task_spec(directory),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    trace_bound=report.get('physical_sha256')==hashlib.sha256(json.dumps(physical,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    trajectory=directory/'trajectory.json'
    trajectory_bound=not trajectory.is_file() or report.get('trajectory_sha256')==hashlib.sha256(trajectory.read_bytes()).hexdigest()
    passed=(report.get('verdict')=='PASS' and report.get('status')=='completed'
            and not report.get('error') and report.get('physical_samples',0)>0
            and bool(report.get('details')) and all(r.get('pass') is True for r in report['details'])
            and bound and spec_bound and trace_bound and trajectory_bound
            and bool(physical) and len(physical)==report.get('physical_samples'))
    return {'status':'passed' if passed else 'failed','run_id':report.get('id'),
            'samples':report.get('physical_samples',0),'details':report.get('details',[])}


class WorkbenchOrchestrator(Orchestrator):
    def __init__(self, *, directory, session, runtime, report, on_motion=None, **kwargs):
        super().__init__(**kwargs)
        self.directory=Path(directory)
        self.session=session
        self.runtime=runtime
        self.report=report
        self.on_motion=on_motion or (lambda motion:None)
        self.verified_iteration=None
        self.last_deployment=None
        self.best_physical=None

    def solve(self, *args, **kwargs):
        self.active_generator=args[1] if len(args)>1 else kwargs.get('generator')
        result=super().solve(*args,**kwargs)
        result['validation']=validation_result(self.directory)
        result['deployment']=self.last_deployment
        return result

    def build_artifacts(self, scene, spec, iter_dir):
        """迭代隔离；候选的 final 仅供已有物理运行器读取，不是任务交付。"""
        iter_dir=Path(iter_dir)
        (iter_dir/'final').mkdir(exist_ok=True)
        for name in ('plcopen.xml','scene.spec.json'):
            shutil.copyfile(iter_dir/name,iter_dir/'final'/name)
        shutil.copytree(self.directory/'assets',iter_dir/'assets',dirs_exist_ok=True)
        write_json(iter_dir/'request.json',spec)
        if (self.directory/'trajectory.json').is_file():
            shutil.copyfile(self.directory/'trajectory.json',iter_dir/'trajectory.json')
        result=build_scene(iter_dir)
        if not result['runtime_ready']:
            raise ValueError('仿真通道未就绪：'+'；'.join(result['runtime_problems']))
        return result

    def status_probe(self):
        try:
            return self.runtime.status()
        except RuntimeControlError:
            return None

    @staticmethod
    def _compile_context(result, directory):
        """Keep matiec's exact lines beside its error; avoid guessing from a signature."""
        errors=result.get('errors') or []
        if isinstance(errors,str):errors=[errors]
        source=Path(directory)/'plc.st'
        lines=source.read_text(encoding='utf-8').splitlines() if source.is_file() else []
        numbers=sorted({int(n) for error in errors for n in re.findall(r'\.st:(\d+)',error)})
        excerpts=['本轮编译前ST第%d行附近：\n'%n+'\n'.join('%d: %s'%(i+1,lines[i])
            for i in range(max(0,n-3),min(len(lines),n+2))) for n in numbers[:6] if 0<n<=len(lines)]
        if excerpts:result=dict(result,errors=errors+excerpts)
        return result

    def deploy_gate(self, xml_path):
        try:
            xml=deployment_preflight(Path(xml_path).parent)
        except RuntimeControlError as exc:
            return 'failed',{'status':'FAILED','errors':[str(exc)]}
        try:
            self.report('正在真实编译、部署并启动本轮 PLC…')
            result=self.runtime.action('deploy',xml)
        except RuntimeControlError as exc:
            result=exc.result or {'status':'CONFIGURATION_ERROR','errors':[str(exc)]}
            return 'failed',self._compile_context(result,Path(xml_path).parent)
        if result.get('status')!='OK':
            return 'failed',self._compile_context(result,Path(xml_path).parent)
        self.last_deployment=result
        return 'ok',result

    def acceptance_gate(self, scenario, runtime_expected=False):
        iteration=Path(self._acceptance_context['AGENT_EVIDENCE_DIR'])
        attempts=[]
        for attempt in range(1,3):
            state,detail=self._acceptance_attempt(scenario,runtime_expected)
            attempts.append({'attempt':attempt,'state':state,'detail':detail})
            write_json(iteration/'acceptance-attempts.json',attempts)
            evidence='\n'.join(map(str,detail if isinstance(detail,list) else [detail]))
            transport=(state=='env_down' and self._environment_error(evidence)
                and not re.search(r'程序身份|源码|回执|停止确认失败',evidence))
            if not transport or attempt==2:
                return state,detail
            # Retain interrupted evidence before restarting the same frozen source.
            target=iteration/'acceptance-attempts'/('attempt_%03d'%attempt)
            target.mkdir(parents=True,exist_ok=True)
            for name in ('verdict.json','trace.json','physical_trace.json','report.md'):
                if (iteration/name).is_file():shutil.copy2(iteration/name,target/name)
            self.report('Modbus通信中断，保留本次证据并对同一源码重试一次物理验收，不调用模型；重新核对Runtime身份。')
        return state,detail

    def _acceptance_attempt(self, scenario, runtime_expected=False):
        iteration=Path(self._acceptance_context['AGENT_EVIDENCE_DIR'])
        xml=(iteration/'plcopen.xml').read_bytes()
        probe=self.status_probe()
        for _ in range(40):
            if (probe and probe.get('runtime',{}).get('status')!='BUSY'
                    and (probe.get('runtime',{}).get('status')!='RUNNING' or probe.get('prog_id') is not None)):
                break
            time.sleep(.25)
            probe=self.status_probe()
        if not probe or probe.get('runtime',{}).get('status')!='RUNNING':
            return 'env_down',['Runtime 未运行，未执行仿真验收；不能交付通过产物']
        if (probe.get('deployment') or {}).get('sha256')!=hashlib.sha256(xml).hexdigest():
            return 'env_down',['Runtime XML 回执不是本轮源码，停止验收']
        target=probe.get('modbus') or {}
        host=target.get('host') or urlsplit(probe.get('runtime',{}).get('url','')).hostname
        if not host:
            return 'configuration_error',['部署服务未提供 Modbus 地址']
        motion=None
        try:
            motion=Motion(iteration,self.session,host,int(target.get('port') or 502),mode='verify',task=self.directory.name)
            self.on_motion(motion)
            self.report('正在对本轮 PLC 执行 MuJoCo 物理仿真与验收…')
            motion.start()
            deadline=time.monotonic()+self.acceptance_timeout
            progress=time.monotonic()
            while motion.alive and time.monotonic()<deadline:
                motion.thread.join(.25)
                if time.monotonic()-progress>15:
                    status=motion.status()
                    self.report('物理仿真 %ss，状态：%s，实际笔迹 %s 点' %
                        (status['elapsed'],status['status'],status['ink_count']))
                    progress=time.monotonic()
            if motion.alive:
                motion.stop()
                return 'failed',['本轮仿真超时；请修复步序、完成条件或运动停滞，不能修改验收容差']
            status=motion.status()
            report=motion.verification
            if not report:
                if status.get('error') and self._environment_error(status['error']):
                    return 'env_down',[status['error']]
                return 'failed',['物理验收没有生成有效报告，禁止标为通过']
            (iteration/'report.md').write_text('\n'.join(
                ('PASS ' if row.get('pass') else 'FAIL ')+row.get('id','')+': '+row.get('evidence','')
                for row in report.get('details',[])),encoding='utf-8')
            write_json(iteration/'verdict.json',report)
            write_json(iteration/'trace.json',motion.trace)
            write_json(iteration/'physical_trace.json',motion.physical_trace)
            status=motion.status()
            if status.get('error') and self._environment_error(status['error']):
                return 'env_down',[status['error']]
            end_probe=self.status_probe()
            if not end_probe or (end_probe.get('deployment') or {}).get('sha256')!=hashlib.sha256(xml).hexdigest():
                return 'env_down',['验收过程中 Runtime 的源码回执改变，不能确认通过']
            if validation_result(iteration)['status']!='passed':
                errors=[r.get('id','')+': '+r.get('evidence','未通过') for r in report.get('details',[]) if r.get('pass') is not True]
                if status.get('error'):errors.insert(0,status['error'])
                # Fine output commands distinguish a stalled interpolator from coarse INT feedback.
                if motion.trace:
                    names=[a+'_'+suffix for a in 'xyz' for suffix in ('fb','cmd')]
                    recent=motion.trace[-min(50,len(motion.trace)):]
                    observed={name:{'last':recent[-1].get(name),
                        'min':min(row[name] for row in recent if name in row),
                        'max':max(row[name] for row in recent if name in row)}
                        for name in names if any(name in row for row in recent)}
                    errors.append('末段真实PLC采样（cmd为AML编码后的精细位置，fb为INT粗反馈）：'+json.dumps(observed,ensure_ascii=False))
                    errors.append(physical_timing_hint(report) or '排查建议（不改变原判据）：若INTERP在细POSWIN下目标附近反复越过，'
                        '检查离散积分跨越目标时是否正确钳位pos:=tgt并停速，不能只在速度恰为0且误差极小时置Done。'
                        '保持连续跟踪；不要改成单扫描边沿，不要将三轴AND条件仅换括号，也不要放宽POSWIN。')
                score=sum(row.get('pass') is True for row in report.get('details',[]))
                if self.best_physical is None or score>self.best_physical[0]:
                    self.best_physical=(score,xml.decode('utf-8'),'\n'.join(errors))
                elif score<self.best_physical[0]:
                    self.report('本轮物理表现回退，下一轮保留先前通过检查更多的源码继续修复。')
                if self.active_generator is not None:
                    self.active_generator.preferred_candidate=self.best_physical[1:]
                return 'failed',errors or ['物理仿真未完成，不能交付通过产物']
            self.verified_iteration=iteration
            self.report('本轮真实编译、部署及物理仿真验收均已通过。')
            return 'ok',report
        except (RuntimeError,ValueError,OSError) as exc:
            return ('env_down' if self._environment_error(str(exc)) else 'failed'),[str(exc)]
        finally:
            if motion and motion.alive:
                motion.stop()

    @staticmethod
    def _environment_error(error):
        return bool(re.search(r'连接|收发失败|程序身份|停止确认失败|Modbus.*(?:超时|timeout|通信失败|no response)|timed?\s*out|connection',str(error),re.I))

    def _runtime_probe(self, *args, **kwargs):
        # 物理验收已有采样，不能另部署诊断稿改变被验收的程序。
        return []

    def _finalize(self, run_dir, iter_dir, i, history):
        if self.verified_iteration!=Path(iter_dir) or validation_result(iter_dir)['status']!='passed':
            raise ValueError('本轮没有匹配源码的物理验收 PASS，禁止冻结 final')
        super()._finalize(run_dir,iter_dir,i,history)
        shutil.copytree(Path(iter_dir)/'simulation',Path(run_dir)/'simulation',dirs_exist_ok=True)
        write_json(Path(run_dir)/'validation.json',validation_result(run_dir))
