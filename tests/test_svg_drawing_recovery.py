"""重新生成 SVG 程序后的扫描顺序、精度和假完成回归。"""
import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

import pytest
from agent.aml_parser import parse_aml, build_io_list
from agent.position_control import repair_generated_svg, svg_program_problems, convert_position_program
from agent.pipeline import PLCGenerator
from test_workbench_motion import delivery, Client, wait_until
from agent.motion import Motion

NS='{http://www.plcopen.org/xml/tc6_0201}'


@pytest.fixture
def generated_svg():
    model,errors=parse_aml(ROOT/'examples/aml/plotter3axis_precision_station.aml')
    assert not errors
    root=ET.parse(ROOT/'src/plc/plotter3axis.xml').getroot()
    prg=root.find('.//'+NS+'pou[@name="PLC_PRG"]')
    variables={v.get('name'):v for v in prg.findall('.//'+NS+'variable')}
    local=ET.SubElement(prg.find(NS+'interface'),NS+'localVars')
    for name,typ in {'pl_k':'INT','draw_active':'BOOL','plot_done_i':'BOOL',
                     'x_fb_i':'INT','y_fb_i':'INT','z_fb_i':'INT'}.items():
        if name not in variables:
            v=ET.SubElement(local,NS+'variable',name=name)
            ET.SubElement(ET.SubElement(v,NS+'type'),NS+typ)
    # 单独的 Z 插补器与不同扫描常量，覆盖模型生成稿的结构变体。
    interp=root.find('.//'+NS+'pou[@name="INTERP"]')
    z=copy.deepcopy(interp);z.set('name','INTERP_Z')
    for v in z.iter(NS+'variable'):
        if v.get('name') in ('pos','vel','tgt','SCAN_T'):
            v.set('name',v.get('name')+'z' if v.get('name')!='SCAN_T' else 'SCAN_TZ')
    import re
    st=z.find('.//'+NS+'ST')[0]
    for old,new in (('pos','posz'),('vel','velz'),('tgt','tgtz'),('SCAN_T','SCAN_TZ')):
        st.text=re.sub(r'\b'+old+r'\b',new,st.text)
    root.find('.//'+NS+'pous').append(z)
    variables['ix_z'].find(NS+'type/'+NS+'derived').set('name','INTERP_Z')
    body='''prog_id := 2;
IF cmd_draw THEN draw_active := TRUE; pl_step := 1; go_x_exe := TRUE; go_y_exe := TRUE; go_z_exe := TRUE; END_IF;
CASE pl_step OF
1: pl_tx := 35; pl_ty := 53; pl_tz := 10;
2: CASE pl_k OF 0: pl_tx := 45; pl_ty := 53; pl_tz := 0; END_CASE;
END_CASE;
IF draw_active AND pl_step <> 0 AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    IF pl_k < 2 THEN pl_k := pl_k + 1; ELSE pl_step := pl_step + 1; END_IF;
END_IF;
'''
    for a in 'xyz':
        body+=f'{a}_tgt := pl_t{a};\nix_{a}(fire := go_{a}_exe OR hm_{a}_exe, pos_target := {a}_tgt, VMAX := 40.0, ACCEL := 80.0, POSWIN := 0.01, MAXPOS := 100);\n'
    for a in 'xyz':
        body+=f'IF ix_{a}.Done OR ix_{a}.Aborted THEN go_{a}_exe := FALSE; hm_{a}_exe := FALSE; END_IF;\n'
        body+=f'dv_{a}(cw := {a}_cw, Setpoint := ix_{a}.Setpoint, pos_fb := {a}_fb_i);\n'
    prg.find('.//'+NS+'ST')[0].text=body
    trajectory={'shape':'svg','pen_z':{'up':10,'down':0},'steps':[
        {'no':1,'to':[34.75,53.47,10]},
        {'no':2,'to':[34.75,53.47,0]},
        {'no':3,'to':[45.31,53.47,0],'waypoints':[[40.15,53.47,0],[45.31,53.47,0]]},
        {'no':4,'to':[50,50,10]}]}
    trajectory.update(params={'path_count':1,'point_count':4,'center':[50,50],'width':60,'height':60},
                      vmax={'xy':40,'z':20},sampling={'coordinate_quantum':.01})
    for step in trajectory['steps']:step['action']='polyline_sweep' if step.get('waypoints') else 'move'
    return convert_position_program(ET.tostring(root,encoding='unicode'),model),model,trajectory


