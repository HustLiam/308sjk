"""SVG 位置输出迁移：AML 唯一源、冻结交付保留与实际位置通道对账。"""
import copy
import json
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET

import pytest

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'src'))
from agent.aml_parser import parse_aml,build_io_list
from agent.position_control import make_position_aml,position_io,convert_position_program,position_program_problems
from agent.pipeline import PLCGenerator
from agent.consistency_check import consistency_check
from agent.scene_gen import SceneSpecGenerator
from agent.simulation import build_scene
from agent.web import SVGWorkbench,WorkbenchError

AML=REPO/'examples/aml/plotter3axis_station.aml'
NS={'p':'http://www.plcopen.org/xml/tc6_0201'}


def model():
    m,problems=parse_aml(make_position_aml(AML.read_bytes()).decode('utf-8'))
    assert not problems
    return m


def seed():
    # 标准插补栈的绘图种子与用户 LLM 生成形态共用 ix_x/y/z 接线。
    return (REPO/'src/plc/plotter3axis.xml').read_text(encoding='utf-8')


def test_aml_adds_real_position_outputs_without_reassigning_old_channels():
    before,problems=parse_aml(AML)
    assert not problems
    raw=make_position_aml(AML.read_bytes());after,problems=parse_aml(raw.decode('utf-8'))
    assert not problems and position_io(after) and not position_io(before)
    old={p['name']:p for p in before['io_points']};new={p['name']:p for p in after['io_points']}
    assert set(new)-set(old)=={'x_cmd','y_cmd','z_cmd'}
    for name,point in old.items():
        assert all(new[name][key]==point[key] for key in ['address','range','unit','dir','type'])
    assert [new[a+'_cmd']['address'] for a in 'xyz']==['%QW3','%QW4','%QW5']
    for a in 'xyz':assert new[a+'_cmd']['range']==new[a+'_fb']['range']
    assert make_position_aml(raw)==raw
    tree=ET.fromstring(raw);ns={'a':'http://www.dke.de/CAEX'}
    for a in 'xyz':
        wire=tree.find('.//a:InternalLink[@Name="wire_'+a+'_cmd"]',ns)
        assert wire is not None and wire.get('RefPartnerSideB')=='ch_'+a+'_cmd'
        assert tree.find('.//a:InternalLink[@Name="wire_'+a+'_v"]',ns) is None


def test_position_addresses_use_free_slots_and_keep_reserved_identity():
    source=AML.read_bytes().replace(b'%QW13',b'%QW3')
    got,_=parse_aml(make_position_aml(source).decode('utf-8'))
    names={p['name']:p['address'] for p in got['io_points']}
    assert names['x_v']=='%QW3' and names['x_cmd']=='%QW4'
    assert '%QW20' not in names.values()


def test_program_changes_only_output_binding_and_keeps_axis_and_process_logic():
    m=model();original=seed();converted=convert_position_program(original,m)
    old=ET.fromstring(original);new=ET.fromstring(converted)
    for pou in old.findall('.//p:pou',NS):
        if pou.get('name')!='PLC_PRG':
            other=new.find('.//p:pou[@name="'+pou.get('name')+'"]',NS)
            assert ET.tostring(other)==ET.tostring(pou)
    before=''.join(old.find('.//p:pou[@name="PLC_PRG"]/p:body/p:ST',NS).itertext()).strip()
    after=''.join(new.find('.//p:pou[@name="PLC_PRG"]/p:body/p:ST',NS).itertext()).strip()
    assert after.startswith(before) and 'IF run AND all_oe AND NOT quickstop AND NOT fault_any' in after
    for a in 'xyz':
        assert a+'_cmd := '+a+'_interp;' in after
        assert a+'_cmd := '+a+'_fb;' in after
    items,pending=build_io_list(m);assert not pending
    scene=SceneSpecGenerator().generate({'io_list':items,'task_id':'csp_test'},m)['scene']
    ok,problems=consistency_check(converted,items,scene['io_map'],m,scene)
    assert ok,problems
    table={p['name']:p['address'] for p in m['io_points']}
    generator=PLCGenerator(None,position_mode=True,address_table=table)
    assert generator.gate(converted,items)==(True,[])


