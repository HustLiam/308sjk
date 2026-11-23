"""Transient joint preview with optional, explicitly requested PLC control."""
from collections import OrderedDict
import math
import time
import threading
import uuid

import mujoco
import numpy as np

from .common import SceneError


class JointPreview:
    def __init__(self, model, data, owners, components, records=(), *, clock=time.monotonic, include_bodies=False,
                 plc_config=None, plc_connect=None):
        self.lock = threading.RLock()
        self.plc = None
        self.agent = None
        self.model = model
        self.initial = data.qpos.copy()
        self.version = uuid.uuid4().hex
        self.clock = clock
        self.include_bodies = include_bodies
        self.sessions = OrderedDict()
        self.components = {c['id']: c for c in components}
        body_owners = [None] * model.nbody
        self.mechanical_children = {}
        for bid in range(1, model.nbody):
            parent = int(model.body_parentid[bid])
            name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, bid)
            owner = owners.get(name) or body_owners[parent]
            body_owners[bid] = owner
            parent_owner = body_owners[parent]
            if owner and parent_owner and owner != parent_owner:
                children = self.mechanical_children.setdefault(parent_owner, [])
                if owner not in children:
                    children.append(owner)
        sources = {r['body']: r for r in records if r.get('body')}
        self.joints = []
        for jid in range(model.njnt):
            body = int(model.jnt_bodyid[jid])
            body_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body)
            source = sources.get(body_name, {})
            owner = None
            while body:
                owner = owners.get(mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body))
                if owner:
                    break
                body = int(model.body_parentid[body])
            kind = int(model.jnt_type[jid])
            hinge = kind == int(mujoco.mjtJoint.mjJNT_HINGE)
            slide = kind == int(mujoco.mjtJoint.mjJNT_SLIDE)
            adr = int(model.jnt_qposadr[jid])
            width = 7 if kind == int(mujoco.mjtJoint.mjJNT_FREE) else 4 if kind == int(mujoco.mjtJoint.mjJNT_BALL) else 1
            name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, jid)
            self.joints.append({'joint_id': jid, 'joint_name': name, 'name': source.get('name') or name,
                'source_id': source.get('source_id'), 'component_id': owner,
                'component_name': self.components.get(owner, {}).get('name', owner),
                'type': 'hinge' if hinge else 'slide' if slide else 'ball' if width == 4 else 'free',
                'jnt_qposadr': adr, 'qpos_width': width, 'controllable': hinge or slide,
                'unit': 'deg' if hinge else 'mm' if slide else '',
                'display_factor': 180 / math.pi if hinge else 1000 if slide else 1,
                'range': model.jnt_range[jid].tolist() if model.jnt_limited[jid] and (hinge or slide) else None,
                'initial': self.initial[adr:adr + width].tolist()})
        if plc_config:
            from .plc_preview import PlcPreview
            try:
                self.plc = PlcPreview(self, plc_config, data, owners, records, connect=plc_connect)
            except (ValueError, KeyError, TypeError):
                # An incomplete binding must disable PLC control, not geometry.
                self.plc = None

    def close(self):
        if self.agent:
            self.agent.stop()
        if self.plc:
            self.plc.stop()

    def selected(self, ids):
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in self.components for i in ids):
            raise SceneError('请选择当前模型中的组件')
        chosen = set(ids)
        while True:
            expanded = chosen | {cid for cid, c in self.components.items() if c.get('parent_id') in chosen}
            if expanded == chosen:
                break
            chosen = expanded
        return [j for j in self.joints if j['component_id'] in chosen]

    def session(self, client):
        if not isinstance(client, str) or len(client) != 32 or any(c not in '0123456789abcdef' for c in client):
            raise SceneError('关节预览会话无效，请重新选择模型')
        if client not in self.sessions:
            data = mujoco.MjData(self.model)
            data.qpos[:] = self.initial
            mujoco.mj_kinematics(self.model, data)
            self.sessions[client] = {'data': data, 'motion': None, 'sequence': 0}
            if len(self.sessions) > 4:
                idle = next(key for key, value in self.sessions.items() if not value.get('plc_active'))
                self.sessions.pop(idle)
        self.sessions.move_to_end(client)
        return self.sessions[client]

    def update(self, session):
        motion = session['motion']
        if motion is None:
            return
        t = min(1., max(0., (self.clock() - motion['start']) / motion['duration']))
        weight = t * t * (3 - 2 * t)
        data = session['data']
        data.qpos[motion['indices']] = motion['from'] + (motion['to'] - motion['from']) * weight
        data.qvel[:] = 0
        # Position preview needs body/geom poses, not collision or constraint solving.
        mujoco.mj_kinematics(self.model, data)
        if t >= 1:
            session['motion'] = None

    def command(self, client, ids, action, targets=None, duration=2):
        rows = self.selected(ids)
        if self.agent and self.agent.job and self.agent.job['thread'].is_alive():
            if action == 'stop':
                self.agent.stop(client)
            else:
                raise SceneError('Agent 正在控制模型，请先停止')
        if action not in ('move', 'stop', 'reset', 'plc'):
            raise SceneError('未知关节预览操作')
        if action == 'plc' and (not self.plc or not self.plc.available(ids, rows)):
            raise SceneError('当前组件未完成 IO 绑定，不能使用 PLC 控制')
        resolved = []
        if action != 'stop':
            if type(duration) not in (int, float) or not math.isfinite(duration) or not .1 <= duration <= 120:
                raise SceneError('运动时长须为 0.1 到 120 秒')
            available = {j['joint_name']: j for j in rows if j['controllable']}
            if action == 'reset':
                targets = [{'joint_name': j['joint_name'], 'position': j['initial'][0]} for j in available.values()]
            if not isinstance(targets, list) or not targets or len(targets) > len(available):
                raise SceneError('请选择至少一个可控制关节')
            seen = set()
            for target in targets:
                if not isinstance(target, dict) or not isinstance(target.get('joint_name'), str):
                    raise SceneError('关节位置指令格式错误')
                name = target['joint_name']
                if name not in available or name in seen:
                    raise SceneError('关节不属于当前选择、不可控制或重复提交')
                seen.add(name)
                row = available[name]
                value = target.get('position')
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise SceneError('目标位置必须是有限数值')
                if row['range']:
                    lo, hi = row['range']
                    if value < lo - 1e-9 or value > hi + 1e-9:
                        raise SceneError(f"{row['name']} 的目标位置超出关节限位")
                    value = min(hi, max(lo, value))
                resolved.append((row['jnt_qposadr'], value))
        if self.plc and action != 'plc':
            self.plc.stop(client)
        with self.lock:
            session = self.session(client)
            self.update(session)
            if action == 'plc':
                self.plc.start(client, ids, targets, duration, session)
            else:
                session['motion'] = None
                if action != 'stop':
                    session['plc_error'] = None
                    session['plc_feedback'] = None
                if resolved:
                    indices = np.array([r[0] for r in resolved], dtype=int)
                    session['motion'] = {'start': self.clock(), 'duration': duration, 'indices': indices,
                        'from': session['data'].qpos[indices].copy(), 'to': np.array([r[1] for r in resolved])}
            session['sequence'] += 1
            return self.snapshot(client, ids)

    def snapshot(self, client, ids, *, metadata=False):
        with self.lock:
            return self._snapshot(client, ids, metadata=metadata)

    def _snapshot(self, client, ids, *, metadata=False):
        rows = self.selected(ids)
        session = self.session(client)
        self.update(session)
        if self.plc:
            self.plc.seen(client)
        if self.agent:
            self.agent.seen(client)
        d = session['data']
        # Only transforms are sent each frame; static mesh buffers stay in the browser.
        result = {'version': self.version, 'sequence': session['sequence'],
            'active': session['motion'] is not None or session.get('plc_active', False),
            'agent': session.get('agent'),
            'plc': {'available': bool(self.plc and self.plc.available(ids, rows)),
                    'active': session.get('plc_active', False), 'error': session.get('plc_error'),
                    'feedback': session.get('plc_feedback')},
            'positions': {j['joint_name']: d.qpos[j['jnt_qposadr']:j['jnt_qposadr'] + j['qpos_width']].tolist() for j in rows},
            'anchors': {j['joint_name']: d.xanchor[j['joint_id']].round(9).tolist() for j in rows},
            'transforms': np.concatenate((d.geom_xpos, d.geom_xmat), axis=1).round(9).tolist()}
        if metadata:
            result['joints'] = rows
        if self.include_bodies:
            result['body_transforms'] = np.concatenate((d.xpos, d.xmat), axis=1).round(9).tolist()
        return result
