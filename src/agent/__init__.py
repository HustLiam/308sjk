"""gc 智能体与闭环侧：需求理解校验、PLC 生成智能体、一致性检查、闭环编排。

模块地图（gc 文档 §0 的代码落点）：
  spec_validator      ① requirement_spec 校验（契约①可执行权威）
  consistency_check   跨模块三方一致性（io_list 单一源头对账）
  patternlib          ② 的 ST 模式库（种子 = 已验收 6 场景）
  pipeline            ② PLC 生成器（生成-校验回灌循环）
  orchestrator        闭环编排 solve()（半环：生成→闸门→一致性→部署接口）
"""
