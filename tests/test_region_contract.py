"""Feedback region binding is explicit, ordered and checked before PLC generation."""
import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'src/pipeline'))
from agent.completion_contract import CompletionConflict, validate_completion_contract
from agent.requirement import RequirementUnderstander
from agent.spec_validator import validate_requirement_spec
from agent.test_plan import TestPlanGenerator, PlanFeasibilityError
from mujoco_verdict import evaluate
from src.pipeline.region_contract import terminal_region_problems


def spec():
    return json.loads((ROOT / 'examples/specs/plotter_square_csp.spec.json').read_text(encoding='utf-8'))


def region(signal='x_fb', target=50, tolerance=2, identifier='AC91'):
    return {'id': identifier, 'desc': 'explicit feedback terminal target', 'type': 'region_containment',
            'asset': 'axis_or_process', 'signals': [signal], 'region_center': [target],
            'tolerance': tolerance, 'check_at': 'end'}


@pytest.mark.parametrize('name', ['pump_pressure', 'conveyor_position', 'cylinder_stroke'])
def test_scalar_feedback_ignores_unselected_values(name):
    ac = region(name, 7)
    assert evaluate([ac], [{'t': 0, name: 7, 'unrelated': -999}])[0]['pass']
    assert not evaluate([ac], [{'t': 0, name: 20, 'unrelated': 7}])[0]['pass']


def test_binding_order_is_declared_not_address_or_alphabetical():
    ac = region()
    ac.update(signals=['b', 'a'], region_center=[70, 10])
    assert evaluate([ac], [{'t': 0, 'a': 10, 'b': 70}])[0]['pass']
    assert not evaluate([ac], [{'t': 0, 'a': 70, 'b': 10}])[0]['pass']


def test_missing_feedback_fails_instead_of_truncating_zip():
    ac = region()
    ac.update(signals=['a', 'b'], region_center=[10, 20])
    assert not evaluate([ac], [{'t': 0, 'a': 10}])[0]['pass']


@pytest.mark.parametrize('signals,center', [([], []), (['x_fb','x_fb'], [50,50]),
    (['x_fb','y_fb'], [50]), (None, [50]), (['bad.name'], [50]), ([['x_fb']], [50])])
def test_malformed_bindings_fail_closed(signals, center):
    ac = region()
    ac.update(signals=signals, region_center=center)
    s = spec()
    s['acceptance'] = [ac]
    assert validate_requirement_spec(s)
    assert not evaluate([ac], [{'t': 0, 'x_fb': 50}])[0]['pass']


@pytest.mark.parametrize('signal', ['missing', 'x_cmd', 'run'])
def test_region_must_reference_declared_numeric_input(signal):
    s = spec()
    s['acceptance'] = [region(signal)]
    assert any('input INT' in e for e in validate_requirement_spec(s))


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True])
def test_nonfinite_and_boolean_targets_are_rejected(value):
    s = spec()
    s['acceptance'] = [region(target=value)]
    assert validate_requirement_spec(s)
    assert not evaluate(s['acceptance'], [{'t': 0, 'x_fb': 50}])[0]['pass']


def test_contradictory_regions_rejected_but_touching_intervals_allowed():
    s = spec()
    s['acceptance'] = [region(target=50), region(target=55, identifier='AC92')]
    assert any('无共同INT值' in e for e in validate_requirement_spec(s))
    s['acceptance'][1]['region_center'] = [54]
    assert validate_requirement_spec(s) == []


def test_empty_integer_intersection_is_not_considered_feasible():
    s = spec()
    s['acceptance'] = [region(target=50.5, tolerance=.1)]
    assert terminal_region_problems(s)


def test_legacy_whole_region_preserves_existing_semantics():
    s = spec()
    ac = next(a for a in s['acceptance'] if a['type'] == 'region_containment')
    channels = [{'name': n, 'type': 'analog', 'dir': 'input', 'axis': axis, 'addr': i}
                for i, (n, axis) in enumerate([('x_fb','X'),('y_fb','Y'),('z_fb','Z')])]
    sample = dict(zip(['x_fb','y_fb','z_fb'], ac['region_center']), t=0)
    assert evaluate([ac], [sample], channels)[0]['pass']
    assert validate_requirement_spec(s) == []