def test_new_generation_rejects_stale_done_and_integer_waypoints(generated_svg):
    xml,model,trajectory=generated_svg
    problems=svg_program_problems(xml,trajectory)
    assert any('Done' in p for p in problems) and any('REAL' in p for p in problems)
    class ClientReply:
        def chat_completions(self,*args,**kwargs):
            return {'choices':[{'message':{'content':xml}}]}
    generator=PLCGenerator(ClientReply(),max_rounds=1,position_mode=True,
        address_table={p['name']:p['address'] for p in model['io_points']})
    result=generator.generate({'io_list':build_io_list(model)[0]},trajectory=trajectory)
    assert not result['ok'] and any('[SVG]' in p for p in result['errors'])


def test_recovery_keeps_nested_waypoints_and_continuous_fire(generated_svg):
    xml,model,trajectory=generated_svg
    fixed=repair_generated_svg(xml,model,trajectory)
    root=ET.fromstring(fixed);prg=root.find('.//'+NS+'pou[@name="PLC_PRG"]')
    body=''.join(prg.find('.//'+NS+'ST').itertext())
    assert not svg_program_problems(fixed,trajectory)
    assert body.index('IF draw_active AND ix_x.Done')>body.index('ix_z(fire')
    assert 'pl_step := -1;' in body and 'pl_tx := 34.75;' in body
    assert 'pl_tx := 40.15;' in body and 'pl_tx := 45.31;' in body
    assert 'IF pl_k < 1 THEN' in body
    assert 'IF NOT draw_active THEN\nIF ix_x.Done' in body
    assert 'rel_x_exe' not in body.split('CSP_POSITION_OUTPUT:')[-1] or 'rel_x_exe' in {
        v.get('name') for v in prg.findall('.//'+NS+'variable')}
    z=root.find('.//'+NS+'pou[@name="INTERP_Z"]')
    assert 'ABS(velz * SCAN_TZ)' in ''.join(z.find('.//'+NS+'ST').itertext())
    with pytest.raises(ValueError,match='重复'):repair_generated_svg(fixed,model,trajectory)


@pytest.mark.parametrize('shape',['svg','square','circle'])
def test_done_without_ink_is_failure_not_drawing_completed(delivery,shape):
    app,ident,_,directory=delivery
    (directory/'trajectory.json').write_text(json.dumps({'shape':shape,'pen_z':{'down':0,'up':10},'steps':[{'no':1,'to':[35,25,0]}]}))
    client=Client();motion=Motion(directory,ident,'unused',client=client);app.motion=motion
    motion.start();wait_until(lambda:motion.status()['status']=='running')
    client.bits[motion.bits['plot_done']]=True
    wait_until(lambda:not motion.alive,timeout=8)
    assert motion.status()['status']=='failed'
    assert '没有形成实际笔迹' in motion.status()['error']
    assert motion.verification['verdict']=='UNVERIFIED'


def test_single_interp_real_targets_and_nested_busy_variant(generated_svg):
    xml,model,trajectory=generated_svg
    root=ET.fromstring(xml);prg=root.find('.//'+NS+'pou[@name="PLC_PRG"]')
    variables={v.get('name'):v for v in prg.findall('.//'+NS+'interface//'+NS+'variable')}
    variables['ix_z'].find(NS+'type/'+NS+'derived').set('name','INTERP')
    for a in 'xyz':
        typ=variables[a+'_tgt'].find(NS+'type');typ.clear();ET.SubElement(typ,NS+'REAL')
    st=prg.find('.//'+NS+'ST')[0]
    start=st.text.index('IF draw_active AND pl_step <> 0')
    end=st.text.index('x_tgt := pl_tx;',start)
    st.text=st.text[:start]+'''IF draw_active AND pl_step <> 0 THEN
    IF NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy THEN
        pl_step := pl_step + 1;
    END_IF;
END_IF;
'''+st.text[end:]
    fixed=repair_generated_svg(ET.tostring(root,encoding='unicode'),model,trajectory)
    body=''.join(ET.fromstring(fixed).find('.//'+NS+'pou[@name="PLC_PRG"]//'+NS+'ST').itertext())
    assert 'ABS(ix_z.pos - pl_tz)' in body and 'ix_z.posz' not in body
    assert 'path_goal_x := x_tgt;' in body and 'INT_TO_REAL(x_tgt)' not in body
    assert 'x_tgt := pl_tx;' in body and 'x_tgt := REAL_TO_INT(pl_tx);' not in body
    assert not svg_program_problems(fixed,trajectory)


