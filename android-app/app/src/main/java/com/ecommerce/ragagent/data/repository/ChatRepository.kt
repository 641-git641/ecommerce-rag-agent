package com.ecommerce.ragagent.data.repository

import com.ecommerce.ragagent.data.api.ApiClient
import com.ecommerce.ragagent.data.api.GoServerApi
import com.ecommerce.ragagent.data.api.SSEClientFactory
import com.ecommerce.ragagent.data.model.*
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import java.io.File

class ChatRepository(
    private val api: GoServerApi = ApiClient.getApi(),
    private val sseClientFactory: SSEClientFactory = ApiClient.createSSEClientFactory()
) {
    private var currentSessionId: String? = null
    private var currentEventSource: EventSource? = null
    
    private val gson = Gson()
    private val baseUrl get() = ApiClient.getBaseUrl()
    private val goAgentUrl get() = "${ApiClient.getBaseUrl()}api/agent/stream"

    val sessionId: String? get() = currentSessionId

    fun initSession(sid: String) {
        if (currentSessionId == null && sid.isNotBlank()) {
            currentSessionId = sid
        }
    }
    
    suspend fun createSession(): Result<Session> = withContext(Dispatchers.IO) {
        try {
            val response = api.createSession()
            if (response.isSuccessful) {
                val sessionId = response.body()?.sessionId ?: return@withContext Result.failure(Exception("空响应"))
                currentSessionId = sessionId
                Result.success(Session(sessionId))
            } else {
                Result.failure(Exception("创建会话失败: ${response.code()}"))
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }
    
    suspend fun getSessionHistory(): Result<List<ChatMessage>> = withContext(Dispatchers.IO) {
        val sessionId = currentSessionId ?: return@withContext Result.failure(Exception("无活跃会话"))
        try {
            val response = api.getSessionHistory(sessionId)
            if (response.isSuccessful) {
                val messages = response.body()?.history?.map { it.toChatMessage() } ?: emptyList()
                Result.success(messages)
            } else {
                Result.failure(Exception("获取历史失败: ${response.code()}"))
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }
    
    suspend fun sendMessage(question: String): Result<ChatResponse> = withContext(Dispatchers.IO) {
        val sessionId = currentSessionId ?: return@withContext Result.failure(Exception("无活跃会话"))
        try {
            val request = ChatRequest(question, sessionId)
            val response = api.sendMessage(request)
            if (response.isSuccessful) {
                Result.success(response.body() ?: ChatResponse())
            } else {
                Result.failure(Exception("发送消息失败: ${response.code()}"))
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }
    
    suspend fun agentQuery(query: String): Result<AgentQueryResponse> = withContext(Dispatchers.IO) {
        val sessionId = currentSessionId ?: ""
        try {
            val request = AgentQueryRequest(query, sessionId)
            val response = api.agentQuery(request)
            if (response.isSuccessful) {
                Result.success(response.body() ?: AgentQueryResponse())
            } else {
                Result.failure(Exception("Agent查询失败: ${response.code()}"))
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }
    
    fun streamChat(
        question: String,
        onMessage: (String) -> Unit,
        onDone: (List<String>?) -> Unit,
        onError: (Throwable) -> Unit
    ) {
        val sessionId = currentSessionId ?: throw Exception("无活跃会话")
        currentEventSource = sseClientFactory.create(
            question = question,
            sessionId = sessionId,
            onMessage = onMessage,
            onDone = onDone,
            onError = onError
        ).streamChat(
            ChatRequest(question, sessionId),
            onMessage,
            onDone,
            onError
        )
    }
    
    fun streamChatFromPythonRAG(
        question: String,
        onSession: (String) -> Unit,
        onWaiting: (String) -> Unit,
        onChunk: (String) -> Unit,
        onCards: (List<ProductCard>) -> Unit,
        onVoice: (url: String, text: String) -> Unit,
        onDone: () -> Unit,
        onError: (Throwable) -> Unit
    ) {
        cancelStream()

        val sid = currentSessionId ?: ""
        val escapedQuestion = question.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")
        val jsonBody = "{\"question\":\"$escapedQuestion\",\"session_id\":\"$sid\"}"
        val requestBody = jsonBody.toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())
        
        val request = okhttp3.Request.Builder()
            .url("${baseUrl}chat/stream")
            .post(requestBody)
            .header("Accept", "text/event-stream")
            .header("Cache-Control", "no-cache")
            .build()
        
        val client = ApiClient.getOkHttpClient()
        
        val factory = EventSources.createFactory(client)
        currentEventSource = factory.newEventSource(request, object : EventSourceListener() {
            override fun onEvent(
                eventSource: EventSource,
                id: String?,
                type: String?,
                data: String
            ) {
                try {
                    val trimmed = data.trim()
                    if (trimmed.isEmpty()) return
                    
                    val json = gson.fromJson(trimmed, Map::class.java)
                    val eventType = json["type"] as? String ?: return
                    
                    when (eventType) {
                        "waiting" -> {
                            val content = json["content"] as? String ?: ""
                            onWaiting(content)
                        }
                        "clear_waiting" -> {
                            // 检索完成，前端清除 waiting 动画
                        }
                        "session" -> {
                            val sid = json["sid"] as? String ?: ""
                            if (sid.isNotEmpty()) {
                                currentSessionId = sid
                                onSession(sid)
                            }
                        }
                        "chunk" -> {
                            val content = json["content"] as? String ?: ""
                            onChunk(content)
                        }
                        "cards" -> {
                            @Suppress("UNCHECKED_CAST")
                            val rawCards = json["cards"] as? List<Map<String, Any>> ?: emptyList()
                            val cards = rawCards.mapNotNull { card ->
                                try {
                                    ProductCard(
                                        productId = card["product_id"] as? String ?: "",
                                        name = card["name"] as? String ?: "",
                                        price = formatPrice(card["price"]),
                                        recommendReason = card["reason"] as? String ?: "",
                                        imageUrl = card["image_url"] as? String ?: ""
                                    )
                                } catch (e: Exception) {
                                    null
                                }
                            }
                            if (cards.isNotEmpty()) {
                                onCards(cards)
                            }
                        }
                        "voice" -> {
                            val url = json["url"] as? String ?: ""
                            val text = json["text"] as? String ?: ""
                            onVoice(url, text)
                        }
                        "done" -> {
                            onDone()
                        }
                        "error" -> {
                            val content = json["content"] as? String ?: "未知错误"
                            onError(Exception(content))
                        }
                    }
                } catch (e: Exception) {
                    onChunk(data)
                }
            }
            
            override fun onFailure(
                eventSource: EventSource,
                t: Throwable?,
                response: okhttp3.Response?
            ) {
                t?.let { onError(it) } ?: onError(Exception("SSE连接失败"))
            }
            
            override fun onClosed(eventSource: EventSource) {
                onDone()
            }
        })
    }
    
    fun streamFromGoAgent(
        question: String,
        onSession: (String) -> Unit,
        onWaiting: (String) -> Unit,
        onChunk: (String) -> Unit,
        onCards: (List<ProductCard>) -> Unit,
        onVoice: (url: String, text: String) -> Unit,
        onDone: () -> Unit,
        onError: (Throwable) -> Unit
    ) {
        cancelStream()

        val sid = currentSessionId ?: ""
        val escapedQuery = question.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")
        val jsonBody = "{\"query\":\"$escapedQuery\",\"session_id\":\"$sid\"}"
        val requestBody = jsonBody.toRequestBody("application/json; charset=utf-8".toMediaTypeOrNull())

        val request = okhttp3.Request.Builder()
            .url(goAgentUrl)
            .post(requestBody)
            .header("Accept", "text/event-stream")
            .header("Cache-Control", "no-cache")
            .build()

        val client = ApiClient.getOkHttpClient()
        val factory = EventSources.createFactory(client)
        currentEventSource = factory.newEventSource(request, object : EventSourceListener() {
            override fun onEvent(
                eventSource: EventSource,
                id: String?,
                type: String?,
                data: String
            ) {
                try {
                    val trimmed = data.trim()
                    if (trimmed.isEmpty() || trimmed == "{}") return

                    val json = gson.fromJson(trimmed, Map::class.java)
                    val eventType = json["type"] as? String

                    when (eventType) {
                        "waiting" -> {
                            val content = json["content"] as? String ?: ""
                            onWaiting(content)
                        }
                        "clear_waiting" -> {
                            // 检索完成，前端清除 waiting 动画
                        }
                        "session" -> {
                            val sid = json["sid"] as? String ?: ""
                            if (sid.isNotEmpty()) {
                                currentSessionId = sid
                                onSession(sid)
                            }
                        }
                        "chunk" -> {
                            val content = json["content"] as? String ?: ""
                            onChunk(content)
                        }
                        "cards" -> {
                            @Suppress("UNCHECKED_CAST")
                            val rawCards = json["cards"] as? List<Map<String, Any>> ?: emptyList()
                            val cards = rawCards.mapNotNull { card ->
                                try {
                                    ProductCard(
                                        productId = card["product_id"] as? String ?: "",
                                        name = card["name"] as? String ?: "",
                                        price = formatPrice(card["price"]),
                                        recommendReason = card["reason"] as? String ?: "",
                                        imageUrl = card["image_url"] as? String ?: ""
                                    )
                                } catch (e: Exception) {
                                    null
                                }
                            }
                            if (cards.isNotEmpty()) {
                                onCards(cards)
                            }
                        }
                        "voice" -> {
                            val url = json["url"] as? String ?: ""
                            val text = json["text"] as? String ?: ""
                            onVoice(url, text)
                        }
                        "done" -> {
                            onDone()
                        }
                        "error" -> {
                            val content = json["content"] as? String ?: "未知错误"
                            onError(Exception(content))
                        }
                        "meta" -> {
                            // Agent meta info (mode, used_tools, confidence)
                        }
                    }
                } catch (e: Exception) {
                    if (!data.trim().startsWith("{")) return
                    onChunk(data)
                }
            }

            override fun onFailure(
                eventSource: EventSource,
                t: Throwable?,
                response: okhttp3.Response?
            ) {
                t?.let { onError(it) } ?: onError(Exception("SSE连接失败"))
            }

            override fun onClosed(eventSource: EventSource) {
                onDone()
            }
        })
    }

    private fun formatPrice(price: Any?): String {
        return when (price) {
            is Number -> "¥%.2f".format(price.toDouble())
            is String -> price
            else -> ""
        }
    }

    /**
     * 以图搜图（vision）：上传图片到 /api/agent/vision，SSE 流返回结果
     */
    fun streamFromVision(
        imageBytes: ByteArray,
        imageName: String,
        query: String = "",
        onSession: (String) -> Unit,
        onWaiting: (String) -> Unit,
        onChunk: (String) -> Unit,
        onCards: (List<ProductCard>) -> Unit,
        onVoice: (url: String, text: String) -> Unit,
        onDone: () -> Unit,
        onError: (Throwable) -> Unit
    ) {
        cancelStream()

        val visionUrl = "${ApiClient.getBaseUrl()}api/agent/vision"
        val sid = currentSessionId ?: ""

        try {
            val mediaType = "image/*".toMediaTypeOrNull()
            val imageBody = imageBytes.toRequestBody(mediaType)

            val multipartBody = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart("image", imageName, imageBody)
                .addFormDataPart("query", query)
                .addFormDataPart("session_id", sid)
                .build()

            val request = okhttp3.Request.Builder()
                .url(visionUrl)
                .post(multipartBody)
                .header("Accept", "text/event-stream")
                .header("Cache-Control", "no-cache")
                .build()

            val client = ApiClient.getOkHttpClient()
            val factory = EventSources.createFactory(client)
            currentEventSource = factory.newEventSource(request, object : EventSourceListener() {
                override fun onEvent(
                    eventSource: EventSource,
                    id: String?,
                    type: String?,
                    data: String
                ) {
                    try {
                        val trimmed = data.trim()
                        if (trimmed.isEmpty() || trimmed == "{}") return

                        val json = gson.fromJson(trimmed, Map::class.java)
                        val eventType = json["type"] as? String

                        when (eventType) {
                            "waiting" -> {
                                val content = json["content"] as? String ?: ""
                                onWaiting(content)
                            }
                            "session" -> {
                                val sid = json["sid"] as? String ?: ""
                                if (sid.isNotEmpty()) {
                                    currentSessionId = sid
                                    onSession(sid)
                                }
                            }
                            "chunk" -> {
                                val content = json["content"] as? String ?: ""
                                onChunk(content)
                            }
                            "cards" -> {
                                @Suppress("UNCHECKED_CAST")
                                val rawCards = json["cards"] as? List<Map<String, Any>> ?: emptyList()
                                val cards = rawCards.mapNotNull { card ->
                                    try {
                                        ProductCard(
                                            productId = card["product_id"] as? String ?: "",
                                            name = card["name"] as? String ?: "",
                                            price = formatPrice(card["price"]),
                                            recommendReason = card["reason"] as? String ?: "",
                                            imageUrl = card["image_url"] as? String ?: ""
                                        )
                                    } catch (e: Exception) {
                                        null
                                    }
                                }
                                if (cards.isNotEmpty()) {
                                    onCards(cards)
                                }
                            }
                            "voice" -> {
                                val url = json["url"] as? String ?: ""
                                val text = json["text"] as? String ?: ""
                                onVoice(url, text)
                            }
                            "done" -> {
                                onDone()
                            }
                            "error" -> {
                                val content = json["content"] as? String ?: "未知错误"
                                onError(Exception(content))
                            }
                        }
                    } catch (e: Exception) {
                        if (!data.trim().startsWith("{")) return
                        onChunk(data)
                    }
                }

                override fun onFailure(
                    eventSource: EventSource,
                    t: Throwable?,
                    response: okhttp3.Response?
                ) {
                    t?.let { onError(it) } ?: onError(Exception("Vision SSE连接失败"))
                }

                override fun onClosed(eventSource: EventSource) {
                    onDone()
                }
            })
        } catch (e: Exception) {
            onError(e)
        }
    }

    /**
     * 纯语音识别：上传音频到 Python /asr（阿里云 DashScope fun-asr），只返回文本
     */
    suspend fun sendAsr(voiceBytes: ByteArray, voiceName: String): Result<String> = withContext(Dispatchers.IO) {
        try {
            val voiceBody = voiceBytes.toRequestBody("audio/*".toMediaTypeOrNull())
            val multipartBody = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart("voice", voiceName, voiceBody)
                .build()

            val request = okhttp3.Request.Builder()
                .url("${baseUrl}asr")
                .post(multipartBody)
                .build()

            val response = ApiClient.getOkHttpClient().newCall(request).execute()
            val body = response.body?.string() ?: ""
            if (response.isSuccessful) {
                val json = gson.fromJson(body, Map::class.java)
                val text = json["text"] as? String ?: ""
                Result.success(text)
            } else {
                Result.failure(Exception("语音识别失败: ${response.code}"))
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    fun cancelStream() {
        currentEventSource?.cancel()
        currentEventSource = null
    }
    
    fun clearSession() {
        currentSessionId = null
        cancelStream()
    }
}
