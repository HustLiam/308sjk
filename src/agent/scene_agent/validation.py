"""Independent deterministic candidate and assembly checks; no LLM verdicts."""
from pathlib import Path
import numpy as np

from .blender import run_blender
from .common import SceneError, digest, expression, read_json, write_json
from .device_ir import values
from .rules import validate_rule


def instantiate_geometry(rule,parameters,directory, *, blender='E:/Blender/blender.exe'):
    """Build one parameter instance without modifying its reviewed source bundle."""
    checks=[];issues=list(rule['unresolved']);result=None
    def check(name,passed,detail=None):checks.append({'name':name,'passed':bool(passed),'detail':detail})
    model=validate_rule(rule,parameters)
    check('parameter_domain',True)
    check('declared_interfaces',isinstance(model.get('interfaces',[]),list))
    for geom in model.get('geoms',[]):
        check('dimensions:'+geom['name'],geom['type']=='mesh' or all(float(v)>0 for v in geom['size']))
    if rule.get('blender_script'):
        result=run_blender(rule['blender_script'],parameters,directory,executable=blender)
        for body in result['bodies']:
            check('manifold:'+body['name'],body['nonmanifold_edges']==0,{'volume_m3':body['volume_m3'],'bounds_m':body['bounds_m']})
            check('positive_volume:'+body['name'],body['volume_m3']>1e-12)
            actual=[g for g in model.get('geoms',[]) if g.get('body','root')==body['name'] and g.get('collision',True)]
            declared=body['collision']
            def same(a,b):
                return (a['type']==b['type'] and np.allclose(a['size'],b['size'],atol=1e-9)
                        and np.allclose(a.get('pos',[0,0,0]),b.get('pos',[0,0,0]),atol=1e-9)
                        and np.allclose(a.get('rpy_rad',[0,0,0]),b.get('rpy_rad',[0,0,0]),atol=1e-9))
            check('collision_proxy_matches_build:'+body['name'],len(actual)==len(declared) and all(any(same(a,b) for b in declared) for a in actual))
            if len(result['bodies'])==1 and all(k in parameters for k in ('width_m','depth_m','height_m')):
                expected=[parameters[k] for k in ('width_m','depth_m','height_m')]
                measured=np.asarray(body['bounds_m'][1])-body['bounds_m'][0]
                check('true_scale_bounds:'+body['name'],np.allclose(measured,expected,atol=1e-6),{'expected_m':expected,'actual_m':measured.tolist()})
            # Void probes must remain outside every collision primitive in the body.
            for i,probe in enumerate(result.get('clearance_probes',[])):
                if probe['body']!=body['name']:continue
                point=np.asarray(probe['point_m'],dtype=float);hit=False
                for g in body['collision']:
                    if g['type']!='box':issues.append('Clearance check currently requires box proxies');continue
                    center=np.asarray(g.get('pos',[0,0,0]),dtype=float)
                    if g.get('rpy_rad'):issues.append('Rotated collision proxy clearance check not implemented');continue
                    hit=hit or bool(np.all(np.abs(point-center)<=np.asarray(g['size'])+1e-9))
                check('clearance:'+body['name']+':'+str(i),not hit,probe)
        check('blend_and_preview',True,{'version':result['blender_version']})
        declared={g['body'] for g in model.get('blender_bodies',[])}
        check('export_body_mapping',declared=={b['name'] for b in result['bodies']})
    return result,checks,issues


