# legacy_opcua（已废弃，勿在新链路使用）

主路线已切换 **Modbus TCP**（`../isaac_modbus_server.py` + `../gantry_jog_gui.py` +
`../gantry_bridge.py`），与主方案 §3.4.2 / csk 文档 §4 的 OpenPLC 主链路同语义。
本目录保留 OPC UA 时期实现，仅为将来评估 OpenPLC Runtime v4 + OPC UA 备选链路
（csk 文档 §4.5）时参考。

实测问题（切换原因，详见 csk devlog 2026-09-02）：
- asyncua sync 包装（服务端/客户端各自线程 + 事件循环）在 Isaac 进程内与 GUI/物理
  线程争用 GIL，长会话后出现 CloseSession 超时（problem.txt：session timeout
  3600000→600000、close_session TimeoutError、Unhandled exception）；
- 断连时客户端与服务端互相等待，Isaac 侧偶发整进程卡死；
- Modbus 无会话语义、pymodbus 同步客户端超时可控，从结构上消除此类故障。
