from openai import OpenAI
from typing import Generator, Optional
import time

# ── LLM 调用配置 ──
LLM_MAX_RETRIES = 2       # 最多重试次数
LLM_RETRY_BACKOFF = 2.0   # 重试退避倍数（2s → 4s）


class LLMService:
    """大语言模型服务类，用于与OpenAI兼容的API进行交互
    
    支持同步调用和流式输出两种模式，适配不同的应用场景需求。
    内置 Token 用量统计，通过 last_tokens 属性获取最近一次调用的用量。
    """
    
    def __init__(self, base_url: str, api_key: str, model_name: str):
        """
        初始化LLM服务
        
        Args:
            base_url: API的基础URL地址，支持通义千问、DeepSeek、豆包等兼容OpenAI格式的服务
            api_key: API密钥
            model_name: 模型名称，如 'qwen-plus', 'deepseek-chat', 'doubao-pro'
        """
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=180
        )
        self.model_name = model_name
        self.last_tokens: dict = {}  # {"prompt": N, "completion": N, "total": N}
        self.total_tokens: dict = {"prompt": 0, "completion": 0, "total": 0, "calls": 0}

    def _record_tokens(self, response):
        """从 API 响应中记录 token 用量"""
        try:
            usage = response.usage
            if usage:
                self.last_tokens = {
                    "prompt": usage.prompt_tokens or 0,
                    "completion": usage.completion_tokens or 0,
                    "total": usage.total_tokens or 0,
                }
                self.total_tokens["prompt"] += self.last_tokens["prompt"]
                self.total_tokens["completion"] += self.last_tokens["completion"]
                self.total_tokens["total"] += self.last_tokens["total"]
                self.total_tokens["calls"] += 1
        except Exception:
            pass

    def chat(self, user_prompt: str, temperature: float = 0.1, max_tokens: int = 2048) -> str:
        """
        与模型进行同步对话，等待完整响应后返回（含自动重试）

        Args:
            user_prompt: 用户输入的提示词，包含上下文和问题
            temperature: 采样温度，控制输出的随机性，默认0.1（较低的值更专注）
            max_tokens: 最大生成token数，默认2048

        Returns:
            模型生成的完整回复内容
        """
        last_error = ""
        _t0 = time.time()
        for attempt in range(LLM_MAX_RETRIES + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": user_prompt}],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                _elapsed = time.time() - _t0
                _answer_len = len(response.choices[0].message.content or "")
                print(f"[LLM] chat 完成: {_answer_len}字 | {_elapsed:.2f}s | model={self.model_name} | max_tokens={max_tokens}", flush=True)
                self._record_tokens(response)
                return response.choices[0].message.content
            except Exception as e:
                last_error = str(e)
                if attempt < LLM_MAX_RETRIES:
                    wait = LLM_RETRY_BACKOFF * (2 ** attempt)
                    print(f"[LLM] 第{attempt + 1}次调用失败，{wait:.0f}s 后重试: {last_error[:80]}", flush=True)
                    time.sleep(wait)
                else:
                    import traceback
                    print(f"[LLM Error] chat() failed after {LLM_MAX_RETRIES + 1} attempts: {traceback.format_exc()}", flush=True)
        return f"LLM调用失败: {last_error}"

    def chat_stream(self, user_prompt: str, temperature: float = 0.1, max_tokens: int = 2048) -> Generator[str, None, None]:
        """
        与模型进行流式对话，逐chunk返回生成内容
        
        Args:
            user_prompt: 用户输入的提示词
            temperature: 采样温度，默认0.1
            max_tokens: 最大生成token数，默认2048
        
        Yields:
            逐块生成的文本内容
        """
        try:
            _t_stream_start = time.time()
            stream = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            _first = True
            _total_chars = 0
            _chunk_count = 0
            _usage_info = ""
            for chunk in stream:
                _chunk_count += 1
                if _first:
                    _first = False
                    print(f"[LLM] 首 token 延迟: {time.time() - _t_stream_start:.2f}s | model={self.model_name}", flush=True)
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    _total_chars += len(chunk.choices[0].delta.content)
                    yield chunk.choices[0].delta.content
                # DashScope 流式最后一个 chunk 带 usage
                if hasattr(chunk, 'usage') and chunk.usage:
                    _usage_info = f" | input={chunk.usage.prompt_tokens} output={chunk.usage.completion_tokens} total={chunk.usage.total_tokens} tokens"
            _elapsed = time.time() - _t_stream_start
            _speed = f" | ~{(_total_chars / _elapsed):.0f}字/s" if _total_chars > 0 and _elapsed > 0 else ""
            print(f"[LLM] 流式完成: {_total_chars}字符 | {_chunk_count}chunks{_speed} | 总耗时 {_elapsed:.2f}s | model={self.model_name}{_usage_info}", flush=True)
        except Exception as e:
            import traceback
            print(f"[LLM Error] chat_stream() failed: {traceback.format_exc()}", flush=True)
            yield f"LLM调用失败: {str(e)}"
            return

    def chat_with_history(self, messages: list, temperature: float = 0.1, max_tokens: int = 2048) -> str:
        """
        带历史对话的完整对话接口（含自动重试）

        Args:
            messages: 历史消息列表，格式为 [{"role": "user/assistant", "content": "..."}, ...]
            temperature: 采样温度，默认0.1
            max_tokens: 最大生成token数，默认2048

        Returns:
            模型生成的完整回复内容
        """
        last_error = ""
        _t0 = time.time()
        for attempt in range(LLM_MAX_RETRIES + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                _elapsed = time.time() - _t0
                _answer_len = len(response.choices[0].message.content or "")
                print(f"[LLM] chat_with_history 完成: {_answer_len}字 | {_elapsed:.2f}s | model={self.model_name} | max_tokens={max_tokens}", flush=True)
                self._record_tokens(response)
                return response.choices[0].message.content
            except Exception as e:
                last_error = str(e)
                if attempt < LLM_MAX_RETRIES:
                    wait = LLM_RETRY_BACKOFF * (2 ** attempt)
                    print(f"[LLM] chat_with_history 第{attempt + 1}次调用失败，{wait:.0f}s 后重试: {last_error[:80]}", flush=True)
                    time.sleep(wait)
                else:
                    import traceback
                    print(f"[LLM Error] chat_with_history() failed after {LLM_MAX_RETRIES + 1} attempts: {traceback.format_exc()}", flush=True)
        return f"LLM调用失败: {last_error}"
