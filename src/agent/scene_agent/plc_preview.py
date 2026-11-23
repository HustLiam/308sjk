"""Manual PLC control for a browser joint-preview session using the IO adapter."""
from copy import deepcopy
import math
from pathlib import Path
import threading
import time

import mujoco

from .common import SceneError
from .robotcell_io import BoundModel, Bridge, TestDriver, load_config, config_paths, component_id


PROFILE = Path(__file__).resolve().parents[3] / 'examples/robotcell/010RB_100.io.json'
_lease_lock = threading.Lock()
_leases = {}


def profile_signature():
    # Any of the three files invalidates the cached preview, including deletion.
    return tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size)
                 if path.is_file() else (str(path), None, None) for path in config_paths(PROFILE))


def load_profile():
    try:
        return load_config(PROFILE)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


class PlcPreview:
    def __init__(self, preview, config, data, owners, records, connect=None):
        self.preview = preview
        self.config = deepcopy(config)
        self.owners = {name: {'component_id': owner} for name, owner in owners.items()}
        self.records = records
        bound = BoundModel(config, preview.model, data, self.owners, records)
        self.points = {bound.channels[p['name']]['joint_name']: p for p in bound.targets}
        self.endpoint = (config['adapter']['runtime']['host'], config['adapter']['runtime']['port'])
        self.connect = connect or self._connect
        self.job = None

    def _connect(self):
        from pymodbus.client import ModbusTcpClient
        client = ModbusTcpClient(self.endpoint[0], port=self.endpoint[1], timeout=1, retries=0)
        if not client.connect():
            client.close()
            raise ConnectionError('无法连接 OpenPLC Runtime')
        return client

    def available(self, ids, rows):
        # A structural parent or a mixed selection is not a bound component.
        return (ids == [component_id(self.config)] and bool(rows)
                and all(j['joint_name'] in self.points for j in rows))

    def start(self, client, ids, targets, duration, session):
        if not self.available(ids, self.preview.selected(ids)):
            raise SceneError('当前组件未完成 IO 绑定，不能使用 PLC 控制')
        if self.job and self.job['thread'].is_alive():
            raise SceneError('PLC 正在控制关节，请先停止')
        final = {}
        for target in targets:
            p = self.points[target['joint_name']]
            value = round(math.degrees(target['position']) / p['deg_per_count'])
            if not p['range'][0] <= value <= p['range'][1]:
                raise SceneError('目标位置超出 IO 配置范围')
            final[p['name']] = value
        with _lease_lock:
            if self.endpoint in _leases:
                raise SceneError('此 PLC Runtime 正由另一个预览会话控制')
            _leases[self.endpoint] = self
        job = {'client': client, 'session': session, 'cancel': threading.Event(),
               'last_seen': time.monotonic(), 'initial': session['data'].qpos.copy()}
        job['thread'] = threading.Thread(target=self._run, args=(job, final, duration),
                                         name='robotcell-plc-preview', daemon=True)
        self.job = job
        session.update(plc_active=True, plc_error=None, plc_feedback=None, motion=None)
        try:
            job['thread'].start()
        except BaseException:
            session['plc_active'] = False
            with _lease_lock:
                _leases.pop(self.endpoint, None)
            raise

    def seen(self, client):
        if self.job and self.job['client'] == client:
            self.job['last_seen'] = time.monotonic()

    def stop(self, client=None):
        job = self.job
        if job and (client is None or client == job['client']):
            job['cancel'].set()
            job['thread'].join(timeout=8)
            if job['thread'].is_alive():
                raise SceneError('PLC 正在停止，请稍后重试')

    def _run(self, job, final, duration):
        clients, driver, bound = [], None, None
        session = job['session']
        error = None
        try:
            data = mujoco.MjData(self.preview.model)
            data.qpos[:] = job['initial']
            bound = BoundModel(self.config, self.preview.model, data, self.owners, self.records)
            initial = {p['name']: round(math.degrees(data.qpos[bound.channels[p['name']]['jnt_qposadr']])
                                       / p['deg_per_count']) for p in bound.targets}
            # Publish feedback for all six axes, move only the checked axes.
            bound.targets = [p for p in bound.targets if p['name'] in final]
            # Driver and adapter run sequentially in this worker. Share its
            # connection instead of opening two simultaneous Runtime sessions.
            clients.append(self.connect())
            bridge = Bridge(self.config, bound, clients[0])
            driver = TestDriver(self.config, clients[0])
            if job['cancel'].is_set():
                return
            driver.command({**initial, bridge.enable['name']: 0}, timeout=1)
            duration = max(duration, max(abs(final[p['name']] - initial[p['name']]) * p['deg_per_count']
                           / self.config['motion']['max_speed_deg_s'] for p in bound.targets))
            started = time.monotonic()
            while not job['cancel'].is_set():
                tick = time.monotonic()
                if tick - job['last_seen'] > 5:
                    raise TimeoutError('预览连接已中断，PLC 控制已停止')
                if tick - started > duration * 3 + 5:
                    raise TimeoutError('PLC 关节运动超时')
                ratio = min(1., (tick - started) / duration)
                values = {name: round(initial[name] + (value - initial[name]) * ratio) for name, value in final.items()}
                driver.command({**values, bridge.enable['name']: 1}, timeout=1)
                if job['cancel'].is_set():
                    break
                event = bridge.cycle()
                event['plc_observed_inputs'] = driver.observed(event['feedback'], timeout=1)
                with self.preview.lock:
                    # A completed IO cycle remains visible even if Stop arrived
                    # during feedback acknowledgement; otherwise PLC and screen
                    # could disagree by one last movement step.
                    session['data'].qpos[:] = data.qpos
                    session['data'].qvel[:] = data.qvel
                    session['data'].time = data.time
                    mujoco.mj_kinematics(self.preview.model, session['data'])
                    session['plc_feedback'] = event
                    session['sequence'] += 1
                if ratio >= 1 and all(abs(data.qpos[bound.channels[p['name']]['jnt_qposadr']]
                                         - math.radians(final[p['name']] * p['deg_per_count'])) < 1e-9
                                      for p in bound.targets):
                    break
                job['cancel'].wait(max(0, 1 / self.config['adapter']['cycle_hz'] - (time.monotonic() - tick)))
        except Exception as exc:
            error = 'PLC 控制失败：' + str(exc)
        finally:
            if bound:
                bound.hold()
            if driver:
                try:
                    enable = next(p['name'] for p in self.config['io_list'] if p['role'] == 'enable')
                    driver.command({enable: 0}, timeout=1)
                except Exception as exc:
                    error = (error + '；' if error else '') + '未确认 PLC 使能清零：' + str(exc)
            for connection in clients:
                connection.close()
            with self.preview.lock:
                session['data'].qvel[:] = 0
                session.update(plc_active=False, plc_error=error)
                session['sequence'] += 1
            with _lease_lock:
                if _leases.get(self.endpoint) is self:
                    _leases.pop(self.endpoint)
