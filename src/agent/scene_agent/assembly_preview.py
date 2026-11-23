"""Ephemeral, read-only snapshots of the actual resource importer, before compilation."""
import threading
import uuid

import numpy as np

from .package_preview import display_mesh


class AssemblyPreview:
    def __init__(self, task):
        self.task = task
        self.token = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.assets = {}
        self.bounds = {}
        self.keys = {}
        self.snapshot = None
        self.revision = 0

    def publish(self, report, nodes, meshes):
        # Pack each source mesh once; instances share it and retain all triangles.
        for key, mesh in meshes.items():
            if key in self.keys:
                continue
            name = str(len(self.keys))
            asset = display_mesh(mesh['vertices'], mesh['faces'])
            vertices = np.asarray(mesh['vertices'])
            low, high = vertices.min(axis=0), vertices.max(axis=0)
            self.bounds[key] = ((low + high) / 2, (high - low) / 2)
            with self.lock:
                self.keys[key] = name
                self.assets[name] = asset
        geoms, transforms, lows, highs = [], [], [], []
        for node in nodes.values():
            world = node['world']
            for key in node['meshes']:
                center, half = self.bounds[key]
                center = world[:3, :3] @ center + world[:3, 3]
                half = np.abs(world[:3, :3]) @ half
                lows.append(center - half)
                highs.append(center + half)
                geoms.append({'type': 7, 'size': half.tolist(), 'color': [.55, .65, .72, 1],
                              'mesh_asset': self.keys[key], 'component_id': node['component_id']})
                transforms.append([*world[:3, 3].tolist(), *world[:3, :3].ravel().tolist()])
        low = np.min(lows, axis=0) if lows else np.zeros(3)
        high = np.max(highs, axis=0) if highs else np.ones(3)
        components = [{'id': c['id'], 'name': c['name'], 'parent_id': c['parent_id'],
                       'type': c['role'].get('RefBaseRoleClassPath', 'AML').split('/')[-1]}
                      for c in report['components']]
        with self.lock:
            self.revision += 1
            self.snapshot = {'source': 'resource_import_in_progress', 'assembly_token': self.token,
                'assembly_revision': self.revision, 'components': components,
                'center': ((low + high) / 2).tolist(), 'span': max(float(np.max(high - low)), .4),
                'geoms': geoms, 'transforms': transforms, 'mesh_assets': {
                    name: {'vertices': asset['vertices'], 'url':
                        f'/api/device-scene/assembly-mesh?task={self.task}&version={self.token}&mesh={name}'}
                    for name, asset in self.assets.items()}}

    def read(self):
        with self.lock:
            return self.snapshot

    def mesh(self, token, name):
        with self.lock:
            if token != self.token or name not in self.assets:
                raise KeyError(name)
            return self.assets[name]['data']
