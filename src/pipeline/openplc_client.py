#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenPLC v3 运行时 HTTP 客户端。

封装部署所需的全部交互（对 webserver.py 的表单路由，字段名依据官方源码）：
  login                POST /login                {username, password}
  upload_and_compile   POST /upload-program       multipart, 字段名 file
                       POST /upload-program-action{file, name, description, epoch_time}
                       GET  /compile-program?file=<st>
  compile_status       GET  /compilation-logs     SUCCESS / FAILED / COMPILING
  start / stop         GET  /start_plc /stop_plc
  status               GET  /dashboard            Running / Stopped / Compiling
  runtime_logs         GET  /runtime-logs

会话有效期约 5 分钟，超时自动重登录一次。
"""

import re
import time

import requests


class OpenPLCError(Exception):
    pass


INPUT_RE = re.compile(r"<input[^>]*name=[\"']([^\"']+)[\"'][^>]*value=[\"']([^\"']*)[\"']",
                      re.IGNORECASE)


class OpenPLCClient:
    def __init__(self, base_url="http://127.0.0.1:8080",
                 username="openplc", password="openplc", timeout=30):
        self.base = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()

    # ------------------------------------------------------------------ 内部
    def _request(self, method, path, **kwargs):
        """发请求；发现被踢回登录页则重登录一次。"""
        url = self.base + path
        kwargs.setdefault("timeout", self.timeout)
        r = self.session.request(method, url, allow_redirects=True, **kwargs)
        if "/login" in r.url and path != "/login":
            self._do_login()
            r = self.session.request(method, url, allow_redirects=True, **kwargs)
        return r

    def _do_login(self):
        r = self.session.post(self.base + "/login",
                              data={"username": self.username,
                                    "password": self.password},
                              timeout=self.timeout, allow_redirects=False)
        # 成功: 302 -> /dashboard；失败: 200 渲染 bad_login 页
        if r.status_code != 302:
            raise OpenPLCError("登录失败（用户名/密码错误，默认 openplc/openplc）")

    # ------------------------------------------------------------------ 公开
    def login(self):
        self._do_login()
        return True

    def upload_and_compile(self, st_text, name):
        """上传 .st 并触发编译，返回 st_file 名。

        表单字段名以容器内 webserver.py 源码为准（upload-program-action 读取
        prog_name / prog_descr / prog_file / epoch_time），并从上传响应的
        隐藏域动态核对，避免字段漂移。
        """
        files = {"file": (name + ".st", st_text.encode("utf-8"))}
        r = self._request("POST", "/upload-program", files=files)
        if r.status_code != 200:
            raise OpenPLCError("上传失败 HTTP %d" % r.status_code)

        # 上传后返回元数据表单：解析其中的隐藏字段（含随机 .st 文件名）
        fields = dict(INPUT_RE.findall(r.text))
        st_file = None
        for val in fields.values():
            if val.endswith(".st"):
                st_file = val
                break
        if st_file is None:
            m = re.search(r"(\d{1,7})\.st", r.text)
            if not m:
                raise OpenPLCError("无法从上传响应中解析 st 文件名")
            st_file = m.group(0)

        data = {"prog_name": name,
                "prog_descr": "deployed by agent pipeline",
                "prog_file": st_file,
                "epoch_time": str(int(time.time()))}
        # 服务端表单里出现的同名字段优先（如隐藏的 epoch_time/prog_file）
        for k in ("prog_name", "prog_descr", "prog_file", "epoch_time"):
            if k in fields and k in ("epoch_time", "prog_file"):
                data[k] = fields[k] if fields[k] else data[k]
        resp = self._request("POST", "/upload-program-action", data=data)
        if resp.status_code != 200 or "Error connecting" in resp.text:
            raise OpenPLCError("程序登记失败: HTTP %d / %s"
                               % (resp.status_code, resp.text[:200]))

        self._request("GET", "/compile-program", params={"file": st_file})
        return st_file

    # 编译页自身使用的判定串（webserver.py draw_compiling_page 的 JS）
    OK_MARKER = "Compilation finished successfully!"
    FAIL_MARKER = "Compilation finished with errors!"

    def compile_status(self):
        """返回 'SUCCESS' / 'FAILED' / 'COMPILING' / 'NOT_STARTED'。"""
        r = self._request("GET", "/compilation-logs")
        if r.status_code == 500:
            return "NOT_STARTED"   # compilation_object 未定义 = 无编译被触发过
        text = r.text
        if self.OK_MARKER in text:
            return "SUCCESS"
        if self.FAIL_MARKER in text:
            return "FAILED"
        return "COMPILING"

    def wait_compilation(self, timeout=300, poll=3.0):
        """轮询直到编译结束，返回 (ok, 末次日志文本)。"""
        deadline = time.time() + timeout
        last_text = ""
        consecutive_500 = 0
        while time.time() < deadline:
            r = self._request("GET", "/compilation-logs")
            if r.status_code == 500:
                consecutive_500 += 1
                if consecutive_500 >= 5:
                    raise OpenPLCError("编译似乎从未启动（/compilation-logs 持续 500）")
                time.sleep(poll)
                continue
            consecutive_500 = 0
            last_text = r.text
            if self.OK_MARKER in last_text:
                return True, last_text
            if self.FAIL_MARKER in last_text:
                return False, last_text
            time.sleep(poll)
        raise OpenPLCError("编译超时（>%d 秒），最后日志: %s"
                           % (timeout, last_text[-300:]))

    def start(self):
        self._request("GET", "/start_plc")
        return self.status()

    def stop(self):
        self._request("GET", "/stop_plc")
        return self.status()

    def status(self):
        r = self._request("GET", "/dashboard")
        if "Compiling" in r.text:
            return "COMPILING"
        if "Running" in r.text:
            return "RUNNING"
        if "Stopped" in r.text:
            return "STOPPED"
        return "UNKNOWN"

    def runtime_logs(self):
        return self._request("GET", "/runtime-logs").text
