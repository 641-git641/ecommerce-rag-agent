from typing import List, Optional
import requests
from langchain_core.embeddings import Embeddings


class TongyiEmbedding(Embeddings):
    def __init__(self, api_key: str, model_name: str = "text-embedding-v4", dimensions: Optional[int] = None):
        """
        初始化TongyiEmbedding实例。
        
        Args:
            api_key: DashScope API密钥，用于认证请求
            model_name: 模型名称，默认text-embedding-v4（Qwen3-Embedding）
            dimensions: 向量输出维度，可选值：2048/1536/1024/768/512/256/128/64，默认1024
        """
        self.api_key = api_key
        self.model_name = model_name
        self.dimensions = dimensions
        # DashScope文本嵌入服务的API端点
        self.url = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"

    def embed_documents(self, texts: List[str]) -> List[List[float]]:

        result = []
        # 分批处理（text-embedding-v4 单次最多10条）
        batch_size = 10
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i + batch_size]
            payload = {
                "model": self.model_name,
                "input": {"texts": batch_texts},
            }
            if self.dimensions is not None:
                payload["parameters"] = {"dimensions": self.dimensions}
            resp = requests.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=120,
            )
            resp_json = resp.json()
            if "output" not in resp_json or "embeddings" not in resp_json["output"]:
                raise Exception(f"Embedding API调用失败: {resp_json}")
            # 从响应中提取所有嵌入向量
            for item in resp_json["output"]["embeddings"]:
                result.append(item["embedding"])
        return result

    def embed_query(self, text: str) -> List[float]:
        # 复用embed_documents方法，将单个文本包装为列表处理
        return self.embed_documents([text])[0]