"""JSON 解析工具函数 — 从 RAG/LLM 输出中提取结构化数据

与 api/routes.py 中的 _parse_stream_structured 逻辑一致，
消除重复代码。
"""

import json
import re
from typing import Any, Dict, List, Optional


def _strip_markdown_fence(text: str) -> str:
    """剥离 LLM 输出的 markdown 代码块包裹（```json ... ``` → ...）"""
    text = text.strip()
    m = re.match(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def _parse_structured_json(text: str) -> Optional[Dict[str, Any]]:
    """从文本中提取最外层 JSON 对象（含 markdown fence 剥离 + 容错）"""
    if not text:
        return None
    text = _strip_markdown_fence(text)
    json_start = text.find("{")
    if json_start == -1:
        return None
    depth = 0
    json_end = -1
    for i in range(json_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                json_end = i + 1
                break
    if json_end == -1:
        return None
    raw = text[json_start:json_end]
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError, TypeError):
        # 容错：去除尾部逗号（LLM 常见问题: {"a": 1,}）
        try:
            cleaned = re.sub(r',\s*}', '}', raw)
            cleaned = re.sub(r',\s*]', ']', cleaned)
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError, TypeError):
            return None


def extract_answer_text(answer: str) -> str:
    """从 structured JSON 回答中提取 answer_text，失败则返回原文"""
    parsed = _parse_structured_json(answer)
    if parsed is None:
        return answer
    return parsed.get("answer_text", "") or answer


def parse_cards_from_answer(answer: str) -> List[Dict[str, Any]]:
    """从 RAG 回答中提取推荐商品卡片"""
    parsed = _parse_structured_json(answer)
    if parsed is None:
        return []
    recs = parsed.get("recommendations", [])
    if not isinstance(recs, list):
        return []
    cards = []
    for r in recs[:5]:
        if isinstance(r, dict):
            cards.append({
                "product_id": str(r.get("product_id", "")),
                "name": str(r.get("name", "")),
                "price": float(r.get("price", 0) or 0),
                "reason": str(r.get("reason", "")),
            })
    return cards
