"""Resource imports are deterministic and must never manufacture acceptance facts."""
import json
from pathlib import Path

import mujoco
import numpy as np
import pytest

from src.agent.scene_agent.common import SceneError
from src.agent.scene_agent.resources import PackageResources
from src.agent.scene_agent.package_import import import_package, inspect_package, frame
from src.agent.scene_agent.collada_import import ColladaImporter


GEOMETRY = '''<geometry id="shape"><mesh>
<source id="pos"><float_array id="arr">0 0 0 100 0 0 0 100 0 0 0 100</float_array>
<technique_common><accessor source="#arr" count="4" stride="3"/></technique_common></source>
<vertices id="v"><input semantic="POSITION" source="#pos"/></vertices>
<triangles count="4"><input semantic="VERTEX" source="#v" offset="0"/><p>0 2 1 0 1 3 0 3 2 1 2 3</p></triangles>
</mesh></geometry>'''


@pytest.fixture
def package(tmp_path):
    source = tmp_path/'source';source.mkdir()
    (source/'part.dae').write_text('''<COLLADA xmlns="http://www.collada.org/2008/03/COLLADASchema" version="1.5.0">
<asset><unit meter="0.001"/><up_axis>Z_UP</up_axis></asset>
<library_geometries>'''+GEOMETRY+'''</library_geometries>
<library_nodes><node id="base"><instance_geometry url="#shape"/>
<node id="mount" sid="mount"><translate>0 0 100</translate></node></node></library_nodes>
<library_visual_scenes><visual_scene id="scene"><instance_node url="#base"/></visual_scene></library_visual_scenes>
<scene><instance_visual_scene url="#scene"/></scene></COLLADA>''', encoding='utf-8')
    (source/'cell.aml').write_text('''<CAEXFile SchemaVersion="2.15"><InstanceHierarchy Name="Cell">
<InternalElement ID="part" Name="Part"><Attribute Name="Frame"><Attribute Name="x"><Value>2</Value></Attribute></Attribute>
<ExternalInterface ID="mesh" Name="Representation" RefBaseClassPath="COLLADAInterface">
<Attribute Name="refURI"><Value>part.dae</Value></Attribute><Attribute Name="refType"><Value>explicit</Value></Attribute></ExternalInterface>
<InternalElement ID="port" Name="Port"><ExternalInterface ID="implicit" Name="Frame" RefBaseClassPath="COLLADAInterface">
<Attribute Name="refURI"><Value>part.dae#base</Value></Attribute><Attribute Name="refType"><Value>implicit</Value></Attribute>
<Attribute Name="target"><Value>./mount</Value></Attribute></ExternalInterface>
<ExternalInterface ID="attach" Name="Mount" RefBaseClassPath="AttachmentInterface"/></InternalElement>
</InternalElement></InstanceHierarchy></CAEXFile>''', encoding='utf-8')
    return source/'cell.aml'


def test_import_real_mujoco_preserves_scale_pose_and_implicit_frame(package, tmp_path):
    report = import_package(package,tmp_path/'out')
    assert report['assembly']['compiled']
    assert report['counts']['imported_meshes'] == 1
    assert report['simulation_acceptance'] == 'blocked' and not report['model_frozen']
    ledger=json.loads((tmp_path/'out/data_destinations.json').read_text(encoding='utf-8'))
    port=next(n for n in ledger['nodes'] if n['id']=='port')
    assert np.allclose(np.array(port['world'])[:3,3],[2,0,.1])
    model=mujoco.MjModel.from_xml_path(str(tmp_path/'out/scene.xml'))
    assert model.ngeom==1 and np.max(np.ptp(model.mesh_vert,axis=0)) < .2
    assert np.all(model.geom_contype==0)  # No source collision contract: never claim contact-ready.
    assert (tmp_path/'out/source/part.dae').read_bytes()==(package.parent/'part.dae').read_bytes()


