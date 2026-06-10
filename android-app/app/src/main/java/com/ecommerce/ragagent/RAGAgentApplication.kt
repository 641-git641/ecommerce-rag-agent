package com.ecommerce.ragagent

import android.app.Application
import com.ecommerce.ragagent.data.api.ApiClient

class RAGAgentApplication : Application() {
    
    override fun onCreate() {
        super.onCreate()
        ApiClient.init()
    }
}
