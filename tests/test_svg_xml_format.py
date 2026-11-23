"""Full XML generation must retain the trajectory and repair exact format errors."""
import copy
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from agent.pipeline import PLCGenerator, extract_xml
from agent.xml_normalization import xml_structure_advice, normalize_fb_type_references, PLC_NS
from agent.svg_input import plan_svg


def source():
    root=ET.parse(ROOT/'src/plc/plotter_square_csp.xml').getroot()
    library=ET.parse(ROOT/'src/agent/templates/motion_stack_v4.xml').getroot()
    ns='{'+PLC_NS+'}'
    pous=root.find(ns+'types/'+ns+'pous')
    for pou in library.iter(ns+'pou'):pous.insert(0,copy.deepcopy(pou))
    local=pous.find(ns+'pou[@pouType="program"]/'+ns+'interface/'+ns+'localVars')
    for name in ('TON','INTERP'):
        var=ET.SubElement(local,ns+'variable',name='probe_'+name)
        ET.SubElement(ET.SubElement(var,ns+'type'),ns+'derived',name=name)
    ET.register_namespace('',PLC_NS)
    return ET.tostring(root,encoding='unicode')


def malformed(mixed=False):
    root=ET.fromstring(source());ns='{'+PLC_NS+'}'
    for pou in root.iter(ns+'pou'):
        if mixed and pou.get('pouType')=='program':continue
        pou.tag=ns+'pouType'
        if pou.get('pouType')=='functionBlock':
            for block in pou.find(ns+'interface'):
                block.tag=ns+'localVars'
    return ET.tostring(root,encoding='unicode')


@pytest.mark.parametrize('mixed',[False,True])
def test_wrong_pou_element_and_collapsed_interfaces_are_rejected_with_exact_fix(mixed):
    xml=malformed(mixed)
    spec=json.loads((ROOT/'examples/specs/plotter_square_csp.spec.json').read_text(encoding='utf-8'))
    ok,errors=PLCGenerator(None,no_cards=True).gate(xml,spec['io_list'])
    assert not ok
    text='\n'.join(errors)
    assert '<pouType>' in text and '<pou name="INTERP" pouType="functionBlock">' in text
    assert 'inputVars/outputVars/inOutVars' in text and '不能只替换标签' in text
    assert xml==malformed(mixed) # Diagnostics do not guess or rewrite declarations.
    assert not xml_structure_advice(source())


def test_model_receives_specific_format_repair_and_all_svg_points(tmp_path):
    good=source();bad=malformed();calls=[]
    class Client:
        def chat_completions(self,payload,**kwargs):
            calls.append(copy.deepcopy(payload))
            return {'choices':[{'message':{'content':bad if len(calls)==1 else good},'finish_reason':'stop'}]}
    spec=json.loads((ROOT/'examples/specs/plotter_square_csp.spec.json').read_text(encoding='utf-8'))
    trajectory=plan_svg(ROOT/'examples/svg/house.svg')
    generator=PLCGenerator(Client(),no_cards=True,max_rounds=2)
    generator.evidence_dir=tmp_path/'generation'
    result=generator.generate(spec,trajectory=trajectory)
    assert result['ok'] and len(calls)==2
    assert '<pouType>' in calls[1]['messages'][-1]['content']
    assert 'inputVars/outputVars/inOutVars' in calls[1]['messages'][-1]['content']
    for call in calls:
        prompt=call['messages'][1]['content']
        for step in trajectory['steps']:
            for point in [step['to'],*step.get('waypoints',[])]:
                assert '(%g,%g,%g)'%tuple(point) in prompt
    assert (generator.evidence_dir/'call_001.feedback.json').is_file()


@pytest.mark.parametrize('override,expected',[(None,32768),('65536',65536)])
def test_complete_xml_output_budget_is_increased_and_configurable(monkeypatch,override,expected):
    if override is None:monkeypatch.delenv('LLM_MAX_OUTPUT_TOKENS',raising=False)
    else:monkeypatch.setenv('LLM_MAX_OUTPUT_TOKENS',override)
    calls=[]
    class Client:
        def chat_completions(self,payload,**kwargs):
            calls.append(payload)
            return {'choices':[{'message':{'content':'ok'},'finish_reason':'stop'}]}
    PLCGenerator(Client())._call([])
    assert calls[0]['max_tokens']==expected and calls[0]['temperature']==.3