@pytest.mark.parametrize('uri',['../secret.xml','file:///C:/secret.xml','https://example.com/a.dae','//server/a','%2e%2e/secret.xml'])
def test_resource_paths_confined(package,uri):
    with pytest.raises(SceneError):PackageResources(package.parent).resolve(package,uri)


def test_missing_asset_is_reported_not_replaced_by_agent(package,tmp_path):
    package.write_text(package.read_text().replace('part.dae','absent.dae'))
    report=import_package(package,tmp_path/'out')
    assert any(i['code']=='component_import_failed' for i in report['issues'])
    assert report['counts']['imported_meshes']==0
    assert not report['model_frozen']
    from src.agent.scene_agent import package_web
    state=package_web.state({'scene_kind':'out'},tmp_path,False)
    assert report['assembly']['compiled']  # Compilation alone does not make a visible scene.
    assert not state['scene_ready'] and state['resource_preview_issue']['code']=='no_geometry'
    assert state['device_ir']['components']  # Preserve the parsed facts for resolving resources.
    with pytest.raises(SceneError,match='未加载三维几何'):
        package_web.model(tmp_path,'out')


def test_explicit_alias_preserves_actual_filename_and_source_hash(package):
    original=package.parent/'part.dae';original.rename(package.parent/'part .dae')
    resources=PackageResources(package.parent,{'part.dae':'part .dae'})
    path,node=resources.element(package,'part.dae#base')
    assert path.name=='part .dae' and node.get('id')=='base'
    assert resources.references[-1]['alias']=='part .dae'
    resources.verify()
    path.write_text('<changed/>')
    with pytest.raises(SceneError,match='变化'):resources.verify()


def test_external_entity_and_reference_cycles_rejected(package):
    resources=PackageResources(package.parent)
    f=package.parent/'bad.xml';f.write_text('<!DOCTYPE a [<!ENTITY b SYSTEM "file:///secret">]><a/>')
    with pytest.raises(SceneError,match='DTD'):resources.document(f)
    f.write_bytes('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE a [<!ENTITY b "value">]><a>&b;</a>'.encode('utf-16'))
    with pytest.raises(SceneError,match='DTD'):resources.document(f)
    f=package.parent/'part.dae';f.write_text(f.read_text().replace('<instance_geometry url="#shape"/>','<instance_node url="#base"/>'))
    with pytest.raises(SceneError,match='循环'):ColladaImporter(resources).scene(package,'part.dae')


def test_caex_named_link_and_frame_units(package):
    content=package.read_text().replace('</InstanceHierarchy>', '<InternalElement ID="other" Name="Other"><ExternalInterface ID="otherport" Name="Mount" RefBaseClassPath="AttachmentInterface"/><InternalLink Name="join" RefPartnerSideA="[port]:[Mount]" RefPartnerSideB="[other]:[Mount]"/></InternalElement></InstanceHierarchy>')
    package.write_text(content)
    _,manifest=inspect_package(package)
    assert manifest['connections'][0]['a']=='attach'
    assert manifest['connections'][0]['b']=='otherport'
    t=frame({'Frame':{'children':{'x':{'value':'100','unit':'mm'},'rz':{'value':'90'}}}})
    assert np.allclose(t[:3,3],[.1,0,0]) and np.allclose(t[:3,:3]@[1,0,0],[0,1,0],atol=1e-9)


