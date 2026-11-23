"""Browser-owned, qualified-device PLC generation/deployment and IO execution."""
import math
from pathlib import Path
import threading
import time
import uuid

import mujoco

from ..generation_control import GenerationControl, GenerationCancelled
from ..config import RUNS_DIR
from ..pipeline import PLCGenerator
from .common import SceneError, write_json
from .binding_registry import profiles, qualified
from .motion_agent import BoundMotionAgent, resolve_device
from .robotcell_io import BoundModel, Bridge, TestDriver
from .plc_preview import _lease_lock, _leases


def deploy_program(xml_path, config, folder, check):
    from src.pipeline.xml2st import convert
    from src.pipeline.openplc_client import OpenPLCClient
    ok, st, errors = convert(xml_path)
    if not ok:
        raise SceneError('PLC XML 转换失败：'+str(errors))
    plc = OpenPLCClient(config['adapter']['runtime']['web_url'])
    try:
        check();plc.login()
        status = plc.status()
        if status == 'RUNNING':
            status = plc.stop()
        if status != 'STOPPED':
            raise SceneError('PLC Runtime 无法停止：'+status)
        check()
        program = plc.upload_and_compile(st, 'agent_bound_motion')
        deadline = time.monotonic()+180
        while True:
            check()
            state = plc.compile_status()
            if state in ('SUCCESS', 'FAILED'):
                break
            if time.monotonic() > deadline:
                raise TimeoutError('PLC 编译超时')
            time.sleep(.5)
        (folder/'compilation.log').write_text(plc._request('GET','/compilation-logs').text, encoding='utf8')
        if state != 'SUCCESS':
            raise SceneError('matiec 编译失败，详见 compilation.log')
        check()
        if plc.start() != 'RUNNING':
            raise SceneError('PLC Runtime 未启动')
        write_json(folder/'deployment.json', {'status':'deployed','program':program,'url':plc.base})
    finally:
        plc.session.close()