def direct_references():
    root=ET.fromstring(source());ns='{'+PLC_NS+'}';changed=0
    for parent in root.iter(ns+'type'):
        if len(parent)==1 and parent[0].tag==ns+'derived':
            child=parent[0];child.tag=ns+child.attrib.pop('name');changed+=1
    assert changed
    return ET.tostring(root,encoding='unicode'),changed


def test_fb_reference_normalization_preserves_entire_model_except_reference_syntax():
    raw,count=direct_references()
    fixed,changed=normalize_fb_type_references(raw)
    assert count==changed
    assert ET.tostring(ET.fromstring(fixed))==ET.tostring(ET.fromstring(source()))
    assert normalize_fb_type_references(fixed)==(fixed,0)


def test_text_fb_types_use_declared_names_without_changing_st_or_interfaces():
    root=ET.fromstring(source());ns='{'+PLC_NS+'}';count=0
    for typ in root.iter(ns+'type'):
        if len(typ)==1 and typ[0].tag==ns+'derived':
            name=typ[0].get('name');typ.remove(typ[0]);typ.text=name.lower();count+=1
    fixed,changed=normalize_fb_type_references(ET.tostring(root,encoding='unicode'))
    assert changed==count
    assert ET.tostring(ET.fromstring(fixed))==ET.tostring(ET.fromstring(source()))


@pytest.mark.parametrize('xml',[
    '<project xmlns="http://www.plcopen.org/xml/tc6_0201"><type><UNKNOWN_FB/></type></project>',
    '<project xmlns="http://www.plcopen.org/xml/tc6_0201"><type><TON attribute="unknown"/></type></project>',
    '<project xmlns="wrong"><type><TON/></type></project>',
    '<project xmlns="http://www.plcopen.org/xml/tc6_0201"><type>UNKNOWN_FB</type></project>',
    '<project xmlns="http://www.plcopen.org/xml/tc6_0201"><type attribute="unknown">TON</type></project>',
    '<project xmlns="http://www.plcopen.org/xml/tc6_0201"><type>ARRAY [1..2] OF TON</type></project>',
])
def test_unknown_or_ambiguous_type_syntax_is_left_to_original_static_gate(xml):
    assert normalize_fb_type_references(xml)==(xml,0)


def test_format_only_recovery_reuses_agent_candidate_without_model_call():
    from agent.workbench_validation import VerifiedPLCGenerator
    raw,_=direct_references()
    spec=json.loads((ROOT/'examples/specs/plotter_square_csp.spec.json').read_text(encoding='utf-8'))
    class Client:
        def chat_completions(self,*args,**kwargs):raise AssertionError('Format recovery must not call model')
    generator=VerifiedPLCGenerator(Client(),no_cards=True,initial_candidate=raw)
    result=generator.generate(spec)
    assert result['ok'] and result['mode']=='format_resume' and result['rounds']==0
    assert generator.gate(result['xml'],spec['io_list'])[0]


@pytest.mark.parametrize('prefix',['','ns0:','plc:'])
def test_full_project_extraction_accepts_valid_namespace_prefixes(prefix):
    declaration=('xmlns:'+prefix[:-1] if prefix else 'xmlns')+'="'+PLC_NS+'"'
    xml=f'<{prefix}project {declaration}><{prefix}types/></{prefix}project>'
    extracted=extract_xml('完整工程：\n```xml\n<?xml version="1.0"?>\n'+xml+'\n```')
    assert extracted and ET.fromstring(extracted).tag=='{'+PLC_NS+'}project'
    assert ET.tostring(ET.fromstring(extracted))==ET.tostring(ET.fromstring(xml))


def test_mismatched_or_incomplete_project_is_still_rejected():
    assert extract_xml('<a:project xmlns:a="'+PLC_NS+'"></b:project>') is None
    assert extract_xml('<projectVersion>42</projectVersion>') is None
    assert extract_xml('<ns0:project xmlns:ns0="'+PLC_NS+'">') is None
