"""可替换的基础资产必须被实际消费，并与任务版本、历史产物隔离。"""
import copy
import base64
import json
import sys
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import requests

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'src'))
from agent import web
from agent.assets import read_json, validate_library
from agent.requirement import RequirementUnderstander
from agent.scene_gen import SceneSpecGenerator
from runtime.mujoco_build import build_mjcf, GANTRY_CONST
from runtime.physics_assets import load_physics, validate_physics


@pytest.fixture
def app(tmp_path,monkeypatch):
    monkeypatch.setattr(web,'get_api_key',lambda:'')
    instance=web.SVGWorkbench(tmp_path)
    yield instance
    instance.close()


def svg(app,state=None):
    return app.import_svg('house.svg',(REPO/'examples/svg/house.svg').read_bytes(),
        state['id'] if state else None,state['revision'] if state else None)


def aml_bytes():
    return (REPO/'examples/aml/plotter3axis_station.aml').read_bytes().replace(b'PlotterCell',b'UpdatedCell').replace(b'<Value>40.0</Value>',b'<Value>25.0</Value>')


def physics_bytes(app,state,mass=6.0):
    physics=copy.deepcopy(state['assets']['library']['physics'])
    physics['gantry']['mass'][0]=mass
    physics['gantry']['kp'][0]=7200
    return app.assets.encode(physics)


def test_three_asset_sections_exist_before_svg_import(app):
    state=app.create_session()
    assert set(state['assets'])=={'aml','library','svg'}
    assert state['assets']['svg'] is None and state['trajectory'] is None
    assert state['assets']['aml']['station']=='PlotterCell'
    assert state['assets']['aml']['axes'] and state['assets']['aml']['io_points']
    assert state['assets']['library']['contract']['types']['gantry_xyz']
    assert state['assets']['library']['physics']['gantry']['mass']==[4,3,.4]
    assert state['assets']['aml']['name']=='plotter3axis_precision_station.aml'


def test_replacing_aml_is_session_local_and_used_for_planning(app):
    first=svg(app)
    second=svg(app)
    changed=app.replace_asset(first['id'],first['revision'],'aml','updated.aml',aml_bytes())
    assert changed['assets']['aml']['station']=='UpdatedCell'
    assert changed['assets']['aml']['name']=='updated.aml'
    assert changed['device_model']['source']['file']=='updated.aml'
    assert changed['trajectory']['vmax']['xy']==25
    assert app.state(second['id'])['assets']['aml']['station']=='PlotterCell'
    assert app.model['station']=='PlotterCell'
    app.sessions.clear()
    assert app.state(first['id'])['assets']['aml']['station']=='UpdatedCell'


@pytest.mark.parametrize('data',[b'<svg/>',b'<!DOCTYPE x><CAEXFile/>',b'not xml'])
def test_bad_aml_preserves_previous_asset_and_revision(app,data):
    state=svg(app)
    with pytest.raises(ValueError):
        app.replace_asset(state['id'],1,'aml','bad.aml',data)
    latest=app.state(state['id'])
    assert latest['revision']==1 and latest['assets']['aml']['id']==state['assets']['aml']['id']


def test_library_parts_replace_and_restore_without_losing_svg(app):
    state=svg(app)
    original=state['assets']['library']['id']
    app._session(state['id'])['spec']={'old':True}
    changed=app.replace_asset(state['id'],1,'library','physics-new.json',physics_bytes(app,state),part='physics')
    assert changed['spec'] is None and changed['revision']==2
    assert changed['assets']['library']['id']!=original
    assert changed['assets']['library']['physics']['gantry']['mass'][0]==6
    assert changed['assets']['library']['parts']['physics']=='physics-new.json'
    assert changed['selected_paths']==state['selected_paths']
    restored=app.replace_asset(state['id'],2,'library',restore=True)
    assert restored['assets']['library']['id']==original
    assert restored['assets']['svg']['id']==state['assets']['svg']['id']
    assert GANTRY_CONST['mass'][0]==4  # 不修改模块全局物理参数。


def test_full_library_import_exports_exact_selected_bundle(app):
    state=app.create_session()
    library=app.assets.library(app._session(state['id'])['assets']['library'])
    library.update(name='实验组件库',version='2.1')
    library['physics']['gantry']['mass'][0]=8
    updated=app.replace_asset(state['id'],0,'library','experiment.json',app.assets.encode(library))
    assert updated['assets']['library']['title']=='实验组件库'
    data,name=app.asset_file('library',updated['assets']['library']['id'],'source')
    assert json.loads(data)==library and name=='component-library.json'
    part,_=app.asset_file('library',updated['assets']['library']['id'],'physics')
    assert json.loads(part)['gantry']['mass'][0]==8


