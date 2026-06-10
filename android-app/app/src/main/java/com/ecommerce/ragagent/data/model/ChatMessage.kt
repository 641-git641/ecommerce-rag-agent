package com.ecommerce.ragagent.data.model

data class ChatMessage(
    val id: Long = 0,
    val sessionId: String = "",
    val role: String,
    val content: String,
    val sources: List<String>? = null,
    val productCards: List<ProductCard>? = null,
    val agentSteps: List<AgentStep>? = null,
    val metrics: ChatMetrics? = null,
    val voiceUrl: String? = null,
    /** TTS 播报文本（服务端生成，即便不合成音频也会下发） */
    val voiceText: String? = null,
    val timestamp: Long = System.currentTimeMillis(),
    /** 用户消息中的图片附件（本地路径或 URI） */
    val attachedImages: List<String> = emptyList(),
    /** 用户消息中附带的商品卡片（从助手回答中选中的卡片，最多2张） */
    val selectedCards: List<ProductCard> = emptyList()
) {
    companion object {
        const val ROLE_USER = "user"
        const val ROLE_ASSISTANT = "assistant"
    }
    
    val isUser: Boolean get() = role == ROLE_USER
    val isAssistant: Boolean get() = role == ROLE_ASSISTANT
}

data class AgentStep(
    val toolName: String,
    val success: Boolean,
    val input: Map<String, Any>? = null,
    val output: Any? = null,
    val error: String? = null
)

data class ChatMetrics(
    val searchTime: Float = 0f,
    val totalTime: Float = 0f
)

data class ChatRequest(
    val question: String,
    val sessionId: String
)

data class ChatResponse(
    val answer: String = "",
    val sources: List<String>? = null,
    val searchTime: Float = 0f,
    val totalTime: Float = 0f,
    val productCards: List<ProductCard>? = null
)

data class AgentQueryRequest(
    val query: String,
    val sessionId: String = ""
)

data class AgentQueryResponse(
    val mode: String = "",
    val answer: String = "",
    val steps: List<AgentStep>? = null,
    val usedTools: List<String>? = null,
    val confidence: Float = 0f,
    val sources: List<String>? = null,
    val productCards: List<ProductCard>? = null
)

data class StreamEvent(
    val type: String,
    val content: String? = null,
    val cards: List<ProductCard>? = null,
    val url: String? = null,
    val timing: Map<String, Float>? = null
)

data class StreamProductCard(
    val product_id: String = "",
    val name: String = "",
    val price: Double = 0.0,
    val reason: String = ""
) {
    fun toProductCard(): ProductCard = ProductCard(
        productId = product_id,
        name = name,
        price = "%.2f".format(price),
        recommendReason = reason
    )
}
