package com.ecommerce.ragagent

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder.AudioSource
import android.os.Bundle
import android.view.View
import android.view.animation.AccelerateDecelerateInterpolator
import android.view.animation.AlphaAnimation
import android.view.animation.Animation
import android.view.animation.TranslateAnimation
import android.widget.TextView
import android.widget.Toast
import java.io.File
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.LinearLayoutManager
import com.ecommerce.ragagent.data.api.ApiClient
import com.ecommerce.ragagent.data.model.SessionSummary
import com.ecommerce.ragagent.databinding.ActivityMainBinding
import com.ecommerce.ragagent.ui.cart.CartActivity
import com.ecommerce.ragagent.ui.chat.ChatFragment
import com.ecommerce.ragagent.ui.chat.SelectedCardAdapter
import com.ecommerce.ragagent.ui.chat.SessionListAdapter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.GlobalScope
import kotlinx.coroutines.launch
import com.ecommerce.ragagent.data.repository.ChatRepository
import com.bumptech.glide.Glide
import kotlin.jvm.Volatile

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private var chatFragment: ChatFragment? = null

    private var voicePanelVisible = false
    private var audioRecord: AudioRecord? = null
    private var recordingThread: Thread? = null
    @Volatile private var isRecording = false
    private var voiceFile: File? = null

    /** 用户在输入区选中的图片 URI（缩略图展示，不自动发送） */
    private var selectedImageUri: Uri? = null

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            showVoicePanel()
            startRecording()
        } else {
            Toast.makeText(this, "需要录音权限才能使用语音输入", Toast.LENGTH_SHORT).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        // Default to light mode, must explicitly set to avoid MODE_NIGHT_FOLLOW_SYSTEM
        if (getSharedPreferences("app_theme", MODE_PRIVATE).getBoolean("dark_mode", false)) {
            AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_YES)
        } else {
            AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_NO)
        }
        
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        findChatFragment()
        setupListeners()
        refreshCartBadge()
    }

    override fun onResume() {
        super.onResume()
        refreshCartBadge()
    }

    override fun finish() {
        super.finish()
        overridePendingTransition(R.anim.slide_in_left, R.anim.slide_out_right)
    }

    private fun findChatFragment() {
        chatFragment = supportFragmentManager.findFragmentById(R.id.chat_container) as? ChatFragment
        chatFragment?.onImageReady = { uri ->
            // 选图后只显示缩略图在输入框，不自动发送
            selectedImageUri = uri
            Glide.with(this)
                .load(uri)
                .into(binding.inputBar.ivImagePreview)
            binding.inputBar.llImagePreview.visibility = View.VISIBLE
        }
        chatFragment?.onCartChanged = { refreshCartBadge() }
        chatFragment?.onCardAdded = { card ->
            val added = selectedCardAdapter?.addCard(card) ?: false
            if (added) {
                binding.inputBar.rvSelectedCardsInput.visibility = View.VISIBLE
            }
        }
        chatFragment?.onCardLimitReached = {
            Toast.makeText(this, "最多添加2张卡片", Toast.LENGTH_SHORT).show()
        }
        setupInputSelectedCards()
    }

    private var selectedCardAdapter: SelectedCardAdapter? = null
    private var sessionPanelContent: View? = null
    private var sessionListAdapter: SessionListAdapter? = null

    private fun setupInputSelectedCards() {
        selectedCardAdapter = SelectedCardAdapter()
        selectedCardAdapter?.onRemoveClickListener = { pos ->
            selectedCardAdapter?.removeCard(pos)
            if ((selectedCardAdapter?.getAllCards() ?: emptyList()).isEmpty()) {
                binding.inputBar.rvSelectedCardsInput.visibility = View.GONE
            }
        }
        binding.inputBar.rvSelectedCardsInput.adapter = selectedCardAdapter
    }

    private fun setupListeners() {
        // ── 顶栏：会话管理 ──
        binding.btnSessionIcon.setOnClickListener {
            if (isSessionPanelOpen()) hideSessionPanel()
            else {
                if (voicePanelVisible) hideVoicePanel { showSessionPanel() }
                else showSessionPanel()
            }
        }

        // ── 顶栏：购物车 ──
        binding.btnCartIcon.setOnClickListener {
            if (isSessionPanelOpen()) hideSessionPanel { openCart() }
            else if (voicePanelVisible) hideVoicePanel { openCart() }
            else openCart()
        }

        // ── 顶栏：主题切换 ──
        updateThemeIcon()
        binding.btnThemeToggle.setOnClickListener {
            val prefs = getSharedPreferences("app_theme", MODE_PRIVATE)
            val isDark = (AppCompatDelegate.getDefaultNightMode() != AppCompatDelegate.MODE_NIGHT_YES)
            if (isDark) {
                AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_YES)
                prefs.edit().putBoolean("dark_mode", true).apply()
            } else {
                AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_NO)
                prefs.edit().putBoolean("dark_mode", false).apply()
            }
            delegate.applyDayNight()
            binding.btnThemeToggle.post { updateThemeIcon() }
            overridePendingTransition(android.R.anim.fade_in, android.R.anim.fade_out)
        }

        // ── 输入栏：语音 ──
        binding.inputBar.btnVoice.setOnClickListener {
            if (voicePanelVisible) {
                // 正在录音中，点击停止并上传
                stopRecordingAndSend()
            } else {
                if (isSessionPanelOpen()) hideSessionPanel { checkAndStartRecording() }
                else checkAndStartRecording()
            }
        }

        // ── 输入栏：发送 ──
        binding.inputBar.btnSendMsg.setOnClickListener {
            val text = binding.inputBar.etChatInput.text.toString().trim()
            val hasCards = ((selectedCardAdapter?.getAllCards() ?: emptyList()).isNotEmpty())
            val hasImage = selectedImageUri != null

            if (hasImage) {
                // 以图搜图：带图片发送
                chatFragment?.sendImageMessage(selectedImageUri, text)
                clearImagePreview()
                binding.inputBar.etChatInput.text?.clear()
                selectedCardAdapter?.updateCards(emptyList())
                binding.inputBar.rvSelectedCardsInput.visibility = View.GONE
                chatFragment?.clearSelectedCards()
            } else if (text.isNotEmpty() || hasCards) {
                chatFragment?.sendTextMessage(text)
                binding.inputBar.etChatInput.text?.clear()
                selectedCardAdapter?.updateCards(emptyList())
                binding.inputBar.rvSelectedCardsInput.visibility = View.GONE
                chatFragment?.clearSelectedCards()
            }
        }

        // ── 输入栏：图片上传 ──
        binding.inputBar.btnImageInput.setOnClickListener {
            chatFragment?.imagePickerLauncher?.launch("image/*")
        }

        // ── 输入栏：移除图片缩略图 ──
        binding.inputBar.btnRemoveImage.setOnClickListener {
            clearImagePreview()
        }

        // ── 会话遮罩点击关闭 ──
        binding.sessionOverlay.setOnClickListener { hideSessionPanel() }
    }

    // ============================================================
    //  会话管理面板
    // ============================================================

    private fun showSessionPanel() {
        binding.sessionPanel.visibility = View.VISIBLE
        binding.sessionOverlay.visibility = View.VISIBLE

        val slideIn = TranslateAnimation(
            Animation.ABSOLUTE, -binding.sessionPanel.width.toFloat(),
            Animation.ABSOLUTE, 0f, Animation.ABSOLUTE, 0f, Animation.ABSOLUTE, 0f
        ).apply { duration = 350; interpolator = AccelerateDecelerateInterpolator() }
        val fadeIn = AlphaAnimation(0f, 1f).apply { duration = 350 }

        binding.sessionPanel.startAnimation(slideIn)
        binding.sessionOverlay.startAnimation(fadeIn)
        binding.sessionPanel.translationX = 0f
        binding.sessionOverlay.alpha = 1f
        setupSessionPanelContent()
    }

    private fun hideSessionPanel(onComplete: (() -> Unit)? = null) {
        val slideOut = TranslateAnimation(
            Animation.ABSOLUTE, 0f, Animation.ABSOLUTE, -binding.sessionPanel.width.toFloat(),
            Animation.ABSOLUTE, 0f, Animation.ABSOLUTE, 0f
        ).apply { duration = 300; interpolator = AccelerateDecelerateInterpolator() }
        val fadeOut = AlphaAnimation(1f, 0f).apply { duration = 300 }

        slideOut.setAnimationListener(object : Animation.AnimationListener {
            override fun onAnimationEnd(p0: Animation?) {
                binding.sessionPanel.visibility = View.GONE
                binding.sessionOverlay.visibility = View.GONE
                onComplete?.invoke()
            }
            override fun onAnimationStart(p0: Animation?) {}
            override fun onAnimationRepeat(p0: Animation?) {}
        })

        binding.sessionPanel.startAnimation(slideOut)
        binding.sessionOverlay.startAnimation(fadeOut)
        binding.sessionPanel.translationX = -binding.sessionPanel.width.toFloat()
        binding.sessionOverlay.alpha = 0f
    }

    private fun isSessionPanelOpen() = binding.sessionPanel.visibility == View.VISIBLE

    private fun setupSessionPanelContent() {
        if (sessionPanelContent == null) {
            sessionPanelContent = layoutInflater.inflate(R.layout.session_panel_content, binding.sessionPanel, false)
            binding.sessionPanel.addView(sessionPanelContent)

            sessionPanelContent?.findViewById<View>(R.id.btn_new_chat)?.setOnClickListener {
                chatFragment?.clearChat()
                binding.tvSessionTitle.text = "电商导购助手"
                hideSessionPanel()
            }

            val rv = sessionPanelContent!!.findViewById<androidx.recyclerview.widget.RecyclerView>(R.id.rv_session_list)
            sessionListAdapter = SessionListAdapter()
            sessionListAdapter?.onSessionClick = { session ->
                binding.tvSessionTitle.text = session.title
                loadSessionFromBackend(session)
                hideSessionPanel()
            }
            sessionListAdapter?.onSessionDelete = { session -> deleteSessionFromBackend(session) }
            rv.layoutManager = LinearLayoutManager(this)
            rv.adapter = sessionListAdapter
        }
        loadSessionList()
    }

    private fun loadSessionList() {
        GlobalScope.launch(Dispatchers.Main) {
            try {
                val resp = ApiClient.getApi().getSessions()
                if (resp.isSuccessful) {
                    val list = resp.body()?.sessions ?: emptyList()
                    sessionListAdapter?.updateSessions(list)
                    val tvEmpty = sessionPanelContent?.findViewById<TextView>(R.id.tv_session_empty)
                    val rv = sessionPanelContent?.findViewById<androidx.recyclerview.widget.RecyclerView>(R.id.rv_session_list)
                    if (list.isEmpty()) { tvEmpty?.visibility = View.VISIBLE; rv?.visibility = View.GONE }
                    else { tvEmpty?.visibility = View.GONE; rv?.visibility = View.VISIBLE }
                }
            } catch (_: Exception) {}
        }
    }

    private fun loadSessionFromBackend(session: SessionSummary) {
        GlobalScope.launch(Dispatchers.Main) {
            try {
                val resp = ApiClient.getApi().getSessionHistory(session.id)
                if (resp.isSuccessful) {
                    chatFragment?.restoreSession(session.id, resp.body()?.history?.map { it.toChatMessage() } ?: emptyList())
                }
            } catch (_: Exception) {
                Toast.makeText(this@MainActivity, "加载会话失败", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun deleteSessionFromBackend(session: SessionSummary) {
        GlobalScope.launch(Dispatchers.Main) {
            try {
                val resp = ApiClient.getApi().deleteSession(session.id)
                if (resp.isSuccessful) { loadSessionList(); Toast.makeText(this@MainActivity, "已删除", Toast.LENGTH_SHORT).show() }
            } catch (_: Exception) {}
        }
    }

    // ============================================================
    //  语音录制（AudioRecord + 后端阿里云 DashScope ASR，非 Google 服务）
    // ============================================================

    private fun showVoicePanel() {
        voicePanelVisible = true
        binding.tvVoiceStatus.text = "正在录音..."
        binding.tvVoiceResult.visibility = View.INVISIBLE
        binding.tvVoiceResult.text = ""
        binding.voicePanel.visibility = View.VISIBLE
        binding.voicePanel.alpha = 0f
        binding.voicePanel.translationY = 40f
        binding.voicePanel.animate().alpha(1f).translationY(0f).setDuration(350)
            .setInterpolator(AccelerateDecelerateInterpolator()).start()
    }

    private fun hideVoicePanel(onComplete: (() -> Unit)? = null) {
        voicePanelVisible = false
        binding.voicePanel.animate().alpha(0f).translationY(40f).setDuration(250)
            .setInterpolator(AccelerateDecelerateInterpolator()).withEndAction {
                binding.voicePanel.visibility = View.GONE; onComplete?.invoke()
            }.start()
    }

    private fun checkAndStartRecording() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            showVoicePanel()
            startRecording()
        } else {
            permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun startRecording() {
        try {
            val sampleRate = 16000
            val channelConfig = AudioFormat.CHANNEL_IN_MONO
            val audioFormat = AudioFormat.ENCODING_PCM_16BIT
            val minBuf = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat)

            audioRecord = AudioRecord(
                AudioSource.MIC,
                sampleRate,
                channelConfig,
                audioFormat,
                minBuf
            )

            voiceFile = File.createTempFile("voice_", ".pcm", cacheDir)
            val fos = voiceFile!!.outputStream()

            audioRecord!!.startRecording()
            isRecording = true

            recordingThread = Thread {
                val buffer = ByteArray(minBuf)
                try {
                    while (isRecording) {
                        val read = audioRecord?.read(buffer, 0, buffer.size) ?: -1
                        if (read > 0) fos.write(buffer, 0, read)
                    }
                } catch (_: Exception) {}
                try { fos.close() } catch (_: Exception) {}
            }.apply { start() }
        } catch (e: Exception) {
            Toast.makeText(this, "录音启动失败: ${e.message}", Toast.LENGTH_SHORT).show()
            hideVoicePanel()
        }
    }

    private fun stopRecordingAndSend() {
        isRecording = false
        try { recordingThread?.join(1000) } catch (_: Exception) {}
        recordingThread = null
        try { audioRecord?.apply { stop(); release() } } catch (_: Exception) {}
        audioRecord = null

        val pcmFile = voiceFile
        if (pcmFile == null || !pcmFile.exists() || pcmFile.length() == 0L) {
            Toast.makeText(this, "未录制到有效音频", Toast.LENGTH_SHORT).show()
            hideVoicePanel()
            voiceFile = null
            return
        }

        binding.tvVoiceStatus.text = "识别中..."

        // PCM → WAV (fun-asr 需要 WAV 格式)
        val pcmBytes = pcmFile.readBytes()
        val wavFile = File(pcmFile.parent, pcmFile.name.replace(".pcm", ".wav"))
        writeWav(wavFile, pcmBytes, 16000, 1, 16)
        pcmFile.delete()
        voiceFile = null

        val bytes = wavFile.readBytes()
        val name = wavFile.name

        val repo = ChatRepository()
        GlobalScope.launch(Dispatchers.Main) {
            hideVoicePanel()
            val result = kotlinx.coroutines.withContext(Dispatchers.IO) {
                repo.sendAsr(bytes, name)
            }
            result.fold(
                onSuccess = { text ->
                    if (text.isNotEmpty()) {
                        val cur = binding.inputBar.etChatInput.text.toString().trim()
                        val newText = if (cur.isEmpty()) text else "$cur $text"
                        binding.inputBar.etChatInput.setText(newText)
                        binding.inputBar.etChatInput.setSelection(binding.inputBar.etChatInput.text.length)
                    } else {
                        Toast.makeText(this@MainActivity, "未识别到语音内容", Toast.LENGTH_SHORT).show()
                    }
                },
                onFailure = { e ->
                    Toast.makeText(this@MainActivity, "识别失败: ${e.message}", Toast.LENGTH_SHORT).show()
                }
            )
        }
    }

    /** 将 PCM 原始数据写入标准 WAV 文件 */
    private fun writeWav(file: File, pcm: ByteArray, sampleRate: Int, channels: Int, bitsPerSample: Int) {
        val byteRate = sampleRate * channels * bitsPerSample / 8
        val header = java.io.ByteArrayOutputStream().apply {
            write("RIFF".toByteArray())
            write(intToBytes(36 + pcm.size, 4))
            write("WAVE".toByteArray())
            write("fmt ".toByteArray())
            write(intToBytes(16, 4))
            write(intToBytes(1, 2))        // PCM
            write(intToBytes(channels, 2))
            write(intToBytes(sampleRate, 4))
            write(intToBytes(byteRate, 4))
            write(intToBytes(channels * bitsPerSample / 8, 2))
            write(intToBytes(bitsPerSample, 2))
            write("data".toByteArray())
            write(intToBytes(pcm.size, 4))
        }.toByteArray()
        file.outputStream().use { it.write(header); it.write(pcm) }
    }

    private fun intToBytes(value: Int, len: Int): ByteArray {
        return ByteArray(len) { i -> (value shr (i * 8)).toByte() }
    }

    // ============================================================
    //  购物车
    // ============================================================

    private fun openCart() {
        val intent = Intent(this, CartActivity::class.java).apply { putExtra("user_id", getUserId()) }
        startActivity(intent)
        overridePendingTransition(R.anim.slide_in_right, R.anim.slide_out_left)
    }

    private fun getUserId(): String {
        val prefs = getSharedPreferences("rag_session", MODE_PRIVATE)
        prefs.getString("current_sid", null)?.let { return it }
        val cartPrefs = getSharedPreferences("cart_prefs", MODE_PRIVATE)
        cartPrefs.getString("cart_user_id", null)?.let { return it }
        val n = "user_" + System.currentTimeMillis()
        cartPrefs.edit().putString("cart_user_id", n).apply()
        return n
    }

    private fun refreshCartBadge() {}

    /** 清除输入区的图片缩略图预览 */
    private fun clearImagePreview() {
        selectedImageUri = null
        binding.inputBar.llImagePreview.visibility = View.GONE
        binding.inputBar.ivImagePreview.setImageDrawable(null)
    }

    private fun updateThemeIcon() {
        val isDark = AppCompatDelegate.getDefaultNightMode() == AppCompatDelegate.MODE_NIGHT_YES
        binding.btnThemeToggle.setImageResource(
            if (isDark) R.drawable.ic_theme_moon else R.drawable.ic_theme_toggle
        )
    }

    override fun onDestroy() {
        super.onDestroy()
        isRecording = false
        try { audioRecord?.apply { stop(); release() } } catch (_: Exception) {}
        audioRecord = null
    }
}
