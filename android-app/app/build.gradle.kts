import java.net.Inet4Address
import java.net.NetworkInterface

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

fun getHostIp(): String {
    val virtualNames = listOf("vEthernet", "VirtualBox", "VMware", "Docker", "WSL", "Hyper-V", "Loopback")
    val virtualPrefixes = listOf("192.168.137.", "192.168.56.", "192.168.99.")
    return try {
        NetworkInterface.getNetworkInterfaces().asSequence()
            .filter { iface ->
                val name = iface.displayName ?: iface.name ?: ""
                !iface.isLoopback && iface.isUp &&
                virtualNames.none { name.contains(it, ignoreCase = true) }
            }
            .flatMap { it.inetAddresses.asSequence() }
            .filterIsInstance<Inet4Address>()
            .filter { addr ->
                val ip = addr.hostAddress ?: ""
                !addr.isLoopbackAddress &&
                virtualPrefixes.none { ip.startsWith(it) }
            }
            .firstOrNull()
            ?.hostAddress ?: "127.0.0.1"
    } catch (e: Exception) {
        "127.0.0.1"
    }
}

android {
    namespace = "com.ecommerce.ragagent"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.ecommerce.ragagent"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        buildConfigField("String", "HOST_IP", "\"127.0.0.1\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
    
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }
    
    kotlinOptions {
        jvmTarget = "1.8"
    }
    
    buildFeatures {
        viewBinding = true
        buildConfig = true
    }
}

tasks.register("adbReverse") {
    doLast {
        try {
            val adb = "${android.sdkDirectory}/platform-tools/adb"
            exec {
                commandLine(adb, "reverse", "tcp:8080", "tcp:8080")
            }
            exec {
                commandLine(adb, "reverse", "tcp:9000", "tcp:9000")
            }
            println("✅ ADB reverse: 8080 and 9000 forwarded to device")
        } catch (e: Exception) {
            println("⚠ ADB reverse skipped (no device or adb not available): ${e.message}")
        }
    }
}

afterEvaluate {
    tasks.matching { it.name == "assembleDebug" }.configureEach {
        finalizedBy("adbReverse")
    }
    tasks.matching { it.name.startsWith("install") }.configureEach {
        dependsOn("adbReverse")
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.7.0")
    implementation("androidx.lifecycle:lifecycle-livedata-ktx:2.7.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")
    
    implementation("androidx.navigation:navigation-fragment-ktx:2.7.6")
    implementation("androidx.navigation:navigation-ui-ktx:2.7.6")
    
    implementation("com.squareup.retrofit2:retrofit:2.9.0")
    implementation("com.squareup.retrofit2:converter-gson:2.9.0")
    implementation("com.squareup.retrofit2:converter-scalars:2.9.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")
    implementation("com.squareup.okhttp3:okhttp-sse:4.12.0")
    
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.7.3")
    
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
    
    implementation("com.google.code.gson:gson:2.10.1")

    implementation("com.github.bumptech.glide:glide:4.16.0")
    
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.1.5")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.5.1")
}
