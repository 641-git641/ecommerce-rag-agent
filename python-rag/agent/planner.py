"""ReAct Planner：基于 LLM 的决策引擎

每一步调用 LLM，根据用户问题和历史记录决定：
- 调用哪个工具（及参数）
- 是否收集足够信息可以回答（finish）
"""

import json
from typing import Any, Dict, List, Optional

from .intent import classify_query, intent_label


MAX_REACT_STEPS = 3


def build_react_prompt(query: str, history: List[Dict[str, Any]], intent: str = "") -> str:
    """构建 ReAct 决策提示词

    Args:
        query: 用户原始问题
        history: ReAct历史步骤 [{"action":"...", "input":"...", "output":"..."}, ...]
        intent: 外部已分类的意图（来自 Agent.process），避免重复调用 classify_query

    Returns:
        完整的 LLM prompt
    """
    if not intent:
        intent = classify_query(query)
    lines = [f"[系统提示] 查询意图预分类: {intent_label(intent)}\n"]
    lines.append("""你是一个电商导购智能助手，可以调用以下工具来帮助用户选购商品：

## 可用工具

| 工具 | 用途 | 参数示例 |
|------|------|----------|
| search | 搜索商品信息 | {"query":"搜索词"} |
| recommend | 条件推荐最佳商品 | {"criteria":"标准","budget":"预算","scenario":"场景"} |
| compare | 对比多个商品 | {"products":["商品A","商品B"]} |
| clarify | 信息不足时反问 | {"query":"用户原问题"} |
| combo | 跨品类组合推荐 | {"scenario":"场景描述"} |
| cart | 购物车管理 | 详见下方 |
| finish | 输出最终回答 | {"answer":"完整回答文本"} |

### cart 工具详细说明

支持以下 action：

| action | 用途 | tool_args 示例 |
|--------|------|---------------|
| add | 加购（从对话中提取商品信息）| {"action":"add","product":"商品名"} |
| remove | 删除商品（支持"第二个"等序号）| {"action":"remove","product":"第二个"} |
| update_qty | 改数量 | {"action":"update_qty","product":"第二个","quantity":2} |
| view | 查看购物车 | {"action":"view"} |
| clear | 清空购物车 | {"action":"clear"} |
| order_preview | 预览订单（计算选中商品总价）| {"action":"order_preview"} |
| order_confirm | 确认下单（需要地址信息）| {"action":"order_confirm","address":"...","contact_name":"...","contact_phone":"..."} |

### 购物车/下单决策规则

当用户表达以下意图时，优先使用 cart 工具：

- "加购物车""加入购物车""买这个"→ cart add（product 填上一轮推荐的商品名）
- "看看购物车""我的购物车"→ cart view
- "删除第二个""移除第一个"→ cart remove（product 填"第二个"等序号）
- "把数量改成N"→ cart update_qty
- "去下单""结算""结账"→ 先 cart order_preview 展示订单摘要
- 订单预览后用户说"确认下单 地址XXX"→ cart order_confirm
- 用户说"清空购物车"→ cart clear

重要：当用户说「推荐的不错，第二个加购物车」，你应该：
1. 先 cart add product="第二个" ← CartTool 会通过序号找到上一轮推荐的商品并加购
2. 然后 cart view ← 展示购物车状态
3. finish 告知用户加购成功

如果用户说「把推荐的商品都加购」，你需要逐个 cart add。

## 决策规则（重要！）""")

    if intent == "simple":
        lines.append("""
【简单推荐 - 快速路径】
1. 直接 search → finish，最多 2 轮
2. search 结果充足时第 2 轮必须 finish
3. 不要 clarify 反问""")
    elif intent == "compare":
        lines.append("""
【对比决策 - 对比路径】
1. 对商品分别 search → compare → finish，最多 3 轮
2. 结论要明确：给出推荐结果和理由""")
    elif intent == "exclude":
        lines.append("""
【反选排除 - 过滤路径】
1. search 时在 query 中附加排除条件
2. 过滤后 → finish，最多 2 轮""")
    elif intent == "combo":
        lines.append("""
【场景组合 - 组合路径】
1. 直接 combo(scenario) → finish，1-2 轮
2. 不要逐个品类搜索""")
    else:
        lines.append("""
1. 需求模糊 → clarify
2. 需求明确 → search
3. 需对比 → compare
4. 购物车 → cart
5. 信息充足 → finish
6. 单轮结果充足就 finish，不要绕""")

    lines.append("""
## 全局规则
- 信息充足就 finish，最多 3 轮
- finish 回答要自然，包含商品名+价格+理由
- 不要反复 clarify""")

    if history:
        lines.append("\n## 已执行步骤")
        for i, h in enumerate(history):
            inp = str(h.get("input", ""))[:150]
            out = str(h.get("output", ""))[:300]
            lines.append(f"{i + 1}. [{h.get('action', '?')}] 参数: {inp}")
            lines.append(f"   结果: {out}")
        lines.append("\n请根据以上历史决定下一步。")

    lines.append(f"\n## 用户输入\n{query}")
    lines.append("""
## 输出要求
只输出一个 JSON（不要 markdown 包裹）:
{"action":"工具名","tool_args":{...},"reason":"你的推理"}
或结束:
{"action":"finish","answer":"给用户的完整回答","reason":"你的推理"}""")

    return "\n".join(lines)


def fallback_decision(query: str, step_count: int) -> Dict[str, Any]:
    """LLM 不可用时的兜底决策"""
    if step_count == 0:
        return {
            "action": "search",
            "tool_args": {"query": query},
            "reason": "LLM 不可用，默认搜索",
        }
    return {
        "action": "finish",
        "answer": "抱歉，当前服务暂时不可用，请稍后重试。",
        "reason": "LLM 不可用且已达最大尝试次数",
    }


def extract_json(text: str) -> str:
    """从 LLM 回复中提取 JSON 对象"""
    text = text.strip()
    # 去 markdown 包裹
    if text.startswith("```"):
        lines = text.split("\n")
        if len(lines) > 2 and lines[-1].startswith("```"):
            text = "\n".join(lines[1:-1])
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def decide(llm_service, query: str, history: List[Dict[str, Any]], intent: str = "") -> Dict[str, Any]:
    """调用 LLM 做一步 ReAct 决策

    Args:
        llm_service: LLMService 实例
        query: 用户原始问题
        history: ReAct 历史步骤
        intent: 外部已分类的意图，避免重复 classify_query

    Returns:
        决策字典 {"action":"...","tool_args":{...},"reason":"..."} 或 {"action":"finish","answer":"...","reason":"..."}
    """
    if llm_service is None:
        return fallback_decision(query, len(history))

    prompt = build_react_prompt(query, history, intent=intent)
    try:
        raw = llm_service.chat(prompt, temperature=0.1)
        json_str = extract_json(raw)
        decision = json.loads(json_str)
        if "action" not in decision:
            return fallback_decision(query, len(history))
        return decision
    except Exception as e:
        print(f"[ReAct Planner] LLM 决策失败: {e}")
        return fallback_decision(query, len(history))