def test_kinematic_binding_creates_actual_slide_and_retains_initial(package,tmp_path):
    f=package.parent/'part.dae'
    content=f.read_text().replace('<node id="mount" sid="mount"><translate>0 0 100</translate></node>',
        '<node id="moving"><translate>10 0 0</translate><instance_geometry url="#shape"/></node><node id="mount" sid="mount"><translate>0 0 100</translate></node>')
    kin='''<library_joints><joint id="j"><prismatic><axis>1 0 0</axis><limits><min>0</min><max>100</max></limits></prismatic></joint></library_joints>
<library_kinematics_models><kinematics_model id="km"><technique_common><instance_joint sid="slide" url="#j"/>
<link name="base"><attachment_full joint="km/slide"><link name="moving"/></attachment_full></link></technique_common></kinematics_model></library_kinematics_models>
<library_kinematics_scenes><kinematics_scene id="ks"><newparam sid="a"><SIDREF>ks/km/slide/axis</SIDREF></newparam><newparam sid="v"><float>10</float></newparam></kinematics_scene></library_kinematics_scenes>'''
    content=content.replace('<scene>',kin+'<scene>').replace('</scene>','<instance_kinematics_scene><bind_kinematics_model node="base"/><bind_joint_axis target="moving/axis"><axis><param>a</param></axis><value><param>v</param></value></bind_joint_axis></instance_kinematics_scene></scene>')
    f.write_text(content)
    report=import_package(package,tmp_path/'out')
    assert report['assembly']['compiled'] and report['assembly']['movable_joints']==1
    m=mujoco.MjModel.from_xml_path(str(tmp_path/'out/scene.xml'));d=mujoco.MjData(m);mujoco.mj_forward(m,d)
    assert np.allclose(m.jnt_range[0],[0,.1]) and d.qpos[0]==pytest.approx(.01)
    bid=m.jnt_bodyid[0];before=d.xpos[bid].copy();d.qpos[0]+=.02;mujoco.mj_forward(m,d)
    assert np.allclose(d.xpos[bid]-before,[.02,0,0])


def test_attachment_builds_physical_parent_without_moving_source_geometry(package,tmp_path):
    source=package.read_text()
    second='''<InternalElement ID="tool" Name="Tool"><ExternalInterface ID="toolmesh" Name="Mesh" RefBaseClassPath="COLLADAInterface">
    <Attribute Name="refURI"><Value>part.dae</Value></Attribute><Attribute Name="refType"><Value>explicit</Value></Attribute></ExternalInterface>
    <ExternalInterface ID="toolport" Name="Mount" RefBaseClassPath="AttachmentInterface"/>
    <InternalLink Name="install" RefPartnerSideA="[port]:[Mount]" RefPartnerSideB="[tool]:[Mount]"/></InternalElement>'''
    package.write_text(source.replace('</InstanceHierarchy>',second+'</InstanceHierarchy>'))
    report=import_package(package,tmp_path/'out')
    assert report['assembly']['compiled']
    assert report['connections'][0]['assembly_status']=='attached_preserving_source_pose'
    ledger=json.loads((tmp_path/'out/data_destinations.json').read_text(encoding='utf-8'))
    tool=next(n for n in ledger['nodes'] if n['id']=='tool')
    assert tool['parent']=='port' and np.allclose(tool['world'],np.eye(4))
    assert report['assembly']['max_body_position_error_m']<1e-9
    assert report['assembly']['attachment_checks'][0]['position_residual_m']>1


def test_import_web_does_not_invoke_author_and_cannot_freeze(package,tmp_path):
    import time
    from src.agent.scene_agent.web import SceneWorkbench,SceneWebError
    def forbidden(*args):raise AssertionError('Existing assets must not invoke author')
    workbench=SceneWorkbench(tmp_path/'web',author_factory=forbidden)
    try:
        state=workbench.import_package({'path':str(package)})
        deadline=time.monotonic()+15
        while state['busy'] and time.monotonic()<deadline:
            time.sleep(.02);state=workbench.state(state['id'])
        assert not state['busy'] and state['scene_ready'] and not state['can_build']
        assert state['resource_preview_issue'] is None
        view=workbench.geometry(state['id'])
        assert view['geoms'] and view['mesh_assets'] and view['preview_quality']['display_only']
        quality=view['preview_quality']
        assert not quality['simplified'] and quality['original_faces']==quality['display_faces']
        json.dumps(view)  # Geometry metadata stays small and JSON serializable.
        mesh,item=next(iter(view['mesh_assets'].items()))
        assert 'data' not in item and item['url'].startswith('/api/device-scene/mesh?')
        payload=workbench.mesh_asset(state['id'],view['joint_preview_version'],mesh)
        assert len(payload)==item['byte_length']==item['vertices']*24
        with pytest.raises(SceneWebError,match='版本'):
            workbench.mesh_asset(state['id'],'stale',mesh)
        with pytest.raises(SceneWebError,match='网格不存在'):
            workbench.mesh_asset(state['id'],view['joint_preview_version'],'../../other')
        assert workbench.file(state['id'],'imported_scene/data_destinations.json').is_file()
        import io,zipfile
        with zipfile.ZipFile(io.BytesIO(workbench.bundle(state['id']))) as archive:
            assert 'imported_scene/source/part.dae' in archive.namelist()
            assert 'imported_scene/meshes/m0.obj' in archive.namelist()
        with pytest.raises(SceneWebError):workbench.operate(state['id'],state['revision'],'freeze')
    finally:workbench.close()


