import base64
import os
import requests
from typing import Optional


class VisionService:

    def __init__(self, api_key: str, model_name: str = "qwen-vl-plus", max_image_size: int = 1024):
        self.api_key = api_key
        self.model_name = model_name
        self.max_image_size = max_image_size
        self.chat_url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

    def describe_product(self, image_path: str) -> str:
        image_b64 = self._encode_image(image_path)
        if image_b64 is None:
            return ""

        prompt = """请提取这张图片中的商品属性，只输出以下字段，每个一行，用最简单的中文词语：
品类、品牌、颜色、款式、材质、图案

格式示例：
品类:卫衣
品牌:nike
颜色:蓝色
款式:圆领长袖
材质:棉质
图案:字母印花

如果没有识别到某字段，跳过该行。不要输出任何多余文字。"""

        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": 0.1,
            "max_tokens": 256,
        }

        resp = requests.post(
            self.chat_url,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        resp_json = resp.json()
        description = resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
        return description.strip()

    def _encode_image(self, image_path: str) -> Optional[str]:
        if not os.path.exists(image_path):
            return None
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
