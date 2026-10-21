"""本地 SVG 绘图工作台：python -m src.agent.web --open。

仅监听 loopback，复用确定性规划、需求理解与编排器。HTTP 线程不等待模型；
单个后台任务执行需求对话或生成，页面通过轮询取进度。不会自动部署 PLC。
"""

import argparse
import base64
import copy
import json
from .task_inputs import load_task_spec
import os
import re
import secrets
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

from .chat import save_preparation
from .config import MODEL, PROJECT_ROOT, get_api_key
from .svg_input import SVGError, prepare_svg_request, refine_svg_request, svg_catalog
from .assets import AssetStore, read_json, validate_library, validate_pair
from .presentation import process_summary
from .position_control import position_io, make_position_aml, convert_position_program
from .drawing_input import prepare_language_drawing, drawing_description

UI_DIR = Path(__file__).with_name("web_ui")
DEFAULT_ROOT = Path("E:/chagpt/308sjk-svg-workbench")
DEFAULT_AML = PROJECT_ROOT/"examples/aml/plotter3axis_precision_station.aml"
_ID = re.compile(r"[0-9a-f]{32}\Z")
_TASK = re.compile(r"svg_web_[0-9a-f]{32}\Z")
_SETTING_KEYS = {"center", "width", "height", "tolerance", "max_points", "flip_y"}
_BODY_LIMIT = 8 * 1024 * 1024
_DEFAULT_REQUEST = "按照所选 SVG 路径描画，保留路径次序与抬落笔动作。"
_DEFAULT_PROCESS = "按启动按钮后，沿所选轨迹依次绘制，轨迹之间自动抬笔；完成后抬笔回到中心，急停时停止绘制。"


