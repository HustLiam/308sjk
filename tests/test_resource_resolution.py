"""Real MuJoCo checks for explicit completion and task-scoped generated geometry."""
import base64
import json
import time
import xml.etree.ElementTree as ET

import mujoco
import numpy as np
import pytest

from src.agent.scene_agent.common import SceneError
from src.agent.scene_agent.package_import import import_package
from src.agent.scene_agent.resource_mechanics import FORMAT
from src.agent.scene_agent.resource_resolution import generate_box, obj_mesh, rigid_matrix
from src.agent.scene_agent.web import SceneWorkbench, SceneWebError


IDENTITY = np.eye(4).tolist()
PROPOSAL = {'tool':'box','size_parameters':['size_x_m','size_y_m','size_z_m']}


def definition(connection=None):
    return {'format':FORMAT,'interfaces':{'mount':IDENTITY},'connection':connection or {'type':'root'}}


@pytest.fixture
def source(tmp_path):
    root=ET.Element('CAEXFile',SchemaVersion='2.15')
    hierarchy=ET.SubElement(root,'InstanceHierarchy',Name='Cell')
    c=ET.SubElement(hierarchy,'InternalElement',ID='part',Name='Part')
    for name,value in {'geometry_role':'physical','primitive_shape':'box','geometry_origin':'center',
                       'size_x_m':'.2','size_y_m':'.3','size_z_m':'.4',
                       'MechanicalDefinition':json.dumps(definition())}.items():
        ET.SubElement(ET.SubElement(c,'Attribute',Name=name),'Value').text=value
    file=tmp_path/'cell.aml';ET.ElementTree(root).write(file,encoding='utf-8')
    return file


def wait(workbench, state):
    deadline=time.monotonic()+20
    while state['busy'] and time.monotonic()<deadline:
        time.sleep(.02);state=workbench.state(state['id'])
    assert not state['busy']
    return state


def operation(workbench, state, action, **extra):
    return wait(workbench,workbench.resolve_resource({'task':state['id'],'revision':state['revision'],
                'component_id':'part','action':action,**extra}))


def test_missing_physical_and_structural_classification(source,tmp_path):
    tree=ET.parse(source);h=tree.getroot().find('InstanceHierarchy')
    ET.SubElement(h,'InternalElement',ID='unknown',Name='Unknown')
    structural=ET.SubElement(h,'InternalElement',ID='group',Name='Group')
    ET.SubElement(structural,'RoleRequirements',RefBaseRoleClassPath='AML/Structure')
    tree.write(source)
    report=import_package(source,tmp_path/'out')
    assert {r['component_id']:r['status'] for r in report['component_resolution']}=={
        'part':'missing_geometry','unknown':'needs_classification','group':'structural'}
    assert report['component_resolution'][0]['options']==['import','temporary']
    assert report['assembly']['compiled'] and not report['model_frozen']


def test_temporary_generation_requires_choice_and_never_reuses_task_cache(source,tmp_path):
    calls=[]
    class Agent:
        def author_resource_component(self,component,evidence):
            calls.append(component['id']);return PROPOSAL
    workbench=SceneWorkbench(tmp_path/'web',author_factory=lambda control:Agent())
    try:
        state=wait(workbench,workbench.import_package({'path':str(source)}))
        assert not calls
        state=operation(workbench,state,'temporary')
        assert state['error'] is None and calls==['part']
        assert state['resource_resolution'][0]['status']=='resolved'
        assert state['package_assembly']['actual_geoms']==1
        assert state['package_assembly']['structural_checks_passed']
        assert state['package_assembly']['interface_sites'][0]['interface']=='mount'
        assert not state['resource_library'] and not state['model_frozen']
        old=state['resource_resolution'][0]['decision']['generation_id']
        new=wait(workbench,workbench.import_package({'source_task':state['id']}))
        assert new['resource_resolution'][0]['status']=='missing_geometry' and calls==['part']
        new=operation(workbench,new,'temporary')
        assert calls==['part','part'] and new['resource_resolution'][0]['decision']['generation_id']!=old
        with pytest.raises(SceneWebError,match='不需要'):
            operation(workbench,new,'temporary')
    finally:workbench.close()


def test_agent_cannot_invent_geometry_or_modify_mechanics(source,tmp_path):
    component=import_package(source,tmp_path/'out')['components'][0]
    with pytest.raises(SceneError,match='只支持'):
        generate_box(component,{'tool':'box','size':[10,10,10]},tmp_path/'bad.obj')
    del component['attributes']['geometry_origin']
    with pytest.raises(SceneError,match='原点'):
        generate_box(component,PROPOSAL,tmp_path/'bad.obj')
    assert not (tmp_path/'bad.obj').exists()


