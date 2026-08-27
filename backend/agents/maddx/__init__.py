"""
MADDx: Multi-Agent Differential Diagnosis
=========================================
4 角色协作的鉴别诊断辩论子图：
  Proposer  → 住院医师，初诊给出 Top-3 候选
  Critic    → 主治医师，基于证据挑毛病
  Defender  → Proposer 的同身份二次调用，回应 objections 并修正
  Moderator → 科主任，判断收敛并产出最终报告

每个会话独占一个 Blackboard 实例，所有 Agent 通过 append-only 日志通信。
"""
