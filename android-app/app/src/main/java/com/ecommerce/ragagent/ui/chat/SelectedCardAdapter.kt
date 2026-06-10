package com.ecommerce.ragagent.ui.chat

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.ecommerce.ragagent.R
import com.ecommerce.ragagent.data.model.ProductCard

/**
 * 输入区上方已选商品卡片的 Adapter（chip 样式，可删除）
 */
class SelectedCardAdapter(
    private val cards: MutableList<ProductCard> = mutableListOf()
) : RecyclerView.Adapter<SelectedCardAdapter.ViewHolder>() {

    var onRemoveClickListener: ((Int) -> Unit)? = null

    fun updateCards(newCards: List<ProductCard>) {
        cards.clear()
        cards.addAll(newCards)
        notifyDataSetChanged()
    }

    fun addCard(card: ProductCard): Boolean {
        // 去重
        if (cards.any { it.productId == card.productId }) return false
        if (cards.size >= 2) return false // 最多2张
        cards.add(card)
        notifyItemInserted(cards.size - 1)
        return true
    }

    fun removeCard(position: Int) {
        if (position in 0 until cards.size) {
            cards.removeAt(position)
            notifyItemRemoved(position)
        }
    }

    fun getAllCards(): List<ProductCard> = cards.toList()

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_selected_card_chip, parent, false)
        return ViewHolder(view)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        holder.bind(cards[position], position)
    }

    override fun getItemCount(): Int = cards.size

    inner class ViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val tvName: TextView = itemView.findViewById(R.id.tv_selected_card_name)
        private val ivRemove: ImageView = itemView.findViewById(R.id.iv_remove_card)

        fun bind(card: ProductCard, position: Int) {
            tvName.text = card.name
            ivRemove.setOnClickListener {
                onRemoveClickListener?.invoke(position)
            }
        }
    }
}
