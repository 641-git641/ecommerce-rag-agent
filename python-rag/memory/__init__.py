"""二层记忆系统：实体记忆桥接 + 短期记忆槽位

架构：
  EntityMemory   — 记住上一轮推荐的商品ID，通过知识图谱找同款/搭配
  SessionSlots   — 从对话中增量提取结构化需求槽位（品类/属性/预算/品牌/排除）

使用方式：
  from memory import get_memory, init_memory

  init_memory(graph=...)

  memory = get_memory()

  # 查询前：用记忆增强
  entity_ctx = memory.get_entity_context(sid)          # → "同款替代: A、B  搭配推荐: C"
  enhanced_q = memory.build_enriched_query(sid, q)      # → "原问题 轻量 跑鞋 预算800以内"

  # 查询后：更新记忆
  memory.remember(sid, q, full_answer, recommendations)
"""

import os
import json
import re
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


# ── Window Config ─────────────────────────────────────────────────
SLOT_ATTRS_MAX = 5          # 属性槽位最多保留 5 个（FIFO 淘汰旧属性）
ENTITY_ROUNDS_MAX = 3       # 实体记忆最多保留 3 轮商品
KNOWLEDGE_TOKEN_LIMIT = 2800  # knowledge_context 硬截断字符数（≈中文 1400 字）


@dataclass
class SessionSlots:
    """短期结构化槽位：从多轮对话中增量累积的用户需求（滑动窗口）"""
    category: str = ""
    attributes: List[str] = field(default_factory=list)
    budget_max: float = 0.0
    budget_min: float = 0.0
    brand: str = ""
    exclude: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.category or self.attributes or self.brand or
                     self.budget_max > 0 or self.exclude)

    def to_query_fragment(self) -> str:
        """把槽位拼接成搜索查询片段，属性只取最近 SLOT_ATTRS_MAX 个"""
        parts = []
        if self.category:
            parts.append(self.category)
        active_attrs = self.attributes[-SLOT_ATTRS_MAX:] if self.attributes else []
        if active_attrs:
            parts.extend(active_attrs)
        if self.brand:
            parts.append(self.brand)
        if self.budget_max > 0:
            parts.append(f"预算{int(self.budget_max)}以内")
        return " ".join(parts)


# ── Slot Fragment Helper ────────────────────────────────────────

def _rebuild_fragment_without_brand(state) -> str:
    """重建查询片段但排除品牌（用于多样性/排除追问场景）"""
    parts = []
    if state.category:
        parts.append(state.category)
    active_attrs = state.attributes[-SLOT_ATTRS_MAX:] if state.attributes else []
    if active_attrs:
        parts.extend(active_attrs)
    if state.budget_max > 0:
        parts.append(f"预算{int(state.budget_max)}以内")
    return " ".join(parts)


# ── ConversationMemory ───────────────────────────────────────────

