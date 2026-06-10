import os
import requests
from typing import Optional


class TtsService:

    SYSTEM_VOICES = {
        "longanyang": "温柔女声",
        "longxiaochun_v2": "活泼女声",
        "longxiaokun": "沉稳男声",
        "longwan": "知性女声",
        "longyue": "亲切女声",
        "longhua_v2": "活力男声",
    }

    def __init__(self, api_key: str, model_name: str = "cosyvoice-v3-flash", voice: str = "longanyang",
                 audio_dir: str = "./uploads/audio"):
        self.api_key = api_key
        self.model_name = model_name
        self.voice = voice
        self.audio_dir = audio_dir
        self.tts_url = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
        os.makedirs(audio_dir, exist_ok=True)

    def synthesize(self, text: str, output_filename: Optional[str] = None, voice: Optional[str] = None) -> Optional[str]:
        if not text.strip():
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model_name,
            "input": {
                "text": text,
                "voice": voice or self.voice,
                "format": "mp3",
            },
        }

        try:
            resp = requests.post(self.tts_url, headers=headers, json=payload, timeout=60)
            if resp.status_code == 200:
                content_type = resp.headers.get("Content-Type", "")
                if content_type.startswith("audio"):
                    import uuid
                    filename = output_filename or f"{uuid.uuid4().hex}.mp3"
                    filepath = os.path.join(self.audio_dir, filename)
                    with open(filepath, "wb") as f:
                        f.write(resp.content)
                    return filepath
                resp_json = resp.json()
                audio_data = resp_json.get("output", {}).get("audio", {})
                audio_url = audio_data.get("url", "")
                if audio_url:
                    import uuid
                    filename = output_filename or f"{uuid.uuid4().hex}.mp3"
                    filepath = os.path.join(self.audio_dir, filename)
                    audio_resp = requests.get(audio_url, timeout=30)
                    if audio_resp.status_code == 200:
                        with open(filepath, "wb") as f:
                            f.write(audio_resp.content)
                        return filepath
                print(f"[TTS] 合成失败，响应中无音频: {resp.text[:200]}")
                return None
            else:
                print(f"[TTS] 合成失败 status={resp.status_code}: {resp.text[:200]}")
                return None
        except Exception as e:
            print(f"[TTS] 合成异常: {e}")
            return None
