"""Interface edits preserve IO, scope and the original frozen device facts."""
import copy
import json
import math
from pathlib import Path
import shutil
import subprocess

import pytest

from src.agent.scene_agent.common import SceneError
from src.agent.scene_agent.device_ir import parse_device_ir, facts_digest
from src.agent.scene_agent.properties import apply_property_patch, interface_properties

REPO=Path(__file__).resolve().parents[1]


@pytest.fixture
def ir():return parse_device_ir(REPO/'examples/aml/factoryforge_sorting_reference.aml')


def patch(ir,scope,op):
    return {'base_sha256':facts_digest(ir),'editable_ids':scope,'reason':'Explicit fixture interface revision','operations':[op]}


def add_mount(ir,cid=''):
    cid=cid or ir['components'][0]['id']
    return apply_property_patch(ir,patch(ir,[cid],{'op':'add_interface','component_id':cid,'interface_id':'mount_fixture',
        'name':'fixture_mount','position_m':[.1,.2,.3],'rpy_rad':[0,0,math.pi/2]}))


def test_add_mount_is_local_revision_without_new_io_or_topology(ir):
    revised=add_mount(ir);cid=ir['components'][0]['id']
    assert revised['io']==ir['io'] and revised['connections']==ir['connections'] and revised['joints']==ir['joints']
    assert revised['components'][1:]==ir['components'][1:]
    assert revised['components'][0]['interfaces']==ir['components'][0]['interfaces']+['mount_fixture']
    assert revised['revision']==ir['revision']+1 and ir['revision']==0
    assert interface_properties(revised)['mount_fixture']['position_m']==[.1,.2,.3]
    assert interface_properties(revised)['mount_fixture']['rpy_rad']==pytest.approx([0,0,math.pi/2])


def test_legacy_degree_frame_edit_preserves_original_provenance(ir):
    revised=add_mount(ir);frame=revised['interfaces'][-1]
    frame['parameters']['rotation_z_deg'].update(value=90,original_unit=None,original_value=90,unit=None)
    cid=frame['component_id'];p=patch(revised,[cid],{'op':'set_interface_pose','component_id':cid,'interface_id':frame['id'],
        'position_m':[.3,.2,.1],'rpy_rad':[0,0,math.pi]})
    changed=apply_property_patch(revised,p);record=changed['interfaces'][-1]['parameters']['rotation_z_deg']
    assert record['value']==pytest.approx(180) and record['original_value']==90 and record['original_unit'] is None
    assert interface_properties(changed)[frame['id']]['rpy_rad']==pytest.approx([0,0,math.pi])
    assert changed['io']==ir['io']


def test_joint_anchor_requires_joint_and_both_bodies_then_updates_canonical_anchor(ir):
    joint=ir['joints'][0]
    frame=next(f for f in ir['interfaces'] if f['component_id']==joint['parent_body'] and f['type']=='JointAttachment')
    info=interface_properties(ir)[frame['id']]
    assert set(info['required_ids'])=={joint['id'],joint['parent_body'],joint['child_body']}
    assert info['position_m']==joint['parent_anchor_m'] and info['rpy_rad'] is None
    op={'op':'set_interface_pose','component_id':frame['component_id'],'interface_id':frame['id'],'position_m':[0,0,.25]}
    with pytest.raises(SceneError,match='connected nodes'):apply_property_patch(ir,patch(ir,[frame['component_id']],op))
    revised=apply_property_patch(ir,patch(ir,info['required_ids'],op))
    assert revised['joints'][0]['parent_anchor_m']==[0,0,.25] and ir['joints'][0]['parent_anchor_m']==[0,0,.21]
    assert next(c for c in revised['components'] if c['id']==joint['id'])['parameters']['parent_anchor_z_m']['value']==.25
    assert revised['io']==ir['io'] and revised['connections']==ir['connections']
    assert [c for c in revised['components'] if c['id']!=joint['id']]==[c for c in ir['components'] if c['id']!=joint['id']]