def test_ambiguous_axis_asset_stops_before_any_planner_call(tmp_path):
    s = spec()
    ac = region()
    del ac['signals']
    ac.update(asset=s['axes'][0]['axis'], region_center=[50,0,0])
    s['acceptance'] = [ac]
    assert any('signals' in e for e in validate_requirement_spec(s))
    class NeverClient:
        def chat_completions(self, *args, **kwargs):
            pytest.fail('Invalid requirement must stop before model planning or PLC generation')
    with pytest.raises(PlanFeasibilityError, match='先修正规格'):
        TestPlanGenerator(NeverClient()).generate('task', s, tmp_path)


def test_region_conflict_with_completion_or_terminal_waypoint():
    s = spec()
    s['acceptance'] = [region(target=50)]
    p = {'initial_inputs': {'run': False}, 'actions': [], 'path_checks': [],
         'completion': {'all': [{'signal': 'plot_done', 'equals': True},
                                {'signal': 'x_fb', 'target': 80, 'tolerance': 1}]}}
    with pytest.raises(CompletionConflict, match='终态区域'):
        validate_completion_contract(p, s)
    p['completion']['all'].pop()
    p['path_checks'] = [{'id': 'HOME', 'terminal': True, 'points': [
        {'values': {'x_fb': 80}, 'tolerance': {'x_fb': 1}, 'states': {}}]}]
    with pytest.raises(CompletionConflict, match='最终路点'):
        validate_completion_contract(p, s)


def test_new_model_requirement_repairs_missing_explicit_binding():
    bad = spec()
    bad['acceptance'] = [region()]
    bad['acceptance'][0].update(region_center=[50,50,10], asset='gantry')
    del bad['acceptance'][0]['signals']
    good = copy.deepcopy(bad)
    good['acceptance'][0]['signals'] = ['x_fb','y_fb','z_fb']
    class Client:
        calls = []
        def chat_completions(self, payload):
            self.calls.append(copy.deepcopy(payload))
            return {'choices': [{'message': {'content': json.dumps(bad if len(self.calls) == 1 else good)}}]}
    client = Client()
    result = RequirementUnderstander(client).understand('generic terminal goal')
    assert result['spec'] is not None and len(client.calls) == 2
    assert 'signals' in client.calls[1]['messages'][-1]['content']


def test_schema_supports_explicit_scalar_and_rejects_legacy_short_center():
    js = pytest.importorskip('jsonschema')
    schema = json.loads((ROOT / 'schemas/requirement_spec.schema.json').read_text(encoding='utf-8'))
    s = spec()
    s['acceptance'] = [region()]
    js.validate(s, schema)
    del s['acceptance'][0]['signals']
    with pytest.raises(js.ValidationError):
        js.validate(s, schema)


def test_bad_root_types_are_reported_instead_of_crashing_validator():
    s = spec()
    s.update(io_list=None, acceptance=None, axes=None)
    assert validate_requirement_spec(s)


def test_region_prompt_example_matches_schema_and_declares_feedback():
    js = pytest.importorskip('jsonschema')
    prompt = (ROOT / 'src/agent/prompts/specgen_skill.md').read_text(encoding='utf-8')
    line = next(line for line in prompt.splitlines() if line.startswith('- `region_containment`'))
    example = json.loads(line.split('：`', 1)[1].split('`', 1)[0])
    example.update(id='AC1', desc='terminal feedback target', type='region_containment')
    schema = json.loads((ROOT / 'schemas/requirement_spec.schema.json').read_text(encoding='utf-8'))
    js.validate(example, schema['definitions']['ac_region_containment'])
    assert len(example['signals']) == len(example['region_center'])