@pytest.mark.parametrize('replacement',['x_v','x_sp','dv_x.v_cmd'])
def test_position_gate_rejects_speed_and_hmi_target_as_command(replacement):
    m=model();table={p['name']:p['address'] for p in m['io_points']}
    converted=convert_position_program(seed(),m).replace('x_cmd := x_interp;','x_cmd := '+replacement+';')
    assert position_program_problems(converted,table)


def test_conversion_refuses_unknown_axis_layout_and_identity_collision():
    with pytest.raises(ValueError,match='ix_x'):
        convert_position_program(seed().replace('ix_x','other_interp'),model())
    with pytest.raises(ValueError,match='地址冲突'):
        convert_position_program(seed().replace('address="%QW13"','address="%QW20"'),model())


def test_generated_prompt_specifies_csp_and_actual_aml_addresses():
    m=model();items,_=build_io_list(m);table={p['name']:p['address'] for p in m['io_points']}
    generator=PLCGenerator(None,position_mode=True,address_table=table)
    messages=generator.build_messages({'io_list':items,'task_goal':'描画 SVG'})
    assert 'ix_x.Setpoint' in messages[1]['content'] and '%QW3' in messages[1]['content']
    assert 'prog_id 必须为 AT %QW20' in messages[1]['content']


def test_workbench_conversion_preserves_candidate_without_publishing_unverified_delivery(tmp_path):
    app=SVGWorkbench(tmp_path,aml=AML)
    try:
        state=app.import_svg('house.svg',(REPO/'examples/svg/house.svg').read_bytes())
        state=app.default_process(state['id'],state['revision']);ident=state['id']
        stored=app._session(ident)
        task='svg_web_'+'a'*32;directory=app.root/task;directory.mkdir();(directory/'final').mkdir()
        source=seed();(directory/'final/plcopen.xml').write_text(source,encoding='utf-8')
        stored['jobs']=[{'id':task,'revision':state['revision'],'status':'final','files':[]}]
        old_assets=copy.deepcopy(stored['assets']);old_geometry=copy.deepcopy(stored['prepared']['trajectory'])
        before_spec=copy.deepcopy(stored['spec'])
        state=app.enable_position(ident,state['revision'])
        deadline=time.monotonic()+10
        while state['busy'] and time.monotonic()<deadline:
            time.sleep(.02);state=app.state(ident)
        assert not state['busy'] and state['jobs'][-1]['status']=='configuration_error',state['messages'][-1]
        assert not state['jobs'][-1]['files']
        assert state['position_mode'] and state['trajectory']==old_geometry
        assert state['assets']['library']['id']==old_assets['library']['id']
        assert state['assets']['aml']['id']!=old_assets['aml']['id']
        assert state['spec']['constraints']==before_spec['constraints']
        assert (directory/'final/plcopen.xml').read_text(encoding='utf-8')==source
        new_task=app.root/state['workflow']['task']
        assert not (new_task/'final').exists()
        new_dir=new_task/'iter_001'
        from agent.runtime_control import deployment_preflight,RuntimeControlError
        assert deployment_preflight(new_dir)==(new_dir/'final/plcopen.xml').read_bytes()
        build=build_scene(new_dir)
        assert build['runtime_ready'],build['runtime_problems']
        entries=json.loads((new_dir/'simulation/io_map.json').read_text())
        assert [e['modbus']['plc_addr'] for e in entries if e['dir']=='output']==['%QW3','%QW4','%QW5']
        assert len(state['jobs'])==2
        again=app.enable_position(ident,state['revision'])
        assert len(again['jobs'])==2 and again['revision']==state['revision']
        with pytest.raises(WorkbenchError):app.enable_position(ident,state['revision']-1)
        xml=new_dir/'final/plcopen.xml'
        xml.write_text(xml.read_text(encoding='utf-8').replace('x_cmd := x_interp;','x_cmd := x_v;'),encoding='utf-8')
        with pytest.raises(RuntimeControlError,match='位置输出'):deployment_preflight(new_dir)
    finally:app.close()
