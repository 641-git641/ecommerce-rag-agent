package com.ecommerce.ragagent.ui.chat

import android.content.Context
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.speech.tts.TextToSpeech
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.FileProvider
import androidx.fragment.app.Fragment
import androidx.fragment.app.viewModels
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.ecommerce.ragagent.data.model.ChatMessage
import com.ecommerce.ragagent.data.model.ProductCard
import com.ecommerce.ragagent.R
import com.ecommerce.ragagent.databinding.FragmentChatBinding
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class ChatFragment : Fragment() {

    private var _binding: FragmentChatBinding? = null
    private val binding get() = _binding!!

    private val viewModel: ChatViewModel by viewModels()
    private lateinit var chatAdapter: ChatAdapter
    private var tts: TextToSpeech? = null

    /** 回调接口，与 MainActivity 通信 */
    var onCardAdded: ((ProductCard) -> Unit)? = null
    var onCardLimitReached: (() -> Unit)? = null

    private var selectedImageUri: Uri? = null
    var imagePickerLauncher = registerForActivityResult(
        ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        uri?.let {
            selectedImageUri = it
            onImageReady?.invoke(it)
        }
    }

    /** 拍照临时 URI（FileProvider 生成） */
    private var cameraPhotoUri: Uri? = null
    var cameraLauncher = registerForActivityResult(
        ActivityResultContracts.TakePicture()
    ) { success ->
        if (success) {
            cameraPhotoUri?.let { uri ->
                selectedImageUri = uri
                onImageReady?.invoke(uri)
            }
        }
    }

    /** 打开相机拍照，由 MainActivity 调用。无相机时回退到图片选择器 */
    fun launchCamera() {
        try {
            val ctx = context ?: return
            val photoFile = createCameraTempFile()
            cameraPhotoUri = FileProvider.getUriForFile(
                ctx,
                "${ctx.packageName}.fileprovider",
                photoFile
            )

            // 检查是否有相机 App；没有则回退到图片选择器
            val cameraIntent = android.content.Intent(android.provider.MediaStore.ACTION_IMAGE_CAPTURE)
            if (cameraIntent.resolveActivity(ctx.packageManager) != null) {
                cameraLauncher.launch(cameraPhotoUri!!)
            } else {
                // 模拟器无相机时自动回退到相册选图
                Toast.makeText(ctx, "当前设备无相机，已切换为图片上传", Toast.LENGTH_SHORT).show()
                imagePickerLauncher.launch("image/*")
            }
        } catch (e: Exception) {
            Toast.makeText(context, "无法启动相机: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    /** 创建摄像头拍照的临时文件 */
    private fun createCameraTempFile(): File {
        val dir = File(requireContext().cacheDir, "camera_photos")
        if (!dir.exists()) dir.mkdirs()
        val timestamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
        return File(dir, "IMG_$timestamp.jpg")
    }

    /** 让 MainActivity 知道图片已就绪 */
    var onImageReady: ((Uri) -> Unit)? = null
    /** 购物车有变动时通知 MainActivity 刷新角标 */
    var onCartChanged: (() -> Unit)? = null
    /** 提供给 MainActivity 读取的当前会话 ID */
    var currentSid: String? = null
        private set

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentChatBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        // 恢复上次的会话 ID，确保购物车 user_id 跨重启一致
        val prefs = requireContext().getSharedPreferences("rag_session", Context.MODE_PRIVATE)
        val savedSid = prefs.getString("current_sid", null)
        if (!savedSid.isNullOrBlank()) {
            viewModel.initSession(savedSid)
            currentSid = savedSid
        }
        setupRecyclerView()
        observeViewModel()
    }

    private fun setupRecyclerView() {
        chatAdapter = ChatAdapter()
        chatAdapter.onProductCardClickListener = { card ->
            val added = viewModel.addSelectedCard(card)
            if (added) {
                onCardAdded?.invoke(card)
                Toast.makeText(requireContext(), R.string.card_added, Toast.LENGTH_SHORT).show()
            } else {
                onCardLimitReached?.invoke()
                Toast.makeText(requireContext(), R.string.max_cards_hint, Toast.LENGTH_SHORT).show()
            }
        }
        chatAdapter.onTtsSpeak = { context, text ->
            if (tts == null) {
                tts = TextToSpeech(context) { status ->
                    if (status == TextToSpeech.SUCCESS) {
                        tts?.language = java.util.Locale.CHINESE
                        tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "chat_tts")
                    }
                }
            } else {
                tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "chat_tts")
            }
        }
        val rv = binding.rvMessages
        rv.layoutManager = LinearLayoutManager(requireContext())
        rv.adapter = chatAdapter

        // 新消息来时自动滚动到底部（仅当用户已在底部时）
        val scrollObserver = object : RecyclerView.AdapterDataObserver() {
            override fun onItemRangeInserted(positionStart: Int, itemCount: Int) {
                val lm = rv.layoutManager as? LinearLayoutManager ?: return
                val lastVisible = lm.findLastCompletelyVisibleItemPosition()
                val total = chatAdapter.itemCount
                if (lastVisible == -1 || lastVisible >= total - itemCount - 1) {
                    rv.post { rv.smoothScrollToPosition(total - 1) }
                }
            }
        }
        chatAdapter.registerAdapterDataObserver(scrollObserver)

        // 键盘弹出/收起时，如果用户在底部则自动跟随滚动
        rv.viewTreeObserver.addOnGlobalLayoutListener {
            val lm = rv.layoutManager as? LinearLayoutManager ?: return@addOnGlobalLayoutListener
            val lastVisible = lm.findLastCompletelyVisibleItemPosition()
            val total = chatAdapter.itemCount
            if (lastVisible >= total - 2 && total > 0) {
                rv.post { rv.smoothScrollToPosition(total - 1) }
            }
        }
    }

    private fun observeViewModel() {
        viewModel.messages.observe(viewLifecycleOwner) { messages ->
            chatAdapter.submitList(messages)
            // 购物车操作后刷新角标
            onCartChanged?.invoke()
        }

        viewModel.currentSessionId.observe(viewLifecycleOwner) { sid ->
            if (!sid.isNullOrBlank()) {
                currentSid = sid
                // 持久化，确保重启后购物车 user_id 不变
                requireContext().getSharedPreferences("rag_session", Context.MODE_PRIVATE)
                    .edit().putString("current_sid", sid).apply()
            }
        }

        viewModel.error.observe(viewLifecycleOwner) { error ->
            error?.let {
                Toast.makeText(requireContext(), it, Toast.LENGTH_LONG).show()
                viewModel.clearError()
            }
        }
    }

    // ── 对外暴露的方法 ──

    /** 发送文本消息（由 MainActivity 调用） */
    fun sendTextMessage(text: String) {
        val question = text.trim()
        val hasSelectedCards = (viewModel.selectedCards.value?.size ?: 0) > 0
        if (question.isNotEmpty() || hasSelectedCards) {
            viewModel.sendMessage(question)
        }
    }

    /** 发送图片消息（由 MainActivity 调用） */
    fun sendImageMessage(uri: Uri?, textQuery: String) {
        if (uri == null) return
        val uris = listOf(uri.toString())
        
        // 使用 ContentResolver 读取真实字节（兼容 content:// 和 file://）
        try {
            val inputStream = requireContext().contentResolver.openInputStream(uri)
            val imageBytes = inputStream?.readBytes()
            inputStream?.close()
            if (imageBytes == null || imageBytes.isEmpty()) {
                Toast.makeText(requireContext(), "无法读取图片文件", Toast.LENGTH_SHORT).show()
                return
            }
            val imageName = uri.lastPathSegment ?: "image.jpg"
            viewModel.sendImageMessage(uris, imageBytes, imageName, textQuery)
        } catch (e: Exception) {
            Toast.makeText(requireContext(), "读取图片失败: ${e.message}", Toast.LENGTH_SHORT).show()
        }
        selectedImageUri = null
    }

    /** 获取当前已选卡片（用于显示在输入区） */
    fun getSelectedCards(): List<ProductCard> =
        viewModel.selectedCards.value.orEmpty()

    /** 清除已选卡片 */
    fun clearSelectedCards() {
        viewModel.clearSelectedCards()
    }

    /** 滚动消息列表到底部 */
    fun scrollToBottom() {
        val adapter = chatAdapter
        val count = adapter.itemCount
        if (count > 0) {
            binding.rvMessages.smoothScrollToPosition(count - 1)
        }
    }

    /** 清空对话 */
    fun clearChat() {
        viewModel.clearChat()
        currentSid = null
    }

    /** 从历史恢复会话 */
    fun restoreSession(sid: String, messages: List<ChatMessage>) {
        currentSid = sid
        viewModel.initSession(sid)
        viewModel.restoreMessages(messages)
    }

    override fun onDestroyView() {
        super.onDestroyView()
        chatAdapter.releaseTts()
        tts?.apply { stop(); shutdown() }
        tts = null
        _binding = null
    }
}
