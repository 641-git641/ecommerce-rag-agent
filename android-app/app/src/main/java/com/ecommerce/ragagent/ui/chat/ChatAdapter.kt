package com.ecommerce.ragagent.ui.chat

import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.speech.tts.TextToSpeech
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.TextView
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.ecommerce.ragagent.R
import com.ecommerce.ragagent.data.api.ApiClient
import com.ecommerce.ragagent.data.model.ChatMessage
import com.ecommerce.ragagent.data.model.ProductCard

class ChatAdapter : ListAdapter<ChatMessage, ChatAdapter.ChatViewHolder>(ChatDiffCallback()) {

    var onProductCardClickListener: ((ProductCard) -> Unit)? = null

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ChatViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_chat_message, parent, false)
        return ChatViewHolder(view)
    }

    override fun onBindViewHolder(holder: ChatViewHolder, position: Int) {
        holder.bind(getItem(position), onProductCardClickListener)
    }

    class ChatViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val tvContent: TextView = itemView.findViewById(R.id.tv_content)
        private val tvSources: TextView = itemView.findViewById(R.id.tv_sources)
        private val tvMetrics: TextView = itemView.findViewById(R.id.tv_metrics)
        private val tvAgentInfo: TextView = itemView.findViewById(R.id.tv_agent_info)
        private val messageCard: View = itemView.findViewById(R.id.message_card)
        private val cardIsland: View = itemView.findViewById(R.id.card_island)
        private val rvProductCards: RecyclerView = itemView.findViewById(R.id.rv_product_cards)
        private val rvUserSelectedCards: RecyclerView = itemView.findViewById(R.id.rv_user_selected_cards)
        private val btnVoicePlay: ImageButton = itemView.findViewById(R.id.btn_voice_play)
        private val hsvAttachedImages: HorizontalScrollView = itemView.findViewById(R.id.hsv_attached_images)
        private val llAttachedImages: LinearLayout = itemView.findViewById(R.id.ll_attached_images)
        private val ivAvatarUser: ImageView = itemView.findViewById(R.id.iv_avatar_user)
        private val ivAvatarAssistant: ImageView = itemView.findViewById(R.id.iv_avatar_assistant)

        private var mediaPlayer: MediaPlayer? = null

        fun bind(message: ChatMessage, clickListener: ((ProductCard) -> Unit)?) {
            tvContent.text = message.content
            tvContent.visibility = if (message.content.isEmpty()) View.GONE else View.VISIBLE

            if (message.isUser) {
                ivAvatarUser.visibility = View.VISIBLE
                ivAvatarAssistant.visibility = View.GONE
                messageCard.setBackgroundResource(R.drawable.bg_chat_user)
                btnVoicePlay.visibility = View.GONE

                // 渲染用户附件图片（以图搜图）
                bindAttachedImages(message.attachedImages)
                // 渲染用户选中的商品卡片
                bindUserSelectedCards(message.selectedCards)
                // 用户消息不显示卡片浮岛
                cardIsland.visibility = View.GONE
            } else {
                ivAvatarUser.visibility = View.GONE
                ivAvatarAssistant.visibility = View.VISIBLE
                messageCard.setBackgroundResource(R.drawable.bg_chat_assistant)
                // 助手消息不显示用户侧的图片和卡片
                hsvAttachedImages.visibility = View.GONE
                rvUserSelectedCards.visibility = View.GONE

                if (!message.voiceUrl.isNullOrEmpty()) {
                    // 有服务端合成的音频 → 直接播放
                    btnVoicePlay.visibility = View.VISIBLE
                    btnVoicePlay.setOnClickListener {
                        playVoice(message.voiceUrl)
                    }
                } else if (!message.voiceText.isNullOrEmpty()) {
                    // 无音频但有播报文本 → 使用 Android TTS
                    btnVoicePlay.visibility = View.VISIBLE
                    btnVoicePlay.setOnClickListener {
                        speakText(itemView.context, message.voiceText)
                    }
                } else {
                    btnVoicePlay.visibility = View.GONE
                }

                // 助手侧的商品卡片（可点击添加到输入区）
                if (!message.productCards.isNullOrEmpty()) {
                    cardIsland.visibility = View.VISIBLE
                    rvProductCards.layoutManager = LinearLayoutManager(
                        itemView.context,
                        LinearLayoutManager.HORIZONTAL,
                        false
                    )
                    val productCardAdapter = ProductCardAdapter(message.productCards)
                    productCardAdapter.onItemClickListener = clickListener
                    rvProductCards.adapter = productCardAdapter
                } else {
                    cardIsland.visibility = View.GONE
                }
            }

            if (!message.sources.isNullOrEmpty()) {
                tvSources.visibility = View.VISIBLE
                tvSources.text = "来源:\n" + message.sources.joinToString("\n")
            } else {
                tvSources.visibility = View.GONE
            }

            message.metrics?.let { metrics ->
                tvMetrics.visibility = View.VISIBLE
                tvMetrics.text = "检索: ${metrics.searchTime}s | 总耗时: ${metrics.totalTime}s"
            } ?: run {
                tvMetrics.visibility = View.GONE
            }

            if (!message.agentSteps.isNullOrEmpty()) {
                tvAgentInfo.visibility = View.VISIBLE
                val toolsUsed = message.agentSteps.mapNotNull { it.toolName }.distinct().joinToString(", ")
                tvAgentInfo.text = "使用工具: $toolsUsed"
            } else {
                tvAgentInfo.visibility = View.GONE
            }
        }

        private fun bindAttachedImages(imageUris: List<String>) {
            if (imageUris.isEmpty()) {
                hsvAttachedImages.visibility = View.GONE
                return
            }
            hsvAttachedImages.visibility = View.VISIBLE
            llAttachedImages.removeAllViews()

            val imageSize = (itemView.context.resources.displayMetrics.density * 120).toInt()
            for (uri in imageUris) {
                val imageView = ImageView(itemView.context).apply {
                    layoutParams = LinearLayout.LayoutParams(imageSize, imageSize).apply {
                        marginEnd = 8
                    }
                    scaleType = ImageView.ScaleType.CENTER_CROP
                }
                Glide.with(itemView.context)
                    .load(uri)
                    .placeholder(android.R.drawable.ic_menu_gallery)
                    .error(android.R.drawable.ic_menu_gallery)
                    .into(imageView)
                llAttachedImages.addView(imageView)
            }
        }

        private fun bindUserSelectedCards(selectedCards: List<ProductCard>) {
            if (selectedCards.isEmpty()) {
                rvUserSelectedCards.visibility = View.GONE
                return
            }
            rvUserSelectedCards.visibility = View.VISIBLE
            rvUserSelectedCards.layoutManager = LinearLayoutManager(
                itemView.context,
                LinearLayoutManager.HORIZONTAL,
                false
            )
            // 用户侧的选中卡片不可点击（已经在消息中，只是展示）
            val adapter = ProductCardAdapter(selectedCards)
            adapter.onItemClickListener = null
            rvUserSelectedCards.adapter = adapter
        }

        private fun playVoice(voiceUrl: String) {
            releasePlayer()

            try {
                // 走 Go 网关代理 /voice/playback/* → Python:9000
                val fullUrl = if (voiceUrl.startsWith("http")) {
                    voiceUrl
                } else {
                    ApiClient.getBaseUrl().trimEnd('/') + "/" + voiceUrl.trimStart('/')
                }
                mediaPlayer = MediaPlayer().apply {
                    setAudioAttributes(
                        AudioAttributes.Builder()
                            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                            .setUsage(AudioAttributes.USAGE_MEDIA)
                            .build()
                    )
                    setDataSource(fullUrl)
                    setOnPreparedListener { start() }
                    setOnErrorListener { _, _, _ ->
                        releasePlayer()
                        true
                    }
                    setOnCompletionListener { releasePlayer() }
                    prepareAsync()
                }
            } catch (e: Exception) {
                e.printStackTrace()
            }
        }

        private fun releasePlayer() {
            mediaPlayer?.apply {
                if (isPlaying) stop()
                reset()
                release()
            }
            mediaPlayer = null
        }

        companion object {
            private var ttsInstance: TextToSpeech? = null

            fun speakText(context: Context, text: String) {
                if (ttsInstance == null) {
                    ttsInstance = TextToSpeech(context) { status ->
                        if (status == TextToSpeech.SUCCESS) {
                            ttsInstance?.language = java.util.Locale.CHINESE
                            ttsInstance?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "chat_tts")
                        }
                    }
                } else {
                    ttsInstance?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "chat_tts")
                }
            }
        }
    }

    class ProductCardAdapter(
        private val cards: List<ProductCard>
    ) : RecyclerView.Adapter<ProductCardAdapter.ProductCardViewHolder>() {

        var onItemClickListener: ((ProductCard) -> Unit)? = null
        private var cardWidthPx: Int = -1

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ProductCardViewHolder {
            // 根据卡片数量动态计算宽度，减少右侧空隙
            if (cardWidthPx < 0) {
                val density = parent.context.resources.displayMetrics.density
                val screenWidth = parent.context.resources.displayMetrics.widthPixels
                val availWidth = screenWidth - (28f * density * 2).toInt() // 减去两侧头像
                cardWidthPx = when {
                    cards.size == 1 -> (availWidth * 0.43).toInt()
                    cards.size == 2 -> (availWidth * 0.43).toInt()
                    cards.size == 3 -> (availWidth * 0.28).toInt()
                    else -> (140f * density).toInt() // 4+ 卡片保持可滚动
                }
            }
            val view = LayoutInflater.from(parent.context)
                .inflate(R.layout.item_product_card, parent, false)
            view.layoutParams.width = cardWidthPx
            // 图片高度按比例缩放（原 100dp / 120dp ≈ 0.83）
            val ivImage = view.findViewById<ImageView>(R.id.iv_product_image)
            val imageHeight = (cardWidthPx * 0.75).toInt()
            ivImage.layoutParams.height = imageHeight
            return ProductCardViewHolder(view)
        }

        override fun onBindViewHolder(holder: ProductCardViewHolder, position: Int) {
            holder.bind(cards[position], onItemClickListener)
        }

        override fun getItemCount(): Int = cards.size

        class ProductCardViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
            private val ivImage: ImageView = itemView.findViewById(R.id.iv_product_image)
            private val tvName: TextView = itemView.findViewById(R.id.tv_product_name)
            private val tvDescription: TextView = itemView.findViewById(R.id.tv_product_description)
            private val tvPrice: TextView = itemView.findViewById(R.id.tv_product_price)
            private val tvOriginalPrice: TextView = itemView.findViewById(R.id.tv_product_original_price)
            private val tvRating: TextView = itemView.findViewById(R.id.tv_product_rating)
            private val tvRecommendReason: TextView = itemView.findViewById(R.id.tv_recommend_reason)

            fun bind(card: ProductCard, clickListener: ((ProductCard) -> Unit)?) {
                tvName.text = card.name
                tvDescription.visibility = View.GONE
                tvPrice.text = formatPrice(card.price)
                tvOriginalPrice.visibility = View.GONE
                tvRating.visibility = View.GONE
                tvRecommendReason.text = if (card.recommendReason.isNotEmpty()) card.recommendReason else ""

                if (card.imageUrl.isNotEmpty()) {
                    val fullImageUrl = if (card.imageUrl.startsWith("http")) {
                        card.imageUrl
                    } else {
                        // 走 Go 网关代理 /product-images/* → Python:9000
                        ApiClient.getBaseUrl().trimEnd('/') + "/" + card.imageUrl.trimStart('/')
                    }
                    Glide.with(itemView.context)
                        .load(fullImageUrl)
                        .placeholder(android.R.drawable.ic_menu_gallery)
                        .error(android.R.drawable.ic_menu_gallery)
                        .into(ivImage)
                } else {
                    ivImage.setImageResource(android.R.drawable.ic_menu_gallery)
                }

                itemView.setOnClickListener {
                    clickListener?.invoke(card)
                }
            }

            private fun formatPrice(price: String): String {
                val num = price.replace("¥", "").trim().toDoubleOrNull()
                return if (num != null && num > 0) "¥%.2f".format(num) else if (price.isNotBlank()) price else "--"
            }
        }
    }

    class ChatDiffCallback : DiffUtil.ItemCallback<ChatMessage>() {
        override fun areItemsTheSame(oldItem: ChatMessage, newItem: ChatMessage): Boolean {
            return oldItem.timestamp == newItem.timestamp
        }

        override fun areContentsTheSame(oldItem: ChatMessage, newItem: ChatMessage): Boolean {
            return oldItem == newItem
        }
    }
}