class WorkbenchError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class SVGWorkbench:
    def __init__(self, runs_root=DEFAULT_ROOT, aml=DEFAULT_AML, max_iters=6):
        self.root = Path(runs_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.assets = AssetStore(self.root, aml)
        self.model = self.assets.default_model  # 兼容旧调用；任务消费各自 device_model。
        self.max_iters = max_iters
        self.lock = threading.RLock()
        self.render_lock = threading.Lock()
        self.sessions = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="svg-agent")
        self.active = None
        self.token = secrets.token_urlsafe(32)
        from .runtime_control import LXRuntime
        self.runtime = LXRuntime()
        self.motion = None
        self.scene_cache = {}
        self.motion_cache = {}

    def close(self):
        self.executor.shutdown(wait=True)
        if self.motion:
            self.motion.stop()

    def config(self):
        return {"station": self.model["station"], "model": MODEL,
                "model_ready": bool(get_api_key()), "token": self.token}

    def create_session(self):
        with self.lock:
            ident = secrets.token_hex(16)
            self._directory(ident).mkdir(parents=True)
            state = {"id":ident, "name":"", "revision":0, "catalog":{"paths":[]},
                "selected_paths":[], "settings":{}, "prepared":None, "spec":None, "report":None,
                "busy":False, "phase":"draft", "logs":[], "jobs":[],
                "request":"", "drawing_mode":"language", "drawing_text":"", "language_geometry":None,
                "device_model":copy.deepcopy(self.model),
                "assets":{"aml":copy.deepcopy(self.assets.default_aml),
                          "library":copy.deepcopy(self.assets.default_library), "svg":None},
                "messages":[{"role":"agent", "text":"可直接输入“画一个正方形”或“画一个圆，半径20”，也可切换到 SVG 导入。先预览轨迹，再补充工艺或使用默认工艺。"}]}
            self.sessions[ident] = state
            self._save(state)
        return self.state(ident)

    def _directory(self, ident):
        if not isinstance(ident, str) or not _ID.fullmatch(ident):
            raise WorkbenchError("任务不存在", 404)
        return self.root / "sessions" / ident

    def _save(self, state):
        path = self._directory(state["id"]) / "session.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _session(self, ident):
        directory = self._directory(ident)
        if ident not in self.sessions:
            path = directory / "session.json"
            if not path.is_file():
                raise WorkbenchError("任务不存在，请重新导入 SVG", 404)
            state = json.loads(path.read_text(encoding="utf-8"))
            state.setdefault('drawing_mode','svg' if state.get('assets',{}).get('svg') or state.get('catalog',{}).get('paths') else 'language')
            state.setdefault('drawing_text','')
            state.setdefault('language_geometry',None)
            if state['drawing_mode']=='language' and state.get('request')==_DEFAULT_REQUEST:
                state['request']=''
            if "assets" not in state:  # 升级已有 SVG 会话，保留选择、历史产物与输入。
                state["assets"] = {"aml":copy.deepcopy(self.assets.default_aml),
                    "library":copy.deepcopy(self.assets.default_library), "svg":None}
                state["device_model"] = copy.deepcopy(self.model)
                source = directory/"source.svg"
                if source.is_file():
                    state["assets"]["svg"] = self.assets.put("svg", state["name"], source.read_bytes())
            if state.get("busy"):
                state["busy"] = False
                state["phase"] = "interrupted"
                if state.get("runtime_action",{}).get("status")=="running":
                    state["runtime_action"].update(status="interrupted", errors=["服务已重启，操作结果未确认，请刷新 Runtime 状态"])
                for job in state["jobs"]:
                    if job["status"]=="running":
                        job["status"] = "failed"
                state["messages"].append({"role": "agent", "text": "服务已重启，上一任务中断；可重新整理需求或生成。"})
            self.sessions[ident] = state
            self._upgrade_builtin_scene(state)
            self._save(state)
        return self.sessions[ident]

    def _upgrade_builtin_scene(self, state):
        """仅迁移已知内置契约下界修正，派生新任务，旧交付保持不变。"""
        from .simulation import build_scene
        from .runtime_control import deployment_preflight
        library = self.assets.library(state["assets"]["library"])
        current = self.assets.library(self.assets.default_library)["contract"]
        legacy = copy.deepcopy(current)
        parameter = next(p for p in legacy["types"]["gantry_xyz"]["params"] if p["name"]=="travel_z")
        parameter["minimum"] = .01
        if library["contract"] != legacy or legacy == current:
            return
        candidate = copy.deepcopy(state)
        job = self._simulation_job(state)
        reusable = bool(job and job["status"]=="final" and job.get("source")!="external" and self._job_current(job,state))
        library["contract"] = current
        record = self.assets.put("library",state["assets"]["library"]["name"],self.assets.encode(library))
        candidate["assets"]["library"] = record
        candidate["revision"] += 1
        if reusable:
            source = self.root/job["id"]
            scene = json.loads((source/"final/scene.spec.json").read_text(encoding="utf-8"))
            spec = load_task_spec(source)
            from .aml_parser import build_axes
            from .scene_gen import _axes_info
            axis_info = _axes_info(spec,state["device_model"],spec["io_list"]).get("z",{})
            stroke=axis_info.get("stroke",[])
            travel=(stroke[1]-stroke[0])*axis_info["scale"] if len(stroke)==2 else None
            gantry=next((a for a in scene.get("assets",[]) if a["type"]=="gantry_xyz"),None)
            if gantry and travel is not None and abs(travel-.01)<1e-9 and abs(gantry["params"]["travel_z"]-.011)<1e-9:
                task="svg_web_"+secrets.token_hex(16);directory=self.root/task
                directory.mkdir();(directory/"final").mkdir();(directory/"requirements").mkdir()
                self._snapshot_assets(candidate,directory)
                gantry["params"]["travel_z"]=.01
                spec.update(task_id=task,axes=build_axes(state["device_model"]))
                (directory/"requirements/requirement_spec.json").write_bytes(self.assets.encode(spec))
                (directory/"final/plcopen.xml").write_bytes((source/"final/plcopen.xml").read_bytes())
                (directory/"final/scene.spec.json").write_bytes(self.assets.encode(scene))
                for name in ("trajectory.json","input.svg","trajectory-preview.html"):
                    if (source/name).is_file():(directory/name).write_bytes((source/name).read_bytes())
                (directory/"migration.json").write_bytes(self.assets.encode({"source_task":job["id"],"change":"travel_z 0.011 -> 0.01; PLC unchanged"}))
                deployment_preflight(directory)
                built=build_scene(directory)
                (directory/"web-request.json").write_bytes(self.assets.encode({key:candidate[key] for key in ("revision","assets","request","selected_paths","settings")}))
                candidate["jobs"].append({"id":task,"status":"final","source":"migrated","revision":candidate["revision"],"simulation":built,"files":[]})
                candidate["simulation_task"]=task
                candidate["phase"]="scene_ready"
        state.update(candidate)

    def _editable(self, state, revision):
        if self.motion and self.motion.alive:
            raise WorkbenchError("请先停止模型运动，再修改输入或操作 Runtime", 409)
        if state["busy"]:
            raise WorkbenchError("Agent 正在处理当前任务，请等待完成", 409)
        if revision != state["revision"] or isinstance(revision, bool):
            raise WorkbenchError("页面版本已更新，请刷新后重试", 409)

    def state(self, ident):
        with self.lock:
            state = copy.deepcopy(self._session(ident))
            for job in state["jobs"]:
                job["files"] = self._result_files(ident, job)
        state["model_ready"] = bool(get_api_key())
        state["position_mode"] = position_io(state["device_model"])
        state["preview_url"] = ("/file/" + ident + "/draft/trajectory-preview.html?v=" +
                                str(state["revision"])) if state.get("prepared") else None
        state["trajectory"] = (state.get("prepared") or {}).get("trajectory")
        state['drawing_summary']=(state.get('prepared') or {}).get('summary','')
        latest = self._simulation_job(state)
        current = bool(latest and self._job_current(latest, state))
        state["workflow"] = {"task":latest["id"] if latest else None, "current":current,
            "can_build":bool(current and latest["status"]=="final"),
            "files":latest.get("files",[]) if latest else [],
            "source":latest.get("source","generated") if latest else None,
            "verification_configured":latest.get("verification_configured",True) if latest else False,
            "simulation":latest.get("simulation") if latest else None}
        state["motion"] = self.motion_status(ident)
        if state["workflow"]["simulation"]:
            state["workflow"]["simulation"]["url"] = "/file/"+ident+"/"+latest["id"]+"/simulation/scene.xml"
            state["workflow"]["simulation"]["frame_url"] = "/api/simulation/frame?session="+ident+"&task="+latest["id"]
            state["workflow"]["simulation"]["view_url"] = "/api/simulation/view?session="+ident+"&task="+latest["id"]
            state["workflow"]["simulation"]["live_url"] = "/api/motion/frame?session="+ident+"&task="+latest["id"]
        state["assets"] = self.assets.summary(state["assets"], state["device_model"])
        for message in state["messages"]:
            if message["role"]=="agent" and message["text"].startswith("需求规格已整理"):
                match = re.search(r"工艺目标:\s*([^\n]*)", message["text"])
                if match:
                    message["text"] = process_summary({"task_goal":match[1],
                        "io_list":state["device_model"].get("io_points",[])}, state["trajectory"])
        state.pop("prepared", None)
        return state

    def _job_revision(self, job):
        if "revision" in job:
            return job["revision"]
        if not _TASK.fullmatch(job["id"]):
            return None
        path = self.root/job["id"]/"web-request.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8")).get("revision")
        return None

    @staticmethod
    def _simulation_job(state):
        selected = state.get("simulation_task")
        return next((j for j in state["jobs"] if j["id"]==selected),
                    state["jobs"][-1] if state["jobs"] else None)

    def select_simulation(self, ident, revision, task):
        with self.lock:
            state = self._session(ident)
            self._editable(state,revision)
            if not any(j["id"]==task and j["status"]=="final" for j in state["jobs"]):
                raise WorkbenchError("仿真任务不存在",404)
            state["simulation_task"]=task
            self._save(state)
        return self.state(ident)

    def _job_current(self, job, state):
        if self._job_revision(job)==state["revision"]:
            return True
        if not _TASK.fullmatch(job["id"]):
            return False
        path = self.root/job["id"]/"web-request.json"
        if not path.is_file():
            return False
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        # 切换后恢复相同输入时，允许复用既有交付；仍对账全部资产、工艺、选区与参数。
        return (snapshot.get('drawing_mode','svg')==state.get('drawing_mode','svg') and
                snapshot.get('language_geometry')==state.get('language_geometry') and
                all(key in snapshot and snapshot[key]==state[key]
                    for key in ("assets","request","selected_paths","settings")))

    def _result_files(self, ident, job):
        """仅发布成功任务的场景描述和最终 PLC XML，兼容历史任务。"""
        task_id = job["id"]
        if job["status"] != "final" or not _TASK.fullmatch(task_id):
            return []
        directory = self.root/task_id
        return [{"name":name, "url":"/file/"+ident+"/"+task_id+"/"+name}
                for name in ("final/scene.spec.json", "final/plcopen.xml")
                if (directory/name).is_file()]

    def _args(self, ident, state=None):
        state = self._session(ident) if state is None else state
        record = state["assets"]["svg"]
        return SimpleNamespace(svg=str(self.assets.path("svg", record["id"])),
            svg_center=None, svg_width=None, svg_height=None, svg_tolerance=None,
            svg_max_points=None, svg_keep_y=False, svg_paths=None)

    def _prepare(self, state, selected, settings, text=None):
        if state.get('drawing_mode')=='language':
            return self._prepare_language(state,text or state['drawing_text'],request=state['request'])
        if not isinstance(settings, dict) or set(settings)-_SETTING_KEYS:
            raise WorkbenchError("轨迹参数包含不支持的字段")
        prepared = prepare_svg_request(self._args(state["id"], state),
            text if text is not None else state["request"], state["device_model"],
            {"svg": dict(settings, selected_paths=selected)})
        save_preparation(prepared, self._directory(state["id"])/"draft")
        return prepared

    def _prepare_language(self,state,text,request=None):
        previous={'trajectory':state.get('language_geometry')} if state.get('language_geometry') else None
        prepared=prepare_language_drawing(text,state['device_model'],previous,request)
        draft=self._directory(state['id'])/'draft'
        save_preparation(prepared,draft)
        (draft/'input.svg').unlink(missing_ok=True)
        return prepared

    @staticmethod
    def _remember_language(state,prepared):
        state['language_geometry']=copy.deepcopy(prepared['trajectory'])
        state['drawing_text']=drawing_description(prepared['trajectory'])

    def drawing_source(self,ident,revision,mode):
        if mode not in ('language','svg'):
            raise WorkbenchError('轨迹来源须为 language 或 svg')
        with self.lock:
            current=self._session(ident);self._editable(current,revision)
            if current.get('drawing_mode')==mode:return self.state(ident)
            candidate=copy.deepcopy(current)
            candidate.update(drawing_mode=mode,prepared=None,spec=None,report=None,phase='draft',
                request=_DEFAULT_REQUEST if mode=='svg' else candidate['drawing_text'],revision=revision+1)
            try:
                if mode=='svg' and candidate['assets']['svg'] and candidate['selected_paths']:
                    candidate['prepared']=self._prepare(candidate,candidate['selected_paths'],candidate['settings'])
                elif mode=='language' and candidate.get('language_geometry'):
                    candidate['prepared']=self._prepare_language(candidate,candidate['drawing_text'])
            except ValueError as exc:
                candidate['messages'].append({'role':'agent','text':'已保留绘图输入，请调整轨迹后重新预览：'+str(exc)})
            candidate['messages'].append({'role':'agent','text':'已切换到'+('语言描述，可直接告诉我要画什么。' if mode=='language' else 'SVG 导入，请选择路径并预览。')+'原需求规格已失效。'})
            self.sessions[ident]=candidate;self._save(candidate)
        return self.state(ident)

    def plan_language(self,ident,revision,text):
        with self.lock:
            current=self._session(ident);self._editable(current,revision)
            if current.get('drawing_mode')!='language':raise WorkbenchError('请先切换到语言描述',409)
            candidate=copy.deepcopy(current)
            prepared=self._prepare_language(candidate,text)
            self._remember_language(candidate,prepared)
            candidate.update(prepared=prepared,request=text,spec=None,report=None,phase='draft',revision=revision+1)
            candidate['messages'].extend([{'role':'user','text':text},{'role':'agent','text':'轨迹已规划：'+prepared['summary']+'。可使用默认工艺，或继续补充启动、停止和完成条件。'}])
            self.sessions[ident]=candidate;self._save(candidate)
        return self.state(ident)

    def import_svg(self, name, data, ident=None, revision=None):
        if not isinstance(name, str) or not name.lower().endswith(".svg"):
            raise WorkbenchError("请选择 .svg 文件")
        if not isinstance(data, bytes) or not data or len(data) > 2*1024*1024:
            raise WorkbenchError("SVG 文件必须非空且不超过 2 MiB")
        with self.lock:
            if ident is not None:
                self._editable(self._session(ident), revision)
            record = self.assets.put("svg", name, data)
            catalog = svg_catalog(self.assets.path("svg", record["id"]))
            if ident is None:
                ident = self.create_session()["id"]
            state = copy.deepcopy(self._session(ident))
            if state.get('drawing_mode')!='svg':state['request']=_DEFAULT_REQUEST
            state['drawing_mode']='svg'
            state["assets"]["svg"] = record
            state.update(name=record["name"], revision=state["revision"]+1, catalog=catalog,
                selected_paths=[p["id"] for p in catalog["paths"]], settings={}, prepared=None,
                spec=None, report=None, phase="draft", logs=[])
            try:
                state["prepared"] = self._prepare(state, state["selected_paths"], {})
                state["settings"] = {key:state["prepared"]["trajectory"]["params"][key] for key in _SETTING_KEYS}
            except SVGError as exc:
                state["messages"].append({"role":"agent", "text":str(exc)+"。可取消部分路径或修改参数后重试。"})
            state["messages"].append({"role":"agent", "text":"SVG 已更新，原规格已失效。请选择路径、预览并补充工艺要求。"})
            self.sessions[ident] = state
            self._save(state)
        return self.state(ident)

    def replace_asset(self, ident, revision, kind, name=None, data=None, part=None, restore=False):
        if kind not in ("aml", "library") or not isinstance(restore, bool):
            raise WorkbenchError("资产类型或操作无效")
        with self.lock:
            current = self._session(ident)
            self._editable(current, revision)
            candidate = copy.deepcopy(current)
            if kind == "aml":
                if restore:
                    record, model = copy.deepcopy(self.assets.default_aml), copy.deepcopy(self.model)
                else:
                    record, model = self.assets.import_aml(name, data)
                candidate["device_model"] = model
            else:
                if restore:
                    record = copy.deepcopy(self.assets.default_library)
                else:
                    if not isinstance(name, str) or not name.lower().endswith(".json") or not isinstance(data, bytes) or len(data)>2*1024*1024:
                        raise WorkbenchError("组件库资产须为不超过 2 MiB 的 JSON 文件")
                    uploaded = read_json(data)
                    if part in ("contract", "physics"):
                        library = self.assets.library(current["assets"]["library"])
                        library[part] = uploaded
                    elif part is None:
                        library = uploaded
                    else:
                        raise WorkbenchError("组件库子资产类型无效")
                    library = validate_library(library)
                    validate_pair(candidate["device_model"], library)
                    record = self.assets.put("library", name if part is None else current["assets"]["library"]["name"],
                                             self.assets.encode(library))
                    record["parts"] = copy.deepcopy(current["assets"]["library"].get("parts", {})) if part else {}
                    if part:
                        record["parts"][part] = name.replace("\\", "/").split("/")[-1][:180]
            candidate["assets"][kind] = record
            validate_pair(candidate["device_model"], self.assets.library(candidate["assets"]["library"]))
            candidate.update(prepared=None, spec=None, report=None, phase="draft", logs=[], revision=current["revision"]+1)
            if candidate.get('drawing_mode')=='language' and candidate.get('language_geometry'):
                try:
                    candidate['prepared']=self._prepare_language(candidate,candidate['drawing_text'],candidate['request'])
                except ValueError as exc:
                    candidate['messages'].append({'role':'agent','text':'新资产已保存，但语言轨迹需要调整：'+str(exc)})
            elif candidate.get('drawing_mode')=='svg' and candidate["assets"]["svg"] and candidate["selected_paths"]:
                try:
                    candidate["prepared"] = self._prepare(candidate, candidate["selected_paths"], {} if kind=="aml" else candidate["settings"])
                    candidate["settings"] = {key:candidate["prepared"]["trajectory"]["params"][key] for key in _SETTING_KEYS}
                except SVGError as exc:
                    candidate["messages"].append({"role":"agent", "text":"新资产已保存，但轨迹需要调整："+str(exc)})
            candidate["messages"].append({"role":"agent", "text":("AML 设备描述" if kind=="aml" else "组件库")+
                "已更新，旧规格已失效，请重新核对轨迹和工艺要求。"+("绘图尺寸已按新设备行程重置。" if kind=="aml" else "")})
            self.sessions[ident] = candidate
            self._save(candidate)
        return self.state(ident)

    def plan(self, ident, revision, selected, settings):
        with self.lock:
            state = self._session(ident)
            self._editable(state, revision)
            if state.get('drawing_mode')!='svg':raise WorkbenchError('请先切换到 SVG 导入',409)
            if not isinstance(selected, list):
                raise WorkbenchError("请选择路径列表")
            if not selected:
                state.update(prepared=None, selected_paths=[], spec=None, report=None,
                             phase="draft", revision=state["revision"]+1)
                self._save(state)
                return self.state(ident)
            # 规划成功后才替换状态；无效参数不丢失上一版。
            prepared = self._prepare(state, selected, settings)
            state.update(prepared=prepared, selected_paths=prepared["trajectory"]["params"]["selected_paths"],
                         settings={key: prepared["trajectory"]["params"][key] for key in _SETTING_KEYS},
                         spec=None, report=None, phase="draft", revision=state["revision"]+1)
            self._save(state)
        return self.state(ident)

    def _log(self, ident, message):
        with self.lock:
            state = self._session(ident)
            state["logs"].append({"time": datetime.now(timezone.utc).isoformat(), "text": str(message)[:3000]})
            state["logs"] = state["logs"][-300:]
            self._save(state)

    def _launch(self, state, kind, function):
        if self.active:
            raise WorkbenchError("已有 Agent 任务正在执行，请稍后重试", 409)
        state.update(busy=True, phase=kind, logs=[])
        state.pop("workflow_error",None)
        self.active = state["id"]
        self._save(state)
        self.executor.submit(self._work, state["id"], function)

    def _work(self, ident, function):
        try:
            function()
        except Exception as exc:
            message = str(exc)
            key = get_api_key()
            if key:
                message = message.replace(key, "[hidden]")
            with self.lock:
                state = self._session(ident)
                state["phase"] = "failed"
                for job in state["jobs"]:
                    if job["status"]=="running":
                        job["status"] = "failed"
                state["messages"].append({"role":"agent", "text":"处理失败："+message[:3000]})
                state["workflow_error"] = message[:3000]
                (self._directory(ident)/"worker-error.txt").write_text(traceback.format_exc().replace(key, "[hidden]") if key else traceback.format_exc(), encoding="utf-8")
        finally:
            with self.lock:
                state = self._session(ident)
                state["busy"] = False
                state["messages"] = state["messages"][-60:]
                self.active = None
                self._save(state)

    def chat(self, ident, revision, message):
        if not isinstance(message, str) or not message.strip() or len(message) > 8000:
            raise WorkbenchError("请输入 1..8000 字的工艺要求或修改意见")
        with self.lock:
            state = self._session(ident)
            self._editable(state, revision)
            language=state.get('drawing_mode')=='language'
            if not language and not state["prepared"]:
                raise WorkbenchError("请先选择路径并生成有效预览")
            if self.active:
                raise WorkbenchError("已有 Agent 任务正在执行，请稍后重试", 409)
            # 几何修正先确定性执行并更新预览，之后由模型整理工艺规格。
            request=state['request']+'\n用户补充：'+message if state['request'] else message
            revised = (self._prepare_language(state,message,request) if language else
                refine_svg_request(self._args(ident), state["prepared"], message, state["device_model"]))
            save_preparation(revised, self._directory(ident)/"draft")
            old_spec = state["spec"]
            state.update(prepared=revised, request=request,
                         spec=None, report=None, revision=state["revision"]+1)
            if language:self._remember_language(state,revised)
            else:state['settings']={key: revised['trajectory']['params'][key] for key in _SETTING_KEYS}
            state["messages"].append({"role":"user", "text":message.strip()})
            if not get_api_key():
                state["messages"].append({"role":"agent", "text":
                    "已保存要求并更新轨迹预览。服务未配置模型，暂不能整理工艺规格或生成 PLC；配置后可再次发送要求。"})
                state["phase"] = "draft"
                self._save(state)
            else:
                self._launch(state, "understanding", lambda: self._understand(ident, old_spec, message))
        return self.state(ident)

    def clear_context(self, ident, revision):
        with self.lock:
            state = self._session(ident)
            self._editable(state, revision)
            candidate = copy.deepcopy(state)
            candidate.update(request=candidate['drawing_text'] if candidate.get('drawing_mode')=='language' else _DEFAULT_REQUEST, spec=None, report=None, messages=[], logs=[],
                             phase="draft", revision=revision+1)
            if state.get("prepared"):
                candidate["prepared"] = self._prepare(candidate, candidate["selected_paths"], candidate["settings"])
            self.sessions[ident] = candidate
            self._save(candidate)
        return self.state(ident)

    def default_process(self, ident, revision):
        from .requirement import RequirementUnderstander
        with self.lock:
            state = self._session(ident)
            self._editable(state, revision)
            if not state.get("prepared"):
                raise WorkbenchError("请先描述绘图任务并预览，或选择有效 SVG 轨迹", 409)
            candidate = copy.deepcopy(state)
            candidate["request"] = _DEFAULT_PROCESS
            prepared = self._prepare(candidate, candidate["selected_paths"], candidate["settings"])
            result = RequirementUnderstander().understand(prepared["request_text"],
                device_model=candidate["device_model"], task_id="svg_web_"+ident)
            if result["spec"] is None:
                raise WorkbenchError("默认工艺无法生成："+"；".join(result["report"].get("problems",[])))
            candidate.update(prepared=prepared, spec=result["spec"], report=result["report"],
                             phase="ready", revision=revision+1)
            candidate["messages"].append({"role":"agent", "text":process_summary(result["spec"], prepared["trajectory"])})
            self.sessions[ident] = candidate
            self._save(candidate)
        return self.state(ident)

    def _understand(self, ident, old_spec, correction):
        from .client import BigModelClient
        from .requirement import RequirementUnderstander
        self._log(ident, "Agent 正在根据当前轨迹整理工艺要求…")
        with self.lock:
            state = copy.deepcopy(self._session(ident))
        understander = RequirementUnderstander(BigModelClient(get_api_key()), model=MODEL)
        task_id = "svg_web_"+ident
        request = state["prepared"]["request_text"]
        result = (understander.refine(old_spec, request, device_model=state["device_model"], correction=correction)
                  if old_spec else understander.understand(request, device_model=state["device_model"], task_id=task_id))
        with self.lock:
            current = self._session(ident)
            spec = result["spec"]
            if spec is not None:
                spec["task_id"] = task_id
                current.update(spec=spec, report=result["report"], phase="ready")
                text = process_summary(spec, state["prepared"]["trajectory"])
            else:
                current["phase"] = "draft"
                text = "规格尚未通过检查，请补充或修改要求：\n"+"\n".join(result["report"].get("problems", []))
            current["messages"].append({"role":"agent", "text":text})

    def generate(self, ident, revision):
        with self.lock:
            state = self._session(ident)
            self._editable(state, revision)
            if not get_api_key():
                raise WorkbenchError("服务未配置模型，无法生成 PLC", 409)
            if not state.get("spec") or not state.get("prepared"):
                raise WorkbenchError("请先向 Agent 发送工艺要求，核对整理后的规格", 409)
            if self.active:
                raise WorkbenchError("已有 Agent 任务正在执行，请稍后重试", 409)
            self._begin_generation(state)
        return self.state(ident)

    def _begin_generation(self, state, seed=None, source=None, repair_kind=None):
        ident=state['id'];task_id='svg_web_'+secrets.token_hex(16)
        snapshot=copy.deepcopy(state)
        snapshot['spec']['task_id']=task_id
        if position_io(snapshot['device_model']):snapshot['spec'].setdefault('prog_id',2)
        directory=self.root/task_id;directory.mkdir()
        save_preparation(snapshot['prepared'],directory)
        self._snapshot_assets(snapshot,directory)
        (directory/'web-request.json').write_text(json.dumps({
            'session':ident,'revision':state['revision'],'selected_paths':state['selected_paths'],
            'settings':state['settings'],'request':state['request'],'assets':state['assets'],
            'drawing_mode':state.get('drawing_mode','svg'),'language_geometry':state.get('language_geometry')},
            ensure_ascii=False,indent=2),encoding='utf-8')
        if seed is not None:
            (directory/'position-source.xml').write_text(seed,encoding='utf-8')
            (directory/'conversion.json').write_text(json.dumps({'source_job':source,'kind':repair_kind or 'CSP position output'},ensure_ascii=False,indent=2),encoding='utf-8')
            snapshot['_position_seed']=True
        elif state['jobs']:
            previous=state['jobs'][-1]
            if previous['status'] not in ('final','running') and self._job_current(previous,state):
                from .workbench_validation import failed_candidate, resumable_environment_failure
                candidate=failed_candidate(self.root/previous['id'])
                if candidate:
                    xml,feedback,origin=candidate
                    source_path=Path(origin) if Path(origin).is_absolute() else self.root/previous['id']/origin
                    (directory/'repair-source.xml').write_text(xml,encoding='utf-8')
                    (directory/'repair-source.json').write_text(json.dumps({'task':previous['id'],
                        'path':origin,'feedback':feedback,'resume_validation':resumable_environment_failure(source_path)},ensure_ascii=False,indent=2),encoding='utf-8')
                    snapshot['_repair_candidate']=True
        state['jobs'].append({'id':task_id,'status':'running','files':[],'revision':state['revision']})
        if repair_kind:state['jobs'][-1]['source']='repaired'
        state.pop('simulation_task',None)
        state['messages'].append({'role':'agent','text':'正在生成位置指令 PLC，校验当前轨迹、IO 和场景。' if seed is not None else '开始按当前轨迹生成 PLC，并执行静态校验与场景一致性检查。'})
        self._launch(state,'generating',lambda:self._generate(ident,task_id,snapshot))

    def enable_position(self, ident, revision):
        from .aml_parser import build_io_list
        from .spec_validator import validate_requirement_spec
        with self.lock:
            current=self._session(ident);self._editable(current,revision)
            if position_io(current['device_model']):return self.state(ident)
            if self.active:raise WorkbenchError('已有后台任务正在执行',409)
            candidate=copy.deepcopy(current)
            data=make_position_aml(self.assets.bytes(current['assets']['aml']))
            name=Path(current['assets']['aml']['name']).stem+'-csp.aml'
            record,model=self.assets.import_aml(name,data)
            validate_pair(model,self.assets.library(current['assets']['library']))
            candidate['assets']['aml']=record;candidate['device_model']=model
            candidate.update(revision=revision+1,phase='draft',logs=[])
            if candidate.get('prepared'):
                candidate['prepared']=self._prepare(candidate,candidate['selected_paths'],candidate['settings'])
            if candidate.get('spec'):
                items,pending=build_io_list(model)
                if pending:raise WorkbenchError('位置指令 IO 量程未完整定义')
                old={p['name']:p for p in candidate['spec']['io_list']}
                for item in items:
                    if item['name'] in old and item['name'] not in ('x_v','y_v','z_v'):
                        item['device']=old[item['name']].get('device',item.get('device',''))
                candidate['spec']['io_list']=items
                candidate['spec'].setdefault('prog_id',2)
                problems=validate_requirement_spec(candidate['spec'])
                if problems:raise WorkbenchError('位置指令规格校验失败：'+'；'.join(problems))
                candidate['phase']='ready'
            latest=current['jobs'][-1] if current['jobs'] else None
            seed=None
            if latest and latest['status']=='final' and self._job_current(latest,current) and candidate.get('spec'):
                seed=convert_position_program((self.root/latest['id']/'final/plcopen.xml').read_text(encoding='utf-8'),model,candidate['spec']['prog_id'])
            candidate['messages'].append({'role':'agent','text':'已切换为位置指令资产，轨迹与工艺保持；原交付已保留。'})
            self.sessions[ident]=candidate;self._save(candidate)
            if seed is not None:self._begin_generation(candidate,seed,latest['id'])
        return self.state(ident)

    def _snapshot_assets(self, snapshot, directory):
        target = directory/"assets"
        target.mkdir()
        assets = snapshot["assets"]
        library = self.assets.library(assets["library"])
        (target/"device.aml").write_bytes(self.assets.bytes(assets["aml"]))
        (target/"component-library.json").write_bytes(self.assets.bytes(assets["library"]))
        (target/"components.json").write_bytes(self.assets.encode(library["contract"]))
        (target/"physics.json").write_bytes(self.assets.encode(library["physics"]))
        (directory/"assets-manifest.json").write_bytes(self.assets.encode(assets))

    def _solve(self, snapshot, directory, log):
        from .attribution import AttributionEngine
        from .client import BigModelClient
        from .workbench_validation import WorkbenchOrchestrator
        from .workbench_validation import VerifiedPLCGenerator
        from .scene_gen import SceneSpecGenerator
        seed = directory/'position-source.xml' if snapshot.get('_position_seed') else None
        client = None if seed else BigModelClient(get_api_key())
        model = snapshot["device_model"]
        from .aml_parser import build_axes
        snapshot["spec"].setdefault("axes", build_axes(model))
        addresses = {p["name"]:p["address"] for p in model["io_points"] if p.get("address")}
        for point in snapshot["spec"]["io_list"]:
            if point["name"] in addresses:
                point["address"] = addresses[point["name"]]
        library = self.assets.library(snapshot["assets"]["library"])
        retry=json.loads((directory/'repair-source.json').read_text(encoding='utf-8')) if snapshot.get('_repair_candidate') else None
        generator = VerifiedPLCGenerator(client, model=MODEL, no_cards=True,device_model=model,
            initial_candidate=(directory/'repair-source.xml').read_text(encoding='utf-8') if retry else None,
            initial_feedback=retry['feedback'] if retry else None,
            resume_validation=bool(retry and retry.get('resume_validation')),
            address_table={p["name"]:p["address"] for p in model["io_points"] if p.get("address")}, report=log,
            seed_xml=seed,position_mode=position_io(model),prog_id=snapshot['spec'].get('prog_id',2))
        def on_motion(motion):
            with self.lock:
                self.motion=motion
        orchestrator = WorkbenchOrchestrator(directory=directory,session=snapshot['id'],
            runtime=self.runtime,report=log,on_motion=on_motion,
            runs_root=directory.parent, max_iters=self.max_iters,
            attribution_engine=AttributionEngine(client=client))
        def echo(event, payload):
            log(event+" · "+json.dumps(payload, ensure_ascii=False, default=str))
        result=orchestrator.solve(snapshot["spec"], generator, deploy=True, acceptance='workbench',
            require_online=seed is None,
            trajectory=snapshot["prepared"]["trajectory"], device_model=model,
            scene_generator=SceneSpecGenerator(library["contract"]), echo=echo,
            aml_path=directory/"assets/device.aml")
        return result

    @staticmethod
    def _generation_validation(directory):
        from .workbench_validation import validation_result
        return validation_result(directory)

    def _generate(self, ident, task_id, snapshot):
        directory = self.root/task_id
        result = self._solve(snapshot, directory, lambda text:self._log(ident, text))
        result['validation']=self._generation_validation(directory)
        if result['status']=='final' and result['validation'].get('status')!='passed':
            raise WorkbenchError('缺少与最终源码绑定的物理验收结果，不能标为生成成功')
        with self.lock:
            state = self._session(ident)
            job = next(job for job in state["jobs"] if job["id"]==task_id)
            job["status"] = result["status"]
            job['stop_reason']=result.get('stop_reason')
            job['validation']=result.get('validation',{'status':'not_verified'})
            if result['status']=='final':
                job['simulation']=json.loads((directory/'simulation/build.json').read_text(encoding='utf-8'))
                state['runtime_deployment']={'action':'deploy','task':task_id,'status':'success',
                    'steps':(result.get('deployment') or {}).get('steps',[]),'errors':[]}
                state['runtime_action']=copy.deepcopy(state['runtime_deployment'])
            job["files"] = self._result_files(ident, job)
            state["phase"] = "completed" if result["status"]=="final" else "failed"
            state["messages"].append({"role":"agent", "text":
                ("PLC 已生成，通过真实编译、部署及物理仿真验收。可下载场景描述 JSON 和最终 PLC XML。" if result["status"]=="final" else
                 {'env_down':'通信或Runtime环境中断，已保留候选及失败证据；恢复环境后可继续生成。',
                  'no_progress':'模型返回了重复候选，已停止重复调用、编译与验收；已保留候选及失败证据。',
                  'output_limit':'模型回复被输出额度截断，已停止自动调用并保留此前候选；未将截断工程用于编译或仿真。',
                  'invalid_output':'模型未返回完整 XML，已停止自动调用并保留此前候选；请检查模型响应。'
                 }.get(result.get('stop_reason'),"本轮未通过全部编译和物理仿真验收，已保留候选与失败证据；没有交付验收通过的 PLC。"))})

    def build_simulation(self, ident, revision, task):
        with self.lock:
            state = self._session(ident)
            self._editable(state, revision)
            job = self._simulation_job(state)
            if not job or job["id"]!=task or job["status"]!="final":
                raise WorkbenchError("请先完成当前任务的 PLC 代码生成", 409)
            if not self._job_current(job, state):
                raise WorkbenchError("当前资产或需求已更新，请重新生成 PLC 代码", 409)
            self._launch(state, "building_scene", lambda:self._build_simulation(ident, task))
        return self.state(ident)

    def _build_simulation(self, ident, task):
        from .simulation import build_scene
        self._log(ident, "正在根据场景描述和组件物理参数构建仿真场景…")
        result = build_scene(self.root/task)
        with self.lock:
            state = self._session(ident)
            job = next(job for job in state["jobs"] if job["id"]==task)
            job["simulation"] = result
            state["phase"] = "scene_ready"
        self._log(ident, "仿真场景已生成并通过 MuJoCo 加载校验。")

    def import_simulation(self, ident, revision, files):
        """配对导入只建立新仿真任务，保留原文件和已有生成交付。"""
        import shutil
        from .aml_parser import build_io_list, build_axes
        from .runtime_control import deployment_preflight
        from .simulation import build_scene
        from .consistency_check import xml2st
        from .spec_validator import validate_requirement_spec
        from .requirement import _anchor_problems
        with self.lock:
            state = self._session(ident)
            self._editable(state, revision)
            if self.active:
                raise WorkbenchError("已有后台任务正在执行",409)
            if not isinstance(files,dict) or set(files)-{"scene","xml","spec"}:
                raise WorkbenchError("请选择场景 JSON 和 PLC XML")
            decoded = {}
            for key, extension in (("scene",".json"),("xml",".xml"),("spec",".json")):
                item = files.get(key)
                if not item and key=="spec":continue
                if not isinstance(item,dict) or not str(item.get("name","")).lower().endswith(extension):
                    raise WorkbenchError("请选择有效的 "+key+extension+" 文件")
                raw = base64.b64decode(item.get("data",""),validate=True)
                if not 0<len(raw)<=2*1024*1024:
                    raise WorkbenchError("单个导入文件须非空且不超过 2 MiB")
                decoded[key] = raw
            scene = read_json(decoded["scene"])
            task = "svg_web_"+secrets.token_hex(16)
            directory = self.root/task
            directory.mkdir()
            try:
                (directory/"final").mkdir()
                self._snapshot_assets(state,directory)
                (directory/"final/plcopen.xml").write_bytes(decoded["xml"])
                (directory/"final/scene.spec.json").write_bytes(decoded["scene"])
                ok, st, errors = xml2st.convert(directory/"final/plcopen.xml")
                if not ok:raise WorkbenchError("XML 校验失败："+"；".join(errors))
                ids = set(re.findall(r"\bprog_id\s*:=\s*(?:INT#)?(\d+)\s*;",st,re.I))
                if len(ids)!=1:raise WorkbenchError("XML 须包含唯一的 prog_id 常量赋值")
                points,pending = build_io_list(state["device_model"])
                if pending:raise WorkbenchError("当前 AML 的 IO 量程尚未完整定义")
                configured = "spec" in decoded
                spec = read_json(decoded["spec"]) if configured else {
                    "schema_version":"1.0.0-draft.3", "task_id":task,
                    "task_goal":"使用导入的外部 PLC 与仿真场景进行手动调试",
                    "io_list":points, "constraints":[],
                    "acceptance":[{"id":"AC1","desc":"IO 量程检查","type":"sim_health"}]}
                spec.update(task_id=task,prog_id=int(next(iter(ids))))
                spec.setdefault("axes",build_axes(state["device_model"]))
                problems = validate_requirement_spec(spec)+_anchor_problems(spec,points)
                if problems:raise WorkbenchError("规格与当前 AML 不一致："+"；".join(problems))
                req = directory/"requirements";req.mkdir()
                (req/"requirement_spec.json").write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding="utf-8")
                manifest={"verification_configured":configured,"files":{k:Path(files[k]["name"]).name for k in decoded}}
                (directory/"import.json").write_text(json.dumps(manifest,ensure_ascii=False),encoding="utf-8")
                deployment_preflight(directory)
                built=build_scene(directory)
                job={"id":task,"status":"final","revision":revision,"source":"external",
                     "verification_configured":configured,"simulation":built,"files":[]}
                state["jobs"].append(job)
                state["simulation_task"]=task
                state["phase"]="scene_ready"
                self._save(state)
            except Exception:
                if directory.parent==self.root and _TASK.fullmatch(directory.name):
                    shutil.rmtree(directory)
                raise
        return self.state(ident)

    def scene_frame(self, ident, task, azimuth=135, elevation=-35, distance=1.8, pan_x=0, pan_y=0):
        from .simulation import render_scene
        path = self.file(ident, task, "simulation/scene.xml")
        with self.render_lock:
            try:
                motion = self.motion
                snapshot = motion.snapshot() if motion and motion.session==ident and motion.task==task else None
                if snapshot is None:
                    return render_scene(path, azimuth, elevation, distance, pan_x, pan_y)
                return render_scene(path, azimuth, elevation, distance, pan_x, pan_y, snapshot=snapshot)
            except RuntimeError as exc:
                raise WorkbenchError("场景预览渲染失败："+str(exc), 503) from exc

    def motion_status(self, ident):
        with self.lock:
            state=self._session(ident)
            job=self._simulation_job(state)
            task=job['id'] if job else None
            if self.motion and self.motion.session==ident and self.motion.task==task:
                return self.motion.status()
            path=self.root/task/'simulation/motion.json' if task and _TASK.fullmatch(task) else None
            if path and path.is_file():
                record=json.loads(path.read_text(encoding='utf-8'))
                return {k:v for k,v in dict(record,active=False).items() if k!='snapshot'}
            return {"status":"not_started", "active":False,
                    "occupied":bool(self.motion and self.motion.alive)}

    def scene_view(self, ident, task):
        from .simulation import scene_geometry
        path=self.file(ident,task,'simulation/scene.xml')
        key=(str(path),path.stat().st_mtime_ns)
        with self.render_lock:
            if key not in self.scene_cache:
                self.scene_cache={key:scene_geometry(path)}
            return self.scene_cache[key]

    def motion_frame(self, ident, task, after=0, run=None):
        path=self.file(ident,task,'simulation/scene.xml')
        if not 0<=after<=1000000:raise WorkbenchError('墨迹位置无效')
        motion=self.motion
        if motion and motion.session==ident and motion.task==task:
            return motion.frame(after,run)
        record=path.with_name('motion.json')
        if record.is_file():
            from .simulation import scene_geometry
            key=(str(record),record.stat().st_mtime_ns)
            with self.render_lock:
                if key not in self.motion_cache:
                    data=json.loads(record.read_text(encoding='utf-8'));snapshot=data['snapshot']
                    geometry=scene_geometry(path,snapshot)
                    self.motion_cache={key:(snapshot,geometry['transforms'])}
                snapshot,transforms=self.motion_cache[key]
            token='recorded-'+str(key[1]);ink=snapshot['ink'];reset=run!=token or after>len(ink);start=0 if reset else after
            return {'run':token,'active':False,'sequence':key[1],'transforms':transforms,
                    'ink_reset':reset,'ink_total':len(ink),'ink':ink[start:],
                    'strokes':snapshot.get('strokes',[1]*len(ink))[start:]}
        return {'active':False}

    def control_motion(self, ident, revision, action, task, mode="auto"):
        if action not in ("start", "stop") or mode not in ("auto", "manual", "verify"):
            raise WorkbenchError("未知模型操作")
        if action=="stop":
            with self.lock:
                self._session(ident)
                motion = self.motion
                if not motion or motion.session!=ident:
                    raise WorkbenchError("当前会话没有模型运动任务",409)
            motion.stop()
            return self.state(ident)
        with self.lock:
            state = self._session(ident)
            self._editable(state,revision)
            if self.active:
                raise WorkbenchError("已有后台任务正在执行",409)
            job = self._simulation_job(state)
            if not job or job["id"]!=task or job["status"]!="final" or not self._job_current(job,state):
                raise WorkbenchError("请先生成当前输入的 PLC",409)
            if not job.get("simulation",{}).get("runtime_ready"):
                raise WorkbenchError("请先生成可连接的仿真场景",409)
            if mode!="manual" and not job.get("verification_configured",True):
                raise WorkbenchError("外部文件尚未配置工艺验收，请使用手动调试或同时导入需求规格",409)
            self._launch(state,"motion_start",lambda:self._start_motion(ident,task,mode))
        return self.state(ident)

    def _start_motion(self, ident, task, mode="auto"):
        import hashlib
        from .motion import Motion
        from .runtime_control import deployment_preflight
        xml = deployment_preflight(self.root/task)
        probe = self.runtime.status()
        # 状态轮询会短暂占用 lx 的串行 Runtime 锁，BUSY 不代表 PLC 停机。
        import time
        for _ in range(10):
            if probe.get("runtime",{}).get("status")!="BUSY":
                break
            time.sleep(.25)
            probe = self.runtime.status()
        if (probe.get("deployment") or {}).get("sha256")!=hashlib.sha256(xml).hexdigest():
            with self.lock:
                failed = self._session(ident).get("runtime_deployment") or {}
            if failed.get("task")==task and failed.get("status")=="failed":
                raise WorkbenchError("当前 PLC 部署失败，Runtime 仍是旧程序。请先修复部署错误："+
                                     "；".join(failed.get("errors") or []),409)
            raise WorkbenchError("当前 XML 尚未成功部署；请使用“部署并启动 PLC”，单独启动 Runtime 只会运行已有程序",409)
        if probe.get("runtime",{}).get("status")!="RUNNING":
            raise WorkbenchError("请先启动 OpenPLC Runtime；若状态查询中，请稍后重试",409)
        spec = load_task_spec(self.root/task)
        if probe.get("prog_id")!=spec.get("prog_id",2):
            raise WorkbenchError("Runtime 程序身份与当前任务不一致",409)
        target = probe.get("modbus") or {}
        host = target.get("host") or os.environ.get("MODBUS_HOST") or urlsplit(probe["runtime"].get("url","")).hostname
        if not host:
            raise WorkbenchError("部署服务未提供 Modbus 目标地址",409)
        motion = Motion(self.root/task,ident,host,int(target.get("port") or os.environ.get("MODBUS_PORT","502")),mode=mode)
        with self.lock:
            self.motion = motion
            motion.start()
            self._session(ident)["phase"] = "motion_ready"
        self._log(ident,"模型已连接位置桥。"+("手动调试面板已就绪。" if mode=="manual" else "正在使能并启动绘制。"))

    def studio_state(self, ident):
        from .studio_web import panel, verification_records
        with self.lock:
            state = self._session(ident)
            job = self._simulation_job(state)
            if not job or job["status"]!="final":
                return {"panel":{"elements":[]}, "values":{}, "trace":[], "records":[], "history":[]}
            task = job["id"]
            self.file(ident, task, "final/plcopen.xml")
            spec = load_task_spec(self.root/task)
            motion = self.motion
            matching = motion and motion.session==ident and motion.task==task
            status = motion.status() if matching else self.motion_status(ident)
            records = verification_records(self.root/task)
            history = []
            for item in reversed(state["jobs"]):
                if _TASK.fullmatch(item["id"]):
                    for record in verification_records(self.root/item["id"]):
                        history.append(record)
                if len(history)>=30:
                    break
            trace = motion.trace_window() if matching else []
            if not trace and records:
                path = self.root/task/"simulation/verifications"/records[0]["id"]/"trace.json"
                trace = json.loads(path.read_text(encoding="utf-8"))[-600:]
            return {"task":task, "panel":panel(spec), "values":status.get("values", {}),
                    "trace":trace, "records":records, "history":history[:30],
                    "writable":bool(matching and motion.alive and motion.mode=="manual" and not state["busy"])}

    def studio_signal(self, ident, revision, task, signal, value):
        with self.lock:
            state = self._session(ident)
            if state["revision"]!=revision or state["busy"]:
                raise WorkbenchError("页面输入已变化或任务正在执行",409)
            motion = self.motion
            job = self._simulation_job(state)
            if (not motion or motion.session!=ident or motion.task!=task or not job
                    or job["id"]!=task or not self._job_current(job,state)):
                raise WorkbenchError("请先连接当前任务的手动调试",409)
            try:
                motion.signal_command(signal,value)
            except ValueError as exc:
                raise WorkbenchError(str(exc),409) from exc
        return {"queued":True}

    def runtime_status(self, ident):
        from .runtime_control import RuntimeControlError
        with self.lock:
            state = self._session(ident)
            last_action = copy.deepcopy(state.get("runtime_action"))
            job = self._simulation_job(state)
            deployment = copy.deepcopy(state.get("runtime_deployment") or {})
        result = {"service":"offline", "runtime":"unknown", "program":None,
                  "prog_id":None, "control":"not_connected", "simulation":self.motion_status(ident)["status"],
                  "capabilities":[], "last_action":last_action}
        try:
            probe = self.runtime.status()
            result.update(service="online", runtime=probe.get("runtime",{}).get("status","UNKNOWN"),
                          program=probe.get("program"), prog_id=probe.get("prog_id"),
                          capabilities=probe.get("capabilities",["deploy"]),
                          control="connected" if "start" in probe.get("capabilities",[]) else "deploy_only",
                          deployment=probe.get("deployment"), error=probe.get("runtime",{}).get("error"))
            target = self.root/job["id"]/"final/plcopen.xml" if job else None
            import hashlib
            result["deployment_matches"] = bool(target and target.is_file() and
                (probe.get("deployment") or {}).get("sha256")==hashlib.sha256(target.read_bytes()).hexdigest())
            if (job and deployment.get("task")==job["id"] and deployment.get("status")=="failed"
                    and not result["deployment_matches"]):
                result["deployment_error"] = "当前 PLC 部署失败，请查看部署日志；单独启动 Runtime 会运行旧程序。"
        except (RuntimeControlError, TypeError, AttributeError) as exc:
            result["error"] = str(exc)
        return result

    def runtime_logs(self, ident):
        from .runtime_control import RuntimeControlError
        with self.lock:
            self._session(ident)
        try:
            return self.runtime.logs()
        except RuntimeControlError as exc:
            raise WorkbenchError(str(exc), 502) from exc

    def control_runtime(self, ident, revision, action, task=None):
        if action not in ("deploy", "start", "stop"):
            raise WorkbenchError("未知 Runtime 操作", 404)
        with self.lock:
            state = self._session(ident)
            self._editable(state, revision)
            if self.active:
                raise WorkbenchError("已有后台任务正在执行，请稍后重试", 409)
            if action == "deploy":
                job = self._simulation_job(state)
                if not job or job["id"]!=task or job["status"]!="final":
                    raise WorkbenchError("请先完成当前任务的 PLC 生成", 409)
                if not self._job_current(job, state):
                    raise WorkbenchError("当前资产或需求已更新，请重新生成 PLC", 409)
            state["runtime_action"] = {"action":action,"task":task if action=="deploy" else None,
                "status":"running", "steps":[], "errors":[]}
            if action=="deploy":
                state["runtime_deployment"] = copy.deepcopy(state["runtime_action"])
            self._launch(state, "runtime_"+action, lambda:self._control_runtime(ident, action, task))
        return self.state(ident)

    def _control_runtime(self, ident, action, task):
        from .runtime_control import deployment_preflight
        names = {"deploy":"部署并启动 PLC", "start":"启动 Runtime", "stop":"停止 Runtime"}
        self._log(ident, names[action]+"…")
        try:
            xml = deployment_preflight(self.root/task) if action=="deploy" else None
            result = self.runtime.action(action, xml)
            if result.get("status")!="OK":
                raise RuntimeError("；".join(map(str,result.get("errors") or ["Runtime 操作失败"])))
            record = {"action":action,"task":task if action=="deploy" else None,
                      "status":"success", "steps":result.get("steps",[]), "errors":[]}
            self._log(ident, names[action]+"完成。")
        except Exception as exc:
            key = get_api_key()
            message = str(exc).replace(key,"[hidden]") if key else str(exc)
            record = {"action":action,"task":task if action=="deploy" else None,
                      "status":"failed", "steps":getattr(exc,"result",{}).get("steps",[]), "errors":[message[:4000]]}
            self._log(ident, names[action]+"失败："+message)
        with self.lock:
            state = self._session(ident)
            state["runtime_action"] = record
            if action=="deploy":
                state["runtime_deployment"] = copy.deepcopy(record)
            state["phase"] = "runtime_ready" if record["status"]=="success" else "runtime_failed"

    def file(self, ident, task, relative):
        with self.lock:
            state = self._session(ident)
            if task=="draft":
                base = self._directory(ident)/"draft"
            elif _TASK.fullmatch(task) and any(job["id"]==task for job in state["jobs"]):
                base = self.root/task
            else:
                raise WorkbenchError("产物不存在", 404)
        allowed = {"trajectory-preview.html", "replay.html", "summary.md", "trajectory.json", "input.svg",
                   "final/plcopen.xml", "final/scene.spec.json", "final/gate.json", "scene.xml",
                   "assets-manifest.json", "assets/device.aml", "assets/component-library.json",
                   "assets/components.json", "assets/physics.json", "simulation/scene.xml"}
        if (relative not in allowed and not re.fullmatch(r"iter_\d{3}/plcopen\.xml", relative)
                and not re.fullmatch(r"simulation/verifications/\d{8}_\d{6}_[0-9a-f]{8}/(?:trace\.json|physical_trace\.json|verdict\.json|report\.md)",relative)):
            raise WorkbenchError("产物不存在", 404)
        path = (base/relative).resolve()
        if not path.is_relative_to(base.resolve()) or not path.is_file():
            raise WorkbenchError("产物不存在", 404)
        return path

    def asset_file(self, kind, digest, part):
        if part not in ("source", "contract", "physics") or (part!="source" and kind!="library"):
            raise WorkbenchError("资产不存在", 404)
        path = self.assets.path(kind, digest)
        if not path.is_file():
            raise WorkbenchError("资产不存在", 404)
        if part == "source":
            return path.read_bytes(), {"aml":"device.aml", "library":"component-library.json", "svg":"drawing.svg"}[kind]
        library = validate_library(read_json(path.read_bytes()))
        return self.assets.encode(library[part]), part+".json"


