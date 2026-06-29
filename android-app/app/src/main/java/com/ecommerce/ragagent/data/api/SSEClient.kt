package com.ecommerce.ragagent.data.api

import com.ecommerce.ragagent.data.model.ChatRequest
import com.google.gson.Gson
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources

class SSEClient(
    private val baseUrl: String
) {
    private val gson = Gson()
    private var eventSourceFactory: EventSource.Factory? = null
    
    init {
        val client = okhttp3.OkHttpClient.Builder()
            .addInterceptor { chain ->
                val request = chain.request().newBuilder()
                    .addHeader("Accept", "text/event-stream")
                    .addHeader("Cache-Control", "no-cache")
                    .build()
                chain.proceed(request)
            }
            .build()
        
        eventSourceFactory = EventSources.createFactory(client)
    }
    
    fun streamChat(
        request: ChatRequest,
        onMessage: (String) -> Unit,
        onDone: (sources: List<String>?) -> Unit,
        onError: (Throwable) -> Unit
    ): EventSource {
        val mediaType = "application/json; charset=utf-8".toMediaType()
        val jsonBody = gson.toJson(request)
        val requestBody = RequestBody.create(mediaType, jsonBody)

        return eventSourceFactory!!.newEventSource(
            okhttp3.Request.Builder()
                .url("${baseUrl}api/chat/stream")
                .post(requestBody)
                .header("Accept", "text/event-stream")
                .header("Content-Type", "application/json")
                .build(),
            object : EventSourceListener() {
                override fun onEvent(
                    eventSource: EventSource,
                    id: String?,
                    type: String?,
                    data: String
                ) {
                    try {
                        val trimmed = data.trim()
                        if (trimmed.startsWith("{")) {
                            val json = gson.fromJson(trimmed, Map::class.java)
                            if (json.containsKey("content")) {
                                onMessage(json["content"] as String)
                            } else if (json.containsKey("done")) {
                                @Suppress("UNCHECKED_CAST")
                                val sources = json["sources"] as? List<String>
                                onDone(sources)
                            } else if (json.containsKey("error")) {
                                onError(Exception(json["error"] as String))
                            }
                        }
                    } catch (e: Exception) {
                        onMessage(data)
                    }
                }
                
                override fun onFailure(eventSource: EventSource, t: Throwable?, response: okhttp3.Response?) {
                    t?.let { onError(it) }
                }
                
                override fun onClosed(eventSource: EventSource) {
                    onDone(null)
                }
            }
        )
    }
}

class SSEClientFactory(
    private val baseUrl: String
) {
    fun create(): SSEClient {
        return SSEClient(baseUrl)
    }
}
