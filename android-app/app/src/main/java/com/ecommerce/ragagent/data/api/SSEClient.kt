package com.ecommerce.ragagent.data.api

import com.ecommerce.ragagent.data.model.AgentQueryRequest
import com.ecommerce.ragagent.data.model.AgentQueryResponse
import com.ecommerce.ragagent.data.model.ChatRequest
import com.ecommerce.ragagent.data.model.ChatResponse
import com.google.gson.Gson
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import okhttp3.MediaType
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody
import okhttp3.ResponseBody
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.converter.scalars.ScalarsConverterFactory

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
        val retrofit = Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(okhttp3.OkHttpClient())
            .addConverterFactory(ScalarsConverterFactory.create())
            .addConverterFactory(GsonConverterFactory.create())
            .build()
        
        val api = retrofit.create(GoServerApi::class.java)
        
        val mediaType = "application/json; charset=utf-8".toMediaType()
        val jsonBody = gson.toJson(request)
        val requestBody = RequestBody.create(mediaType, jsonBody)
        
        val call = api.streamChat(ChatRequest(request.question, request.sessionId))
        
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
    fun create(
        question: String,
        sessionId: String,
        onMessage: (String) -> Unit,
        onDone: (List<String>?) -> Unit,
        onError: (Throwable) -> Unit
    ): SSEClient {
        val client = SSEClient(baseUrl)
        val request = ChatRequest(question, sessionId)
        client.streamChat(request, onMessage, onDone, onError)
        return client
    }
}
