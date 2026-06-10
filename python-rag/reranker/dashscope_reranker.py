import requests
from typing import List, Tuple, Optional


class DashScopeReranker:

    def __init__(self, api_key: str, model_name: str = "qwen3-rerank", instruct: Optional[str] = None):
        self.api_key = api_key
        self.model_name = model_name
        self.instruct = instruct
        self.url = "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"

    def rerank(self, query: str, documents: List[str], top_n: int = 3, return_documents: bool = False) -> List[dict]:
        if not documents:
            return []

        num_docs = len(documents)
        top_n_clamped = min(top_n, num_docs)

        payload = {
            "model": self.model_name,
            "query": query,
            "documents": documents,
            "top_n": top_n_clamped,
            "return_documents": return_documents,
        }
        if self.instruct:
            payload["instruct"] = self.instruct

        resp = requests.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        resp_json = resp.json()

        if "results" not in resp_json:
            raise Exception(f"DashScope重排序API调用失败: {resp_json}")

        return resp_json["results"]

    def rerank_documents(self, question: str, docs, top_k: int) -> Tuple[list, List[float]]:
        if len(docs) <= top_k:
            return docs, [1.0] * len(docs)

        doc_texts = [doc.page_content.strip()[:1000] for doc in docs]

        results = self.rerank(question, doc_texts, top_n=top_k, return_documents=False)

        ranked_docs = []
        ranked_scores = []
        for item in results:
            idx = item["index"]
            score = round(item["relevance_score"], 6)
            if 0 <= idx < len(docs):
                ranked_docs.append(docs[idx])
                ranked_scores.append(score)

        return ranked_docs, ranked_scores
