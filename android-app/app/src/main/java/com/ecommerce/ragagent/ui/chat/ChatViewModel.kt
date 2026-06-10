package com.ecommerce.ragagent.ui.chat

import androidx.lifecycle.LiveData
import androidx.lifecycle.MutableLiveData
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ecommerce.ragagent.data.model.ChatMessage
import com.ecommerce.ragagent.data.model.ProductCard
import com.ecommerce.ragagent.data.repository.ChatRepository
class ChatViewModel(
    private val repository: ChatRepository = ChatRepository()
) : ViewModel() {

    companion object {
        private val WELCOME_MESSAGE = ChatMessage(
            role = ChatMessage.ROLE_ASSISTANT,
            content = "欢迎使用电商智能导购助手，请问有什么可以帮到您？"
        )
    }

    private val _messages = MutableLiveData<List<ChatMessage>>(listOf(WELCOME_MESSAGE))
    val messages: LiveData<List<ChatMessage>> = _messages

    private val _isLoading = MutableLiveData(false)
    val isLoading: LiveData<Boolean> = _isLoading

    private val _error = MutableLiveData<String?>()
    val error: LiveData<String?> = _error

    /** 当前会话 ID（用于购物车 user_id 关联） */
    private val _currentSessionId = MutableLiveData<String?>(null)
    val currentSessionId: LiveData<String?> = _currentSessionId

    /** 输入区已选中的商品卡片（从助手回答中点击添加，最多2张） */
    private val _selectedCards = MutableLiveData<List<ProductCard>>(emptyList())
    val selectedCards: LiveData<List<ProductCard>> = _selectedCards

    /**
     * 发送文本消息（可选附加已选中的卡片）
     */
    fun sendMessage(question: String) {
        val text = question.trim()
        val hasAttachments = _selectedCards.value?.isNotEmpty() == true

        if (text.isEmpty() && !hasAttachments) return

        // 构建用户消息
        val userMessage = ChatMessage(
            role = ChatMessage.ROLE_USER,
            content = text,
            selectedCards = _selectedCards.value?.toList() ?: emptyList()
        )
        val currentList = _messages.value?.toMutableList() ?: mutableListOf()
        currentList.add(userMessage)

        // 清空已选卡片
        _selectedCards.value = emptyList()

        val assistantMsg = ChatMessage(
            role = ChatMessage.ROLE_ASSISTANT,
            content = "正在查询中..."
        )
        val assistantIndex = currentList.size
        currentList.add(assistantMsg)
        _messages.value = currentList.toList()

        _isLoading.value = true

        var pendingMsg = assistantMsg

        // 构建带卡片引用的增强查询
        val enrichedQuestion = buildEnrichedQuery(text, userMessage.selectedCards)

        repository.streamFromGoAgent(
            question = enrichedQuestion,
            onSession = { sid ->
                if (sid.isNotEmpty()) _currentSessionId.postValue(sid)
            },
            onWaiting = {
                // 客户端已显示硬编码占位文字，忽略后端 waiting 事件
            },
            onChunk = { chunk ->
                pendingMsg = pendingMsg.copy(content = chunk)
                val list = _messages.value?.toMutableList() ?: return@streamFromGoAgent
                if (assistantIndex < list.size) {
                    list[assistantIndex] = pendingMsg
                    _messages.postValue(list.toList())
                }
            },
            onCards = { cards ->
                pendingMsg = pendingMsg.copy(productCards = cards)
                val list = _messages.value?.toMutableList() ?: return@streamFromGoAgent
                if (assistantIndex < list.size) {
                    list[assistantIndex] = pendingMsg
                    _messages.postValue(list.toList())
                }
            },
            onVoice = { url, text ->
                pendingMsg = pendingMsg.copy(voiceUrl = url, voiceText = text)
                val list = _messages.value?.toMutableList() ?: return@streamFromGoAgent
                if (assistantIndex < list.size) {
                    list[assistantIndex] = pendingMsg
                    _messages.postValue(list.toList())
                }
            },
            onDone = {
                _isLoading.postValue(false)
            },
            onError = { e ->
                val list = _messages.value?.toMutableList() ?: return@streamFromGoAgent
                if (assistantIndex < list.size) {
                    list[assistantIndex] = list[assistantIndex].copy(content = "请求失败: ${e.message}")
                    _messages.postValue(list.toList())
                }
                _isLoading.postValue(false)
                _error.postValue(e.message)
            }
        )
    }

    /**
     * 发送图片进行以图搜图（vision 路径）
     * imageUris: 仅用于前端展示，实际上传用 imageBytes
     */
    fun sendImageMessage(
        imageUris: List<String>,
        imageBytes: ByteArray,
        imageName: String,
        textQuery: String = ""
    ) {
        if (imageUris.isEmpty()) return

        val userMessage = ChatMessage(
            role = ChatMessage.ROLE_USER,
            content = textQuery,
            attachedImages = imageUris,
            selectedCards = _selectedCards.value?.toList() ?: emptyList()
        )
        val currentList = _messages.value?.toMutableList() ?: mutableListOf()
        currentList.add(userMessage)

        // 清空已选卡片
        _selectedCards.value = emptyList()

        val assistantMsg = ChatMessage(
            role = ChatMessage.ROLE_ASSISTANT,
            content = "正在查询中..."
        )
        val assistantIndex = currentList.size
        currentList.add(assistantMsg)
        _messages.value = currentList.toList()

        _isLoading.value = true

        var pendingMsg = assistantMsg

        // 使用 vision 端点
        repository.streamFromVision(
            imageBytes = imageBytes,
            imageName = imageName,
            query = textQuery,
            onSession = { sid ->
                if (sid.isNotEmpty()) _currentSessionId.postValue(sid)
            },
            onWaiting = {
                // 客户端已显示硬编码占位文字，忽略后端 waiting 事件
            },
            onChunk = { chunk ->
                pendingMsg = pendingMsg.copy(content = chunk)
                val list = _messages.value?.toMutableList() ?: return@streamFromVision
                if (assistantIndex < list.size) {
                    list[assistantIndex] = pendingMsg
                    _messages.postValue(list.toList())
                }
            },
            onCards = { cards ->
                pendingMsg = pendingMsg.copy(productCards = cards)
                val list = _messages.value?.toMutableList() ?: return@streamFromVision
                if (assistantIndex < list.size) {
                    list[assistantIndex] = pendingMsg
                    _messages.postValue(list.toList())
                }
            },
            onVoice = { url, text ->
                pendingMsg = pendingMsg.copy(voiceUrl = url, voiceText = text)
                val list = _messages.value?.toMutableList() ?: return@streamFromVision
                if (assistantIndex < list.size) {
                    list[assistantIndex] = pendingMsg
                    _messages.postValue(list.toList())
                }
            },
            onDone = {
                _isLoading.postValue(false)
            },
            onError = { e ->
                val list = _messages.value?.toMutableList() ?: return@streamFromVision
                if (assistantIndex < list.size) {
                    list[assistantIndex] = list[assistantIndex].copy(content = "图片搜索失败: ${e.message}")
                    _messages.postValue(list.toList())
                }
                _isLoading.postValue(false)
                _error.postValue(e.message)
            }
        )
    }

    /** 添加商品卡片到输入区（从助手回答中点击） */
    fun addSelectedCard(card: ProductCard): Boolean {
        val current = _selectedCards.value?.toMutableList() ?: mutableListOf()
        if (current.any { it.productId == card.productId }) return false
        if (current.size >= 2) return false
        current.add(card)
        _selectedCards.value = current.toList()
        return true
    }

    /** 移除输入区已选中的卡片 */
    fun removeSelectedCard(position: Int) {
        val current = _selectedCards.value?.toMutableList() ?: return
        if (position in 0 until current.size) {
            current.removeAt(position)
            _selectedCards.value = current.toList()
        }
    }

    /** 清空已选卡片 */
    fun clearSelectedCards() {
        _selectedCards.value = emptyList()
    }

    fun clearError() {
        _error.value = null
    }

    fun initSession(sid: String) {
        repository.initSession(sid)
        _currentSessionId.value = sid
    }

    fun restoreMessages(messages: List<ChatMessage>) {
        if (messages.isEmpty()) return
        _messages.value = messages.toList()
    }

    fun clearChat() {
        repository.clearSession()
        _messages.value = listOf(WELCOME_MESSAGE)
        _selectedCards.value = emptyList()
    }

    /**
     * 构建带卡片引用的增强查询
     */
    private fun buildEnrichedQuery(text: String, cards: List<ProductCard>): String {
        if (cards.isEmpty()) return text

        val cardNames = cards.joinToString("、") { it.name }
        val cardInfo = cards.joinToString("\n") { card ->
            "- ${card.name} (${card.price})${if (card.recommendReason.isNotEmpty()) " - ${card.recommendReason}" else ""}"
        }
        return "$text\n\n【用户关注的商品】\n$cardInfo\n\n请针对以上「$cardNames」提供详细信息。"
    }

    override fun onCleared() {
        super.onCleared()
        repository.cancelStream()
    }
}