def validate_candidate(library,path,parameters, *, blender='E:/Blender/blender.exe'):
    from .common import read_json, quaternion, pose_matrix
    path=Path(path);rule=read_json(path/'rule.json')
    _,checks,issues=instantiate_geometry(rule,parameters,path/'geometry',blender=blender)
    model=validate_rule(rule,parameters)
    def check(name,passed,detail=None):checks.append({'name':name,'passed':bool(passed),'detail':detail})
    # Compile rule geometry and internal joints in a minimal, isolated device.
    import xml.etree.ElementTree as ET
    import mujoco
    root=ET.Element('mujoco');ET.SubElement(root,'compiler',angle='radian')
    wb=ET.SubElement(root,'worldbody');bodies={'root':ET.SubElement(wb,'body',name='root')}
    assets=ET.SubElement(root,'asset');vfs={}
    ET.SubElement(assets,'texture',type='skybox',builtin='gradient',rgb1='.92 .95 .96',rgb2='.82 .87 .90',width='128',height='128')
    for sub in model.get('bodies',[]):
        if sub.get('parent','root') not in bodies:raise SceneError('Internal parent must precede child')
        bodies[sub['name']]=ET.SubElement(bodies[sub.get('parent','root')],'body',name=sub['name'],
            pos=' '.join(map(str,sub.get('position_m',[0,0,0]))),quat=' '.join(map(str,quaternion(pose_matrix(sub)[:3,:3]))))
    for g in model.get('geoms',[])+model.get('blender_bodies',[]):
        attrs={k:(' '.join(map(str,g[k])) if isinstance(g[k],list) else str(g[k])) for k in ('type','size','pos','mass','density','friction') if k in g}
        if 'rpy_rad' in g:attrs['quat']=' '.join(map(str,quaternion(pose_matrix({'rpy_rad':g['rpy_rad']})[:3,:3])))
        attrs['name']=g['name']
        if not g.get('collision',True):attrs.update(contype='0',conaffinity='0',group='5')
        if not g.get('collision',True) and 'mass' not in g and 'density' not in g:attrs['mass']='0'
        if g['type']=='mesh':
            from .common import confined
            mesh=confined(path,g['mesh'])
            key=g['name']+'.obj';vfs[key]=mesh.read_bytes()
            ET.SubElement(assets,'mesh',name=g['name'],file=key);attrs['mesh']=g['name']
        ET.SubElement(bodies[g.get('body','root')],'geom',**attrs)
    for joint in model.get('joints',[]):
        ET.SubElement(bodies[joint['body']],'joint',name=joint['name'],type=joint['type'],
                      axis=' '.join(map(str,joint.get('axis',[0,0,1]))),limited='true',range=' '.join(map(str,joint['range'])))
    equality=ET.SubElement(root,'equality')
    for constraint in model.get('equalities',[]):
        attrs={k:(' '.join(map(str,v)) if isinstance(v,list) else str(v)) for k,v in constraint.items() if k!='type'}
        ET.SubElement(equality,constraint['type'],**attrs)
    from .common import finalize_visual_inertia
    for body in bodies.values():finalize_visual_inertia(body)
    try:
        m=mujoco.MjModel.from_xml_string(ET.tostring(root,encoding='unicode'),assets=vfs);d=mujoco.MjData(m)
        for _ in range(50):mujoco.mj_step(m,d)
        check('isolated_mjcf_smoke',np.isfinite(d.qpos).all() and not any(w.number for w in d.warning),{'nbody':m.nbody,'nv':m.nv,'seconds':float(d.time)})
        if m.ngeom and not rule.get('blender_script'):
            from PIL import Image
            m.vis.headlight.active=1;m.vis.headlight.ambient[:]=[.55,.55,.55];m.vis.headlight.diffuse[:]=[.8,.8,.8]
            camera=mujoco.MjvCamera();camera.lookat[:]=m.stat.center;camera.distance=max(.3,1.1*m.stat.extent)
            camera.azimuth=135;camera.elevation=-30
            renderer=mujoco.Renderer(m,height=320,width=480)
            try:
                option=mujoco.MjvOption();option.geomgroup[:]=1
                renderer.update_scene(d,camera=camera,scene_option=option)
                Image.fromarray(renderer.render()).save(path/'preview.png')
            finally:renderer.close()
            check('component_preview',True)
        elif not m.ngeom:check('structural_container_without_geometry',True,'No rigid geometry to preview; inspect structural rule')
    except ValueError as exc:check('isolated_mjcf_smoke',False,str(exc))
    report={'status':'passed' if all(c['passed'] for c in checks) and not issues else 'failed',
            'checks':checks,'unresolved':issues,'parameters':parameters,
            'scope':'component geometry/internal-joint smoke; assembly/runtime behavior checks are separate',
            'plc_acceptance':'not_run'}
    token=library.submit_validation(path,report)
    return report,token


def check_assembly(metadata,xml, *, steps=500,preview=None):
    from runtime.device_scene import DeviceRuntime
    runtime=DeviceRuntime(metadata,xml)
    checks=list(metadata['model_checks'])
    for _ in range(steps):runtime.step()
    checks.append({'name':'physics_smoke','passed':bool(np.isfinite(runtime.data.qpos).all()),'seconds':float(runtime.data.time)})
    for jid,j in metadata['joints'].items():
        import mujoco
        ji=mujoco.mj_name2id(runtime.model,mujoco.mjtObj.mjOBJ_JOINT,j['mjcf_joint'])
        if j['type'] in ('slide','hinge'):
            checks.append({'name':'joint_range:'+jid,'passed':bool(np.allclose(runtime.model.jnt_range[ji],j['range']))})
    checks.append({'name':'io_coverage','passed':len(metadata['io'])==len(metadata['io_map'])})
    if preview:runtime.render(preview)
    return {'status':'passed' if all(c['passed'] for c in checks) else 'failed','checks':checks,
            'scene_sha256':metadata['model_xml_sha256'],'scope':'assembly smoke only; functional behavior validation required before freeze',
            'definition_sha256':runtime.definition_sha256,
            'behavior_checks_complete':False,'plc_acceptance':'not_run'}