def test_retriggering_edge_and_unconditional_done_cleanup_are_rejected(generated_svg):
    xml,_,trajectory=generated_svg
    root=ET.fromstring(xml);st=root.find('.//'+NS+'pou[@name="PLC_PRG"]//'+NS+'ST')[0]
    st.text='cmd_draw_edge := cmd_draw AND NOT cmd_draw_edge;\n'+st.text
    errors=svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory)
    assert any('前值记忆' in error for error in errors)
    assert any('打断连续跟踪' in error for error in errors)


def test_precise_svg_prompt_does_not_instruct_integer_rounding(generated_svg):
    from agent.trajectory import summarize_for_prompt,goal_text
    _,_,trajectory=generated_svg
    text=summarize_for_prompt(trajectory)
    assert 'REAL 路点' in text and '按 PLC INT 分辨率量化' not in text
    assert '34.75' in text and 'REAL 路点' in goal_text(trajectory)
    coarse=copy.deepcopy(trajectory);coarse['sampling']['coordinate_quantum']=1
    assert 'INT 路点' in summarize_for_prompt(coarse)


def test_shared_inactive_guard_covers_all_axis_cleanup_and_not_else(generated_svg):
    xml,_,trajectory=generated_svg
    trajectory={'shape':'svg','steps':[]}
    root=ET.fromstring(xml);st=root.find('.//'+NS+'pou[@name="PLC_PRG"]//'+NS+'ST')[0]
    cleanup='\n'.join('IF ix_'+a+'.Done OR ix_'+a+'.Aborted THEN go_'+a+'_exe := FALSE; END_IF;' for a in 'xyz')
    st.text='draw_active := FALSE; IF NOT draw_active THEN\n'+cleanup+'\nEND_IF;'
    assert not svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory)
    st.text='IF NOT draw_active THEN x := 1; ELSE\n'+cleanup+'\nEND_IF;'
    assert len(svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory))==3
    st.text='(* IF NOT draw_active THEN *)\n'+cleanup
    assert len(svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory))==3
    st.text='pl_step := 1;\n'+cleanup
    assert len(svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory))==3
    st.text='IF pl_step = 0 THEN\n'+cleanup+'\nEND_IF;'
    assert not svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory)
    st.text='IF draw_active THEN x := 1; ELSE\n'+cleanup+'\nEND_IF;'
    assert not svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory)


@pytest.mark.parametrize('shape',['svg','square','circle'])
def test_compound_done_cleanup_and_stale_done_are_checked_for_all_drawing(generated_svg,shape):
    xml,_,trajectory=generated_svg
    trajectory=copy.deepcopy(trajectory);trajectory['shape']=shape
    root=ET.fromstring(xml);st=root.find('.//'+NS+'pou[@name="PLC_PRG"]//'+NS+'ST')[0]
    st.text=st.text.replace('IF ix_x.Done OR ix_x.Aborted THEN',
        'IF (go_x_exe OR hm_x_exe OR rel_x_exe) AND ix_x.Done AND NOT ix_x.Busy THEN')
    errors=svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory)
    assert any('调用之前' in e for e in errors)
    assert any('go_x_exe' in e and '打断连续跟踪' in e for e in errors)


