"""位置编码精度、真实 REAL 输出与 SVG 小数坐标回归。"""
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pytest
REPO=Path(__file__).resolve().parents[1];sys.path.insert(0,str(REPO/'src'))
from agent.position_control import make_precision_aml,position_io,position_scales,convert_position_program,position_program_problems
from agent.aml_parser import parse_aml,build_io_list
from agent.svg_input import plan_svg
from agent.consistency_check import xml2st


def test_precision_keeps_addresses_and_uses_unrounded_real_position():
    original=(REPO/'examples/aml/plotter3axis_csp_station.aml').read_bytes()
    before,_=parse_aml(original.decode());model,errors=parse_aml(make_precision_aml(original).decode())
    assert not errors and position_io(model) and position_scales(model['io_points'])==dict.fromkeys('xyz',100)
    assert {p['name']:p['address'] for p in before['io_points']}=={p['name']:p['address'] for p in model['io_points']}
    source=(REPO/'src/plc/plotter3axis.xml').read_text(encoding='utf-8')
    xml=convert_position_program(source,model)
    assert 'REAL_TO_INT(ix_x.pos * 100.0)' in xml
    table={p['name']:p['address'] for p in model['io_points']}
    assert not position_program_problems(xml,table,2,build_io_list(model)[0])
    damaged=xml.replace('REAL_TO_INT(ix_x.pos * 100.0)','ix_x.Setpoint')
    assert position_program_problems(damaged,table,2,build_io_list(model)[0])


def test_svg_quantization_error_is_bounded_by_hundredth_unit():
    model,errors=parse_aml(REPO/'examples/aml/plotter3axis_precision_station.aml');assert not errors
    trajectory=plan_svg(REPO/'examples/svg/house.svg',device_model=model,selected_paths=[3,4])
    assert trajectory['sampling']['coordinate_quantum']==.01
    assert trajectory['sampling']['flatten_tolerance']==.02
    assert trajectory['sampling']['max_quantization_error']<=.007072
    assert any(not float(p[0]).is_integer() for step in trajectory['steps'] for p in step.get('waypoints',[]))
