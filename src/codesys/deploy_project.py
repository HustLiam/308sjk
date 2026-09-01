# -*- coding: utf-8 -*-
"""
deploy_project.py -- executes INSIDE the CODESYS Script Engine (IronPython 2.7).

Launched headless by the orchestrator (src/pipeline/run_deploy.py):

    CODESYS.exe --noUI --runscript=<this file>

Pipeline:
  1. open or create the .project file
  2. ensure device "CODESYS Control Win V3" exists (soft PLC)
  3. drop the template PLC_PRG and import the POU from the PLCopen XML
     (IEC 61131-10) -- the task configuration keeps calling PLC_PRG by name
  4. ensure a Symbol Configuration object so variables are published via OPC UA
  5. save + compile (generate_sourcecode)
  6. download (login) and start the application on the soft PLC

Inputs (environment variables, set by the orchestrator):
  PLCOPEN_XML          absolute path of the PLCopen XML file to deploy
  CODESYS_PROJECT      absolute path of the .project file (created if missing)
  DEPLOY_RESULT        absolute path where a JSON status report is written
  CODESYS_DEVICE_NAME  optional override of the device type name

The script is intentionally defensive: CODESYS Script API details vary
between service packs, so every optional call is wrapped and the outcome of
each step is written to the JSON report for the orchestrator to inspect.
"""

import os
import io
import json
import traceback

DEVICE_NAME = os.environ.get("CODESYS_DEVICE_NAME", "CODESYS Control Win V3")
XML_PATH = os.environ.get("PLCOPEN_XML", "")
PROJECT_PATH = os.environ.get("CODESYS_PROJECT", "")
RESULT_PATH = os.environ.get("DEPLOY_RESULT", "")

POU_NAME = "PLC_PRG"  # must match the POU name inside the PLCopen XML

steps = []
errors = []


class _Abort(Exception):
    """Raised by finish() to unwind the script at the first failure."""


def log(msg):
    print("[deploy] " + msg)


def step(name, ok, detail=""):
    steps.append({"step": name, "ok": bool(ok), "detail": str(detail)})
    log(("PASS  " if ok else "FAIL  ") + name + ((" -- " + str(detail)) if detail else ""))


def write_result(status):
    if RESULT_PATH:
        try:
            # IronPython 的 open() 默认 ASCII 编码，遇到 CODESYS 返回的中文
            # 错误消息会 UnicodeEncodeError，必须显式指定编码
            with io.open(RESULT_PATH, "w", encoding="utf-8") as f:
                json.dump({"status": status, "steps": steps, "errors": errors},
                          f, indent=2)
        except Exception:
            pass


def finish(status):
    write_result(status)
    log("RESULT: " + status)
    raise _Abort(status)


def collect_compile_errors():
    """Best-effort readout of error messages from the scripting message pool."""
    msgs = []
    try:
        for m in system.messages:
            text = str(m)
            if "error" in text.lower():
                msgs.append(text)
    except Exception:
        pass
    return msgs


