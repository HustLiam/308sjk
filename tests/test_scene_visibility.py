"""Visibility follows AML ownership and optional compiled mechanical attachments."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


def node_json(code):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for viewer checks')
    result = subprocess.run([node, '-e', code], cwd=Path(__file__).resolve().parents[1],
                            text=True, encoding='utf-8', capture_output=True, check=True)
    return json.loads(result.stdout)


def test_visibility_includes_only_requested_hierarchy_and_optional_attachments():
    result = node_json("""
      require('./src/agent/web_ui/device_tree.js');
      const ir={components:[{id:'station'},{id:'arm',parent_id:'station'},
        {id:'link',parent_id:'arm'},{id:'tool',parent_id:'station'},
        {id:'cabinet',parent_id:'station'}]};
      const attached={link:['tool'],tool:['arm','unknown']};
      console.log(JSON.stringify({plain:[...DeviceTree.componentSet(ir,['arm'])].sort(),
        linked:[...DeviceTree.componentSet(ir,['arm'],attached)].sort(),
        unknown:[...DeviceTree.componentSet(ir,['unknown'])],
        station:[...DeviceTree.componentSet(ir,['station'],attached)].sort()}));
    """)
    assert result['plain'] == ['arm', 'link']
    assert result['linked'] == ['arm', 'link', 'tool']
    assert result['unknown'] == []
    assert result['station'] == ['arm', 'cabinet', 'link', 'station', 'tool']


def test_fit_uses_visible_geometry_and_does_not_change_model_poses():
    result = node_json("""
      const fs=require('fs'),vm=require('vm');
      vm.runInThisContext(fs.readFileSync('./src/agent/web_ui/scene_view.js','utf8'));
      const view=Object.create(SceneViewer.prototype);
      view.host={clientWidth:1000,clientHeight:800};
      view.meshes=[{componentId:'arm',size:[1,1,1],geomType:6},
        {componentId:'other',size:[1,1,1],geomType:6}];
      view.current={transforms:[[0,0,0,1,0,0,0,1,0,0,0,1],
        [100,0,0,1,0,0,0,1,0,0,0,1]]};
      const before=JSON.stringify(view.current);view.setVisibleComponents(['arm']);
      view.fitVisible({top:110,bottom:230});
      const selected={center:view.center,span:view.span,hidden:!view.isComponentVisible('other')};
      view.setVisibleComponents(null);view.fitVisible();
      console.log(JSON.stringify({selected,all:view.center,unchanged:before===JSON.stringify(view.current)}));
    """)
    assert result['selected']['center'] == [0, 0, 0]
    assert result['selected']['span'] >= 2
    assert result['selected']['hidden']
    assert result['all'] == [50, 0, 0]
    assert result['unchanged']


def test_pan_moves_projected_model_with_pointer_at_different_orbits_and_zoom():
    result = node_json("""
      const fs=require('fs'),vm=require('vm');
      vm.runInThisContext(fs.readFileSync('./src/agent/web_ui/scene_view.js','utf8'));
      const view=Object.create(SceneViewer.prototype);
      view.host={clientHeight:800};view.canvas={width:1200,height:800};
      view.center=[2,3,4];view.span=5;view.meshes=[];view.current={transforms:[]};
      view.resize=()=>{};view.drawMechanism=()=>{};view.uniform={vp:'vp'};
      view.gl={viewport(){},clearColor(){},clear(){},useProgram(){},
        uniformMatrix4fv:(key,transpose,value)=>{if(key==='vp')view.vp=value;}};
      const project=()=>{view.draw(0);const p=view.center,m=view.vp,
        c=[0,1,2,3].map(i=>m[i]*p[0]+m[i+4]*p[1]+m[i+8]*p[2]+m[i+12]);
        return [(c[0]/c[3]+1)*600,(1-c[1]/c[3])*400];};
      const before=JSON.stringify({center:view.center,frame:view.current});
      const moves=[[135,-27,1.9],[0,-70,.6],[270,-10,5]].map(camera=>{
        view.camera=[...camera,0,0];const from=project();view.panBy(60,35);const to=project();
        view.camera[0]+=40;view.camera[1]=-45;const rotated=project();
        return {delta:to.map((v,i)=>v-from[i]),rotatedDelta:rotated.map((v,i)=>v-to[i])};
      });
      console.log(JSON.stringify({moves,unchanged:before===JSON.stringify({center:view.center,frame:view.current})}));
    """)
    for move in result['moves']:
        assert move['delta'] == pytest.approx([60, 35], abs=.001)
        assert move['rotatedDelta'] == pytest.approx([0, 0], abs=.001)
    assert result['unchanged']


def test_render_bounds_follow_rotated_mesh_and_leave_model_unchanged():
    result = node_json("""
      const fs=require('fs'),vm=require('vm');
      vm.runInThisContext(fs.readFileSync('./src/agent/web_ui/scene_view.js','utf8'));
      const mesh={bounds:SceneViewer.vertexBounds([1,2,3,0,0,1,3,6,5,0,0,1])};
      const t=[10,20,30,0,-1,0,1,0,0,0,0,1],before=JSON.stringify({mesh,t});
      console.log(JSON.stringify({bounds:SceneAppearance.bounds(mesh,t),unchanged:before===JSON.stringify({mesh,t})}));
    """)
    assert result['bounds']['low'] == [4, 21, 33]
    assert result['bounds']['high'] == [8, 23, 35]
    assert result['unchanged']


def test_automatic_materials_distinguish_roles_and_preserve_authored_colors():
    result = node_json("""
      const fs=require('fs'),vm=require('vm');
      vm.runInThisContext(fs.readFileSync('./src/agent/web_ui/scene_view.js','utf8'));
      const style=Object.create(SceneAppearance.prototype);style.materials=new Map();
      const components=[{id:'r',type:'Robot'},{id:'c',type:'Clamp'},{id:'m',type:'Machine'}];
      const meshes=components.map(c=>({componentId:c.id,color:[.3,.4,.5,1]}));
      style.setComponents(components,true);const automatic=meshes.map(m=>style.material(m));
      style.setComponents(components,false);const authored=meshes.map(m=>style.material(m));
      console.log(JSON.stringify({automatic,authored,source:meshes.map(m=>m.color)}));
    """)
    assert len({tuple(m['color']) for m in result['automatic']}) == 3
    assert result['automatic'][1]['metalness'] > result['automatic'][0]['metalness']
    assert all(m['color'] == [.3,.4,.5] for m in result['authored'])
    assert result['source'] == [[.3,.4,.5,1]] * 3