def test_joint_anchor_does_not_accept_unused_rotation(ir):
    props=interface_properties(ir);f=next(f for f in ir['interfaces'] if props[f['id']]['joint_bindings'])
    op={'op':'set_interface_pose','component_id':f['component_id'],'interface_id':f['id'],'position_m':[0,0,0],'rpy_rad':[0,0,1]}
    with pytest.raises(SceneError,match='defined by its axis'):apply_property_patch(ir,patch(ir,props[f['id']]['required_ids'],op))


def test_signal_interfaces_cannot_be_edited_through_pose_properties(ir):
    f=next(f for f in ir['interfaces'] if f['type']=='SignalPort')
    op={'op':'set_interface_pose','component_id':f['component_id'],'interface_id':f['id'],'position_m':[0,0,0],'rpy_rad':[0,0,0]}
    with pytest.raises(SceneError,match='read-only'):apply_property_patch(ir,patch(ir,[f['component_id']],op))


@pytest.mark.parametrize('point',[[0,False,0],[0,float('nan'),0],[0,0],[0,'1',0]])
def test_non_numeric_or_nonfinite_interface_positions_are_rejected(ir,point):
    cid=ir['components'][0]['id'];op={'op':'add_interface','component_id':cid,'interface_id':'m','name':'m','position_m':point,'rpy_rad':[0,0,0]}
    with pytest.raises(SceneError,match='finite numeric'):apply_property_patch(ir,patch(ir,[cid],op))


def test_interface_owner_scope_staleness_and_duplicates(ir):
    revised=add_mount(ir);f=revised['interfaces'][-1];cid=f['component_id']
    op={'op':'set_interface_pose','component_id':cid,'interface_id':f['id'],'position_m':[0,0,0],'rpy_rad':[0,0,0]}
    p=patch(revised,[cid],op)
    with pytest.raises(SceneError,match='Stale'):apply_property_patch(ir,p)
    with pytest.raises(SceneError,match='scope'):apply_property_patch(revised,{**p,'editable_ids':[]})
    other=ir['components'][1]['id']
    with pytest.raises(SceneError,match='owner'):apply_property_patch(revised,patch(revised,[other],{**op,'component_id':other}))
    with pytest.raises(SceneError,match='duplicate'):add_mount(revised)
    with pytest.raises(SceneError,match='separate'):apply_property_patch(revised,{**p,'operations':[op,{'op':'set_pose','component_id':cid,'position_m':[0,0,0],'rpy_rad':[0,0,0]}]})


def test_mechanical_mount_link_requires_both_components(ir):
    revised=add_mount(ir);second=ir['components'][1]['id']
    op={'op':'add_interface','component_id':second,'interface_id':'second_mount','name':'mount','position_m':[0,0,0],'rpy_rad':[0,0,0]}
    revised=apply_property_patch(revised,patch(revised,[second],op));owner=ir['components'][0]['id']
    revised['assemblies']=[{'id':'fixture_link','parent_component':owner,'child_component':second,
        'parent_interface':'mount_fixture','child_interface':'second_mount','placement':'solve'}]
    props=interface_properties(revised)['mount_fixture'];assert set(props['required_ids'])=={owner,second}
    op={'op':'set_interface_pose','component_id':owner,'interface_id':'mount_fixture','position_m':[0,0,0],'rpy_rad':[0,0,0]}
    with pytest.raises(SceneError,match='connected nodes'):apply_property_patch(revised,patch(revised,[owner],op))


def tree_projection(ir,body):
    node=shutil.which('node')
    if not node:pytest.skip('Node is needed for the browser tree projection check')
    script="require('./src/agent/web_ui/device_tree.js');const fs=require('fs'),ir=JSON.parse(fs.readFileSync(0,'utf8'));"+body
    response=subprocess.run([node,'-e',script],cwd=REPO,input=json.dumps(ir),capture_output=True,text=True,check=True)
    return json.loads(response.stdout)


