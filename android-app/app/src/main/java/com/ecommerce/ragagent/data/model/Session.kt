package com.ecommerce.ragagent.data.model

data class Session(
    val id: String,
    val createdAt: Long = System.currentTimeMillis()
)

data class SessionSummary(
    val id: String = "",
    val title: String = "新对话",
    val created_at: String = "",
    val msg_count: Int = 0
) {
    val displayTitle: String get() = title.ifBlank { "新对话" }
    val displayDate: String get() {
        if (created_at.length < 16) return created_at
        // "2026-06-06T12:34:56" → "6/6 12:34"
        return try {
            val t = created_at.substring(0, 16).replace("T", " ")
            val parts = t.split("-", " ")
            if (parts.size >= 3) "${parts[1].trimStart('0')}/${parts[2].trimStart('0')} ${parts[3].take(5)}"
            else created_at
        } catch (_: Exception) { created_at }
    }
}

data class SessionListResponse(
    val sessions: List<SessionSummary> = emptyList()
)

data class SessionCreateResponse(
    val sessionId: String
)

data class SessionHistoryResponse(
    val history: List<ChatMessageDto>
)

data class DeleteResponse(
    val ok: Boolean = false
)

data class ChatMessageDto(
    val id: Long = 0,
    val sessionId: String = "",
    val role: String = "",
    val content: String = "",
    val sources: String? = null,
    val cards: String? = null,
    val voice_url: String? = null,
    val createdAt: String = ""
) {
    fun toChatMessage(): ChatMessage {
        val sourcesList = sources?.let {
            try {
                com.google.gson.Gson().fromJson(it, Array<String>::class.java).toList()
            } catch (e: Exception) {
                null
            }
        }
        val cardList = cards?.let {
            try {
                val rawCards: List<Map<String, Any>> = com.google.gson.Gson().fromJson(
                    it, object : com.google.gson.reflect.TypeToken<List<Map<String, Any>>>() {}.type
                )
                rawCards.mapNotNull { card ->
                    ProductCard(
                        productId = card["product_id"] as? String ?: "",
                        name = card["name"] as? String ?: "",
                        price = formatPrice(card["price"]),
                        recommendReason = card["reason"] as? String ?: "",
                        imageUrl = card["image_url"] as? String ?: ""
                    )
                }
            } catch (e: Exception) {
                null
            }
        }
        return ChatMessage(
            id = id,
            sessionId = sessionId,
            role = role,
            content = content,
            sources = sourcesList,
            productCards = cardList,
            voiceUrl = voice_url
        )
    }

    companion object {
        private fun formatPrice(priceAny: Any?): String {
            val num = when (priceAny) {
                is Number -> priceAny.toDouble()
                is String -> priceAny.toDoubleOrNull()
                else -> null
            }
            return if (num != null && num > 0) "%.2f".format(num)
                   else priceAny?.toString() ?: "--"
        }
    }
}
