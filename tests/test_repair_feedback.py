"""Knowledge-backed repairs and bounded model spending without live API/PLC calls."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

from agent.memory import MemoryStore
from agent.orchestrator import Orchestrator
from agent.pipeline import PLCGenerator
from agent.repair_feedback import GenerationStalled, source_signature, static_feedback
from agent.workbench_validation import VerifiedPLCGenerator


def test_source_signature_ignores_only_presentation_and_keeps_literal_values():
    def xml(code):
        return '<project><ST><![CDATA['+code+']]></ST></project>'
    original=xml("x := 1; s := 'http://a (* value *)';")
    assert source_signature(original)==source_signature(xml("(* note *) x:=1; // comment\ns:='http://a (* value *)';"))
    assert source_signature(original)!=source_signature(xml("x:=2; s:='http://a (* value *)';"))
    assert source_signature(original)!=source_signature(xml("x:=1; s:='http://b (* value *)';"))
    assert source_signature(xml('s := "a b";'))!=source_signature(xml('s := "ab";'))
    assert source_signature('<project><p>x<b/>y</p></project>')!=source_signature('<project><p>x<b/>z</p></project>')


@pytest.mark.parametrize('mode',['generate','repair'])
def test_internal_static_retry_queries_knowledge_and_stops_repeated_source(tmp_path,monkeypatch,mode):
    requests=[]
    class Client:
        def chat_completions(self,payload,**kwargs):
            requests.append(json.loads(json.dumps(payload)))
            return {'choices':[{'message':{'content':'<project><ST>x := 1;</ST></project>'}}]}
    generator=PLCGenerator(Client(),no_cards=True,max_rounds=5)
    generator.evidence_dir=tmp_path/'generation'
    monkeypatch.setattr(generator,'gate',lambda *args:(False,['cannot AND: cw is INT']))
    with pytest.raises(GenerationStalled):
        if mode=='generate':generator.generate({'io_list':[]})
        else:generator.repair('<project/>',{'io_list':[]},'compiler failure',attempts=5)
    assert len(requests)==2 and generator.stop_reason=='no_progress'
    assert 'P03' in requests[1]['messages'][-1]['content']
    assert 'WORD' in requests[1]['messages'][-1]['content']
    record=json.loads((generator.evidence_dir/'call_002.feedback.json').read_text(encoding='utf-8'))
    assert record['repeated'] and 'P03' in record['pitfalls'] and 'WORD' in record['advice']


def test_feedback_budget_keeps_later_error_families():
    generator=PLCGenerator(None,no_cards=True)
    errors=['[consistency] R1: missing %d'%i for i in range(30)]+['[consistency] R8: wrong Velocity']
    feedback=static_feedback(generator,errors,'<project/>')
    assert 'R1:' in feedback and 'R8:' in feedback


def test_recent_successful_lessons_include_concrete_change(tmp_path):
    memory=MemoryStore(lessons_path=tmp_path/'lessons.json')
    memory.record_lesson('acceptance',['missing waypoint'],'repair timing',
        diff_hunks=['-dwell := dwell + 1;\n+IF actual_done THEN dwell := dwell + 1; END_IF;'],
        shape='svg',outcome='resolved')
    assert 'IF actual_done' in memory.recent_lessons('svg')[0]['diff_hunks'][0]
    assert not memory.recent_lessons(None)


def test_preferred_candidate_retains_current_attribution(monkeypatch):
    captured={}
    def repair(self,previous,spec,feedback,**kwargs):
        captured.update(base=previous,feedback=feedback)
        return {'ok':False}
    monkeypatch.setattr(PLCGenerator,'repair',repair)
    generator=VerifiedPLCGenerator(None,no_cards=True)
    generator.preferred_candidate=('better source','PATH failed on better source')
    generator.repair('regressed source',{},'new attribution: never clear go_x_exe')
    assert captured['base']=='better source'
    assert 'PATH failed' in captured['feedback'] and 'never clear go_x_exe' in captured['feedback']


def test_repeated_static_source_stops_outer_loop_without_diagnosis(tmp_path,monkeypatch):
    spec=json.loads((ROOT/'examples/specs/motion3axis.spec.json').read_text(encoding='utf-8'))
    generator=PLCGenerator(object(),no_cards=True,max_rounds=5)
    calls=[]
    def call(*args,**kwargs):
        calls.append(1)
        return '<project><ST>x := 1;</ST></project>'
    monkeypatch.setattr(generator,'_call',call)
    monkeypatch.setattr(generator,'gate',lambda *args:(False,['cannot AND']))
    orch=Orchestrator(runs_root=tmp_path,max_iters=6)
    monkeypatch.setattr(orch.attribution,'attribute',lambda *args,**kwargs:pytest.fail('duplicate triggered diagnosis'))
    result=orch.solve(spec,generator)
    assert len(calls)==2 and result['stop_reason']=='no_progress'
    assert not (Path(result['run_dir'])/'final').exists()


@pytest.mark.parametrize('mode',['generate','repair'])
def test_truncated_outputs_have_bounded_output_failure_not_duplicate_source(tmp_path,mode):
    calls=[]
    class Client:
        def chat_completions(self,payload,**kwargs):
            calls.append(payload)
            return {'choices':[{'finish_reason':'length','message':{'content':'<project><truncated>'}}]}
    generator=PLCGenerator(Client(),no_cards=True,max_rounds=5)
    generator.evidence_dir=tmp_path
    with pytest.raises(GenerationStalled) as error:
        if mode=='generate':generator.generate({'io_list':[]})
        else:generator.repair('<project></project>',{'io_list':[]},'PATH failed')
    assert error.value.reason=='output_limit' and generator.stop_reason=='output_limit'
    assert len(calls)==2 and not generator._failed_candidates
    record=json.loads((tmp_path/'call_002.feedback.json').read_text(encoding='utf-8'))
    assert record['failure_kind']=='output_limit' and not record['repeated']
    assert not record['pitfalls'] and record['source_signature'] is None


@pytest.mark.parametrize('max_rounds',[1,5])
def test_output_failure_stops_outer_loop_without_plc_attribution(tmp_path,monkeypatch,max_rounds):
    spec=json.loads((ROOT/'examples/specs/motion3axis.spec.json').read_text(encoding='utf-8'))
    generator=PLCGenerator(None,no_cards=True,max_rounds=max_rounds)
    monkeypatch.setattr(generator,'_call',lambda *args,**kwargs:'[output_limit] truncated')
    orch=Orchestrator(runs_root=tmp_path,max_iters=6)
    monkeypatch.setattr(orch.attribution,'attribute',lambda *args,**kwargs:pytest.fail('output failure triggered PLC diagnosis'))
    result=orch.solve(spec,generator)
    assert result['stop_reason']=='output_limit'
    assert len(json.loads((tmp_path/spec['task_id']/'iter_001/gate.json').read_text(encoding='utf-8'))['errors'])==1
