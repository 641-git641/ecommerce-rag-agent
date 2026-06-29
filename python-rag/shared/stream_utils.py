"""共享工具模块：流式解析、推荐富化、会话消息回写

从 api/routes.py 中抽取，供 api/routes.py 和 agent/ 包共同使用。
"""

import os
import json
import re
from typing import Dict, Optional, List
from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=2)
_GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8080")


def _save_session_message(session_id: str, role: str, content: str, cards: str = "", voice_url: str = ""):
    """异步回写一条消息到 Go 网关的 MySQL 会话存储"""
    def _post():
        try:
            import requests as _req
            payload = {"role": role, "content": content}
            if cards:
                payload["cards"] = cards
            if voice_url:
                payload["voice_url"] = voice_url
            _req.post(
                f"{_GATEWAY_URL}/api/session/{session_id}/message",
                json=payload,
                timeout=5,
            )
        except Exception as e:
            print(f"[Session] 回写消息失败: {e}", flush=True)
    _executor.submit(_post)


def _parse_stream_structured(raw_text: str) -> dict:
    text = raw_text.strip()
    if not text:
        return {"answer_text": raw_text, "recommendations": [], "voice_friendly": ""}

    # ── 剥离 markdown 代码块包裹 ──
    import re as _re
    m = _re.match(r'```(?:json)?\s*\n(.*?)\n```', text, _re.DOTALL)
    if m:
        text = m.group(1).strip()

    json_start = text.find('{')
    if json_start == -1:
        return {"answer_text": raw_text, "recommendations": [], "voice_friendly": ""}
    depth = 0
    json_end = -1
    for i in range(json_start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                json_end = i + 1
                break
    if json_end == -1:
        return {"answer_text": raw_text, "recommendations": [], "voice_friendly": ""}
    raw_json = text[json_start:json_end]
    try:
        parsed = json.loads(raw_json)
    except (json.JSONDecodeError, ValueError):
        # 容错：去除尾部逗号
        try:
            cleaned = _re.sub(r',\s*}', '}', raw_json)
            cleaned = _re.sub(r',\s*]', ']', cleaned)
            parsed = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            return {"answer_text": raw_text, "recommendations": [], "voice_friendly": ""}

    try:
        answer_text = parsed.get("answer_text", "")
        voice_friendly = parsed.get("voice_friendly", "")
        recommendations = parsed.get("recommendations", [])
        if not isinstance(recommendations, list):
            recommendations = []
        print(f"[RAG Parse] answer={len(answer_text)}字 voice={len(voice_friendly)}字 recs={len(recommendations)}条", flush=True)
        cleaned_recs = []
        for rec in recommendations[:5]:
            if isinstance(rec, dict):
                cleaned_recs.append({
                    "product_id": str(rec.get("product_id", "")),
                    "name": str(rec.get("name", "")),
                    "price": rec.get("price", 0),
                    "reason": str(rec.get("reason", "")),
                })
        if not answer_text and not cleaned_recs:
            answer_text = voice_friendly or "抱歉，当前知识库中没有找到与您问题匹配的商品信息，请尝试更换关键词或扩大搜索范围。"
        elif not answer_text:
            answer_text = "根据您的需求，以下是为您找到的相关商品："
        return {"answer_text": answer_text, "recommendations": cleaned_recs, "voice_friendly": voice_friendly}
    except json.JSONDecodeError:
        pass
    return {"answer_text": raw_text, "recommendations": [], "voice_friendly": ""}


def _enrich_recommendations(recommendations: list, graph) -> list:
    if graph is None:
        return recommendations
    # 建立映射：名称→pid、名称→价格、pid→属性
    name_to_pid: Dict[str, str] = {}
    name_to_price: Dict[str, float] = {}
    pid_to_props: Dict[str, dict] = {}
    for nid, node in graph.nodes.items():
        props = node.get("properties", {})
        title = str(props.get("title", "")).strip()
        pid_val = str(props.get("product_id", "")).strip()
        graph_price = float(props.get("price", 0) or 0)
        if title:
            if pid_val:
                name_to_pid[title] = pid_val
            if graph_price > 0:
                name_to_price[title] = graph_price
        if pid_val:
            pid_to_props[pid_val] = props

    def _match_name(target: str, candidates: dict) -> Optional[str]:
        """多级模糊匹配：精确→长前缀→短前缀→包含"""
        target = target.strip()
        if not target:
            return None
        if target in candidates:
            return candidates[target]
        # 取 token 匹配（忽略空格、品牌后缀等）
        tokens = re.split(r"[\s\-/]+", target)
        for gname, val in candidates.items():
            gtokens = re.split(r"[\s\-/]+", gname)
            if len(tokens) >= 2 and len(gtokens) >= 2:
                # 如果前2个 token 一致就算匹配
                if tokens[0] == gtokens[0] and tokens[1] == gtokens[1]:
                    return val
        # 前缀匹配（6→4 chars）
        for n in (6, 5, 4):
            for gname, val in candidates.items():
                if len(target) >= n and len(gname) >= n:
                    if target[:n] in gname:
                        return val
        return None

    enriched = []
    for rec in recommendations:
        item = dict(rec)
        pid = str(item.get("product_id", "")).strip()
        name = str(item.get("name", "")).strip()

        # 1. 用名称匹配 product_id（pid 为空或不合法时尝试）
        pid_valid = pid and re.match(r'^p_[a-z]+_\d{3}$', pid) and pid in pid_to_props
        if not pid_valid and name:
            matched_pid = _match_name(name, name_to_pid)
            if matched_pid:
                pid = matched_pid
                item["product_id"] = pid
                pid_valid = True

        # 2. 从图谱补属性（名称/价格）
        #    图谱商品名优先于传入名称（ComboTool 传入的是品类名如"防晒霜"）
        if pid_valid:
            props = pid_to_props[pid]
            graph_name = str(props.get("title", "")).strip()
            if graph_name:
                item["name"] = graph_name
            gprice = float(props.get("price", 0) or 0)
            if gprice > 0 and float(item.get("price", 0) or 0) == 0:
                item["price"] = gprice

        # 3. 用名称匹配价格
        if float(item.get("price", 0) or 0) == 0 and name:
            matched = _match_name(name, name_to_price)
            if matched:
                item["price"] = matched

        # 4. 生成图片 URL（仅当 product_id 格式合法: p_xxx_nnn）
        if not item.get("image_url"):
            pid = str(item.get("product_id", "")).strip()
            if pid and re.match(r'^p_[a-z]+_\d{3}$', pid):
                item["image_url"] = f"/product-images/{pid}_live.jpg"
            elif pid:
                print(f"[WARN] _enrich_recommendations: 拒绝非法 product_id='{pid}' → 不生成 image_url", flush=True)

        # 5. 价格缺省
        if item.get("price") is None:
            item["price"] = 0

        enriched.append(item)

    # 去重
    seen_names = set()
    unique = []
    for item in enriched:
        nm = str(item.get("name", "")).strip()
        if nm and nm not in seen_names:
            seen_names.add(nm)
            unique.append(item)
        elif not nm:
            unique.append(item)
    return unique[:5]
