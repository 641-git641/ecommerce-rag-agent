package com.ecommerce.ragagent.ui.cart

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.bumptech.glide.Glide
import com.ecommerce.ragagent.R
import com.ecommerce.ragagent.data.api.ApiClient
import com.ecommerce.ragagent.data.model.*
import com.google.gson.Gson
import kotlinx.coroutines.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody

class CartActivity : AppCompatActivity() {

    private val gson = Gson()
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private lateinit var userId: String
    private var cartItems = mutableListOf<CartItem>()

    private lateinit var rvItems: RecyclerView
    private lateinit var adapter: CartAdapter
    private lateinit var emptyState: LinearLayout
    private lateinit var footerBar: LinearLayout
    private lateinit var tvBadge: TextView
    private lateinit var cbSelectAll: CheckBox
    private lateinit var tvTotalPrice: TextView
    private lateinit var modalOverlay: View
    private lateinit var orderModal: View
    private lateinit var orderSuccess: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_cart)

        // 获得 userId（与 MainActivity 的 getUserId() 逻辑一致）
        userId = intent.getStringExtra("user_id")
            ?: getSharedPreferences("rag_session", MODE_PRIVATE)
                .getString("current_sid", null)
            ?: getSharedPreferences("cart_prefs", MODE_PRIVATE)
                .getString("cart_user_id", null)
            ?: "user_" + System.currentTimeMillis()

        android.util.Log.d("CartActivity", "userId=$userId")
        initViews()
        loadCart()
    }

    override fun onResume() {
        super.onResume()
        loadCart()
    }

    private fun initViews() {
        rvItems = findViewById(R.id.rv_cart_items)
        emptyState = findViewById(R.id.empty_state)
        footerBar = findViewById(R.id.footer_bar)
        tvBadge = findViewById(R.id.tv_cart_badge)
        cbSelectAll = findViewById(R.id.cb_select_all)
        tvTotalPrice = findViewById(R.id.tv_total_price)
        modalOverlay = findViewById(R.id.modal_overlay)
        orderModal = findViewById(R.id.order_modal)
        orderSuccess = findViewById(R.id.order_success)

        adapter = CartAdapter(cartItems, ::onToggle, ::onQtyChange, ::onRemove)
        rvItems.layoutManager = LinearLayoutManager(this)
        rvItems.adapter = adapter

        findViewById<ImageButton>(R.id.btn_back).setOnClickListener {
            finish()
            overridePendingTransition(R.anim.slide_in_left, R.anim.slide_out_right)
        }
        cbSelectAll.setOnCheckedChangeListener { _, isChecked -> toggleSelectAll(isChecked) }
        findViewById<View>(R.id.btn_checkout).setOnClickListener { startOrder() }
        modalOverlay.setOnClickListener { closeOrder() }
        findViewById<View>(R.id.btn_confirm_order).setOnClickListener { confirmOrder() }
        findViewById<View>(R.id.btn_cancel_order).setOnClickListener { closeOrder() }
    }

    private fun loadCart() {
        scope.launch {
            try {
                val url = "${ApiClient.getBaseUrl()}api/cart/list?user_id=$userId"
                android.util.Log.d("CartActivity", "loadCart URL: $url")
                val response = withContext(Dispatchers.IO) {
                    val resp = ApiClient.getOkHttpClient().newCall(
                        okhttp3.Request.Builder().url(url).get().build()
                    ).execute()
                    resp.body?.string() ?: "{}"
                }
                android.util.Log.d("CartActivity", "loadCart response: $response")
                val data = gson.fromJson(response, CartListResponse::class.java)
                android.util.Log.d("CartActivity", "loadCart parsed: cart=${data.cart}, items=${data.cart?.items?.size}")
                cartItems.clear()
                data.cart?.items?.let { cartItems.addAll(it) }
                renderCart()
            } catch (e: Exception) {
                android.util.Log.e("CartActivity", "loadCart failed", e)
                Toast.makeText(this@CartActivity, "加载失败: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun renderCart() {
        tvBadge.text = cartItems.size.toString()
        if (cartItems.isEmpty()) {
            emptyState.visibility = View.VISIBLE
            rvItems.visibility = View.GONE
            footerBar.visibility = View.GONE
        } else {
            emptyState.visibility = View.GONE
            rvItems.visibility = View.VISIBLE
            footerBar.visibility = View.VISIBLE
            updateTotal()
            adapter.notifyDataSetChanged()
        }
    }

    private fun updateTotal() {
        val selItems = cartItems.filter { it.selected }
        val total = selItems.sumOf { it.price * it.quantity }
        tvTotalPrice.text = "%.0f".format(total)
        cbSelectAll.isChecked = cartItems.isNotEmpty() && cartItems.all { it.selected }
    }

    private fun onToggle(item: CartItem) {
        scope.launch {
            withContext(Dispatchers.IO) {
                val body = gson.toJson(mapOf("product_id" to item.product_id))
                val url = "${ApiClient.getBaseUrl()}api/cart/toggle-select?user_id=$userId"
                ApiClient.getOkHttpClient().newCall(
                    okhttp3.Request.Builder().url(url)
                        .post(body.toRequestBody("application/json".toMediaType()))
                        .build()
                ).execute()
            }
            loadCart()
        }
    }

    private fun onQtyChange(item: CartItem, newQty: Int) {
        if (newQty < 1) return
        scope.launch {
            withContext(Dispatchers.IO) {
                val body = gson.toJson(mapOf("product_id" to item.product_id, "quantity" to newQty))
                val url = "${ApiClient.getBaseUrl()}api/cart/update-qty?user_id=$userId"
                ApiClient.getOkHttpClient().newCall(
                    okhttp3.Request.Builder().url(url)
                        .put(body.toRequestBody("application/json".toMediaType()))
                        .build()
                ).execute()
            }
            loadCart()
        }
    }

    private fun onRemove(item: CartItem) {
        scope.launch {
            withContext(Dispatchers.IO) {
                val url = "${ApiClient.getBaseUrl()}api/cart/remove?user_id=$userId&product_id=${item.product_id}"
                ApiClient.getOkHttpClient().newCall(
                    okhttp3.Request.Builder().url(url).delete().build()
                ).execute()
            }
            Toast.makeText(this@CartActivity, R.string.cart_removed, Toast.LENGTH_SHORT).show()
            loadCart()
        }
    }

    private fun toggleSelectAll(checkAll: Boolean) {
        scope.launch {
            cartItems.forEach { item ->
                if (item.selected != checkAll) {
                    withContext(Dispatchers.IO) {
                        val body = gson.toJson(mapOf("product_id" to item.product_id))
                        val url = "${ApiClient.getBaseUrl()}api/cart/toggle-select?user_id=$userId"
                        ApiClient.getOkHttpClient().newCall(
                            okhttp3.Request.Builder().url(url)
                                .post(body.toRequestBody("application/json".toMediaType()))
                                .build()
                        ).execute()
                    }
                }
            }
            loadCart()
        }
    }

    private fun startOrder() {
        val selItems = cartItems.filter { it.selected }
        if (selItems.isEmpty()) {
            Toast.makeText(this, R.string.cart_select_items, Toast.LENGTH_SHORT).show()
            return
        }
        scope.launch {
            try {
                val response = withContext(Dispatchers.IO) {
                    val url = "${ApiClient.getBaseUrl()}api/cart/order/preview?user_id=$userId"
                    val resp = ApiClient.getOkHttpClient().newCall(
                        okhttp3.Request.Builder().url(url).get().build()
                    ).execute()
                    resp.body?.string() ?: "{}"
                }
                val preview = gson.fromJson(response, OrderPreviewResponse::class.java)
                val items = preview.preview?.items ?: emptyList()
                val total = preview.preview?.total ?: 0.0

                val orderItemsList = findViewById<LinearLayout>(R.id.order_items_list)
                orderItemsList.removeAllViews()
                items.forEach { i ->
                    val row = LinearLayout(this@CartActivity).apply {
                        orientation = LinearLayout.HORIZONTAL
                        layoutParams = LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT
                        )
                        setPadding(0, 6, 0, 6)
                    }
                    val nameTv = TextView(this@CartActivity).apply {
                        text = "${i.displayName} x${i.quantity}"
                        textSize = 13f
                        setTextColor(getColor(R.color.text_primary))
                    }
                    val priceTv = TextView(this@CartActivity).apply {
                        text = "%.0f".format((i.price) * (i.quantity))
                        textSize = 13f
                        setTextColor(getColor(R.color.primary))
                    }
                    row.addView(nameTv)
                    row.addView(View(this@CartActivity).apply {
                        layoutParams = LinearLayout.LayoutParams(0, 1, 1f)
                    })
                    row.addView(priceTv)
                    orderItemsList.addView(row)
                }
                findViewById<TextView>(R.id.tv_modal_total).text = "合计: %.0f".format(total)
                modalOverlay.visibility = View.VISIBLE
                orderModal.visibility = View.VISIBLE
            } catch (e: Exception) {
                Toast.makeText(this@CartActivity, "预览失败: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun closeOrder() {
        modalOverlay.visibility = View.GONE
        orderModal.visibility = View.GONE
    }

    private fun confirmOrder() {
        val name = findViewById<EditText>(R.id.et_contact_name).text.toString().trim()
        val phone = findViewById<EditText>(R.id.et_contact_phone).text.toString().trim()
        val addr = findViewById<EditText>(R.id.et_address).text.toString().trim()
        if (name.isEmpty() || addr.isEmpty()) {
            Toast.makeText(this, R.string.cart_fill_info, Toast.LENGTH_SHORT).show()
            return
        }
        scope.launch {
            try {
                val body = gson.toJson(mapOf(
                    "address" to addr,
                    "contact_name" to name,
                    "contact_phone" to phone
                ))
                val response = withContext(Dispatchers.IO) {
                    val url = "${ApiClient.getBaseUrl()}api/cart/order/confirm?user_id=$userId"
                    val resp = ApiClient.getOkHttpClient().newCall(
                        okhttp3.Request.Builder().url(url)
                            .post(body.toRequestBody("application/json".toMediaType()))
                            .build()
                    ).execute()
                    resp.body?.string() ?: "{}"
                }
                val result = gson.fromJson(response, OrderConfirmResponse::class.java)
                closeOrder()

                // 显示成功页
                emptyState.visibility = View.GONE
                rvItems.visibility = View.GONE
                footerBar.visibility = View.GONE
                orderSuccess.visibility = View.VISIBLE

                val order = result.order
                findViewById<TextView>(R.id.tv_order_no).text =
                    getString(R.string.cart_order_no) + (order?.order_no ?: "-")
                findViewById<TextView>(R.id.tv_order_amount).text =
                    "支付金额: ${"%.0f".format(order?.total_amount ?: 0.0)}"
                findViewById<TextView>(R.id.tv_order_address).text =
                    "收货地址: $addr"
            } catch (e: Exception) {
                Toast.makeText(this@CartActivity, "${getString(R.string.cart_order_failed)}: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}

// ── CartAdapter ──

class CartAdapter(
    private val items: MutableList<CartItem>,
    private val onToggle: (CartItem) -> Unit,
    private val onQtyChange: (CartItem, Int) -> Unit,
    private val onRemove: (CartItem) -> Unit
) : RecyclerView.Adapter<CartAdapter.ViewHolder>() {

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_cart_product, parent, false)
        return ViewHolder(view)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        holder.bind(items[position])
    }

    override fun getItemCount(): Int = items.size

    inner class ViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val ivProduct: ImageView = itemView.findViewById(R.id.iv_product)
        private val cbSelect: CheckBox = itemView.findViewById(R.id.cb_select)
        private val tvName: TextView = itemView.findViewById(R.id.tv_name)
        private val tvPrice: TextView = itemView.findViewById(R.id.tv_price)
        private val tvQty: TextView = itemView.findViewById(R.id.tv_qty)
        private val btnMinus: View = itemView.findViewById(R.id.btn_minus)
        private val btnPlus: View = itemView.findViewById(R.id.btn_plus)
        private val btnDelete: View = itemView.findViewById(R.id.btn_delete)

        fun bind(item: CartItem) {
            itemView.alpha = if (item.selected) 1f else 0.55f
            tvName.text = item.displayName.ifBlank { "商品" }
            tvPrice.text = "%.0f".format(item.price)
            tvQty.text = item.quantity.toString()
            cbSelect.isChecked = item.selected

            val imgUrl = getProductImage(item.product_id)
            if (imgUrl.isNotEmpty()) {
                val fullUrl = if (imgUrl.startsWith("http")) imgUrl
                else ApiClient.getBaseUrl().trimEnd('/') + imgUrl
                Glide.with(itemView.context)
                    .load(fullUrl)
                    .placeholder(android.R.drawable.ic_menu_gallery)
                    .error(android.R.drawable.ic_menu_gallery)
                    .into(ivProduct)
            } else {
                ivProduct.setImageResource(android.R.drawable.ic_menu_gallery)
            }

            cbSelect.setOnCheckedChangeListener(null)
            cbSelect.setOnCheckedChangeListener { _, _ -> onToggle(item) }
            btnMinus.setOnClickListener { onQtyChange(item, item.quantity - 1) }
            btnPlus.setOnClickListener { onQtyChange(item, item.quantity + 1) }
            btnDelete.setOnClickListener { onRemove(item) }
        }

        private fun getProductImage(pid: String): String {
            if (pid.isEmpty()) return ""
            return "/product-images/${pid}_live.jpg"
        }
    }
}
