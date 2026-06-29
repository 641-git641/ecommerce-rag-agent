"""共享状态模块：全局 handler 实例和会话存储

从 api/routes.py 抽取，供 api/ 下各路由模块无循环依赖地共享状态。

使用 app 容器对象而非裸变量，避免 `from .state import x` 的引用拷贝问题：
  - app.handlers_instance 每次访问都解析为当前值 ✓
  - from .state import handlers_instance 拷贝的是 import 时的值 ✗
"""

from typing import Dict


class _AppState:
    """应用级全局状态容器。属性可动态更新，所有导入者立即可见。"""
    __slots__ = ('handlers_instance',)

    def __init__(self):
        self.handlers_instance = None


# 模块级单例 — 导入此对象后通过 .handlers_instance 访问
app = _AppState()

# ── 会话历史内存存储 ──
# key=session_id, value=List[dict]（user/assistant 消息列表）
# dict 被 mutate 而非 reassign，from-import 安全
_chat_sessions: Dict[str, list] = {}