def test_joint_class_is_peer_of_components_and_instances_only_reference_bodies(ir):
    result=tree_projection(ir,"""const before=JSON.stringify(ir),t=DeviceTree.build(ir);
console.log(JSON.stringify({parents:Object.fromEntries(t.parents),roots:t.roots,ids:[...t.nodes.keys()],kinds:[...t.nodes.values()].map(n=>n.kind),
nodes:[...t.nodes.values()].map(n=>({id:n.id,kind:n.kind,target:n.targetId,side:n.side})),jointClass:t.jointClassId,
issues:t.issues,unchanged:JSON.stringify(ir)===before,children:[...t.children.values()].flat()}));""")
    joint=ir['joints'][0];by_id={c['id']:c for c in ir['components']}
    assert result['parents'][joint['id']]==result['jointClass']
    assert result['parents'][joint['child_body']]==by_id[joint['child_body']]['parent_id']
    assert result['parents'][joint['parent_body']]==by_id[joint['parent_body']]['parent_id']
    assert result['jointClass'] in result['roots'] and ir['components'][0]['id'] in result['roots']
    assert set(result['kinds'])=={'component','joint_class','joint','reference','interface'}
    components=[n['id'] for n in result['nodes'] if n['kind']=='component']
    assert len(components)==17 and joint['id'] not in components
    refs=[n for n in result['nodes'] if n['kind']=='reference']
    assert {(n['side'],n['target']) for n in refs}=={('parent',joint['parent_body']),('child',joint['child_body'])}
    assert all(result['parents'][n['id']]==joint['id'] for n in refs)
    assert len(result['ids'])==len(set(result['ids']))
    shown={n['id'] for n in result['nodes'] if n['kind']=='interface'}
    expected={f['id'] for f in ir['interfaces'] if f['type'] in ('JointAttachment','MechanicalMount') and f['component_id'] not in {j['id'] for j in ir['joints']}}
    assert shown==expected
    assert all(result['parents'][f['id']]==f['component_id'] for f in ir['interfaces'] if f['id'] in shown)
    assert len(result['children'])+len(result['roots'])==len(set(result['ids']))
    assert result['unchanged'] and not result['issues']


def test_constraint_loops_and_mounts_do_not_reparent_component_instances(ir):
    result=tree_projection(ir,"""const j=ir.joints[0];ir.joints.push({...j,id:'second_joint',name:'return_constraint',parent_body:j.child_body,child_body:j.parent_body});
const conveyor=ir.components.find(c=>c.name==='conveyor'),sensor=ir.components.find(c=>c.name==='sensor_low');
ir.assemblies.push({parent_component:conveyor.id,child_component:sensor.id});const t=DeviceTree.build(ir);
console.log(JSON.stringify({parents:Object.fromEntries(t.parents),issues:t.issues,jointCount:[...t.nodes.values()].filter(n=>n.kind==='joint').length,
refCount:[...t.nodes.values()].filter(n=>n.kind==='reference').length,virtualJointComponent:t.nodes.get('second_joint').component}));""")
    joint=ir['joints'][0];source={c['id']:c for c in ir['components']}
    assert result['parents'][joint['parent_body']]==source[joint['parent_body']]['parent_id']
    assert result['parents'][joint['child_body']]==source[joint['child_body']]['parent_id']
    sensor=next(c for c in ir['components'] if c['name']=='sensor_low')
    assert result['parents'][sensor['id']]==sensor['parent_id']
    assert result['jointCount']==2 and result['refCount']==4
    assert result['virtualJointComponent'] is None and not result['issues']


def test_joint_reference_errors_component_cycles_and_virtual_ids_stay_visible(ir):
    result=tree_projection(ir,"""const root=ir.components[0],oldId=root.id;root.id='@joint-class';for(const c of ir.components)if(c.parent_id===oldId)c.parent_id=root.id;
const p=ir.components.find(c=>c.name==='pusher'),housing=ir.components.find(c=>c.name==='pusher_housing');p.parent_id=housing.id;
ir.joints[0].child_body='missing_body';const t=DeviceTree.build(ir);
console.log(JSON.stringify({jointClass:t.jointClassId,rootKind:t.nodes.get(root.id).kind,issues:t.issues,roots:t.roots,
invalidRefs:[...t.nodes.values()].filter(n=>n.kind==='reference'&&!n.valid).map(n=>n.targetId),ids:[...t.nodes.keys()]}));""")
    assert result['jointClass']!='@joint-class' and result['rootKind']=='component'
    assert 'missing_body' in result['invalidRefs']
    assert {'cycle','missing_component_reference'}.issubset({x['kind'] for x in result['issues']})
    assert result['roots'] and len(result['ids'])==len(set(result['ids']))
