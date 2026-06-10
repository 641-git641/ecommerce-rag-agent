"""Agentic RAG 包：基于 ReAct 模式的电商导购智能体

核心流程：
1. 意图分类（规则 + LLM）→ 选择执行路径
2. ReAct 循环：LLM决定 → 工具执行 → 观察结果 → 下一轮决策
3. 简单查询走快速路径（search → finish），复杂意图走完整ReAct

工具集：search, recommend, compare, clarify, combo, cart
"""

from .agent import Agent

__all__ = ["Agent"]
