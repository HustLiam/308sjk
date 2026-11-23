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


@pytest.mark.parametrize('include_z,advance_z',[(False,False),(True,False),(False,True)])
def test_lift_step_cannot_advance_on_xy_only_timer_alias(generated_svg,include_z,advance_z):
    xml,_,trajectory=generated_svg
    root=ET.fromstring(xml);st=root.find('.//'+NS+'pou[@name="PLC_PRG"]//'+NS+'ST')[0]
    body='''ix_x(fire := TRUE);ix_y(fire := TRUE);ix_z(fire := TRUE);
x_ready := NOT ix_x.Busy AND ix_x.Done;
y_ready := NOT ix_y.Busy AND ix_y.Done;
z_ready := NOT ix_z.Busy AND ix_z.Done;
IF z_tgt <= 0.5 THEN
    ready := z_ready AND x_ready AND y_ready;
ELSE
    ready := x_ready AND y_ready;
END_IF;
hold_tmr(IN := (pl_step <> 0) AND ready, PT := T#150MS);
hold_ok := hold_tmr.Q;
IF pl_step <> 0 AND hold_ok THEN pl_step := pl_step + 1; END_IF;
'''
    if include_z:body=body.replace('ready := x_ready AND y_ready;','ready := x_ready AND y_ready AND z_ready;')
    if advance_z:body=body.replace('AND hold_ok THEN','AND hold_ok AND z_ready THEN')
    st.text=body
    problems=svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory)
    assert any('忽略Z到位' in problem for problem in problems) is not (include_z or advance_z)


@pytest.mark.parametrize('scaled',[False,True])
def test_unequal_svg_diagonal_rejects_identical_raw_axis_dynamics(generated_svg,scaled):
    xml,_,trajectory=generated_svg
    trajectory=copy.deepcopy(trajectory)
    trajectory['steps'][2]['waypoints']=[[45.31,70.25,0]]
    root=ET.fromstring(xml);st=root.find('.//'+NS+'pou[@name="PLC_PRG"]//'+NS+'ST')[0]
    st.text='''go_x(Velocity := 40.0, Acceleration := 80.0, Deceleration := 80.0);
go_y(Velocity := 40.0, Acceleration := 80.0, Deceleration := 80.0);
ix_x(vel_req := x_vel_bus, acc_req := x_acc_bus, dec_req := x_dec_bus);
ix_y(vel_req := y_vel_bus, acc_req := y_acc_bus, dec_req := y_dec_bus);
'''
    if scaled:st.text=st.text.replace('ix_x(','x_vel_bus := x_vel_bus * ratio_x;\nix_x(')
    problems=svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory)
    assert any('相同未缩放动力学' in problem for problem in problems) is not scaled


@pytest.mark.parametrize('guard,extra,broken',[
    ('pl_step <> seg_key','',True),
    ('seg_key <> pl_step','',True),
    ('new_segment','new_segment := pl_step <> seg_key;',True),
    ('(pl_step <> seg_key) OR (pl_k <> old_k)','',False),
    ('(pl_step <> seg_key) OR (x_tgt <> seg_end_x) OR (y_tgt <> seg_end_y)','',False),
    ('segment_key <> last_key','segment_key := pl_step * 1201 + pl_k;',False),
    ('new_segment','new_segment := (pl_step <> seg_key) OR (pl_k <> old_k);',False),
])
def test_inner_waypoints_refresh_latched_axis_synchronization(generated_svg,guard,extra,broken):
    xml,_,trajectory=generated_svg
    root=ET.fromstring(xml);st=root.find('.//'+NS+'pou[@name="PLC_PRG"]//'+NS+'ST')[0]
    st.text='''CASE pl_step OF
3: CASE pl_k OF 0: x_tgt := 40.15; y_tgt := 53.47;
1: x_tgt := 45.31; y_tgt := 54.85; END_CASE;
END_CASE;
'''+extra+'''
IF '''+guard+''' THEN
    seg_key := pl_step;
    seg_start_x := ix_x.pos; seg_start_y := ix_y.pos;
    seg_end_x := x_tgt; seg_end_y := y_tgt;
    seg_dx := ABS(seg_end_x - seg_start_x); seg_dy := ABS(seg_end_y - seg_start_y);
    IF seg_max > 0.0001 THEN scale_x := seg_dx / seg_max; scale_y := seg_dy / seg_max; END_IF;
END_IF;
x_vel_eff := x_vel_bus * scale_x; y_vel_eff := y_vel_bus * scale_y;
ix_x(pos_target := x_tgt, vel_req := x_vel_eff);
ix_y(pos_target := y_tgt, vel_req := y_vel_eff);
'''
    errors=svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory)
    found=[e for e in errors if '锁存仅由pl_step' in e]
    assert bool(found)==broken
    if broken:
        assert 'pl_k' in found[0] and '速度/加速度/减速度' in found[0]
        single=copy.deepcopy(trajectory)
        single['steps'][2]['waypoints']=single['steps'][2]['waypoints'][:1]
        assert not any('锁存仅由pl_step' in e for e in svg_program_problems(ET.tostring(root,encoding='unicode'),single))
        original=st.text
        st.text='(* '+original+' *)'
        assert not any('锁存仅由pl_step' in e for e in svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory))
        st.text=original
        # A separate target-change refresh makes the outer initialization valid.
        st.text+='''IF x_tgt <> seg_end_x OR y_tgt <> seg_end_y THEN
seg_start_x := ix_x.pos; seg_start_y := ix_y.pos;
seg_end_x := x_tgt; seg_end_y := y_tgt; END_IF;'''
        assert not any('锁存仅由pl_step' in e for e in svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory))