def test_upload_missing_asset_import_and_saved_binding(source,tmp_path):
    def forbidden(control):raise AssertionError('Import must not call agent')
    workbench=SceneWorkbench(tmp_path/'web',author_factory=forbidden)
    try:
        state=wait(workbench,workbench.import_package({'aml':{'name':'cell.aml','data':base64.b64encode(source.read_bytes()).decode()}}))
        report=json.loads((workbench.directory(state['id'])/state['scene_kind']/'package_manifest.json').read_text(encoding='utf-8'))
        asset=tmp_path/'box.obj';generate_box(report['components'][0],PROPOSAL,asset)
        with pytest.raises(SceneError,match='单位'):
            operation(workbench,state,'import',path=str(asset),model_to_component=IDENTITY)
        state=operation(workbench,state,'import',path=str(asset),unit='m',up_axis='Z_UP',model_to_component=IDENTITY)
        assert state['error'] is None and state['resource_resolution'][0]['status']=='resolved'
        new=wait(workbench,workbench.import_package({'path':str(source)}))
        assert new['resource_resolution'][0]['decision']['binding_reused']
        # Publication is explicit and requires applicability metadata.
        with pytest.raises(SceneError,match='版本'):
            operation(workbench,state,'publish',version='',scope='')
        state=operation(workbench,state,'publish',version='1.0.0',scope='该尺寸方盒')
        assert len(state['resource_library'])==1
        assert state['resource_library'][0]['dimensions_m']==[.2,.3,.4]
    finally:workbench.close()


def test_bad_agent_output_leaves_previous_scene_visible(source,tmp_path):
    class Agent:
        def author_resource_component(self,*args):return {'tool':'execute','code':'arbitrary()'}
    workbench=SceneWorkbench(tmp_path/'web',author_factory=lambda c:Agent())
    try:
        state=wait(workbench,workbench.import_package({'path':str(source)}));old=state['scene_kind']
        state=operation(workbench,state,'temporary')
        assert state['error'] and state['scene_kind']==old
        assert state['resource_resolution'][0]['status']=='missing_geometry'
    finally:workbench.close()


def test_external_mechanical_definition_and_conflict(source,tmp_path):
    tree=ET.parse(source);c=tree.getroot().find('.//InternalElement')
    f=ET.SubElement(c,'ExternalInterface',ID='mechanics',Name='Mechanics',RefBaseClassPath='MechanicalDefinitionInterface')
    ET.SubElement(ET.SubElement(f,'Attribute',Name='refURI'),'Value').text='mechanics.json'
    tree.write(source)
    external=tmp_path/'mechanics.json';external.write_text(json.dumps(definition()))
    report=import_package(source,tmp_path/'same')
    assert len(report['mechanical_definition']['components'][0]['sources'])==2
    assert (tmp_path/'same/source/mechanics.json').exists()
    changed=definition();changed['interfaces']['other']=IDENTITY
    external.write_text(json.dumps(changed))
    report=import_package(source,tmp_path/'conflict')
    assert any(i['code']=='mechanical_definition_conflict' for i in report['issues'])
    assert not report['mechanical_definition']['components']


def test_inline_slide_is_defined_by_interfaces_in_actual_mujoco(source,tmp_path):
    tree=ET.parse(source);h=tree.getroot().find('InstanceHierarchy')
    c=ET.SubElement(h,'InternalElement',ID='slider',Name='Slider')
    d=definition({'type':'slide','parent_component':'part','parent_interface':'mount','child_interface':'mount',
                  'range':[0,.1],'initial':0,'speed':.02,'acceleration':.1})
    ET.SubElement(ET.SubElement(c,'Attribute',Name='MechanicalDefinition'),'Value').text=json.dumps(d)
    tree.write(source)
    report=import_package(source,tmp_path/'out')
    assert report['assembly']['movable_joints']==1
    checks=report['assembly']['structural_checks']
    assert all(c['passed'] for c in checks if c['check'] in ('body_parent','joint_definition'))
    assert any(c['check']=='geometry_dimensions' and not c['passed'] for c in checks)  # No geometry supplied yet.
    m=mujoco.MjModel.from_xml_path(str(tmp_path/'out/scene.xml'));data=mujoco.MjData(m)
    assert np.allclose(m.jnt_axis[0],[0,0,1]) and np.allclose(m.jnt_range[0],[0,.1])
    data.qpos[0]=.05;mujoco.mj_forward(m,data)
    assert data.xpos[m.jnt_bodyid[0],2]==pytest.approx(.05)


def test_unreasonable_coordinate_mapping_and_invalid_mesh_rejected(tmp_path):
    matrix=np.eye(4);matrix[0,0]=2
    with pytest.raises(SceneError,match='拉伸'):rigid_matrix(matrix)
    file=tmp_path/'bad.obj';file.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 0 2 3\n')
    with pytest.raises(SceneError,match='零'):obj_mesh(file,'m','Z_UP')


def test_empty_visual_reference_requires_purpose_confirmation_not_forced_generation(tmp_path):
    from src.agent.scene_agent.resource_resolution import inventory
    component={'id':'frame','name':'Frame','role':{},'attributes':{}}
    report={'components':[component],'resource_mapping':[{'component_id':'frame','geometry':True,'status':'imported'}]}
    row=inventory(report,{}, {})[0]
    assert row['status']=='needs_classification' and row['options']==['physical','structural']
    assert inventory(report,{}, {'frame':{'action':'structural'}})[0]['status']=='structural'
