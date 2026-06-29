from .routes import router, init_handlers
from . import state


def __getattr__(name):
    """延迟解析 handlers_instance，兼容 `from api import handlers_instance`

    使用模块级 __getattr__（Python 3.7+），每次访问动态读取 state.app.handlers_instance，
    避免 from-import 的引用拷贝问题。
    """
    if name == "handlers_instance":
        return state.app.handlers_instance
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["router", "init_handlers", "handlers_instance"]
