# 部署手册（OpenPLC 版）

适用分支：`lx` ｜ 链路：PLCopen XML → .st → OpenPLC v3 运行时 → Modbus TCP。
**全程零 GUI、无许可证、无网关/符号配置**——CODESYS 版的历史手册见 git 历史。

---

## 0. 架构总览

```
agent 生成 XML ──> xml2st（校验+转换）──> program.st
                                             │ HTTP（:8080）
                                             ▼
                              OpenPLC v3 运行时（容器/WSL2/远程）
                              登录 → 上传 → matiec 编译 → start_plc
                                             │ Modbus TCP（:502）
                                             ▼
                       verify_modbus / Isaac Sim 桥接（pymodbus）
```

- 部署入口：`python src/pipeline/run_deploy.py` 或 `POST /deploy`（serve.py）
- 结果回喂：`workspace/deploy_result.json`（status / steps / errors）
- 变量通道：`%QX` → Modbus 线圈（可读写），`%QW` → 保持寄存器（可读写）；
  `%IX`/`%IW` 为只读方向（仿真侧注入信号请用线圈区）

## 1. 运行时安装（三选一）

### 方案 A：Docker Desktop（推荐，最省事）

1. 安装 Docker Desktop for Windows：https://www.docker.com/products/docker-desktop/
   - 安装中需要启用 WSL2；若提示虚拟化未开启，**进 BIOS 打开 VT-x/AMD-V** 后重试
   （本机此前 `wsl --status` 有相关提示，大概率需要这一步）
2. 启动运行时：

```bash
docker run -d --name openplc -p 8080:8080 -p 502:502 fdamador/openplc
```

3. 浏览器打开 http://localhost:8080（默认账号密码 `openplc` / `openplc`）确认能登录即就绪。

### 方案 B：WSL2 Ubuntu 内原生安装

```bash
# WSL2 内
sudo apt install git build-essential
git clone https://github.com/thiagoralves/OpenPLC_v3.git --depth=1
cd OpenPLC_v3 && ./install.sh
# 启动：./start_openplc.sh  （Web :8080 / Modbus :502 自动映射到 Windows 侧 localhost）
```

### 方案 C：远程 Linux / 云服务器

任意一台 Linux 上按方案 B 安装，然后把地址告诉编排器：

```bash
python src/pipeline/run_deploy.py --url http://<服务器IP>:8080
python src/pipeline/verify_modbus.py --host <服务器IP>
```

（环境变量 `OPENPLC_URL` 亦可。）

## 2. 运行部署

```bash
pip install -r requirements.txt
python src\pipeline\run_deploy.py
```

期望输出：

```
  [PASS] validate & convert XML -- counter.xml -> 22 行 ST
  [PASS] write program.st
  [PASS] login runtime -- http://127.0.0.1:8080
  [PASS] upload & trigger compile -- 384217.st
  [PASS] compile (matiec)
  [PASS] start PLC -- Modbus TCP :502 已随运行时开启

[deploy] RESULT: OK
```

换别的 XML：`python src\pipeline\run_deploy.py --xml path\to\other.xml`。
重复部署幂等：每次上传新副本并重新编译启动。

## 3. 验证（Modbus 读计数器）

```bash
python src\pipeline\verify_modbus.py
```

期望输出（`cnt` 绑定 `%QW0`，DINT 占寄存器 0~1，小端字序）：

```
[verify] sample 1: cnt = 3
[verify] sample 2: cnt = 4
...
[verify] 通过：cnt 在 4.0 秒内 +4，PLC 在运行 ✅
```

也可用任意 Modbus 工具（如 QModMaster / Modbus Poll）连 `127.0.0.1:502`
读保持寄存器 0~1。

## 4. HTTP API（agent 闭环对接点）

```bash
python src\pipeline\serve.py          # 监听 127.0.0.1:8600
curl -X POST http://127.0.0.1:8600/deploy --data-binary @src/plc/counter.xml
```

返回与 `deploy_result.json` 同构；`errors[]` 可直接作为 agent 的纠错 prompt 输入。
Isaac Sim 侧用 pymodbus 连 502 端口读写线圈/寄存器即可替换 verify_modbus 的角色。

## 5. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `无法连接 OpenPLC 运行时` | 容器/服务没起 | `docker ps` 查看；按 §1 启动；`curl http://127.0.0.1:8080` 自检 |
| Docker 安装失败/WSL 报虚拟化 | BIOS 未开 VT-x | 进 BIOS 启用虚拟化后重装；或改用方案 C 远程主机 |
| `登录失败` | 改过默认密码 | 环境变量 `OPENPLC_USER` / `OPENPLC_PASS`（或改 openplc_client.py 默认值） |
| `compile (matiec) FAIL` | ST 语法/类型错误 | 看 `deploy_result.json` 的 errors——matiec 日志已在其中；修 XML 后重跑 |
| 编译一直 COMPILING | 上一次编译卡住 | `docker restart openplc` 后重试 |
| verify 连不上 502 | 端口未映射 | 确认 `docker run` 带 `-p 502:502`；`telnet 127.0.0.1 502` |
| cnt 读出来乱码大数 | 字序解释不一致 | verify 默认小端字序（低字在前）；对调可用 `--help` 查看后改 `read_counter` |
| Web 页面能开但 502 拒绝 | Modbus 未随启动 | 运行时设置页确认 Modbus 已启用（默认启用） |

## 6. 关键踩坑结论（实测得出，agent 契约的一部分）

- **镜像名是 `fdamador/openplc`**（Docker Hub 上没有 openplc/openplc-v3）
- **AT 地址位宽必须匹配**：BOOL→`%QX`，INT/UINT/WORD→`%QW`；
  **`%QD`（双字）不映射 Modbus 缓冲区，编译能过但外部读不到——校验器直接禁用**，
  32 位值请用两个连续 `%QW` 由客户端拼接
- **matiec 要求定位变量独立 VAR 块**：FB 实例与 `AT` 变量混在一个 VAR 块会报
  invalid variable(s) declaration——转换器已自动分块
- **上传表单字段**：`prog_name`/`prog_descr`/`prog_file`/`epoch_time`（源码实测）
- **编译成败标记**：`Compilation finished successfully!` / `... with errors!`
- **换程序后必须 stop→start**：仅 start 不会加载新编译的二进制（run_deploy 已按
  编译自动停机、启动时拉起新二进制的顺序执行）

## 7. 设计约定（与 CODESYS 版的差异）

| | CODESYS 版（历史） | OpenPLC 版（当前） |
|---|---|---|
| 部署单元 | Editor 导入 XML | **xml2st 转出的 .st**（运行时 matiec 编译） |
| 变量发布 | 符号集配置（GUI 一次性） | **AT 地址即接口**，无需任何配置 |
| 变量通道 | OPC UA :4840 | Modbus TCP :502 |
| 环境 | IDE+网关+demo 许可 | 一个容器 |
| GUI 依赖 | 每机一次 | **零** |

agent 生成契约不变：ST 本体、POU 名 `PLC_PRG`；新增要求：**对外变量必须带 AT 地址**。
