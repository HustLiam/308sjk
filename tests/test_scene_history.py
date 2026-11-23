"""Batch cleanup validates a current selection and preserves shared resources."""
import base64
from pathlib import Path
import sys
import time

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from agent.scene_agent.web import SceneWorkbench,SceneWebError
from agent.scene_agent.common import read_json,write_json


@pytest.fixture
def history(tmp_path):
    manager=SceneWorkbench(tmp_path/'device-scenes')
    ids=[]
    for i in range(3):
        aml=f'<CAEXFile SchemaVersion="3.0"><InstanceHierarchy Name="Fixture"><InternalElement Name="part" ID="part-{i}" RefBaseSystemUnitPath="Parts/StaticFixture"/></InstanceHierarchy></CAEXFile>'
        state=manager.upload({'aml':{'name':f'fixture-{i}.aml','data':base64.b64encode(aml.encode()).decode()}})
        deadline=time.monotonic()+10
        while state['busy'] and time.monotonic()<deadline:time.sleep(.01);state=manager.state(state['id'])
        assert not state['busy'] and not state['error'];ids.append(state['id'])
    approved=manager.library.root/'approved/sentinel.json';write_json(approved,{'must':'remain'})
    plc=tmp_path/'sessions/plc.json';write_json(plc,{'must':'remain'})
    yield manager,ids,approved,plc
    manager.close()


def selection(manager,ids):return {'task_ids':ids,'revisions':{i:manager.state(i)['revision'] for i in ids}}


def test_batch_history_clears_only_selected_tasks_and_caches(history):
    manager,ids,approved,plc=history
    result=manager.delete_history(selection(manager,ids[:2]))
    assert result['retired_tasks']==ids[:2] and result['cache_status']=='cleared'
    assert {r['id'] for r in manager.listing()['tasks']}=={ids[2]}
    assert all(not manager.directory(i).exists() for i in ids[:2])
    assert read_json(approved)=={'must':'remain'} and read_json(plc)=={'must':'remain'}
    assert set(read_json(manager.root/'cache_reset.json')['retired_tasks'])==set(ids[:2])
    assert not list((manager.root/'.history-trash').iterdir())
    manager.delete_history(selection(manager,[ids[2]]))
    assert not manager.listing()['tasks']


@pytest.mark.parametrize('bad',['stale','missing','traversal','duplicates','busy'])
def test_invalid_batch_is_rejected_before_any_task_moves(history,bad):
    manager,ids,_,_=history;data=selection(manager,ids[:2])
    if bad=='stale':data['revisions'][ids[1]]=-1
    elif bad=='missing':data['task_ids'][1]='a'*32;data['revisions']={ids[0]:data['revisions'][ids[0]],'a'*32:0}
    elif bad=='traversal':data={'task_ids':['../component_library'],'revisions':{'../component_library':0}}
    elif bad=='duplicates':data['task_ids']=[ids[0],ids[0]]
    else:manager.active=ids[2]
    try:
        with pytest.raises(SceneWebError):manager.delete_history(data)
        assert all(manager.directory(i).is_dir() for i in ids)
        assert not (manager.root/'.history-trash').exists()
    finally:manager.active=None


def test_failed_batch_move_rolls_back_moved_records(history,monkeypatch):
    manager,ids,_,_=history;original=Path.rename
    def fail_second(path,target):
        if path.name==ids[1] and path.parent==manager.root/'tasks':raise PermissionError('test fixture move failure')
        return original(path,target)
    monkeypatch.setattr(Path,'rename',fail_second)
    with pytest.raises(PermissionError):manager.delete_history(selection(manager,ids[:2]))
    assert all(manager.directory(i).is_dir() for i in ids)
    assert not (manager.root/'cache_reset.json').exists()