def test_aml_upload_does_not_claim_external_assets_were_uploaded(package,tmp_path):
    import base64,time
    from src.agent.scene_agent.web import SceneWorkbench
    def forbidden(*args):raise AssertionError('Missing assets require a user choice')
    workbench=SceneWorkbench(tmp_path/'web',author_factory=forbidden)
    try:
        state=workbench.import_package({'aml':{'name':package.name,
            'data':base64.b64encode(package.read_bytes()).decode()}})
        deadline=time.monotonic()+15
        while state['busy'] and time.monotonic()<deadline:
            time.sleep(.02);state=workbench.state(state['id'])
        assert not state['busy'] and not state['error']
        assert not state['scene_ready'] and state['package_counts']['imported_meshes']==0
        assert state['resource_preview_issue']['code']=='no_geometry'
        assert any(p['code']=='component_import_failed' for p in state['pending'])
        assert '未加载三维几何' in state['stage']
    finally:workbench.close()


def test_full_preview_preserves_all_faces_and_vertex_positions():
    from src.agent.scene_agent.package_preview import display_mesh
    vertices=np.array([[x/30,y/30,0] for y in range(31) for x in range(31)])
    faces=[]
    for y in range(30):
        for x in range(30):
            a=y*31+x;faces.extend([[a,a+1,a+31],[a+1,a+32,a+31]])
    original=vertices.copy();source_faces=np.array(faces);saved_faces=source_faces.copy()
    packed=display_mesh(vertices,source_faces)
    assert packed['vertices']//3==len(source_faces) and packed['max_vertex_displacement_m']==0
    assert np.array_equal(original,vertices) and np.array_equal(saved_faces,source_faces)
    decoded=np.frombuffer(packed['data'],dtype='<f4').reshape(-1,6)
    assert np.array_equal(decoded[:,:3],vertices[source_faces].reshape(-1,3).astype('<f4'))
    assert len(decoded)==packed['vertices'] and np.isfinite(decoded).all()
    assert np.allclose(np.linalg.norm(decoded[:,3:],axis=1),1)


def test_full_preview_preserves_tiny_and_degenerate_faces():
    from src.agent.scene_agent.package_preview import display_mesh
    vertices=np.array([[0.,0.,0.],[1e-10,0.,0.],[0.,1e-10,0.]])
    faces=np.array([[0,1,2],[2,1,0],[0,0,1]])
    packed=display_mesh(vertices,faces)
    decoded=np.frombuffer(packed['data'],dtype='<f4').reshape(-1,6)
    assert packed['vertices']==9 and np.isfinite(decoded).all()
    assert np.array_equal(decoded[:,:3],vertices[faces].reshape(-1,3).astype('<f4'))


def test_replacement_mechanism_preserves_implicit_node_mapping(package,tmp_path):
    replacement=package.parent/'replacement.dae'
    (package.parent/'part.dae').rename(replacement)
    report=import_package(package,tmp_path/'out',resolutions={'part':{'action':'import','scope':'task',
        'asset':'replacement.dae','model_to_component':np.eye(4).tolist()}})
    implicit=next(r for r in report['resource_mapping'] if r['ref_type']=='implicit')
    assert implicit['status']=='bound_existing_node' and implicit['resolution_uri']=='replacement.dae#base'
    assert report['assembly']['compiled'] and report['assembly']['actual_geoms']==1
