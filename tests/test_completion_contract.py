import copy
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from agent.completion_contract import CompletionConflict, validate_completion_contract


def fixture():
    spec = {'io_list': [
        {'name': 'permit', 'type': 'BOOL', 'dir': 'input'},
        {'name': 'finished', 'type': 'BOOL', 'dir': 'output'},
        {'name': 'active', 'type': 'BOOL', 'dir': 'output'},
        {'name': 'pressure', 'type': 'INT', 'dir': 'input', 'range': [0, 100]}],
        'acceptance': [{'id': 'SAFE', 'type': 'forbidden_state',
                        'when': {'signal': 'pressure', 'op': '>', 'value': 5},
                        'forbid': {'signal': 'active', 'equals': False}}]}
    plan = {'initial_inputs': {'permit': False}, 'actions': [],
            'completion': {'all': [{'signal': 'finished', 'equals': True}]},
            'path_checks': [{'id': 'ROUTE', 'terminal': True, 'points': [
                {'values': {'pressure': 20}, 'tolerance': {'pressure': 2}, 'states': {'active': True}}]}]}
    return plan, spec


def test_compatible_terminal():
    p, s = fixture()
    validate_completion_contract(p, s)


def test_conflicting_completion_and_terminal_state():
    p, s = fixture()
    p['completion']['all'].append({'signal': 'active', 'equals': False})
    with pytest.raises(CompletionConflict, match='active'):
        validate_completion_contract(p, s)


def test_conflicting_duplicate_completion():
    p, s = fixture()
    p['completion']['all'].append({'signal': 'finished', 'equals': False})
    with pytest.raises(CompletionConflict, match='finished'):
        validate_completion_contract(p, s)


def test_numeric_terminal_conflict():
    p, s = fixture()
    p['completion']['all'].append({'signal': 'pressure', 'target': 40, 'tolerance': 1})
    with pytest.raises(CompletionConflict, match='pressure'):
        validate_completion_contract(p, s)


def test_nonterminal_checkpoint_does_not_constrain_final_state():
    p, s = fixture()
    p['path_checks'][0]['terminal'] = False
    p['completion']['all'].append({'signal': 'active', 'equals': False})
    validate_completion_contract(p, s)


def test_terminal_and_forbidden_condition_conflict():
    p, s = fixture()
    del p['path_checks'][0]['points'][-1]['states']['active']
    p['completion']['all'].append({'signal': 'active', 'equals': False})
    with pytest.raises(CompletionConflict, match='SAFE'):
        validate_completion_contract(p, s)


def test_uncertain_interval_is_not_claimed_impossible():
    p, s = fixture()
    p['path_checks'][0]['points'][-1].update(values={'pressure': 5}, tolerance={'pressure': 2}, states={})
    p['completion']['all'].append({'signal': 'active', 'equals': False})
    validate_completion_contract(p, s)


def test_control_input_is_not_completion_evidence():
    p, s = fixture()
    p['completion']['all'] = [{'signal': 'permit', 'equals': True}]
    with pytest.raises(CompletionConflict, match='控制输入'):
        validate_completion_contract(p, s)


@pytest.mark.parametrize('operator,threshold', [('<', 30), ('<=', 22), ('>', 10), ('>=', 18)])
def test_generic_thresholds(operator, threshold):
    p, s = fixture()
    p['path_checks'][0]['points'][0]['states'] = {}
    p['completion']['all'].append({'signal': 'active', 'equals': False})
    s['acceptance'][0]['when'] = {'signal': 'pressure', 'op': operator, 'value': threshold}
    with pytest.raises(CompletionConflict, match='SAFE'):
        validate_completion_contract(p, s)


def test_multiple_checkpoints_and_final_route():
    p, s = fixture()
    intermediate = copy.deepcopy(p['path_checks'][0])
    intermediate.update(id='INTERMEDIATE', terminal=False)
    intermediate['points'][0]['states']['active'] = False
    p['path_checks'].insert(0, intermediate)
    validate_completion_contract(p, s)
