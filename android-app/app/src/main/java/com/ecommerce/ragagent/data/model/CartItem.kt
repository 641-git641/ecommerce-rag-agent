package com.ecommerce.ragagent.data.model

data class CartItem(
    val product_id: String = "",
    val product_name: String = "",
    val name: String = "",
    val price: Double = 0.0,
    val quantity: Int = 1,
    val selected: Boolean = true
) {
    val displayName: String get() = product_name.ifBlank { name }
}

data class CartListResponse(
    val cart: CartData? = null
)

data class CartData(
    val items: List<CartItem> = emptyList(),
    val count: Int = 0
)

data class OrderPreviewResponse(
    val preview: OrderPreview? = null
)

data class OrderPreview(
    val items: List<CartItem> = emptyList(),
    val total: Double = 0.0
)

data class OrderConfirmRequest(
    val address: String = "",
    val contact_name: String = "",
    val contact_phone: String = ""
)

data class OrderConfirmResponse(
    val order: OrderInfo? = null
)

data class OrderInfo(
    val order_no: String = "",
    val total_amount: Double = 0.0
)
