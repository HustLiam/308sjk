"""生成策略恢复到 2026-10-20 拉取 master 前的本地快照。"""
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from agent.pipeline import PLCGenerator, SKILL_PATH


def spec():
    return json.loads((ROOT/'examples/specs/plotter_square_csp.spec.json').read_text(encoding='utf-8'))


def test_restored_prompt_is_the_exact_pre_pull_asset_for_csp_axes():
    generator=PLCGenerator(None,no_cards=True)
    prompt=SKILL_PATH.read_text(encoding='utf-8')
    assert hashlib.sha256(prompt.encode()).hexdigest()=='4f3d3034a7f6280423a7da96879e5dceb05ecd1903b865245de55b3e988ba571'
    messages=generator.build_messages(spec())
    assert messages[0]['content'].startswith(prompt)
    assert 'FUNCTION_BLOCK INTERP' in messages[0]['content']
    assert 'DRIVE402 权威骨架' in messages[0]['content']
    assert 'CSP 位置从站的完整 PLCopen XML 生成' not in messages[0]['content']
    assert '通用扫描周期检查（所有设备适用）' not in messages[0]['content']


def test_generation_and_repair_use_restored_strategy_and_keep_evidence(tmp_path):
    xml=(ROOT/'src/plc/plotter_square_csp.xml').read_text(encoding='utf-8')
    calls=[]
    class Client:
        def chat_completions(self,payload,**kwargs):
            calls.append(payload)
            return {'choices':[{'message':{'content':xml}}]}
    generator=PLCGenerator(Client(),no_cards=True)
    generator.evidence_dir=tmp_path/'generation'
    assert generator.generate(spec())['ok']
    result=generator.repair(xml,spec(),'保持工艺和所有 IO 不变')
    assert result['ok'] and result['mode']=='repair'
    for call in calls:
        assert call['temperature']==.3
        assert 'FUNCTION_BLOCK INTERP' in call['messages'][0]['content']
    assert '最小修改' in calls[1]['messages'][-1]['content']
    assert (generator.evidence_dir/'call_002.response.json').is_file()


def test_optional_plan_is_compatible_without_changing_system_strategy():
    generator=PLCGenerator(None,no_cards=True)
    original=generator.build_messages(spec())
    plan={'id':'preserve-this-plan'}
    messages=generator.build_messages(spec(),test_plan=plan)
    assert messages[0]==original[0]
    assert plan['id'] in messages[1]['content']
    with pytest.raises(ValueError,match='更新前完整 XML'):
        PLCGenerator(None,generation_mode='hybrid')
