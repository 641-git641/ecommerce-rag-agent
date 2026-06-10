package com.ecommerce.ragagent.data.api

import android.os.Build
import com.ecommerce.ragagent.BuildConfig
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object ApiClient {
    private const val GO_SERVER_PORT = 8080
    private const val PYTHON_RAG_PORT = 9000

    private val EMULATOR_HOST = "http://10.0.2.2"
    private val DEVICE_HOST = "http://${BuildConfig.HOST_IP}"

    private var baseUrl: String = buildDefaultBaseUrl()
    var pythonRagUrl: String = buildDefaultPythonRagUrl()

    private val loggingInterceptor = HttpLoggingInterceptor().apply {
        level = HttpLoggingInterceptor.Level.BODY
    }

    private val okHttpClient = OkHttpClient.Builder()
        .addInterceptor(loggingInterceptor)
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    private var retrofit: Retrofit? = null
    private var api: GoServerApi? = null

    fun isEmulator(): Boolean {
        val fingerprint = Build.FINGERPRINT
        val model = Build.MODEL
        val manufacturer = Build.MANUFACTURER
        val hardware = Build.HARDWARE
        val product = Build.PRODUCT
        val brand = Build.BRAND
        val device = Build.DEVICE

        return (fingerprint.startsWith("generic") ||
                fingerprint.startsWith("unknown") ||
                model.contains("google_sdk") ||
                model.contains("Emulator") ||
                model.contains("Android SDK built for x86") ||
                manufacturer.contains("Genymotion") ||
                brand.startsWith("generic") && device.startsWith("generic") ||
                hardware.contains("goldfish") ||
                hardware.contains("ranchu") ||
                product.contains("sdk_google") ||
                product.contains("google_sdk") ||
                product.contains("sdk") ||
                product.contains("sdk_x86") ||
                product.contains("vbox86p") ||
                product.contains("emulator") ||
                product.contains("simulator"))
    }

    private fun resolveHost(): String {
        return if (isEmulator()) EMULATOR_HOST else DEVICE_HOST
    }

    private fun buildDefaultBaseUrl(): String {
        return "${resolveHost()}:$GO_SERVER_PORT/"
    }

    private fun buildDefaultPythonRagUrl(): String {
        return "${resolveHost()}:$PYTHON_RAG_PORT/"
    }

    fun init(serverUrl: String? = null) {
        baseUrl = serverUrl ?: buildDefaultBaseUrl()
        retrofit = Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(okHttpClient)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
        api = retrofit!!.create(GoServerApi::class.java)
    }
    
    fun getApi(): GoServerApi {
        if (api == null) {
            init()
        }
        return api!!
    }
    
    fun getBaseUrl(): String = baseUrl
    
    fun getOkHttpClient(): OkHttpClient = okHttpClient
    
    fun createSSEClientFactory(): SSEClientFactory {
        return SSEClientFactory(baseUrl)
    }
}
