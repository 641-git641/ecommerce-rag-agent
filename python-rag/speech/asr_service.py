"""语音识别服务 — DashScope SDK Recognition

使用 dashscope SDK 的 Recognition.call() 识别本地音频文件。
"""
import os
from http import HTTPStatus
from typing import Optional

import dashscope
from dashscope.audio.asr import Recognition


class AsrService:
    """DashScope 实时语音识别（SDK）"""

    def __init__(self, api_key: str, model_name: str = "fun-asr-realtime"):
        self.api_key = api_key
        self.model_name = model_name

    def transcribe(self, audio_path: str, timeout: int = 30) -> Optional[str]:
        if not os.path.exists(audio_path):
            print(f"[ASR] 文件不存在: {audio_path}")
            return None

        with open(audio_path, "rb") as f:
            raw = f.read(44)
        is_wav = raw[:4] == b"RIFF"
        sample_rate = int.from_bytes(raw[24:28], "little") if is_wav and len(raw) >= 44 else 16000
        ext = os.path.splitext(audio_path)[1].lower().lstrip(".")
        fmt = "wav" if is_wav else (ext if ext in ("pcm", "mp3", "opus", "speex", "aac", "amr") else "pcm")

        print(f"[ASR] 文件: {os.path.getsize(audio_path)}B, fmt={fmt}, sr={sample_rate}")

        try:
            dashscope.api_key = self.api_key

            recognition = Recognition(
                model=self.model_name,
                format=fmt,
                sample_rate=sample_rate,
                callback=None,
            )
            result = recognition.call(audio_path)

            if result.status_code == HTTPStatus.OK:
                sentence = result.get_sentence()
                if isinstance(sentence, list) and len(sentence) > 0:
                    # 取所有 sentence_end=True 的 text 拼接
                    texts = [s.get("text", "") for s in sentence if s.get("sentence_end")]
                    text = "".join(texts).strip()
                elif isinstance(sentence, dict):
                    text = sentence.get("text", "").strip()
                else:
                    text = str(sentence).strip() if sentence else ""
                print(f"[ASR] 结果: {text[:80]}")
                return text if text else ""
            else:
                print(f"[ASR] 失败: status={result.status_code}, msg={result.message}")
                return None
        except Exception as e:
            print(f"[ASR] 异常: {e}")
            return None
