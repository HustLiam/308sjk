"""Trusted background Blender worker. Run with Blender, never system Python."""
import argparse
import ast
import json
import math
from pathlib import Path
import re
import sys

import bpy
import bmesh
from mathutils import Matrix, Vector


class GeometryAPI:
    def __init__(self):self.objects={};self.bodies=[];self.probes=[];self.operations=0

    def _name(self,name):
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',name) or name in self.objects:
            raise ValueError('Invalid/duplicate geometry name')
        self.operations+=1
        if self.operations>256:raise ValueError('Geometry operation budget exceeded')

    def box(self,name,size,position=(0,0,0)):
        self._name(name)
        if len(size)!=3 or min(size)<=0:raise ValueError('Invalid box dimensions')
        bpy.ops.mesh.primitive_cube_add(size=1,location=position)
        obj=bpy.context.object;obj.name=name;obj.dimensions=size
        bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
        self.objects[name]=obj
        return name

    def cylinder(self,name,radius,depth,position=(0,0,0)):
        self._name(name)
        if radius<=0 or depth<=0:raise ValueError('Invalid cylinder dimensions')
        bpy.ops.mesh.primitive_cylinder_add(vertices=48,radius=radius,depth=depth,location=position)
        obj=bpy.context.object;obj.name=name;self.objects[name]=obj
        return name

    def mesh(self,name,vertices,faces):
        self._name(name)
        if len(vertices)>10000 or len(faces)>20000:raise ValueError('Mesh budget exceeded')
        mesh=bpy.data.meshes.new(name);mesh.from_pydata(vertices,[],faces);mesh.update()
        obj=bpy.data.objects.new(name,mesh);bpy.context.collection.objects.link(obj);self.objects[name]=obj
        return name

    def difference(self,target,cutter):
        self.operations+=1
        if self.operations>256:raise ValueError('Geometry operation budget exceeded')
        obj=self.objects[target];tool=self.objects[cutter]
        bpy.context.view_layer.objects.active=obj
        modifier=obj.modifiers.new(name='candidate_boolean',type='BOOLEAN');modifier.operation='DIFFERENCE';modifier.object=tool
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        bpy.data.objects.remove(tool,do_unlink=True);del self.objects[cutter]
        return target

    def body(self,name,parts,origin=(0,0,0),collision=None):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',name) or any(b['name']==name for b in self.bodies):
            raise ValueError('Invalid/duplicate rigid-body name')
        if not parts or any(p not in self.objects for p in parts):raise ValueError('Unknown rigid-body parts')
        if not collision:raise ValueError('Explicit primitive/convex collision description required')
        self.bodies.append({'name':name,'parts':parts,'origin_m':list(origin),'collision':collision})
        return name

    def clearance_probe(self,body,point):self.probes.append({'body':body,'point_m':list(point)})


def bounded_range(*args):
    result=range(*args)
    if len(result)>512:raise ValueError('Loop budget exceeded')
    return result


def export(api,out,source,parameters):
    bodies=[]
    for body in api.bodies:
        vertices=[];faces=[];nonmanifold=0;volume=0
        for name in body['parts']:
            obj=api.objects[name];bm=bmesh.new();bm.from_mesh(obj.data)
            nonmanifold+=sum(1 for edge in bm.edges if not edge.is_manifold)
            volume+=abs(bm.calc_volume(signed=True));bm.free()
            offset=len(vertices)
            for v in obj.data.vertices:
                vertices.append(list(obj.matrix_world@v.co-Vector(body['origin_m'])))
            faces.extend([[offset+i+1 for i in poly.vertices] for poly in obj.data.polygons])
            obj['rigid_body_id']=body['name'];obj['origin_m']=body['origin_m']
        path=out/'bodies'/(body['name']+'.obj');path.parent.mkdir(exist_ok=True)
        with path.open('w',encoding='utf-8') as f:
            f.write('o '+body['name']+'\n')
            for v in vertices:f.write('v '+' '.join(format(n,'.12g') for n in v)+'\n')
            for face in faces:f.write('f '+' '.join(map(str,face))+'\n')
        bounds=[[min(v[i] for v in vertices) for i in range(3)],[max(v[i] for v in vertices) for i in range(3)]]
        bodies.append({**body,'mesh':path.relative_to(out).as_posix(),'bounds_m':bounds,'volume_m3':volume,'nonmanifold_edges':nonmanifold})
        # Editable Blender source exposes the exact rigid-body origin as an empty.
        frame=bpy.data.objects.new('body_'+body['name'],None);bpy.context.collection.objects.link(frame)
        frame.location=body['origin_m'];frame['rigid_body_id']=body['name']
        for name in body['parts']:
            obj=api.objects[name];world=obj.matrix_world.copy();obj.parent=frame;obj.matrix_world=world
    manifest={'blender_version':bpy.app.version_string,'blender_build_hash':bpy.app.build_hash.decode(),
              'unit_system':'METRIC','length_unit':'m','parameters':parameters,
              'bodies':bodies,'clearance_probes':api.probes,'script':source.name}
    (out/'component_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    scene=bpy.context.scene;scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1
    # CPU render, independent of host GPU and any interactive Blender session.
    scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=8
    scene.render.resolution_x=640;scene.render.resolution_y=480;scene.render.resolution_percentage=100
    points=[obj.matrix_world@Vector(v) for obj in api.objects.values() for v in obj.bound_box]
    center=sum(points,Vector())/len(points)
    radius=max((p-center).length for p in points)
    bpy.ops.object.camera_add(location=center+Vector((1.6,-2.0,1.4))*max(radius,.1))
    camera=bpy.context.object;camera.rotation_euler=(center-camera.location).to_track_quat('-Z','Y').to_euler();scene.camera=camera
    bpy.ops.object.light_add(type='AREA',location=center+Vector((0,-1,3))*max(radius,.1))
    bpy.context.object.data.energy=500;scene.world.color=(.2,.2,.2)
    bpy.ops.wm.save_as_mainfile(filepath=str(out/'component.blend'))
    scene.render.filepath=str(out/'preview.png');bpy.ops.render.render(write_still=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--script');parser.add_argument('--parameters');parser.add_argument('--out')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:]);out=Path(args.out).resolve()
    # The parent validates the AST. Candidate builtins are additionally restricted.
    source=Path(args.script);params=json.loads(Path(args.parameters).read_text(encoding='utf-8'))
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    api=GeometryAPI();scope={'__builtins__':{'range':bounded_range,'min':min,'max':max,'abs':abs}}
    exec(compile(source.read_text(encoding='utf-8'),str(source),'exec'),scope)
    scope['build'](params,api)
    if not api.bodies:raise ValueError('Candidate declared no rigid bodies')
    assigned=[name for b in api.bodies for name in b['parts']]
    if len(set(assigned))!=len(assigned) or set(assigned)!=set(api.objects):raise ValueError('Each geometry must belong to exactly one rigid body')
    export(api,out,source,params)


if __name__=='__main__':main()
