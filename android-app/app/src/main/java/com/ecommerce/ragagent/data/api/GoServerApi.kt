package com.ecommerce.ragagent.data.api

import com.ecommerce.ragagent.data.model.*
import okhttp3.ResponseBody
import retrofit2.Response
import retrofit2.http.*

interface GoServerApi {
    
    @POST("api/session/create")
    suspend fun createSession(): Response<SessionCreateResponse>
    
    @GET("api/sessions")
    suspend fun getSessions(): Response<SessionListResponse>
    
    @GET("api/session/{id}/history")
    suspend fun getSessionHistory(
        @Path("id") sessionId: String
    ): Response<SessionHistoryResponse>

    @DELETE("api/sessions/{id}")
    suspend fun deleteSession(
        @Path("id") sessionId: String
    ): Response<DeleteResponse>
    
    @POST("api/chat/send")
    suspend fun sendMessage(
        @Body request: ChatRequest
    ): Response<ChatResponse>
    
    @Streaming
    @POST("api/chat/stream")
    fun streamChat(
        @Body request: ChatRequest
    ): Response<ResponseBody>
    
    @POST("api/agent/query")
    suspend fun agentQuery(
        @Body request: AgentQueryRequest
    ): Response<AgentQueryResponse>
    
    @GET("api/health")
    suspend fun healthCheck(): Response<Map<String, Any>>
    
    @GET("api/test/python")
    suspend fun testPythonRAG(): Response<Map<String, Any>>
}
