import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


class Settings:
    # OpenAI 相关配置
    OPENAI_BASE_URL: str = os.getenv(
        "OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    LLM_MODEL_NAME: str = os.getenv("LLM_MODEL_NAME", "qwen-turbo")
    
    # Qwen3-Embedding 配置（text-embedding-v4）- 8192长上下文，支持2048维多模态输出
    EMBEDDING_MODEL_NAME: str = os.getenv("EMBEDDING_MODEL_NAME", "text-embedding-v4")
    EMBEDDING_DIMENSIONS: int = int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))
    
    # DashScope Cross-Encoder 重排序配置（qwen3-rerank，gte-rerank已下线）
    RERANKER_MODEL_NAME: str = os.getenv("RERANKER_MODEL_NAME", "qwen3-rerank")
    
    # 视觉理解模型配置（qwen-vl-plus）
    VISION_MODEL_NAME: str = os.getenv("VISION_MODEL_NAME", "qwen-vl-plus")
    VISION_MAX_IMAGE_SIZE: int = int(os.getenv("VISION_MAX_IMAGE_SIZE", "1024"))
    
    # 多模态图片向量化配置（DashScope tongyi-embedding-vision-flash）
    VISION_EMBEDDING_MODEL_NAME: str = os.getenv("VISION_EMBEDDING_MODEL_NAME", "tongyi-embedding-vision-flash")
    VISION_EMBEDDING_DIMENSIONS: int = int(os.getenv("VISION_EMBEDDING_DIMENSIONS", "768"))
    
    # 语音识别配置（DashScope fun-asr-realtime WebSocket）
    ASR_MODEL_NAME: str = os.getenv("ASR_MODEL_NAME", "fun-asr-realtime")
    
    # 语音合成配置（DashScope CosyVoice）
    TTS_MODEL_NAME: str = os.getenv("TTS_MODEL_NAME", "cosyvoice-v3.5-flash")
    TTS_VOICE: str = os.getenv("TTS_VOICE", "longanyang")
    TTS_AUDIO_DIR: str = "./uploads/audio"
    
    # Chroma 向量数据库配置
    CHROMA_COLLECTION_NAME: str = "ecommerce_products"
    CHROMA_IMAGE_COLLECTION_NAME: str = "ecommerce_images"
    CHROMA_PERSIST_DIRECTORY: str = "./chroma_db"
    
    # 文本分块和检索配置
    CHUNK_SIZE: int = 800  # Qwen3-Embedding支持8192上下文，可以用更大分块
    CHUNK_OVERLAP: int = 100  # 分块重叠大小
    RETRIEVAL_K: int = 3  # 检索返回的文档数量
    
    # 服务器配置
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 9000
    
    # Go 网关配置（购物车等 API）
    GO_SERVER_URL: str = os.getenv("GO_SERVER_URL", "http://localhost:8080")


# 创建设置实例
settings = Settings()