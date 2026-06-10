import base64
import requests
from typing import List, Optional
from langchain_core.embeddings import Embeddings


class VisionEmbeddingService(Embeddings):

    def __init__(self, api_key: str, model_name: str = "tongyi-embedding-vision-flash", dimensions: int = 768):
        self.api_key = api_key
        self.model_name = model_name
        self.dimensions = dimensions
        self.url = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self.embed_text(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self.embed_text(text)

    def embed_text(self, text: str) -> Optional[List[float]]:
        payload = {
            "model": self.model_name,
            "input": {
                "contents": [
                    {"text": text}
                ]
            },
            "parameters": {
                "dimension": self.dimensions
            },
        }
        resp = requests.post(
            self.url,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        resp_json = resp.json()
        if "output" not in resp_json or "embeddings" not in resp_json["output"]:
            raise Exception(f"多模态Embedding API调用失败: {resp_json}")
        embeddings = resp_json["output"]["embeddings"]
        if not embeddings:
            return []
        return embeddings[0].get("embedding", [])

    def embed_image(self, image_path: str) -> Optional[List[float]]:
        image_b64 = self._encode_image(image_path)
        if image_b64 is None:
            return None

        payload = {
            "model": self.model_name,
            "input": {
                "contents": [
                    {"image": f"data:image/jpeg;base64,{image_b64}"}
                ]
            },
            "parameters": {
                "dimension": self.dimensions
            },
        }

        resp = requests.post(
            self.url,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        resp_json = resp.json()

        if "output" not in resp_json or "embeddings" not in resp_json["output"]:
            raise Exception(f"多模态Embedding API调用失败: {resp_json}")

        embeddings = resp_json["output"]["embeddings"]
        if not embeddings:
            return None

        return embeddings[0].get("embedding")

    def _encode_image(self, image_path: str) -> Optional[str]:
        import os
        if not os.path.exists(image_path):
            return None
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