@pytest.mark.parametrize('location',['before','after','direct'])
def test_mc_outputs_cannot_overwrite_scaled_drawing_dynamics(generated_svg,location):
    xml,_,trajectory=generated_svg
    trajectory=copy.deepcopy(trajectory);trajectory['steps'][2]['waypoints']=[[40.15,54.0,0],[45.31,54.85,0]]
    root=ET.fromstring(xml);st=root.find('.//'+NS+'pou[@name="PLC_PRG"]//'+NS+'ST')[0]
    mc='\n'.join(f'go_{a}(Velocity := 40.0, Acceleration := 80.0, Deceleration := 80.0, dyn_vel := {a}_vel_bus);' for a in 'xy')
    scale='\n'.join(f'{a}_vel_bus := {a}_vel_bus * ratio_{a};' for a in 'xy')
    calls='\n'.join(f'ix_{a}(vel_req := {a}_vel_bus);' for a in 'xy')
    st.text=(scale+'\n'+mc+'\n'+calls if location=='before' else mc+'\n'+scale+'\n'+calls)
    if location=='direct':st.text=(scale+'\n'+mc+'\n'+calls).replace('vel_req := x_vel_bus','vel_req := x_vel_eff').replace('vel_req := y_vel_bus','vel_req := y_vel_eff')
    errors=svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory)
    assert any('随后被MC' in e for e in errors)==(location=='before')


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


@pytest.mark.parametrize('comment',[False,True])
def test_encoded_targets_are_rejected_before_deployment(generated_svg,comment):
    from agent.position_control import position_program_problems
    xml,model,_=generated_svg
    root=ET.fromstring(xml);st=root.find('.//'+NS+'pou[@name="PLC_PRG"]//'+NS+'ST')[0]
    assignment='x_tgt := REAL_TO_INT(pl_tx * 100.0);'
    st.text=('(* '+assignment+' *)' if comment else assignment)+'\n'+st.text
    errors=position_program_problems(ET.tostring(root,encoding='unicode'),
        {p['name']:p['address'] for p in model['io_points']},io_list=build_io_list(model)[0])
    assert any('x_tgt' in e and '提前乘了编码倍率' in e for e in errors)==(not comment)


def test_fractional_waypoints_require_real_interpolator_input(generated_svg):
    xml,model,trajectory=generated_svg
    fixed=repair_generated_svg(xml,model,trajectory)
    assert not svg_program_problems(fixed,trajectory)
    root=ET.fromstring(fixed)
    typ=root.find('.//'+NS+'pou[@name="INTERP"]//'+NS+'variable[@name="pos_target"]/'+NS+'type')
    typ.clear();ET.SubElement(typ,NS+'INT')
    errors=svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory)
    assert any('ix_x' in e and 'pos_target 必须为 REAL' in e for e in errors)


def test_aliased_timer_advance_cannot_read_busy_before_current_target(generated_svg):
    xml,_,_=generated_svg
    root=ET.fromstring(xml);st=root.find('.//'+NS+'pou[@name="PLC_PRG"]//'+NS+'ST')[0]
    checks='''hold_tmr_in := (pl_step <> 0) AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy;
hold_tmr(IN := hold_tmr_in, PT := T#150MS);
hold_tmr_q := hold_tmr.Q;
seq_adv := (pl_step <> 0) AND hold_tmr_q;
IF seq_adv THEN pl_step := pl_step + 1; END_IF;
'''
    calls='\n'.join('ix_'+a+'(fire := go_'+a+'_exe);' for a in 'xyz')
    trajectory={'shape':'svg','steps':[]}
    st.text=checks+calls
    assert any('调用之前' in e for e in svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory))
    st.text=calls+'\n'+checks
    assert not svg_program_problems(ET.tostring(root,encoding='unicode'),trajectory)
