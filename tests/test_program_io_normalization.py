"""定位 IO 分组纠偏覆盖生成、修复及原始证据，不能改写 FB 或工艺逻辑。"""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from agent.pipeline import PLCGenerator
from agent.xml_normalization import normalize_program_io
from pipeline.xml2st import convert
from test_xml2st import GOOD_MINIMAL

NS='{http://www.plcopen.org/xml/tc6_0201}'


def test_redundant_xml_body_footer_is_removed_once_without_logic_change():
    from agent.xml_normalization import normalize_pou_terminators
    root=ET.fromstring(GOOD_MINIMAL)
    node=root.find('.//'+NS+'ST')[0];logic=node.text or ''
    node.text=logic+'\nEND_PROGRAM\n'
    fixed,count=normalize_pou_terminators(ET.tostring(root,encoding='unicode'))
    assert count==1
    assert ''.join(ET.fromstring(fixed).find('.//'+NS+'ST').itertext()).strip()==logic.strip()
    assert normalize_pou_terminators(fixed)==(fixed,0)


def test_model_footer_normalization_keeps_raw_response_evidence(tmp_path):
    from agent.workbench_validation import VerifiedPLCGenerator
    root=ET.fromstring((ROOT/'src/plc/motion3axis.xml').read_text(encoding='utf-8'))
    pou=root.find('.//'+NS+'pou[@name="DRIVE402"]');node=pou.find('.//'+NS+'ST')[0]
    node.text=(node.text or '')+'\nEND_FUNCTION_BLOCK\n'
    raw_xml=ET.tostring(root,encoding='unicode')
    class Client:
        def chat_completions(self,*args,**kwargs):return {'choices':[{'message':{'content':raw_xml}}]}
    generator=VerifiedPLCGenerator(Client(),no_cards=True);generator.evidence_dir=tmp_path
    spec=json.loads((ROOT/'examples/specs/motion3axis.spec.json').read_text(encoding='utf-8'))
    result=generator.generate(spec)
    assert result['ok'] and 'END_FUNCTION_BLOCK' not in ''.join(ET.fromstring(result['xml']).find('.//'+NS+'pou[@name="DRIVE402"]//'+NS+'ST').itertext())
    assert json.loads((tmp_path/'call_001.response.json').read_text(encoding='utf-8'))['choices'][0]['message']['content']==raw_xml


@pytest.mark.parametrize('block',['inputVars','outputVars','inOutVars'])
def test_program_io_moves_without_losing_values_or_block_attributes(block):
    source=GOOD_MINIMAL.replace('localVars',block).replace('<'+block+'>','<'+block+' retain="true">')
    source=source.replace('<type><INT /></type>','<type><INT /></type><initialValue><simpleValue value="12"/></initialValue>')
    fixed,moved=normalize_program_io(source)
    assert moved==1
    root=ET.fromstring(fixed)
    var=root.find('.//'+NS+'localVars/'+NS+'variable[@name="y"]')
    assert var.get('address')=='%QW2'
    assert var.find(NS+'initialValue/'+NS+'simpleValue').get('value')=='12'
    assert root.find('.//'+NS+'localVars').get('retain')=='true'
    assert root.find('.//'+NS+block+'/'+NS+'variable[@name="x"]') is not None
    assert ''.join(root.find('.//'+NS+'ST').itertext())==''.join(ET.fromstring(source).find('.//'+NS+'ST').itertext())
    assert normalize_program_io(fixed)==(fixed,0)


def test_fb_ports_and_invalid_documents_are_not_rewritten():
    for xml in ['<project><oops', GOOD_MINIMAL.replace('pouType="program"','pouType="functionBlock"').replace('localVars','inputVars'), GOOD_MINIMAL]:
        assert normalize_program_io(xml)==(xml,0)


@pytest.mark.parametrize('repair',[False,True])
def test_generation_and_repair_normalize_io_before_gates_and_keep_raw_reply(tmp_path,repair):
    root=ET.parse(ROOT/'src/plc/plotter3axis.xml').getroot()
    interface=root.find('.//'+NS+'pou[@name="PLC_PRG"]/'+NS+'interface')
    for block in interface:
        if block.tag==NS+'localVars' and any(v.get('address') for v in block):
            block.tag=NS+'inputVars'
    bad=ET.tostring(root,encoding='unicode')
    calls=[]
    class Client:
        def chat_completions(self,payload,**kwargs):
            calls.append(payload)
            return {'choices':[{'message':{'content':bad}}]}
    logs=[];generator=PLCGenerator(Client(),no_cards=True,report=logs.append)
    generator.evidence_dir=tmp_path/'generation'
    spec=json.loads((ROOT/'examples/specs/plotter3axis.spec.json').read_text(encoding='utf-8'))
    result=generator.repair(bad,spec,'修复声明') if repair else generator.generate(spec)
    assert result['ok'] and len(calls)==1
    output=tmp_path/'fixed.xml';output.write_text(result['xml'],encoding='utf-8')
    ok,st,errors=convert(output)
    assert ok,errors
    assert 'AT %QX0.0' in st and any('声明分组' in line for line in logs)
    fixed=ET.fromstring(result['xml'])
    assert ''.join(fixed.find('.//'+NS+'pou[@name="PLC_PRG"]//'+NS+'ST').itertext())==''.join(root.find('.//'+NS+'pou[@name="PLC_PRG"]//'+NS+'ST').itertext())
    raw=json.loads((generator.evidence_dir/'call_001.response.json').read_text(encoding='utf-8'))
    assert raw['choices'][0]['message']['content']==bad
