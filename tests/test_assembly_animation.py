"""Streaming assembly preserves source poses, full meshes and model/control boundaries."""
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.agent.scene_agent.assembly_preview import AssemblyPreview
from src.agent.scene_agent.package_import import import_package
from src.agent.scene_agent.web import SceneWorkbench, SceneWebError
from test_resource_import import package


def test_import_emits_real_geometry_before_compilation_and_final_binding(package, tmp_path):
    stream = AssemblyPreview('task')
    snapshots = []
    output = tmp_path/'out'
    def publish(report, nodes, meshes):
        assert not (output/'assembly_checks.json').exists()
        stream.publish(report, nodes, meshes)
        snapshots.append(stream.read())
    report = import_package(package, output, preview=publish)
    assert report['assembly']['compiled']
    assert snapshots[0]['geoms'] == []
    assert any(s['geoms'] for s in snapshots[1:])
    assert len(stream.assets) == 1  # shared by all snapshots, no repeated packing
    snapshot = snapshots[-1]
    assert snapshot['source'] == 'resource_import_in_progress'
    assert 'joint_preview_version' not in snapshot  # cannot be controlled as a compiled model
    assert snapshot['transforms'][0][:3] == pytest.approx([2, 0, 0])
    asset = snapshot['mesh_assets']['0']
    packed = np.frombuffer(stream.mesh(stream.token, '0'), dtype='<f4').reshape(-1, 6)
    assert len(packed) == asset['vertices'] == 12
    assert np.max(packed[:, :3]) == pytest.approx(.1)
    with pytest.raises(KeyError):stream.mesh('stale', '0')
    with pytest.raises(KeyError):stream.mesh(stream.token, '../0')
    assert snapshots[0]['mesh_assets'] == {}  # publication is immutable
    import_package(package, tmp_path/'without_preview')
    assert (output/'scene.xml').read_bytes() == (tmp_path/'without_preview/scene.xml').read_bytes()


def test_stream_is_scoped_to_latest_import_and_closed_with_workbench(tmp_path):
    wb = SceneWorkbench(tmp_path)
    try:
        wb.assembly_publisher('one')
        wb.assembly_publisher('two')
        assert set(wb.assembly_previews) == {'two'}
        with pytest.raises(SceneWebError):wb.assembly_view('../outside')
    finally:wb.close()
    assert not wb.assembly_previews


def node(code):
    result = subprocess.run(['node', '-e', "const fs=require('fs'),vm=require('vm');vm.runInThisContext(fs.readFileSync('src/agent/web_ui/scene_view.js','utf8'));"+code],
                            cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_animation_waits_for_component_meshes_and_never_writes_model_pose():
    result = node("""
      const v=Object.create(SceneViewer.prototype),now=performance.now();
      v.host={dataset:{}};v.status={};v.meshes=[{componentId:'parent',count:3},{componentId:'child',count:0}];
      v.assembly={steps:new Map([['parent',{start:null,indices:[0],lift:2}],['child',{start:null,indices:[1],lift:2}]]),nextStart:now,gap:100,downloaded:false};
      const pose=[1,2,3,1,0,0,0,1,0,0,0,1],before=JSON.stringify(pose);
      v.stageAssembly();const pending=v.assembly.steps.get('child').start===null;
      const visible=v.assemblyVisible(v.meshes[0]),hidden=!v.assemblyVisible(v.meshes[1]),moving=v.assemblyTransform(v.meshes[0],pose);
      v.meshes[1].count=3;v.stageAssembly();const ordered=v.assembly.steps.get('child').start>v.assembly.steps.get('parent').start;
      for(const step of v.assembly.steps.values())step.start=performance.now()-1000;
      const landed=v.assemblyTransform(v.meshes[0],pose);v.assembly.downloaded=true;v.stageAssembly();
      console.log(JSON.stringify({pending,visible,hidden,ordered,moving,landed,unchanged:before===JSON.stringify(pose),finished:v.assembly===null}));
    """)
    assert all(result[k] for k in ('pending','visible','hidden','ordered','unchanged','finished'))
    assert result['moving'][2] > 3
    assert result['landed'] == [1,2,3,1,0,0,0,1,0,0,0,1]


def test_failed_animation_stops_progress_and_buffer_cleanup_is_shared():
    result = node("""
      const v=Object.create(SceneViewer.prototype);let removed=[];
      v.host={dataset:{assembling:'false'}};v.status={textContent:'failed'};
      v.assembly={failed:true};v.stageAssembly();
      v.gl={deleteBuffer:b=>removed.push(b)};
      v.assemblyBuffers=new Map([['a',{buffer:1}],['b',{buffer:2}]]);
      v.releaseAssemblyBuffers(new Set(['b']));v.releaseAssemblyBuffers();v.releaseAssemblyBuffers();
      console.log(JSON.stringify({removed,status:v.status.textContent,assembling:v.host.dataset.assembling,size:v.assemblyBuffers.size}));
    """)
    assert result == {'removed':[1,2],'status':'failed','assembling':'false','size':0}


def test_assembly_offset_does_not_lift_ground_above_final_model():
    result = node("""
      const a=Object.create(SceneAppearance.prototype),gl=new Proxy({},{get:()=>()=>{}});
      const mesh={componentId:'part',geomType:6,count:3,bounds:{center:[0,0,0],half:[.5,.5,.5]}};
      const pose=[0,0,0,1,0,0,0,1,0,0,0,1];
      a.gl=gl;a.view={gl,meshes:[mesh],current:{transforms:[pose]},previous:null,
        selectedIds:new Set(),canvas:{width:100,height:100},isComponentVisible:()=>true,
        assemblyVisible:()=>true,assemblyTransform:t=>t};
      a.view.assemblyTransform=(mesh,t)=>{const p=t.slice();p[2]+=2;return p;};
      Object.assign(a,{components:new Map(),materials:new Map(),groundZ:null,floorSelection:null,
        shadow:{ready:false},surface:{u:{}},floor:{count:6}});
      a.draw([],[],1,new Set());console.log(JSON.stringify({ground:a.groundZ,pose}));
    """)
    assert result['ground'] < -.5
    assert result['pose'][:3] == [0,0,0]