def test_selected_contract_validates_future_aml_without_global_mutation(app):
    state=svg(app)
    contract=copy.deepcopy(state['assets']['library']['contract'])
    speed=next(p for p in contract['types']['gantry_xyz']['params'] if p['name']=='speed')
    speed['maximum']=.45
    state=app.replace_asset(state['id'],1,'library','restricted.json',app.assets.encode(contract),part='contract')
    too_fast=aml_bytes().replace(b'<Value>25.0</Value>',b'<Value>60.0</Value>')
    with pytest.raises(ValueError,match='契约区间'):
        app.replace_asset(state['id'],state['revision'],'aml','too_fast.aml',too_fast)
    assert app.state(state['id'])['assets']['aml']['station']=='PlotterCell'
    other=app.create_session()
    accepted=app.replace_asset(other['id'],0,'aml','fast.aml',too_fast)
    assert accepted['assets']['aml']['station']=='UpdatedCell'


@pytest.mark.parametrize('change',['negative_mass','nan','wrong_interface','unknown_component','missing_gantry'])
def test_invalid_library_is_rejected_atomically(app,change):
    state=svg(app)
    library=app.assets.library(app._session(state['id'])['assets']['library'])
    if change=='negative_mass':library['physics']['gantry']['mass'][0]=-1
    elif change=='nan':library['physics']['gantry']['kp'][0]=float('nan')
    elif change=='wrong_interface':library['contract']['types']['gantry_xyz']['quantities'][0]['direction']='out'
    elif change=='unknown_component':library['contract']['types']['new_robot']={}
    else:del library['contract']['types']['gantry_xyz']
    raw=json.dumps(library).encode()
    with pytest.raises(ValueError):
        app.replace_asset(state['id'],1,'library','bad.json',raw)
    assert app.state(state['id'])['assets']['library']['id']==state['assets']['library']['id']
    assert app.state(state['id'])['revision']==1


def test_replacing_svg_keeps_device_and_library_and_clears_spec(app):
    state=svg(app)
    state=app.replace_asset(state['id'],1,'aml','updated.aml',aml_bytes())
    state=app.replace_asset(state['id'],2,'library','physics.json',physics_bytes(app,state),part='physics')
    app._session(state['id'])['spec']={'old':True}
    updated=app.import_svg('line.svg',b'<svg><path d="M0 0 L10 10"/></svg>',state['id'],3)
    assert updated['id']==state['id'] and updated['revision']==4
    assert updated['assets']['aml']['id']==state['assets']['aml']['id']
    assert updated['assets']['library']['id']==state['assets']['library']['id']
    assert updated['trajectory']['params']['path_count']==1 and updated['spec'] is None


def test_old_revision_and_busy_session_cannot_replace_assets(app):
    state=svg(app)
    with pytest.raises(web.WorkbenchError,match='版本'):
        app.replace_asset(state['id'],0,'aml',restore=True)
    app._session(state['id'])['busy']=True
    with pytest.raises(web.WorkbenchError,match='正在处理'):
        app.replace_asset(state['id'],1,'library',restore=True)


def test_physics_asset_changes_actual_model_not_shared_defaults():
    spec=json.loads((REPO/'scenegen/out/gantry/scene.spec.json').read_text(encoding='utf-8'))
    physics=load_physics()
    physics['gantry']['mass'][0]=7
    physics['gantry']['kp'][0]=8100
    xml=build_mjcf(spec,physics)
    root=ET.fromstring(xml)
    assert root.find('.//geom[@name="saddle"]').get('mass')=='7'
    assert root.find('.//position[@name="drive_x"]').get('kp')=='8100'
    assert GANTRY_CONST['mass'][0]==4
    mujoco=pytest.importorskip('mujoco')
    model=mujoco.MjModel.from_xml_string(xml)
    drive=mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_ACTUATOR,'drive_x')
    assert model.actuator_gainprm[drive,0]==8100


