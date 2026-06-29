"""文档处理模块：负责解析和切分各种格式的商品文档

支持的格式：PDF、TXT、JSON商品结构化数据
核心功能：文件解析 → 文本提取 → 语义切分 → 返回Document对象列表
FAQ单路优化：每条FAQ为一个chunk，问题文本做嵌入（保证召回），完整FAQ存metadata供LLM
"""

import os
import json
import tempfile
from typing import List, Dict, Any
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import TextLoader, PyPDFLoader


class DocumentProcessor:
    """文档处理器：解析文档并进行语义切分

    使用langchain的文档加载器生态，支持PDF、TXT格式的解析。
    自动按语义切分文档，生成固定大小的Chunk供向量入库使用。
    特别扩展支持结构化JSON商品数据集，自动转换为知识片段。
    FAQ双路优化：每条FAQ拆成2个独立Chunk，问题Chunk专门优化用户问句召回，答案Chunk提供完整知识。
    """

    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 100):
        """
        初始化文档处理器

        Args:
            chunk_size: 每个知识片段的最大字符数，默认800
            chunk_overlap: 相邻片段之间的重叠字符数，默认100，保持上下文连续性
        """
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
        )

        # 支持的文件格式与对应的加载器映射
        self.supported_formats = {
            ".pdf": PyPDFLoader,
            ".txt": TextLoader,
            ".json": None,  # JSON格式用自定义解析
        }

    def _parse_product_json(self, data: Dict[str, Any], filename: str) -> List[Document]:
        """
        解析结构化的商品JSON数据，转换为多个知识片段

        Args:
            data: JSON字典数据
            filename: 原始文件名，用于元数据来源标识

        Returns:
            生成的Document对象列表
        """
        docs = []

        product_id = data.get("product_id", "")
        title = data.get("title", "")
        brand = data.get("brand", "")
        category = data.get("category", "")
        sub_category = data.get("sub_category", "")
        base_price = data.get("base_price", 0)

        # 片段1：商品基础信息
        basic_info = f"""【商品基础信息】
商品ID: {product_id}
商品名称: {title}
品牌: {brand}
分类: {category} / {sub_category}
基础价格: ¥{base_price}
"""
        docs.append(Document(
            page_content=basic_info.strip(),
            metadata={
                "source": filename,
                "type": "basic_info",
                "product_id": product_id,
                "base_price": base_price,
                "brand": brand,
                "category": category,
                "sub_category": sub_category,
            }
        ))

        # 片段2：SKU规格信息
        skus = data.get("skus", [])
        if skus:
            sku_parts = []
            for sku in skus:
                sku_id = sku.get("sku_id", "")
                props = sku.get("properties", {})
                price = sku.get("price", 0)
                props_str = "，".join([f"{k}:{v}" for k, v in props.items()])
                sku_parts.append(f"SKU {sku_id}: {props_str}，价格¥{price}")
            sku_text = f"""【商品SKU规格】
商品: {title}
所有可选规格:
{chr(10).join(sku_parts)}
"""
            docs.append(Document(
                page_content=sku_text.strip(),
                metadata={
                    "source": filename,
                    "type": "sku_info",
                    "product_id": product_id,
                    "base_price": base_price,
                    "brand": brand,
                    "category": category,
                    "sub_category": sub_category,
                }
            ))

        # 片段3：营销描述
        rag_knowledge = data.get("rag_knowledge", {})
        marketing_desc = rag_knowledge.get("marketing_description", "")
        if marketing_desc:
            marketing_text = f"""【商品营销描述】
商品: {title}
{marketing_desc}
"""
            docs.append(Document(
                page_content=marketing_text.strip(),
                metadata={
                    "source": filename,
                    "type": "marketing",
                    "product_id": product_id,
                    "base_price": base_price,
                    "brand": brand,
                    "category": category,
                    "sub_category": sub_category,
                }
            ))

        # 片段4：官方FAQ — 双路优化。问题+答案摘要做嵌入（提升召回质量），完整FAQ存metadata供上下文构建
        official_faq = rag_knowledge.get("official_faq", [])
        for i, faq in enumerate(official_faq):
            q = faq.get("question", "")
            a = faq.get("answer", "")

            # 纯问题文本（type=faq_q）：短文本精确匹配，召回时排名靠前保证精度
            faq_q_text = f"""【FAQ问题】
商品: {title}
问题: {q}
"""
            # 问题 + 答案摘要（type=faq）：语义覆盖更广，召回同义不同问法的查询
            answer_summary = a[:80] + "…" if len(a) > 80 else a
            faq_qa_text = f"""【FAQ】
商品: {title}
问题: {q}
解答: {answer_summary}
"""
            # faq_answer 存完整问答（供 _build_context 读取给 LLM）
            faq_full_text = f"""【FAQ-完整解答】
商品: {title}
问题: {q}
回答: {a}
"""
            # Chunk A: 纯问题（精度优先）
            docs.append(Document(
                page_content=faq_q_text.strip(),
                metadata={
                    "source": filename,
                    "type": "faq_q",
                    "product_id": product_id,
                    "faq_index": i,
                    "faq_answer": faq_full_text,
                    "base_price": base_price,
                    "brand": brand,
                    "category": category,
                    "sub_category": sub_category,
                }
            ))
            # Chunk B: 问题+答案摘要（覆盖优先）
            docs.append(Document(
                page_content=faq_qa_text.strip(),
                metadata={
                    "source": filename,
                    "type": "faq",
                    "product_id": product_id,
                    "faq_index": i,
                    "faq_answer": faq_full_text,
                    "base_price": base_price,
                    "brand": brand,
                    "category": category,
                    "sub_category": sub_category,
                }
            ))

        # 片段5：用户评价
        user_reviews = rag_knowledge.get("user_reviews", [])
        for i, review in enumerate(user_reviews):
            nickname = review.get("nickname", "匿名用户")
            rating = review.get("rating", 0)
            content = review.get("content", "")
            review_text = f"""【用户评价 - 第{i+1}条】
商品: {title}
用户: {nickname}
评分: {rating}星
评价内容: {content}
"""
            docs.append(Document(
                page_content=review_text.strip(),
                metadata={
                    "source": filename,
                    "type": "review",
                    "product_id": product_id,
                    "review_index": i,
                    "base_price": base_price,
                    "brand": brand,
                    "category": category,
                    "sub_category": sub_category,
                }
            ))

        return docs

    async def process_uploaded_file(self, file) -> List[Document]:
        """
        处理上传的文件，解析并切分为知识片段

        Args:
            file: FastAPI上传的文件对象

        Returns:
            切分后的Document对象列表

        Raises:
            ValueError: 不支持的文件格式
        """
        _, suffix = os.path.splitext(file.filename)
        suffix = suffix.lower()

        if suffix not in self.supported_formats:
            supported = ", ".join(self.supported_formats.keys())
            raise ValueError(f"不支持的文件格式: {suffix}，支持的格式: {supported}")

        if suffix == ".json":
            content_bytes = await file.read()
            try:
                data = json.loads(content_bytes.decode("utf-8"))
                if isinstance(data, list):
                    all_docs = []
                    for item in data:
                        all_docs.extend(self._parse_product_json(item, file.filename))
                    return all_docs
                else:
                    return self._parse_product_json(data, file.filename)
            except json.JSONDecodeError as e:
                raise ValueError(f"JSON文件解析失败: {str(e)}")

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        docs = []
        try:
            loader_class = self.supported_formats[suffix]
            if suffix == ".txt":
                loader = loader_class(tmp_path, encoding="utf-8")
            else:
                loader = loader_class(tmp_path)
            docs = loader.load()
        except Exception as e:
            raise ValueError(f"文件解析失败: {str(e)}")
        finally:
            os.unlink(tmp_path)

        if not docs:
            return []

        chunks = self.text_splitter.split_documents(docs)
        for chunk in chunks:
            if 'source' not in chunk.metadata:
                chunk.metadata['source'] = file.filename

        return chunks

    def process_local_file(self, file_path: str) -> List[Document]:
        """
        处理本地文件（同步版本）

        Args:
            file_path: 本地文件路径

        Returns:
            切分后的Document对象列表
        """
        _, suffix = os.path.splitext(file_path)
        suffix = suffix.lower()

        if suffix not in self.supported_formats:
            supported = ", ".join(self.supported_formats.keys())
            raise ValueError(f"不支持的文件格式: {suffix}，支持的格式: {supported}")

        if suffix == ".json":
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            filename = os.path.basename(file_path)
            if isinstance(data, list):
                all_docs = []
                for item in data:
                    all_docs.extend(self._parse_product_json(item, filename))
                return all_docs
            else:
                return self._parse_product_json(data, filename)

        try:
            loader_class = self.supported_formats[suffix]
            if suffix == ".txt":
                loader = loader_class(file_path, encoding="utf-8")
            else:
                loader = loader_class(file_path)
            docs = loader.load()
        except Exception as e:
            raise ValueError(f"文件解析失败: {str(e)}")

        if not docs:
            return []

        chunks = self.text_splitter.split_documents(docs)
        for chunk in chunks:
            if 'source' not in chunk.metadata:
                chunk.metadata['source'] = os.path.basename(file_path)

        return chunks

    def get_supported_formats(self) -> List[str]:
        return list(self.supported_formats.keys())
