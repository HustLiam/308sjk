"""网页模型运动：复用 lx PlcLink，单线程拥有 Modbus 与 MuJoCo 数据面。"""
import copy
import json
from .task_inputs import load_task_spec
import math
from pathlib import Path
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
import queue
import secrets


class Motion:
    def __init__(self, directory, session, host, port=502, client=None, mode="auto", task=None):
        import mujoco
        # lx 原生运行器按同目录模块加载；保持其 bridge/pump_once 实现不变。
        runtime = str(Path(__file__).resolve().parents[2]/"runtime")
        if runtime not in sys.path:
            sys.path.insert(0, runtime)
        from gantry_bridge import GantryBridge, derive_layout
        from plc_link import PlcLink

        self.directory, self.session = Path(directory), session
        self.task = task or self.directory.name
        self.lock, self.cancel = threading.RLock(), threading.Event()
        self.thread = None
        if mode not in ("auto", "manual", "verify"):
            raise ValueError("未知仿真模式")
        self.mode, self.commands = mode, queue.Queue()
        self.run_id = time.strftime("%Y%m%d_%H%M%S_")+secrets.token_hex(4)
        self.trace, self.physical_trace, self.values = [], [], {}
        self.verification = None
        self.info = {"status":"starting", "task":self.task, "sequence":0,
                     "positions":{}, "ink_count":0, "elapsed":0, "error":None, "mode":mode, "run_id":self.run_id}
        scene = json.loads((self.directory/"simulation/scene.spec.json").read_text(encoding="utf-8"))
        spec = load_task_spec(self.directory)
        self.spec = spec
        trajectory_path=self.directory/'trajectory.json'
        self.trajectory=json.loads(trajectory_path.read_text(encoding='utf-8')) if trajectory_path.is_file() else None
        gantry = next(a for a in scene["assets"] if a["type"]=="gantry_xyz")
        travel = {a:float(gantry["params"]["travel_"+a.lower()]) for a in "XYZ"}
        layout, nregs = derive_layout(scene["io_map"], travel)
        self.bridge = GantryBridge(layout, nregs)
        self.link = PlcLink(self.bridge, scene["io_map"],
                            plc_ranges={v["name"]:v.get("range") for v in spec["io_list"]},
                            host=host, port=port)
        if client is not None:
            self.link.client.close()
            self.link.client = client
        self.link.client.timeout = 1
        self.link.client.retries = 0
        for channels in (self.link.in_chs,self.link.out_chs):
            if len(channels)!=3 or {c["axis"] for c in channels}!=set("XYZ"):
                raise ValueError("需要完整的三轴位置指令与反馈通道")
        inputs = self.link.in_chs
        if [c["qw"] for c in inputs]!=list(range(min(c["qw"] for c in inputs),min(c["qw"] for c in inputs)+3)):
            raise ValueError("lx 位置桥要求反馈寄存器按连续地址排列")
        for c in self.link.channels:
            if c["lo_scene"]!=0 or c["hi_scene"]<=0 or c["lo_plc"]<0:
                raise ValueError("当前位置桥仅支持从零开始的非负行程")
        root = ET.parse(self.directory/"final/plcopen.xml").getroot()
        addresses = {v.get("name"):v.get("address") for v in root.iter() if v.get("address")}
        self.bits = {}
        for name in ("run","quickstop","cmd_draw","cmd_home","cmd_go","cmd_reset",
                     "jog_fwd","jog_rev","all_oe","fault_any","pen_down","plot_done"):
            match = re.fullmatch(r"%QX(\d+)\.([0-7])",addresses.get(name,""))
            if not match:
                raise ValueError("当前程序缺少模型运动接口："+name)
            self.bits[name] = int(match[1])*8+int(match[2])
        for point in spec["io_list"]:
            match = re.fullmatch(r"%QX(\d+)\.([0-7])", addresses.get(point["name"], ""))
            if point["type"] == "BOOL" and match:
                self.bits[point["name"]] = int(match[1])*8+int(match[2])
        match = re.fullmatch(r"%QW(\d+)",addresses.get("prog_id",""))
        if not match:
            raise ValueError("当前程序缺少身份寄存器")
        self.identity = int(match[1]),int(spec.get("prog_id",2))
        self.model = mujoco.MjModel.from_xml_path(str(self.directory/"simulation/scene.xml"))
        self.model.opt.timestep = 1/120
        self.data = mujoco.MjData(self.model)
        self.qids, self.aids, self.dids = {},{},{}
        for axis in "XYZ":
            jid = mujoco.mj_name2id(self.model,mujoco.mjtObj.mjOBJ_JOINT,"joint_"+axis.lower())
            aid = mujoco.mj_name2id(self.model,mujoco.mjtObj.mjOBJ_ACTUATOR,"drive_"+axis.lower())
            if min(jid,aid)<0:
                raise ValueError("仿真模型缺少三轴关节或执行器")
            self.qids[axis],self.aids[axis] = int(self.model.jnt_qposadr[jid]),aid
            self.dids[axis]=int(self.model.jnt_dofadr[jid])
        from .aml_parser import parse_aml
        device, errors = parse_aml(self.directory/"assets/device.aml")
        if errors:
            raise ValueError("AML 校验失败")
        self.speed = {}
        for axis in "XYZ":
            entry = next(a for a in device["devices"] if a["name"].lower()==axis.lower()+"_axis")
            # 行程/速度使用同一 AML 工程量；比例取冻结位置通道，避免假定 mm 或 %。
            channel = next(c for c in self.link.in_chs if c["axis"]==axis)
            self.speed[axis] = float(entry["params"]["vmax"])*channel["m_per_unit"]
            if not math.isfinite(self.speed[axis]) or self.speed[axis]<=0:
                raise ValueError("轴最大速度无效")
        self.ink = []
        self.strokes = []
        self.stroke = 0
        self.contact = False
        self.pen_armed = False
        self.transforms = []
        self.qpos = self.data.qpos.tolist()
        self.started = time.monotonic()

    @property
    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    def status(self):
        with self.lock:
            return dict(copy.deepcopy(self.info),active=self.alive, values=dict(self.values), verification=self.verification)

    def signal_command(self, name, value):
        if self.mode != "manual" or not self.alive:
            raise ValueError("请先连接手动调试；自动运行期间面板只读")
        point = next((p for p in self.spec["io_list"] if p["name"] == name), None)
        if not point or point["dir"] != "input" or point["type"] != "BOOL" or name not in self.bits:
            raise ValueError("该信号不是可操作的 BOOL 输入")
        if not isinstance(value, bool):
            raise ValueError("开关值必须是布尔值")
        from .studio_web import panel
        element = next(e for e in panel(self.spec)["elements"] if e["signal"]==name)
        self.commands.put((name, value, element.get("type")=="trigger"))

    def _sample(self):
        from .studio_web import read_signals
        values = read_signals(self.link.client, self.spec)
        for channel in self.link.in_chs:
            axis = channel["axis"]
            values["physical_"+axis.lower()] = channel["lo_plc"]+(float(self.data.qpos[self.qids[axis]])-channel["lo_scene"])/channel["m_per_unit"]
        with self.lock:
            self.values = values
            self.trace.append(dict(values, t=round(time.monotonic()-self.started, 6)))
        return values

    def trace_window(self):
        with self.lock:
            return copy.deepcopy(self.trace[-600:])

    def snapshot(self):
        with self.lock:
            return {"qpos":list(self.qpos),"ink":list(self.ink),"strokes":list(self.strokes)}

    def frame(self, after=0, run=None):
        with self.lock:
            ident=str(self.started)
            reset=run!=ident or after>len(self.ink)
            start=0 if reset else after
            return {'run':ident,'active':self.alive,'sequence':self.info['sequence'],
                    'transforms':copy.deepcopy(self.transforms),'ink_reset':reset,
                    'ink':copy.deepcopy(self.ink[start:]),'strokes':self.strokes[start:],
                    'ink_total':len(self.ink)}

    def start(self):
        self.thread = threading.Thread(target=self._run,daemon=True,name="web-plc-motion")
        self.thread.start()

    def stop(self):
        self.cancel.set()
        if self.thread:
            self.thread.join(timeout=8)
            if self.thread.is_alive():
                raise RuntimeError("模型运动仍在停止，请刷新状态")
        return self.status()

    @staticmethod
    def _check(response):
        if response is None or response.isError():
            raise ConnectionError("OpenPLC Modbus 通信失败")
        return response

    def _read_bits(self):
        count = (max(self.bits.values())//16+1)*16
        response = self._check(self.link.client.read_coils(address=0,count=count))
        return {name:bool(response.bits[bit]) for name,bit in self.bits.items()}

    def _write_bits(self, values):
        # 复用 lx SafeCoilIO 的整组 RMW 纪律，并检查每个响应；禁止窄范围写。
        from src.pipeline.modbus_io import SafeCoilIO
        class CheckedClient:
            def read_coils(_, **kwargs):
                return self._check(self.link.client.read_coils(**kwargs))
            def write_coils(_, **kwargs):
                return self._check(self.link.client.write_coils(**kwargs))
        io = SafeCoilIO(CheckedClient())
        for name,value in values.items():
            io.write(self.bits[name],value)

    def _verify_program(self):
        reg,expected = self.identity
        result = self._check(self.link.client.read_holding_registers(address=reg,count=1))
        if result.registers[0]!=expected:
            raise RuntimeError("Runtime 程序已改变，模型运动已停止")

    def _publish(self):
        from .simulation import geometry_transforms
        with self.lock:
            self.qpos = self.data.qpos.tolist()
            self.transforms = geometry_transforms(self.data)
            self.info.update(positions={a:round(float(self.data.qpos[i]),6) for a,i in self.qids.items()},
                             sequence=self.info["sequence"]+1,ink_count=len(self.ink),
                             elapsed=round(time.monotonic()-self.started,2))

    def _run(self):
        import mujoco
        from plc_link import int_to_m
        error = None
        connected = False
        authorized = False
        try:
            if not self.link.client.connect():
                raise ConnectionError("无法连接 OpenPLC Modbus")
            connected = True
            self._verify_program()
            authorized = True
            self._sample()
            self._write_bits({"quickstop":True,"cmd_draw":False,"run":False,
                             "cmd_home":False,"cmd_go":False,"jog_fwd":False,"jog_rev":False})
            for c in self.link.in_chs:
                raw = self._check(self.link.client.read_holding_registers(address=c["qw"],count=1)).registers[0]
                self.data.qpos[self.qids[c["axis"]]] = int_to_m(raw,c)
            mujoco.mj_forward(self.model,self.data)
            last_cmd = {a:float(self.data.qpos[i]) for a,i in self.qids.items()}
            self.bridge.write_positions(last_cmd)
            self._publish()
            self._sample()
            # 保留急停至少数个 PLC 扫描，清除上次轨迹；之后由使能反馈确认再触发。
            self.cancel.wait(.15)
            if self.cancel.is_set():
                return
            self._write_bits({"quickstop":False,"cmd_reset":self.mode!="manual","run":False})
            # 先复位、后使能，避免复位改写控制字，打断 MC_POWER 握手。
            reset_until = time.monotonic()+.15 if self.mode!="manual" else None
            phase, trigger_at = ("manual" if self.mode=="manual" else "enabling"),None
            pulses = {}
            with self.lock:
                self.info["status"] = phase
            next_io, next_flags, last_identity = 0,0,0
            flags = {}
            physics_clock=time.monotonic()
            from .servo_feedforward import PositionFeedforward
            servo=PositionFeedforward(self.model,self.data,self.qids,self.dids,self.aids)
            samples=[(physics_clock,self.bridge.read_positions())]
            pen = mujoco.mj_name2id(self.model,mujoco.mjtObj.mjOBJ_GEOM,"pen")
            paper = mujoco.mj_name2id(self.model,mujoco.mjtObj.mjOBJ_GEOM,"paper")
            max_time = max(120,float(json.loads((self.directory/"simulation/scene.spec.json").read_text())["script"]["termination"]["max_sim_time"])*3)
            while not self.cancel.is_set():
                t0 = time.monotonic()
                if reset_until is not None and t0>=reset_until:
                    self._write_bits({"cmd_reset":False,"run":True})
                    reset_until = None
                if self.mode!="manual" and t0-self.started>max_time:
                    raise RuntimeError("绘制超时，已停止模型；请检查 PLC 轨迹程序")
                if t0>=last_identity+1:
                    self._verify_program()
                    last_identity = t0
                while not self.commands.empty():
                    name,value,pulse = self.commands.get_nowait()
                    self._verify_program()
                    self._write_bits({name:value})
                    if pulse and value:
                        pulses[name] = t0+.15
                for name, until in list(pulses.items()):
                    if t0>=until:
                        self._write_bits({name:False})
                        del pulses[name]
                if t0>=next_io:
                    if not self.link.pump_once():
                        raise ConnectionError("位置桥收发失败，模型运动已停止")
                    samples.append((time.monotonic(),self.bridge.read_commands()))
                    samples=samples[-16:]
                    next_io = t0+.015
                if t0>=next_flags:
                    flags = self._sample()
                    if flags["fault_any"] and self.mode!="manual":
                        raise RuntimeError("PLC 报告驱动故障，模型运动已停止")
                    if phase=="enabling":
                        if flags["all_oe"] and t0-self.started>.5:
                            self._write_bits({"cmd_reset":False,"cmd_draw":True})
                            phase,trigger_at = "running",t0
                            with self.lock:self.info["status"] = phase
                        elif t0-self.started>8:
                            raise RuntimeError("PLC 使能超时")
                    elif trigger_at is not None and t0-trigger_at>.2:
                        self._write_bits({"cmd_draw":False})
                        trigger_at = None
                    elif phase=="running" and flags["plot_done"]:
                        # 完成信号之外还要求物理位置跟上最后的指令。
                        targets = self.bridge.read_commands()
                        if all(abs(float(self.data.qpos[self.qids[a]])-targets[a])<.0015 for a in "XYZ"):
                            if self.trajectory and self.trajectory.get('shape')=='svg' and not self.ink:
                                raise RuntimeError("PLC 已报完成，但没有形成实际笔迹；绘图程序可能跳步，不能标记绘制完成")
                            with self.lock:self.info["status"] = "completed"
                            break
                    next_flags = t0+.08
                # Modbus 在本机调度中可能阻塞若干毫秒，按墙钟补齐固定物理步，避免仿真变慢。
                ticks=min(12,max(0,int((time.monotonic()-physics_clock)*120)))
                for _ in range(ticks):
                    # 对实际 PLC 样本做统一时间插值，三轴保留相同进度；不从规划轨迹造运动。
                    at=physics_clock-.04
                    left,right=samples[0],samples[-1]
                    for i in range(1,len(samples)):
                        if samples[i][0]>=at:
                            left,right=samples[i-1],samples[i];break
                    ratio=max(0,min(1,(at-left[0])/max(.000001,right[0]-left[0])))
                    for axis in 'XYZ':
                        target=left[1][axis]+ratio*(right[1][axis]-left[1][axis])
                        delta = max(-self.speed[axis]/120,min(target-last_cmd[axis],self.speed[axis]/120))
                        last_cmd[axis] += delta
                    servo.apply(last_cmd,1/120)
                    mujoco.mj_step(self.model,self.data)
                    if not all(math.isfinite(float(v)) for v in self.data.qpos):
                        raise RuntimeError("仿真位置异常")
                    self.bridge.write_positions({a:float(self.data.qpos[i]) for a,i in self.qids.items()})
                    drawing=False
                    if phase in ("running", "manual") and flags.get("pen_down") and min(pen,paper)>=0:
                        tip = self.data.geom_xpos[pen].copy()
                        # 胶囊笔尖包含半长与端帽半径，少算半径会把接触误判为悬空。
                        tip[2] -= self.model.geom_size[pen][1]+self.model.geom_size[pen][0]
                        top = float(self.data.geom_xpos[paper][2]+self.model.geom_size[paper][2])
                        drawing=self.pen_armed and tip[2]<=top+.0015
                        if drawing and (not self.contact or not self.ink or math.dist(tip[:2],self.ink[-1][:2])>=.0005):
                            with self.lock:
                                if not self.contact:self.stroke+=1
                                self.ink.append([float(tip[0]),float(tip[1]),top+.001])
                                self.strokes.append(self.stroke)
                    self.contact=drawing
                    if min(pen,paper)>=0:
                        clearance=float(self.data.geom_xpos[pen][2]-self.model.geom_size[pen][1]-self.model.geom_size[pen][0]-self.data.geom_xpos[paper][2]-self.model.geom_size[paper][2])
                        if clearance>.003:self.pen_armed=True
                    physics_clock+=1/120
                    self.physical_trace.append(dict(
                        {c["plc_var"]:c["lo_plc"]+(float(self.data.qpos[self.qids[c["axis"]]])-c["lo_scene"])/c["m_per_unit"]
                         for c in self.link.in_chs},
                        t=round(physics_clock-self.started,6),pen_down=bool(flags.get("pen_down")),pen_contact=bool(drawing)))
                self._publish()
                self.cancel.wait(max(0,1/120-(time.monotonic()-t0)))
        except Exception as exc:
            error = str(exc)
        finally:
            if connected and authorized:
                try:
                    self._verify_program()
                    resets = {p["name"]:False for p in self.spec["io_list"]
                              if p["type"]=="BOOL" and p["dir"]=="input" and p["name"] in self.bits}
                    self._write_bits(dict(resets, quickstop=True))
                    self.cancel.wait(.05)
                    flags = self._read_bits()
                    if flags["run"] or flags["cmd_draw"] or not flags["quickstop"]:
                        raise RuntimeError("PLC 停止指令未确认")
                    from .studio_web import read_signals
                    with self.lock:
                        self.values=read_signals(self.link.client,self.spec)
                except Exception as exc:
                    error = (error+"；" if error else "")+"停止确认失败："+str(exc)
            self.link.client.close()
            with self.lock:
                self.info["status"] = "failed" if error else "completed" if self.info["status"]=="completed" else "stopped"
                self.info["error"] = error
            try:
                from .studio_web import save_verification
                self.verification = save_verification(self.directory, self.run_id, self.spec, self.trace,
                    self.info["status"], self.mode, error, self.physical_trace)
            except Exception as exc:
                self.info["verification_error"] = "验收记录保存失败："+str(exc)
            path=self.directory/'simulation/motion.json'
            temporary=path.with_suffix('.tmp')
            temporary.write_text(json.dumps(dict(self.status(),active=False,snapshot=self.snapshot()),ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(path)
