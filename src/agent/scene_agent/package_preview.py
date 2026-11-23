"""Lossless triangle transfer from the compiled model to the browser."""
import numpy as np


def display_mesh(vertices, faces):
    vertices=np.asarray(vertices,dtype=float);faces=np.asarray(faces,dtype=int)
    triangles=vertices[faces]
    normals=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
    lengths=np.linalg.norm(normals,axis=1)
    # Keep every face, its winding and all vertex coordinates, including tiny or
    # degenerate triangles. A zero normal avoids dividing by zero on those faces.
    normals=np.divide(normals,lengths[:,None],out=np.zeros_like(normals),where=lengths[:,None]>0)
    packed=np.concatenate([triangles,np.repeat(normals[:,None,:],3,axis=1)],axis=2).astype('<f4')
    return {'data':packed.tobytes(),'vertices':len(triangles)*3,
            'max_vertex_displacement_m':0.}


def assets(model):
    meshes={};full=0;shown=0;error=0.
    for mesh in range(model.nmesh):
        start=int(model.mesh_vertadr[mesh]);count=int(model.mesh_vertnum[mesh])
        face_start=int(model.mesh_faceadr[mesh]);face_count=int(model.mesh_facenum[mesh])
        item=display_mesh(model.mesh_vert[start:start+count],model.mesh_face[face_start:face_start+face_count])
        meshes[str(mesh)]=item;full+=face_count;shown+=item['vertices']//3;error=max(error,item['max_vertex_displacement_m'])
    return meshes,{'display_only':True,'simplified':False,'original_faces':full,'display_faces':shown,'max_vertex_displacement_m':error}