class AgentControl:
    def __init__(self, preview, scene_dir, owners, records, author_factory, *, runs_root=RUNS_DIR):
        self.preview, self.scene_dir = preview, Path(scene_dir).resolve()
        self.owners = {name: {'component_id': cid} for name,cid in owners.items()}
        self.records, self.author_factory, self.runs_root = records, author_factory, Path(runs_root)
        self.job = None

    def catalog(self):
        return profiles(self.scene_dir)

    def seen(self, client):
        if self.job and self.job['client'] == client:
            self.job['last_seen'] = time.monotonic()

    def stop(self, client=None):
        job = self.job
        if job and (client is None or client == job['client']):
            job['cancel'].set();job['control'].request()
            job['thread'].join(timeout=12)
            if job['thread'].is_alive():
                raise SceneError('Agent 正在停止，请稍后重试')

    def start(self, client, request):
        if not isinstance(request,str) or not request.strip() or len(request)>12000:
            raise SceneError('请输入组件名称和运动要求')
        moving = {j['component_id'] for j in self.preview.joints if j['controllable']}
        cid = resolve_device(request, {cid:c for cid,c in self.preview.components.items() if cid in moving})
        entry = self.catalog().get(cid)
        if entry is None:
            raise SceneError('该组件尚未完成当前模型版本的 IO 绑定和双向通信检验，不能下发 PLC 命令。')
        profile, config = entry
        endpoint = (config['adapter']['runtime']['host'], config['adapter']['runtime']['port'])
        with self.preview.lock:
            session = self.preview.session(client)
            if session.get('plc_active') or session.get('motion'):
                raise SceneError('当前会话正在运动，请先停止')
            if self.job and self.job['thread'].is_alive():
                raise SceneError('Agent 正在执行命令，请先停止')
            data = mujoco.MjData(self.preview.model);data.qpos[:] = session['data'].qpos
            bound = BoundModel(config,self.preview.model,data,self.owners,self.records)
            actual = {j['joint_name']:j for j in self.preview.selected([cid])}
            if set(actual) != {v['joint_name'] for v in bound.channels.values()}:
                raise SceneError('组件关节没有全部完成 IO 绑定')
            capability = {'component_id':cid,'component_name':self.preview.components[cid]['name'],
                'max_speed_deg_s':config['motion']['max_speed_deg_s'], 'joints':[
                    {'variable':p['name'],'source_id':p['source_id'],'joint_id':bound.channels[p['name']]['joint_id'],
                     'current_deg':math.degrees(float(data.qpos[bound.channels[p['name']]['jnt_qposadr']])),
                     'range_deg':bound.channels[p['name']]['range_deg'],'deg_per_count':p['deg_per_count']}
                    for p in bound.targets]}
            with _lease_lock:
                if endpoint in _leases:
                    raise SceneError('此 PLC Runtime 正由另一个会话控制')
                _leases[endpoint] = self
            try:
                folder = self.runs_root/('scene_command_'+uuid.uuid4().hex)/'iter_001'
                folder.mkdir(parents=True)
            except BaseException:
                with _lease_lock:_leases.pop(endpoint,None)
                raise
            job = {'client':client,'session':session,'cancel':threading.Event(),'control':GenerationControl(),
                'last_seen':time.monotonic(),'folder':folder,'endpoint':endpoint,'cid':cid}
            job['thread'] = threading.Thread(target=self._run,args=(job,profile,config,bound,capability,request),daemon=True)
            self.job = job
            session.update(plc_active=True,plc_error=None,plc_feedback=None,motion=None,
                           agent={'phase':'planning','run':str(folder),'component_id':cid})
            session['sequence'] += 1
            try:
                job['thread'].start()
            except BaseException:
                session['plc_active']=False
                with _lease_lock:_leases.pop(endpoint,None)
                raise
        return {'components':[cid], 'frame':self.preview.snapshot(client,[cid],metadata=True)}

    def _run(self, job, profile, config, bound, capability, request):
        session, folder = job['session'], job['folder']
        connection = driver = program = None
        error = None;completed = False;events = []
        def check():
            job['control'].check()
            if job['cancel'].is_set():raise GenerationCancelled()
            if time.monotonic()-job['last_seen'] > 10:
                raise SceneError('预览连接已中断，Agent 控制已停止')
        def phase(value):
            with self.preview.lock:
                session['agent']['phase']=value;session['sequence']+=1
        try:
            check()
            author = self.author_factory(job['control'])
            plan = BoundMotionAgent(author.client).plan(request,capability,folder)
            check()
            if not qualified(profile,config):raise SceneError('IO 检验已失效，请重新检验')
            phase('generating')
            program = PLCGenerator(None).generate_bound_motion(config,capability,plan)
            (folder/'plc.xml').write_text(program['xml'],encoding='utf8')
            write_json(folder/'program.json',{k:v for k,v in program.items() if k!='xml'})
            # Physical binding/limits are re-resolved before the expensive deployment gate.
            BoundModel(config,self.preview.model,bound.data,self.owners,self.records)
            write_json(folder/'gates.json',{'xml2st':True,'io_consistency':True,'scene_binding':True})
            phase('deploying')
            deploy_program(folder/'plc.xml',config,folder,check)
            check()
            if not qualified(profile,config):raise SceneError('部署期间 IO 配置或模型已改变，请重新检验')
            from pymodbus.client import ModbusTcpClient
            connection=ModbusTcpClient(*job['endpoint'][:1],port=job['endpoint'][1],timeout=1,retries=0)
            if not connection.connect():raise ConnectionError('无法连接 PLC Modbus')
            driver=TestDriver(config,connection);bridge=Bridge(config,bound,connection)
            for i,addr in enumerate(program['registers']['identity']):
                if driver.wire.read_word(addr)!=int(program['program_hash'][i*4:i*4+4],16):
                    raise SceneError('PLC 中的程序与本次生成结果不一致')
            initial={p['name']:round(math.degrees(bound.data.qpos[bound.channels[p['name']]['jnt_qposadr']])/p['deg_per_count']) for p in bound.targets}
            commanded = {t['variable'] for step in plan['steps'] for t in step['targets']}
            bound.targets = [p for p in bound.targets if p['name'] in commanded]
            enable=bridge.enable['name'];mode=program['registers']['mode']
            driver.wire.write_word(mode,0)
            driver.command({**initial,enable:0},timeout=1)
            event=bridge.cycle();driver.observed(event['feedback'],timeout=1)
            check()
            driver.wire.write_word(mode,1)
            phase('running')
            timeout=sum(s['duration_s']+s['hold_s'] for s in program['steps'])*3+15
            started=time.monotonic()
            while True:
                check();tick=time.monotonic()
                if tick-started>timeout:raise TimeoutError('PLC 任务执行超时')
                # Only a heartbeat/enable. Targets and step progression are computed by PLC.
                driver.command({enable:1},timeout=1)
                event=bridge.cycle();event['plc_observed_inputs']=driver.observed(event['feedback'],timeout=1)
                event['state']=driver.wire.read_word(program['registers']['state'])
                events.append(event)
                with self.preview.lock:
                    session['data'].qpos[:]=bound.data.qpos;session['data'].qvel[:]=bound.data.qvel
                    session['data'].time=bound.data.time
                    mujoco.mj_kinematics(self.preview.model,session['data'])
                    session['plc_feedback']=event;session['sequence']+=1
                if driver.wire.read_word(program['registers']['done'])==1:
                    final=program['steps'][-1]['to_deg']
                    if any(abs(math.degrees(bound.data.qpos[bound.channels[p['name']]['jnt_qposadr']])-final[p['name']])>p['deg_per_count']*1.1 for p in bound.targets):
                        raise SceneError('PLC 完成状态与模型反馈不一致')
                    completed=True;break
                job['cancel'].wait(max(0,1/config['adapter']['cycle_hz']-(time.monotonic()-tick)))
        except GenerationCancelled:
            pass
        except Exception as exc:
            error=str(exc)
        finally:
            bound.hold()
            if driver:
                try:
                    # Disable before returning to manual-compatible mode.
                    driver.command({next(p['name'] for p in config['io_list'] if p['role']=='enable'):0},timeout=1)
                    driver.wire.write_word(program['registers']['mode'],0)
                except Exception as exc:
                    error=(error+'；' if error else '')+'未确认 PLC 停止：'+str(exc)
            if connection:connection.close()
            status='failed' if error else 'completed' if completed else 'cancelled'
            write_json(folder/'execution.json',{'status':status,'error':error,'events':events})
            with self.preview.lock:
                session['data'].qvel[:]=0
                session.update(plc_active=False,plc_error=error)
                session['agent']['phase']=status;session['sequence']+=1
            with _lease_lock:
                if _leases.get(job['endpoint']) is self:_leases.pop(job['endpoint'])