def test_generation_freezes_all_assets_and_uses_selected_model(app,monkeypatch):
    from agent.orchestrator import Orchestrator
    state=svg(app)
    state=app.replace_asset(state['id'],1,'aml','updated.aml',aml_bytes())
    state=app.replace_asset(state['id'],2,'library','physics-new.json',physics_bytes(app,state),part='physics')
    spec=RequirementUnderstander().understand('绘图',device_model=state['device_model'])['spec']
    app._session(state['id'])['spec']=spec
    # 核对真实理解/编排适配器消费当前会话的 AML，而非服务启动时的默认模型。
    def understand(self,request,**options):
        assert options['device_model']['station']=='UpdatedCell'
        return {'spec':spec,'report':{'pending':[]}}
    monkeypatch.setattr(RequirementUnderstander,'understand',understand)
    app._understand(state['id'],None,'绘图')
    def orchestrate(self,current_spec,generator,**options):
        assert options['device_model']['station']=='UpdatedCell'
        assert options['device_model']['kinematics']['axes'][0]['limits']['vmax']==25
        assert options['scene_generator'].registry['gantry_xyz']
        assert options['deploy'] is True and options['require_online'] is True
        return {'status':'final'}
    monkeypatch.setattr(Orchestrator,'solve',orchestrate)
    app._solve(app._session(state['id']),app.root/'adapter_check',lambda text:None)
    def solve(snapshot,directory,log):
        assert snapshot['device_model']['station']=='UpdatedCell'
        library=app.assets.library(snapshot['assets']['library'])
        scene=SceneSpecGenerator(library['contract']).generate(snapshot['spec'],snapshot['device_model'])['scene']
        (directory/'final').mkdir()
        (directory/'final/scene.spec.json').write_text(json.dumps(scene))
        (directory/'final/plcopen.xml').write_text('<project/>')
        (directory/'request.json').write_text(json.dumps(snapshot['spec']))
        (directory/'simulation').mkdir()
        (directory/'simulation/build.json').write_text('{"runtime_ready":true,"runtime_problems":[]}')
        return {'status':'final','iter':1,'run_dir':directory}
    monkeypatch.setattr(web,'get_api_key',lambda:'fake')
    monkeypatch.setattr(app,'_solve',solve)
    monkeypatch.setattr(app,'_generation_validation',lambda path:{'status':'passed'})
    busy=app.generate(state['id'],3)
    deadline=time.monotonic()+4
    while app.state(state['id'])['busy'] and time.monotonic()<deadline:time.sleep(.01)
    done=app.state(state['id'])
    task=done['jobs'][-1]['id']
    assert done['jobs'][-1]['status']=='final'
    directory=app.root/task
    assert [file['name'] for file in done['jobs'][-1]['files']]==[
        'final/scene.spec.json','final/plcopen.xml']
    scene=json.loads((directory/'final/scene.spec.json').read_text())
    assert scene['io_map'] and not (directory/'io_map.json').exists()
    manifest=json.loads((directory/'assets-manifest.json').read_text())
    assert manifest['aml']['id']==state['assets']['aml']['id']
    assert (directory/'assets/device.aml').read_bytes()==aml_bytes()
    physics=json.loads((directory/'assets/physics.json').read_text())
    assert physics['gantry']['mass'][0]==6
    assert not (directory/'simulation/scene.xml').exists()
    app.build_simulation(state['id'],3,task)
    deadline=time.monotonic()+4
    while app.state(state['id'])['busy'] and time.monotonic()<deadline:time.sleep(.01)
    built=app.state(state['id'])
    assert built['workflow']['simulation']['status']=='ready'
    assert ET.fromstring((directory/'simulation/scene.xml').read_text(encoding='utf-8')).find('.//geom[@name="saddle"]').get('mass')=='6'
    # 替换当前资产后，历史任务仍对应此前资产。
    app.replace_asset(state['id'],3,'library',restore=True)
    assert not app.state(state['id'])['workflow']['can_build']
    with pytest.raises(web.WorkbenchError,match='重新生成'):
        app.build_simulation(state['id'],4,task)
    assert json.loads((directory/'assets/physics.json').read_text())['gantry']['mass'][0]==6
    assert app.file(state['id'],task,'assets/device.aml').is_file()


def test_http_asset_controls_and_downloads(app):
    server=web.make_server(app,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=requests.Session();client.trust_env=False
    base='http://127.0.0.1:'+str(server.server_port)
    try:
        page=client.get(base).text
        assert all('id="'+name+'"' in page for name in ('aml-card','library-card','dropzone','physics-file','contract-file'))
        token=client.get(base+'/api/config').json()['token'];headers={'X-Agent-Token':token}
        state=client.post(base+'/api/session',json={},headers=headers).json()
        state=client.post(base+'/api/asset',headers=headers,json={'session':state['id'],'revision':0,
            'kind':'aml','name':'updated.aml','data':base64.b64encode(aml_bytes()).decode()}).json()
        assert state['assets']['aml']['station']=='UpdatedCell'
        assert client.get(base+state['assets']['aml']['url']).content==aml_bytes()
        library=client.get(base+state['assets']['library']['url']).json()
        assert library['format']=='308sjk.component-library.v1'
        assert client.get(base+state['assets']['library']['physics_url']).json()['units']=='SI'
        assert client.post(base+'/api/asset',headers=headers,json={'session':state['id'],'revision':0,'kind':'aml','restore':True}).status_code==409
        assert client.get(base+'/asset/library/../../.env/source').status_code!=200
    finally:
        client.close();server.shutdown();server.server_close();thread.join(timeout=2)