def make_server(workbench, port=8765):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _check_host(self):
            authority = self.headers.get("Host", "")
            allowed = {"127.0.0.1:"+str(self.server.server_port), "localhost:"+str(self.server.server_port)}
            if authority not in allowed:
                raise WorkbenchError("不允许的访问地址", 403)
            origin = self.headers.get("Origin")
            if origin and origin != "http://"+authority:
                raise WorkbenchError("请从工作台页面操作", 403)

        def _send(self, code, data, content_type="application/json; charset=utf-8", attachment=None):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if code>=400:
                self.close_connection=True
                self.send_header("Connection", "close")
            if attachment:
                self.send_header("Content-Disposition", 'attachment; filename="'+attachment+'"')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                self._check_host()
                parsed = urlsplit(self.path)
                if parsed.path in ("/", "/app.js", "/style.css", "/scene_view.js"):
                    filename = "index.html" if parsed.path=="/" else parsed.path[1:]
                    content_type = {"index.html":"text/html", "app.js":"text/javascript", "scene_view.js":"text/javascript", "style.css":"text/css"}[filename]
                    self._send(200, (UI_DIR/filename).read_bytes(), content_type+"; charset=utf-8")
                elif parsed.path=="/api/config":
                    self._send(200, workbench.config())
                elif parsed.path=="/api/state":
                    self._send(200, workbench.state(parse_qs(parsed.query).get("session", [""])[0]))
                elif parsed.path=="/api/runtime/status":
                    self._send(200, workbench.runtime_status(parse_qs(parsed.query).get("session", [""])[0]))
                elif parsed.path=="/api/motion/status":
                    self._send(200, workbench.motion_status(parse_qs(parsed.query).get("session", [""])[0]))
                elif parsed.path=="/api/studio/state":
                    self._send(200, workbench.studio_state(parse_qs(parsed.query).get("session", [""])[0]))
                elif parsed.path in ('/api/simulation/view','/api/motion/frame'):
                    query=parse_qs(parsed.query)
                    ident,task=(query.get(k,[''])[0] for k in ('session','task'))
                    result=(workbench.scene_view(ident,task) if parsed.path.endswith('/view') else
                            workbench.motion_frame(ident,task,int(query.get('after',['0'])[0]),query.get('run',[None])[0]))
                    self._send(200,result)
                elif parsed.path=="/api/runtime/logs":
                    self._send(200, workbench.runtime_logs(parse_qs(parsed.query).get("session", [""])[0]))
                elif parsed.path=="/api/simulation/frame":
                    query = parse_qs(parsed.query)
                    ident, task = (query.get(name,[""])[0] for name in ("session","task"))
                    camera = [float(query.get(name,[default])[0]) for name,default in
                              (("azimuth",135),("elevation",-35),("distance",1.8),("pan_x",0),("pan_y",0))]
                    self._send(200, workbench.scene_frame(ident, task, *camera), "image/png")
                elif parsed.path.startswith("/asset/"):
                    parts = unquote(parsed.path).split("/")
                    if len(parts)!=5:
                        raise WorkbenchError("资产不存在", 404)
                    data, filename = workbench.asset_file(*parts[2:])
                    self._send(200, data, "application/octet-stream", attachment=filename)
                elif parsed.path.startswith("/file/"):
                    parts = unquote(parsed.path).split("/", 4)
                    if len(parts)!=5:
                        raise WorkbenchError("产物不存在", 404)
                    path = workbench.file(*parts[2:])
                    inline = path.suffix==".html"
                    self._send(200, path.read_bytes(), "text/html; charset=utf-8" if inline else "application/octet-stream",
                               attachment=None if inline else path.name)
                else:
                    raise WorkbenchError("地址不存在", 404)
            except ConnectionError:
                pass  # 切换页面或视角时，浏览器可能取消尚未发送完的预览图片。
            except (WorkbenchError, SVGError, ValueError, OSError) as exc:
                self._send(getattr(exc, "status", 400), {"error":str(exc)})

        def do_POST(self):
            try:
                # 先读取受大小限制的请求体，再验证令牌；拒绝请求时不能带着未读数据
                # 关闭 socket，否则 Windows 可能发送 RST，客户端收不到应有的403。
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= _BODY_LIMIT:
                    raise WorkbenchError("请求过大或为空", 413)
                payload = self.rfile.read(length)
                self._check_host()
                if not secrets.compare_digest(self.headers.get("X-Agent-Token", ""), workbench.token):
                    raise WorkbenchError("页面连接已过期，请刷新", 403)
                if self.headers.get_content_type()!="application/json":
                    raise WorkbenchError("请发送 JSON 请求", 415)
                data = json.loads(payload)
                if not isinstance(data, dict):
                    raise WorkbenchError("请求必须是对象")
                route = urlsplit(self.path).path
                if route=="/api/import":
                    content = base64.b64decode(data.get("data", ""), validate=True)
                    result = workbench.import_svg(data.get("name", ""), content, data.get("session"), data.get("revision"))
                elif route=="/api/session":
                    result = workbench.create_session()
                elif route=="/api/asset":
                    restore = data.get("restore", False)
                    content = None if restore else base64.b64decode(data.get("data", ""), validate=True)
                    result = workbench.replace_asset(data.get("session"), data.get("revision"), data.get("kind"),
                        data.get("name"), content, data.get("part"), restore)
                elif route=="/api/example":
                    result = workbench.import_svg("house.svg", (PROJECT_ROOT/"examples/svg/house.svg").read_bytes(),
                        data.get("session"), data.get("revision"))
                elif route=="/api/plan":
                    result = workbench.plan(data.get("session"), data.get("revision"), data.get("selected_paths"), data.get("settings", {}))
                elif route=="/api/drawing/source":
                    result = workbench.drawing_source(data.get('session'),data.get('revision'),data.get('mode'))
                elif route=="/api/drawing/plan":
                    result = workbench.plan_language(data.get('session'),data.get('revision'),data.get('text'))
                elif route=="/api/chat":
                    result = workbench.chat(data.get("session"), data.get("revision"), data.get("message"))
                elif route=="/api/context/clear":
                    result = workbench.clear_context(data.get("session"), data.get("revision"))
                elif route=="/api/process/default":
                    result = workbench.default_process(data.get("session"), data.get("revision"))
                elif route=="/api/motion/position":
                    result = workbench.enable_position(data.get('session'),data.get('revision'))
                elif route=="/api/generate":
                    result = workbench.generate(data.get("session"), data.get("revision"))
                elif route=="/api/simulation/build":
                    result = workbench.build_simulation(data.get("session"), data.get("revision"), data.get("task"))
                elif route=="/api/simulation/import":
                    result = workbench.import_simulation(data.get("session"), data.get("revision"),data.get("files"))
                elif route=="/api/simulation/select":
                    result = workbench.select_simulation(data.get("session"),data.get("revision"),data.get("task"))
                elif route in ("/api/motion/start", "/api/motion/stop"):
                    result = workbench.control_motion(data.get("session"),data.get("revision"),
                        route.rsplit("/",1)[-1],data.get("task"),data.get("mode","auto"))
                elif route=="/api/studio/signal":
                    result = workbench.studio_signal(data.get("session"),data.get("revision"),data.get("task"),
                        data.get("signal"),data.get("value"))
                elif route in ("/api/runtime/deploy", "/api/runtime/start", "/api/runtime/stop"):
                    result = workbench.control_runtime(data.get("session"), data.get("revision"),
                        route.rsplit("/",1)[-1], data.get("task"))
                else:
                    raise WorkbenchError("地址不存在", 404)
                self._send(200, result)
            except (WorkbenchError, SVGError, ValueError, OSError, TypeError) as exc:
                self._send(getattr(exc, "status", 400), {"error":str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main(argv=None):
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="SVG 绘图与 PLC Agent 本地工作台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--aml", type=Path, default=DEFAULT_AML)
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--max-iters", type=int, default=6)
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535 or not 1 <= args.max_iters <= 20:
        parser.error("port 必须为 0..65535，max-iters 必须为 1..20")
    app = SVGWorkbench(args.runs_root, args.aml, args.max_iters)
    server = make_server(app, args.port)
    url = "http://127.0.0.1:"+str(server.server_port)
    print("SVG 工作台："+url, flush=True)
    print("产物目录："+str(app.root), flush=True)
    if args.open:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()
    return 0


if __name__=="__main__":
    raise SystemExit(main())
