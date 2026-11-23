"""Endpoint repair must preserve the process, freeze tolerances, and retain halt behavior."""
import copy
from pathlib import Path
import xml.etree.ElementTree as ET
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

from agent.interp_completion import finish_interpolators
from agent.aml_parser import parse_aml,build_io_list
from agent.consistency_check import xml2st

NS='{http://www.plcopen.org/xml/tc6_0201}'


def inputs():
    model,errors=parse_aml(ROOT/'examples/aml/plotter3axis_precision_station.aml')
    assert not errors
    return (ROOT/'src/plc/plotter3axis.xml').read_text(encoding='utf-8'),{'io_list':build_io_list(model)[0]}


def test_discrete_completion_preserves_process_halt_and_frozen_parameters(tmp_path):
    xml,spec=inputs();original=copy.deepcopy(spec)
    fixed,changed=finish_interpolators(xml,spec)
    assert changed==['INTERP'] and spec==original
    before=ET.fromstring(xml);after=ET.fromstring(fixed)
    for pou in before.findall('.//'+NS+'pou'):
        other=after.find('.//'+NS+'pou[@name="'+pou.get('name')+'"]')
        assert ET.tostring(pou.find(NS+'interface'))==ET.tostring(other.find(NS+'interface'))
        code=''.join(other.find('.//'+NS+'ST').itertext())
        if pou.get('name') in changed:
            assert 'Busy AND NOT hold_req' in code and '>= 0.0' in code
            assert code.count('VERIFIED_ENDPOINT')==1
        else:assert code==''.join(pou.find('.//'+NS+'ST').itertext())
    path=tmp_path/'candidate.xml';path.write_text(fixed,encoding='utf-8')
    ok,_,errors=xml2st.convert(path)
    assert ok,errors
    assert finish_interpolators(fixed,spec)==(fixed,[])


def test_unknown_integrators_and_normal_resolution_are_preserved():
    xml,spec=inputs()
    assert finish_interpolators(xml,{'io_list':[]})==(xml,[])
    root=ET.fromstring(xml)
    for pou in root.findall('.//'+NS+'pou'):
        if pou.get('name') in ('INTERP','INTERP_Z'):
            st=pou.find('.//'+NS+'ST')[0];st.text='custom_integrator();'
    unknown=ET.tostring(root,encoding='unicode')
    assert finish_interpolators(unknown,spec)==(unknown,[])