def check_device_behaviors(metadata,xml, *, preview=None,record=None,verification_ir=None,test_plan=None,test_output=None):
    """Known-command model fixtures, independent of any generated PLC policy.

The integrated sorting fixture fires from measured product position to test
physical contact/transport only. It is never used as the runtime controller.
"""
    import mujoco
    from runtime.device_scene import DeviceRuntime
    report=check_assembly(metadata,xml,steps=100)
    checks=report['checks'];events=[]
    def check(name,passed,**details):checks.append({'name':name,'passed':bool(passed),**details})
    ports={(p['component_id'],p['quantity']):p['name'] for p in metadata['io']}
    by_kind={kind:[(cid,c) for cid,c in metadata['components'].items() if c['behavior']==kind]
             for kind in ('binary_prismatic','source','conveyor','ray','sink')}
    for cid,c in by_kind['binary_prismatic']:
        r=DeviceRuntime(metadata,xml);command=ports[(cid,'extend_cmd')];p=c['parameters']
        for _ in range(750):r.step({command:True})
        check('actuator_extended:'+cid,r.signals[ports[(cid,'extended')]] and not r.signals[ports[(cid,'retracted')]])
        for _ in range(750):r.step({command:False})
        check('actuator_retracted:'+cid,r.signals[ports[(cid,'retracted')]] and not r.signals[ports[(cid,'extended')]])
        for _ in range(50):r.step({command:True})
        j=metadata['joints'][p['driven_joint_id']];ji=mujoco.mj_name2id(r.model,mujoco.mjtObj.mjOBJ_JOINT,j['mjcf_joint']);qa=r.model.jnt_qposadr[ji]
        before=float(r.data.qpos[qa]);fault=ports.get((cid,'fault'))
        check('in_transit_feedback:'+cid,not r.signals[ports[(cid,'extended')]] and not r.signals[ports[(cid,'retracted')]],q=before)
        if fault:
            for _ in range(100):r.step({command:True},{fault:True})
            delta=abs(float(r.data.qpos[qa])-before)
            check('fault_holds_actual_position:'+cid,delta<.002,drift_m=delta)
    for source_id,source in by_kind['source']:
        r=DeviceRuntime(metadata,xml);emit=ports[(source_id,'emit_cmd')]
        for _ in range(10):r.step({emit:True})
        check('one_item_per_edge:'+source_id,len([e for e in r.events if e['event']=='spawn'])==1)
        item=next((i for i in r.products if i['active']),None)
        belt_ids={e['to'] for e in metadata['material_flow'] if e['from']==source_id}
        belts=[(cid,c) for cid,c in by_kind['conveyor'] if cid in belt_ids]
        if len(belts)!=1:check('source_flow_binding:'+source_id,False);continue
        belt_id,belt=belts[0];run=ports[(belt_id,'run_cmd')]
        start=r.data.xpos[item['bid']].copy()
        for _ in range(500):r.step({emit:False,run:True})
        direction=np.array([belt['parameters']['conveying_direction_'+a] for a in 'xyz'])
        displacement=float((r.data.xpos[item['bid']]-start)@direction)
        check('contact_driven_transport:'+source_id,displacement>.2,displacement_m=displacement)
        fault=ports.get((belt_id,'fault'))
        if fault:
            for _ in range(250):r.step({run:True},{fault:True})
            check('conveyor_fault_stops_transport:'+source_id,np.linalg.norm(r.data.qvel[item['v']:item['v']+3])<.05)
        # Beam classification comes from actual geometry, not a product label lookup.
        for sensor_id,sensor in by_kind['ray']:
            for product_id in (source['parameters']['short_product_id'],source['parameters']['tall_product_id']):
                probe=DeviceRuntime(metadata,xml)
                product=next(i for i in probe.products if i['product_id']==product_id)
                t=np.asarray(sensor['world_transform']);bp=belt['parameters']
                top=np.asarray(belt['world_transform'])[2,3]+bp['carrying_surface_offset_z_m']
                height=product['parameters']['size_z_m']
                position=np.array([t[0,3],np.asarray(belt['world_transform'])[1,3],top+height/2+.001])
                probe.data.qpos[product['q']:product['q']+7]=np.r_[position,[1,0,0,0]];product['active']=True
                for g in product['geoms']:probe.model.geom_contype[g]=1;probe.model.geom_conaffinity[g]=1
                mujoco.mj_forward(probe.model,probe.data);probe.step()
                beam_height=t[2,3]+sensor['parameters']['beam_origin_local_z_m']-top
                expected=0<=beam_height<=height
                check('ray_geometry:'+sensor_id+':'+product_id,probe.signals[ports[(sensor_id,'detect')]]==expected)
        # Full source -> conveyor -> physical push/chute -> measured outlets.
        if len(by_kind['binary_prismatic'])==1 and len(by_kind['sink'])==2:
            fixture=DeviceRuntime(metadata,xml);pusher_id,pusher=by_kind['binary_prismatic'][0]
            extend=ports[(pusher_id,'extend_cmd')]
            sequence=0
            def fixture_step(outputs):
                nonlocal sequence
                fixture.step(outputs);sequence+=1
                if record and sequence%25==1:
                    record({'time_s':float(fixture.data.time),
                        'transforms':[list(map(float,p))+list(map(float,r.ravel())) for p,r in zip(fixture.data.geom_xpos,fixture.data.geom_xmat)],
                        'signals':dict(fixture.signals)})
            fixture_step({emit:True,run:True})
            for _ in range(4000):fixture_step({emit:False})
            fixture_step({emit:True});fired=False;released=False
            for _ in range(6000):
                tall=[i for i in fixture.products if i['active'] and i['product_id']==source['parameters']['tall_product_id']]
                if tall and not fired:
                    threshold=np.asarray(pusher['world_transform'])[0,3]-.15
                    fired=bool(fixture.data.xpos[tall[0]['bid'],0]>=threshold)
                if fixture.signals[ports[(pusher_id,'extended')]]:released=True
                fixture_step({emit:False,extend:fired and not released})
                if len([e for e in fixture.events if e['event']=='outlet_entry'])>=2:break
            if record:
                mujoco.mj_forward(fixture.model,fixture.data)
                record({'time_s':float(fixture.data.time),
                    'transforms':[list(map(float,p))+list(map(float,r.ravel())) for p,r in zip(fixture.data.geom_xpos,fixture.data.geom_xmat)],
                    'signals':dict(fixture.signals)})
            entries=[e for e in fixture.events if e['event']=='outlet_entry'];events.extend(fixture.events)
            flow=metadata['material_flow']
            straight={e['to'] for e in flow if e['from']==belt_id and e['a_port']=='main_discharge'}
            chutes={e['to'] for e in flow if e['from']==belt_id and e['a_port']=='side_discharge'}
            side={e['to'] for e in flow if e['from'] in chutes}
            short_name=next(i['category'] for i in fixture.products if i['product_id']==source['parameters']['short_product_id'])
            tall_name=next(i['category'] for i in fixture.products if i['product_id']==source['parameters']['tall_product_id'])
            check('actual_short_outlet:'+source_id,any(e['category']==short_name and e['outlet_id'] in straight for e in entries))
            check('actual_tall_outlet:'+source_id,any(e['category']==tall_name and e['outlet_id'] in side for e in entries))
            check('one_count_per_item:'+source_id,len({e['item_id'] for e in entries})==len(entries)==2)
            if preview:fixture.render(preview)
    # This fixture is specifically for the reference height-sorter topology.
    # Other devices can be built/previewed but require their own reviewed fixtures.
    complete=(len(by_kind['binary_prismatic'])==1 and len(by_kind['source'])==1
              and len(by_kind['conveyor'])==1 and len(by_kind['sink'])==2
              and len(by_kind['ray'])>=1
              and len(metadata['joints'])==len(by_kind['binary_prismatic'])
              and not any(c.get('internal_joints') for c in metadata['components'].values()))
    if metadata.get('simulation_requirements_sha256') or verification_ir is not None:
        from .simulation_tests import generate_test_plan, run_test_plan
        from .device_ir import facts_digest
        ir=verification_ir if verification_ir is not None else read_json(Path(metadata['asset_root'])/'device_ir.json')
        if (facts_digest(ir)!=metadata['device_ir_sha256'] or
                (metadata.get('simulation_requirements_sha256') and digest(ir.get('simulation_requirements'))!=metadata['simulation_requirements_sha256'])):
            raise SceneError('Simulation requirements differ from model facts','simulation_test')
        tests=run_test_plan(ir,metadata,xml,test_plan or generate_test_plan(ir),output=test_output)
        report['simulation_tests']={k:tests[k] for k in ('status','complete','plan_sha256','checker_sha256')}
        # An explicitly scoped AML can validate a different device through its
        # fixed mechanical/IO checkers. Legacy devices still need their fixture.
        complete=(complete or bool(ir.get('simulation_requirements'))) and tests['complete']
    check('functional_fixture_coverage',complete,fixture='aml_scoped_tests' if metadata.get('simulation_requirements_sha256') else 'height_sorter_v1')
    report.update(status='passed' if all(c['passed'] for c in checks) else 'failed',
                  behavior_checks_complete=complete,scope='independent height-sorter model fixtures; not PLC acceptance',events=events)
    return report
