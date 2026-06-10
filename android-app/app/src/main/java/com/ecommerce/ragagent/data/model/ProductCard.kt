package com.ecommerce.ragagent.data.model

data class ProductCard(
    val productId: String = "",
    val name: String = "",
    val price: String = "",
    val originalPrice: String = "",
    val imageUrl: String = "",
    val description: String = "",
    val tags: List<String> = emptyList(),
    val rating: Float = 0f,
    val salesCount: String = "",
    val recommendReason: String = "",
    val detailUrl: String = ""
)
