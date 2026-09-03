# lx 本地开发日志（个人分支，合入 master 前移除）

## 2026-09-03 两项工具增量：run_regression.py + serve GET /status

### run_regression.py（主方案 §8.4 CI 门禁实体）

- **分层**：L1 静态校验（直接 `import xml2st.convert()`，比 subprocess 快且拿到结构化 problems）→ L2 pytest（subprocess）→ L3 逐场景 run_deploy + scenario 脚本（subprocess，看退出码）。
- **场景发现**：`src/plc/*.xml` 与 `src/pipeline/scenario_<stem>.py` 按文件名配对；缺脚本记 SKIP 并提示——新场景入库时只要遵守命名约定即自动纳入回归。
- **运行时可达探测**：L3 前用 `requests.get(url, timeout=3)` 探测（不需要登录，dashboard 会 302 到 /login 但连接成功即算可达）；不可达整层 SKIP。默认 SKIP 不失败，`--require-online` 强制（完整合入门禁），`--skip-online` 显式本地两层。
- **退出码验证（正负测试）**：全绿→0；--skip-online→0；不可达默认→0；不可达+--require-online→1。⚠️ 排坑：bash 管道后 `$?` 是 tail 的退出码，验证退出码必须不经管道直接跑。
- **结果 JSON**：`workspace/regression_result.json`，结构 `{status, layers{static,pytest,online}, errors, duration_sec}`；失败的 subprocess 输出取尾部行存 detail（deploy 尾 3 行 / 验收尾 10 行），避免 JSON 膨胀。
- 基准：motion3axis 三层全绿 25~30s。

### serve.py GET /status

- **两腿采集**：Web 腿 `OpenPLCClient.status()`（_request 自带 5 分钟会话过期自动重登录，timeout=5 防 /status 挂死）；身份腿 `modbus_io.read_reg(%QW20)`（STOPPED 下 %Q 缓冲保持仍可读，lx 文档 §5.3 停止扫描语义）。
- **失败语义**：任一腿失败不 500——runtime.status=UNREACHABLE / prog_id=null + error 字段，编排器按字段路由。
- **prog_id→场景名映射**：serve.py 顶部 `PROG_NAMES = {1: "motion3axis"}`，新场景在此顺延登记（与 lx 文档 §5.3 一致）。
- **回归验证**：/status 返回 RUNNING+prog_id=1+motion3axis；/health 200；POST /deploy 合法 XML→200 OK（6 步全过）、非 XML body→400 REJECTED——主端点未破坏。
- serve.py 新增 sys.path.insert 以 import 同目录模块（此前只有 subprocess 调 run_deploy，零 import）。

### 文档同步

lx 文档 §4.3（/status 接口与语义）/§4.5（一键回归，新增小节）/§8（索引+快速开始）；看板两行 🚧→✅ + 当前状态 + →gc 待配合更新 + 变更记录；changelog 登记"工具 v1.0"。
