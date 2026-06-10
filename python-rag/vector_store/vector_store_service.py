from typing import List, Dict, Any, Optional
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings


class VectorStoreService:
    """向量存储服务类，用于管理文档的嵌入和相似性搜索
    支持元数据过滤检索，可按type/product_id等条件定向检索
    """

    def __init__(
        self,
        collection_name: str,
        embedding_function: Embeddings,
        persist_directory: str,
    ):
        """
        初始化向量存储服务

        Args:
            collection_name: 集合名称
            embedding_function: 嵌入函数，用于将文本转换为向量
            persist_directory: 持久化目录路径
        """
        self.db = Chroma(
            collection_name=collection_name,
            embedding_function=embedding_function,
            persist_directory=persist_directory,
        )

    def add_documents(self, documents: List[Document]) -> None:
        """
        添加文档到向量存储

        Args:
            documents: 要添加的文档列表
        """
        self.db.add_documents(documents)

    def similarity_search(self, query: str, k: int = 3, filter: Optional[Dict[str, Any]] = None) -> List[Document]:
        """
        执行相似性搜索（支持元数据过滤）

        Args:
            query: 搜索查询字符串
            k: 返回的最相似文档数量，默认为3
            filter: 元数据过滤条件字典，例如 {"type": "faq_answer"} 或 {"product_id": "xxx"}
            
        Returns:
            最相似的文档列表
        """
        if filter:
            return self.db.similarity_search(query, k=k, filter=filter)
        return self.db.similarity_search(query, k=k)

    def list_documents(self) -> Dict[str, Any]:
        """
        获取所有文档

        Returns:
            包含documents列表的字典
        """
        data = self.db.get()
        result_docs = []
        ids = data.get('ids', [])
        docs = data.get('documents', [])
        metadatas = data.get('metadatas', [])
        
        for i in range(len(ids)):
            doc = Document(
                page_content=docs[i] if i < len(docs) else "",
                metadata=metadatas[i] if i < len(metadatas) else {}
            )
            result_docs.append(doc)
            
        return {"documents": result_docs}

    def add_embeddings(self, texts: List[str], embeddings: List[List[float]], metadatas: List[Dict[str, Any]], ids: List[str]) -> None:
        if hasattr(self.db, 'add_embeddings'):
            self.db.add_embeddings(texts=texts, embeddings=embeddings, metadatas=metadatas, ids=ids)
        else:
            self.db._collection.add(embeddings=embeddings, documents=texts, metadatas=metadatas, ids=ids)

    def search_by_vector(self, embedding: List[float], k: int = 3, filter: Optional[Dict[str, Any]] = None) -> List[Document]:
        if filter:
            results = self.db.similarity_search_by_vector_with_relevance_scores(embedding, k=k, filter=filter)
            return [doc for doc, _ in results]
        return self.db.similarity_search_by_vector(embedding, k=k)

    def delete_collection(self) -> None:
        self.db.delete_collection()

    def delete_document(self, ids: List[str]) -> None:
        """
        删除指定ID的文档

        Args:
            ids: 要删除的文档ID列表
        """
        self.db.delete(ids=ids)