class ConversationMemory:
    """二层记忆编排器"""

    CATEGORIES = [
        "跑鞋", "运动鞋", "徒步鞋", "登山鞋", "篮球鞋", "帆布鞋", "板鞋", "洗面奶", "面霜", "防晒霜", "T恤", "T恤衫",
        "耳机", "蓝牙耳机", "手机", "笔记本电脑", "平板电脑",
        "精华", "水乳", "粉底", "口红", "背包", "手表", "音箱",
        "连衣裙", "牛仔裤", "衬衫", "卫衣", "外套", "短裤", "瑜伽裤",
        "运动鞋", "拖鞋", "帽子", "墨镜",
        "面膜", "眼霜", "卸妆", "洁面", "爽肤水", "乳液",
        "护肤品", "化妆品", "美妆", "护肤", "彩妆",
        "充电器", "充电宝", "数据线", "键盘", "鼠标",
        "方便面", "牛肉面", "泡面", "零食", "饮料", "牛奶", "饼干", "坚果", "面包",
        "咖啡", "茶", "功能饮料", "酸奶", "酱油", "矿泉水", "可乐", "火腿肠",
    ]

    ATTRS = [
        "轻量", "轻薄", "透气", "防水", "防风", "保暖",
        "保湿", "控油", "美白", "抗皱", "修复", "舒缓",
        "降噪", "高刷", "长续航", "快充", "折叠", "便携",
        "油皮", "干皮", "混合皮", "敏感肌",
        "防晒", "遮瑕", "持久", "哑光", "滋润",
        "大容量", "超轻", "防滑", "耐磨", "速干",
        "无线", "蓝牙", "type-c", "USB", "磁吸",
        "原味", "香辣", "麻辣", "红烧", "酸辣", "藤椒", "桶装", "袋装",
    ]

    BRANDS = [
        "Nike", "nike", "耐克", "Adidas", "adidas", "阿迪达斯",
        "华为", "小米", "三星", "苹果", "OPPO", "vivo",
        "兰蔻", "雅诗兰黛", "科颜氏", "SK-II", "资生堂", "理肤泉",
        "优衣库", "李宁", "安踏", "迪卡侬", "波司登", "海澜之家",
        "索尼", "Bose", "JBL", "漫步者", "韶音", "Beats",
        "戴尔", "联想", "华硕", "惠普", "ThinkPad",
        "花西子", "完美日记", "MAC", "YSL", "Dior",
        "欧莱雅", "奥莱雅", "olay", "赫莲娜", "倩碧",
        "HOKA", "萨洛蒙", "SALOMON", "迈乐", "Merrell",
        "露露乐蒙", "Lululemon", "始祖鸟", "Arc'teryx", "北面", "The North Face",
        "Osprey", "特步", "鸿星尔克", "361", "匹克", "回力",
        "康师傅", "统一", "白象", "今麦郎", "旺旺", "良品", "日清",
        "雀巢", "三顿半", "东方树叶", "元气森林", "东鹏", "红牛",
        "金典", "纯甄", "海天", "农夫山泉", "可口可乐", "蒙牛", "伊利", "李锦记",
    ]

    EXCLUDES = ["日系", "含酒精", "韩国", "杂牌", "三无", "微商"]

    def __init__(
        self,
        graph=None,
    ):
        self._states: Dict[str, SessionSlots] = {}
        self._product_memory: Dict[str, List[List[str]]] = {}
        self._summaries: Dict[str, List[str]] = {}
        self._turn_counters: Dict[str, int] = {}
        self.graph = graph

    # ── Knowledge Context Truncator ───────────────────────────

    @staticmethod
    def truncate_knowledge(text: str, limit: int = KNOWLEDGE_TOKEN_LIMIT) -> str:
        if len(text) <= limit:
            return text
        cut = text.rfind("\n\n", 0, limit)
        if cut == -1 or cut < limit // 2:
            cut = text.rfind("。", 0, limit)
        if cut == -1 or cut < limit // 2:
            cut = limit
        return text[:cut] + "\n\n[上下文窗口已满，更早的信息已截断]"

    # ── Summary Compressor ────────────────────────────────────

    @staticmethod
    def summarize_turn(question: str, answer: str) -> str:
        q_short = question[:60].replace("\n", "")
        a_tokens = answer[:300].replace("\n", " ")
        if len(a_tokens) > 200:
            a_tokens = a_tokens[:200] + "…"
        return f"[用户: {q_short}] [AI: {a_tokens}]"

    def build_summary_context(self, sid: str, max_summaries: int = 3) -> str:
        summaries = self._summaries.get(sid, [])
        if not summaries:
            return ""
        return "【历史对话摘要】\n" + "\n".join(summaries[-max_summaries:])

    # ── Entity Memory Bridge ──────────────────────────────────

    def _get_entity_bridge_cached(self, pid: str) -> dict:
        """获取商品同款/搭配信息，直接从知识图谱查询"""
        if pid.startswith("product:"):
            pid = pid[len("product:"):]

        result: Dict[str, list] = {"same_style": [], "match": []}
        if self.graph:
            node_key = f"product:{pid}"
            if node_key in self.graph.nodes:
                same_nodes = self.graph.get_same_style_products(node_key)[:2]
                result["same_style"] = [
                    n.get("properties", {}).get("title", "?") for n in same_nodes
                ]
                match_nodes = self.graph.get_match_products(node_key)[:2]
                result["match"] = [
                    n.get("properties", {}).get("title", "?") for n in match_nodes
                ]

        return result

    def get_entity_context(self, sid: str) -> str:
        """用知识图谱找最近 ENTITY_ROUNDS_MAX 轮推荐商品的同款/搭配"""
        rounds = self._product_memory.get(sid, [])
        if not rounds:
            return ""

        recent_rounds = rounds[-ENTITY_ROUNDS_MAX:]
        seen_pids = set()
        lines = []
        for round_pids in recent_rounds:
            for pid in round_pids:
                if pid in seen_pids or not pid:
                    continue
                seen_pids.add(pid)

                node_key = f"product:{pid}" if not pid.startswith("product:") else pid
                prod_name = pid
                if self.graph and node_key in self.graph.nodes:
                    prod_name = self.graph.nodes[node_key].get("properties", {}).get("title", pid)

                bridge = self._get_entity_bridge_cached(pid)

                if bridge["same_style"]:
                    lines.append(f"「{prod_name}」的同款替代：{'、'.join(bridge['same_style'])}")
                if bridge["match"]:
                    lines.append(f"「{prod_name}」的搭配推荐：{'、'.join(bridge['match'])}")

        result = "\n".join(lines) if lines else ""
        if len(result) > 600:
            result = "\n".join(lines[:4])
        return result

    # ── Structured Slots ──────────────────────────────────────

    CATEGORY_ALIASES = {
        "防晒": "防晒霜", "洗面": "洗面奶", "面霜": "面霜",
        "T恤": "T恤", "耳机": "耳机", "手机": "手机",
        "笔记本": "笔记本电脑", "平板": "平板电脑",
        "精华": "精华", "粉底": "粉底", "口红": "口红",
        "背包": "背包", "手表": "手表", "音箱": "音箱",
        "运动鞋": "运动鞋", "篮球鞋": "篮球鞋",
    }

    def build_enriched_query(self, sid: str, question: str) -> str:
        """单会话内槽位累积增强（含话题切换检测 + 多样性追问处理）"""
        state = self._states.get(sid)

        # ── 话题切换检测 ──
        if state and state.category:
            switched = False
            for cat in self.CATEGORIES:
                if cat in question and cat != state.category:
                    switched = True
                    break
            if not switched:
                for alias, cat in self.CATEGORY_ALIASES.items():
                    if alias in question and cat != state.category:
                        switched = True
                        break
            # 仅当新问题明确包含一个不同的品类词时才切换。
            # 不含品类词时，用启发式规则判断是细化追问还是新话题：
            #   含属性/预算词 → 同一话题的细化追问，保留现有品类
            #   不含属性/预算词 → 可能是全新话题，清除品类
            if not switched:
                has_attr = any(a in question for a in self.ATTRS)
                has_budget = bool(re.search(r'(\d+)\s*(?:以内|以下|左右|元)', question))
                if has_attr or has_budget:
                    pass  # 细化追问 → 保留品类
                else:
                    switched = True
            if switched:
                old_cat = state.category
                self._states.pop(sid, None)
                self._product_memory.pop(sid, None)
                self._summaries.pop(sid, None)
                state = None
                print(f"[Memory] 话题切换: {old_cat} → ? | sid={sid}")

        # ── 多样性追问 ──
        is_diversity_query = any(kw in question for kw in (
            "另外", "其他", "别的", "不一样", "不同",
            "换一个", "换一款", "换种", "换别的",
            "还有吗", "还有什么", "还有别的", "还有没有",
            "再来", "再推", "再推荐",
            "除了这个", "除了这些",
        ))

        # ── 槽位提取 ──
        self._extract_slots(sid, question)
        state = self._states.get(sid)
        if not state or state.is_empty():
            return question

        fragment = state.to_query_fragment()
        if is_diversity_query and state.brand:
            fragment = _rebuild_fragment_without_brand(state)
        if fragment and fragment not in question:
            return f"{question} {fragment}"
        return question

    def _extract_slots(self, sid: str, text: str):
        if sid not in self._states:
            self._states[sid] = SessionSlots()
        state = self._states[sid]
        t = text.lower()

        # ── 负向排除模式检测 ──
        EXCLUDE_PATTERNS = [
            r'(?:不要|别要|别买|别推|别给|排除|避开|去掉|不含|不能有|不希望|拒绝|过滤|屏蔽)\s*(.+?)(?:[，。,、和与及\s]|$)',
        ]
        exclude_matches = []
        for pat in EXCLUDE_PATTERNS:
            for m in re.finditer(pat, text):
                matched = m.group(1).strip()
                if matched and len(matched) >= 2:
                    exclude_matches.append(matched)

        for cat in self.CATEGORIES:
            if cat in t:
                state.category = cat
                break

        for a in self.ATTRS:
            if a in t and a not in state.attributes:
                state.attributes.append(a)

        budget_pats = [
            r"预算\s*(\d+)\s*(?:以内|以下|左右)?",
            r"(\d+)\s*(?:以内|以下)",
            r"(\d+)\s*元\s*(?:以内|以下|左右)",
            r"不超过\s*(\d+)\s*元?",
        ]
        for p in budget_pats:
            m = re.search(p, t)
            if m:
                budget = float(m.group(1))
                if state.budget_max == 0 or budget < state.budget_max:
                    state.budget_max = budget
                break

        # ── 品牌提取：检查是否为排除品牌 ──
        for b in self.BRANDS:
            if b.lower() in t:
                is_excluded = any(b.lower() in exc.lower() for exc in exclude_matches)
                if is_excluded:
                    if b not in state.exclude:
                        state.exclude.append(b)
                        print(f"[Memory] 品牌 '{b}' 被加入排除列表 (文本: {text[:40]})", flush=True)
                else:
                    state.brand = b
                break

        for e in self.EXCLUDES:
            if e in t and e not in state.exclude:
                state.exclude.append(e)

    # ── Orchestration ──────────────────────────────────────────

    def remember(
        self,
        sid: str,
        question: str,
        full_answer: str,
        recommendations: List[Dict],
    ):
        """一次问答结束后，更新所有记忆层（含滑动窗口裁剪）"""
        # 1. 记住推荐的商品ID（按轮次存储 → 滑动窗口）
        pids = [r.get("product_id", "") for r in (recommendations or []) if r.get("product_id")]
        if pids:
            if sid not in self._product_memory:
                self._product_memory[sid] = []
            self._product_memory[sid].append(pids[:5])
            if len(self._product_memory[sid]) > ENTITY_ROUNDS_MAX:
                self._product_memory[sid] = self._product_memory[sid][-ENTITY_ROUNDS_MAX:]

        # 2. 从问题中提取槽位（不解析回答内容，避免价格被误认为预算）
        self._extract_slots(sid, question)
        state = self._states.get(sid)
        if state and len(state.attributes) > SLOT_ATTRS_MAX:
            state.attributes = state.attributes[-SLOT_ATTRS_MAX:]

        # 3. 轮次计数 + 摘要压缩
        self._turn_counters[sid] = self._turn_counters.get(sid, 0) + 1
        if self._turn_counters[sid] > 6:
            summary = self.summarize_turn(question, full_answer)
            if sid not in self._summaries:
                self._summaries[sid] = []
            self._summaries[sid].append(summary)
            if len(self._summaries[sid]) > 5:
                self._summaries[sid] = self._summaries[sid][-5:]

    def get_memory_context(self, sid: str, question: str) -> dict:
        """
        一站式获取查询前需要的记忆增强信息（含上下文截断）。

        Returns:
            {
                "entity_context": str,
                "enriched_query": str,
                "summary_context": str,
            }
        """
        entity = self.get_entity_context(sid)
        summary = self.build_summary_context(sid)

        return {
            "entity_context": entity,
            "enriched_query": self.build_enriched_query(sid, question),
            "summary_context": summary,
        }

    # ── 供 Agent 使用的公开访问器 ──

    def get_slot_state(self, sid: str) -> Optional[SessionSlots]:
        """获取会话的结构化槽位状态（不修改内部数据）"""
        return self._states.get(sid)

    def get_last_summary(self, sid: str) -> Optional[str]:
        """获取最近一条对话摘要（供 clarify 判断等使用）"""
        summaries = self._summaries.get(sid, [])
        return summaries[-1] if summaries else None

    def pop_last_summary(self, sid: str):
        """移除最近一条摘要（用于 clarify 标记清理）"""
        if sid in self._summaries and self._summaries[sid]:
            self._summaries[sid].pop()

    def get_prev_round_pids(self, sid: str) -> List[str]:
        """获取上一轮推荐的产品 ID 列表"""
        rounds = self._product_memory.get(sid, [])
        return rounds[-1] if rounds else []

    def clear_product_memory(self, sid: str):
        """清除会话的产品记忆"""
        self._product_memory[sid] = []

    def extract_slots(self, sid: str, text: str):
        """公开的槽位提取（供 Agent 直接调用）"""
        self._extract_slots(sid, text)

    def append_summary(self, sid: str, summary: str):
        """追加一条摘要（供 Agent clarify 等路径使用）"""
        if sid not in self._summaries:
            self._summaries[sid] = []
        self._summaries[sid].append(summary)


# ── Singleton ────────────────────────────────────────────────────

_memory_instance: Optional[ConversationMemory] = None


def init_memory(
    graph=None,
):
    """初始化记忆系统（由 main.py 调用）

    Args:
        graph: 知识图谱实例
    """
    global _memory_instance
    _memory_instance = ConversationMemory(
        graph=graph,
    )
    print(f"[Memory] 二层记忆系统已初始化")


def get_memory() -> ConversationMemory:
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = ConversationMemory()
    return _memory_instance