def test_recognized_dwell_sequence_is_fixed_without_changing_route_or_fbs(generated_svg):
    from agent.position_control import repair_drawing_scan_order
    from agent.workbench_validation import VerifiedPLCGenerator
    xml,_,_=generated_svg
    trajectory={'shape':'square','steps':[{'no':1,'to':[30,30,10]}]}
    root=ET.fromstring(xml);prg=root.find('.//'+NS+'pou[@name="PLC_PRG"]')
    local=ET.SubElement(prg.find(NS+'interface'),NS+'localVars')
    for name,typ in [('hold_active','BOOL'),('hold_timer','TON')]:
        var=ET.SubElement(local,NS+'variable',name=name);node=ET.SubElement(var,NS+'type')
        ET.SubElement(node,NS+'derived',name=typ) if typ=='TON' else ET.SubElement(node,NS+typ)
    route='CASE pl_step OF\n1: pl_tx := 30; pl_ty := 30; pl_tz := 10;\nEND_CASE;'
    advance='''IF (pl_step <> 0) AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    IF NOT hold_active THEN hold_active := TRUE;
    ELSIF hold_timer.Q THEN
        hold_active := FALSE;
        IF pl_step = 8 THEN draw_active := FALSE; pl_step := 0;
        ELSE pl_step := pl_step + 1; END_IF;
    END_IF;
ELSE hold_active := FALSE;
END_IF;'''
    st=prg.find('.//'+NS+'ST')[0]
    st.text=route+'\nhold_timer(IN := hold_active, PT := T#150MS);\n'+advance+'\n'
    st.text+='\n'.join('ix_'+a+'(fire := go_'+a+'_exe, pos_target := '+a+'_tgt);' for a in 'xyz')+'\n'
    st.text+='\n'.join('IF (go_'+a+'_exe OR hm_'+a+'_exe OR rel_'+a+'_exe) AND ix_'+a+'.Done AND NOT ix_'+a+'.Busy THEN\n'+
        'go_'+a+'_exe := FALSE; hm_'+a+'_exe := FALSE; rel_'+a+'_exe := FALSE;\nEND_IF;' for a in 'xyz')
    xml=ET.tostring(root,encoding='unicode')
    fixed,changed=repair_drawing_scan_order(xml,trajectory)
    assert changed and not svg_program_problems(fixed,trajectory)
    after=ET.fromstring(fixed);body=''.join(after.find('.//'+NS+'pou[@name="PLC_PRG"]//'+NS+'ST').itertext())
    assert route in body and advance in body
    assert body.index(advance)>body.index('ix_z(')
    assert body.count('IF NOT draw_active THEN')==3
    for pou in root.findall('.//'+NS+'pou'):
        if pou.get('name')!='PLC_PRG':
            assert ET.tostring(pou)==ET.tostring(after.find('.//'+NS+'pou[@name="'+pou.get('name')+'"]'))
    assert repair_drawing_scan_order(fixed,trajectory)==(fixed,False)
    unknown=xml.replace('T#150MS','T#300MS')
    assert repair_drawing_scan_order(unknown,trajectory)==(unknown,False)
    nested=xml.replace('ix_x(fire := go_x_exe, pos_target := x_tgt);',
        'IF draw_active THEN ix_x(fire := go_x_exe, pos_target := x_tgt); END_IF;')
    assert repair_drawing_scan_order(nested,trajectory)==(nested,False)
    class Reply:
        def chat_completions(self,*args,**kwargs):return {'choices':[{'message':{'content':xml}}]}
    generator=VerifiedPLCGenerator(Reply(),position_mode=True,no_cards=True)
    generator._trajectory=trajectory
    assert 'DRAWING_SCAN_ORDER' in generator._call([])


@pytest.mark.parametrize('xml',['<project><broken></project>',
    '<project xmlns="http://www.plcopen.org/xml/tc6_0201"><pou name="PLC_PRG"/></project>'])
def test_drawing_normalizer_leaves_invalid_structure_for_static_gate(xml):
    from agent.position_control import repair_drawing_scan_order
    assert repair_drawing_scan_order(xml,{'shape':'square','steps':[]})==(xml,False)