def main():
    # -----------------------------------------------------------------------
    # 1. sanity checks + open/create project
    # -----------------------------------------------------------------------
    if not XML_PATH or not PROJECT_PATH or not RESULT_PATH:
        errors.append("missing env vars: PLCOPEN_XML / CODESYS_PROJECT / DEPLOY_RESULT")
        finish("FAILED")
    if not os.path.exists(XML_PATH):
        errors.append("PLCopen XML not found: " + XML_PATH)
        finish("FAILED")

    try:
        if os.path.exists(PROJECT_PATH):
            project = projects.open(PROJECT_PATH)
            step("open project", True, PROJECT_PATH)
        else:
            project = projects.create(PROJECT_PATH)
            step("create project", True, PROJECT_PATH)
    except Exception:
        errors.append(traceback.format_exc())
        step("open/create project", False, PROJECT_PATH)
        finish("FAILED")

    # -----------------------------------------------------------------------
    # 2. ensure device (soft PLC) exists
    # -----------------------------------------------------------------------
    try:
        found = project.find(DEVICE_NAME, True)
        if len(found) > 0:
            step("device present", True, DEVICE_NAME)
        else:
            dev_id = None
            versions = []
            for d in device_repository.get_all_devices(DEVICE_NAME):
                dev_id = d.device_id
                try:
                    versions.append(str(d.get_version()))
                except Exception:
                    versions.append("?")
            if dev_id is None:
                errors.append('device type not installed: "%s" (adjust CODESYS_DEVICE_NAME)'
                              % DEVICE_NAME)
                step("add device", False, DEVICE_NAME)
                finish("FAILED")
            project.add(DEVICE_NAME, dev_id)
            step("add device", True,
                 DEVICE_NAME + " versions=[" + ",".join(versions) + "]")
    except Exception:
        errors.append(traceback.format_exc())
        step("ensure device", False, DEVICE_NAME)
        finish("FAILED")

    # 网关/地址保存在工程文件里。SP22 无头模式下 online.gateways 为空集合、
    # set_gateway_and_address 需要网关 GUID，均无法自动获取——首次需在 IDE 中
    # 打开本工程设置一次通信（见部署手册 §2.3），此后配置随工程持久保存。
    try:
        gw = None
        for g in online.gateways:
            gw = g
            break
        if gw is not None:
            dev = project.find(DEVICE_NAME, True)[0]
            try:
                dev.set_gateway_and_address(gw, DEVICE_NAME)
                step("configure gateway", True)
            except Exception:
                step("configure gateway", False, "set_gateway_and_address failed (non-fatal)")
        else:
            step("gateway via project settings", True,
                 "headless gateway list empty; using gateway persisted in project "
                 "(one-time IDE seed, see manual 2.3)")
    except Exception:
        step("configure gateway", False, "unexpected error (non-fatal)")

    # -----------------------------------------------------------------------
    # 3. locate application, replace PLC_PRG with the imported PLCopen XML POU
    # -----------------------------------------------------------------------
    apps = project.find("Application", True)
    if len(apps) == 0:
        errors.append("no Application object under the device (device template missing?)")
        step("locate application", False)
        finish("FAILED")
    app = apps[0]
    step("locate application", True, str(app.get_name()))

    try:
        for old in app.find(POU_NAME, True):
            old.remove()
        step("drop template " + POU_NAME, True)

        imported = False
        last_err = None
        # import_xml signature differs between service packs -- try common forms
        for args in [(XML_PATH,), (XML_PATH, True), (XML_PATH, True, True)]:
            try:
                app.import_xml(*args)
                imported = True
                step("import PLCopen XML", True, "import_xml args=%d" % len(args))
                break
            except Exception as e:
                last_err = e
        if not imported:
            raise last_err if last_err else Exception("import_xml failed")

        if len(app.find(POU_NAME, True)) == 0:
            raise Exception("POU %s not found after import (check pou name in XML)"
                            % POU_NAME)
        step("POU present after import", True, POU_NAME)
    except Exception:
        errors.append(traceback.format_exc())
        step("import PLCopen XML", False, XML_PATH)
        finish("FAILED")

    # -----------------------------------------------------------------------
    # 4. symbol configuration -> publishes variables via OPC UA
    # -----------------------------------------------------------------------
    try:
        if len(project.find("Symbol Configuration", True)) > 0:
            step("symbol configuration present", True)
        else:
            # 泛型 ScriptObject 不能做符号配置的父对象，需取 active_application
            sym_parent = app
            try:
                if hasattr(project, "active_application"):
                    cand = project.active_application
                    if callable(cand):
                        cand = cand()
                    if cand is not None:
                        sym_parent = cand
            except Exception:
                pass
            created = False
            err = None
            try:
                from System import Guid
                sym_parent.create_symbol_config(False, True, Guid.Empty)
                created = True
            except Exception as e1:
                err = e1
                try:
                    sym_parent.create_symbol_config(False, False, Guid.Empty)
                    created = True
                except Exception as e2:
                    err = e2
            if created:
                step("create symbol configuration", True)
            else:
                # non-fatal: the PLC still runs, but variables will not show
                # up in UaExpert until the symbol config is added manually
                errors.append("create_symbol_config failed: %s" % err)
                step("create symbol configuration", False,
                     "MANUAL FIX: Application -> Add Object -> Symbol Configuration, "
                     "open it, press 'Prepare...', tick all variables (cnt), rebuild")
    except Exception:
        errors.append(traceback.format_exc())
        step("symbol configuration", False, "unexpected error")
        finish("FAILED")

    # -----------------------------------------------------------------------
    # 5. save + compile
    # -----------------------------------------------------------------------
    try:
        project.save()
        step("save project", True)
    except Exception:
        errors.append(traceback.format_exc())
        step("save project", False)
        finish("FAILED")

    try:
        # API 随版本变化：SP22+ 为 build()，旧版为 generate_sourcecode()
        if hasattr(app, "build"):
            app.build()
            step("compile (build)", True)
        else:
            app.generate_sourcecode()
            step("compile (generate_sourcecode)", True)
        compile_errors = collect_compile_errors()
        if compile_errors:
            errors.extend(compile_errors)
            step("compile error scan", False, "; ".join(compile_errors[:5]))
            finish("FAILED")
    except Exception:
        errors.append(traceback.format_exc())
        step("compile", False)
        finish("FAILED")

    # -----------------------------------------------------------------------
    # 6. download + start the application
    # -----------------------------------------------------------------------
    try:
        online_app = online.create_online_application(app)
        step("create online application", True)
    except Exception:
        errors.append(traceback.format_exc())
        step("create online application", False,
             "is CODESYS Control Win V3 running? (start it from the Windows start menu)")
        finish("FAILED")

    try:
        try:
            online_app.login(OnlineChangeOption.Try, True)  # silent download
        except NameError:
            online_app.login()
        step("login / download", True)

        online_app.start()
        step("start PLC", True)
    except Exception:
        errors.append(traceback.format_exc())
        step("login / start", False,
             "若提示网关未配置：在 IDE 中打开工程 -> 双击设备 -> 通信设置 -> 选择本机网关并"
             "设置设备名称 -> 保存（一次性，见部署手册 2.3）；另检查 runtime 是否运行/demo 是否到期")
        finish("FAILED")

    project.save()
    finish("OK")


try:
    main()
except _Abort:
    pass
except Exception:
    errors.append(traceback.format_exc())
    write_result("FAILED")
