/*
 * ESP32-S3 Voice Control Satellite Firmware (wake_word_test target)
 * Integrated with ESP-SR / WakeNet, SPH0645 MEMS Microphone, MAX98357A I2S Amplifier,
 * Dual WS2812 & Discrete RGB LED State Indication, Wi-Fi Configuration, NVS identification, and WebSocket connection.
 */

#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_system.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "driver/i2s_std.h"
#include "driver/gpio.h"
#include "esp_websocket_client.h"
#include "esp_http_server.h"
#include "cJSON.h"
#include "led_strip.h"

#if CONFIG_APP_USE_ESP_SR
#include "esp_wn_iface.h"
#include "esp_wn_models.h"
#include "esp_afe_sr_iface.h"
#include "esp_afe_sr_models.h"
#include "model_path.h"

static const esp_afe_sr_iface_t *g_afe_handle = NULL;
static esp_afe_sr_data_t *g_afe_data = NULL;
static esp_wn_iface_t *g_wakenet = NULL;
static model_iface_data_t *g_wn_model_data = NULL;
static int g_wn_chunksize = 512;
#endif

static const char *TAG = "WAKE_WORD_SATELLITE";

// ============================================================================
// 1. Wi-Fi & Server Connection Settings (Provisioning or NVS Configured)
// ============================================================================
static char g_wifi_ssid[32]   = "";
static char g_wifi_pass[64]   = "";
static char g_server_uri[128] = "";
static char g_device_id[64]   = "";
static char g_area_id[64]     = "";

static bool g_is_provisioned  = false;

static float g_mic_gain         = 1.0f;
static float g_speaker_volume   = 1.0f;
static int   g_led_brightness   = 255;
static float g_vad_multiplier   = 3.5f;

// Hardware & DSP Toggles and Parameters
static bool  g_dc_removal_enabled     = true;
static bool  g_noise_tracking_enabled = true;
static bool  g_mic_gain_enabled       = true;
static bool  g_speaker_volume_enabled = true;

static int   g_wakenet_threshold      = 0;
static int   g_mic_shift              = 14;
static int   g_stream_max_ms          = 10000;
static int   g_stream_silence_end_ms  = 1500;
static int   g_mic_slot               = -1; // -1: Auto, 0: Left, 1: Right

// Hardware Pinout Configuration (ESP32-S3)
#define I2S_MIC_SCK_PIN   GPIO_NUM_16
#define I2S_MIC_WS_PIN    GPIO_NUM_15
#define I2S_MIC_SD_PIN    GPIO_NUM_17

#define I2S_SPK_BCLK_PIN  GPIO_NUM_7
#define I2S_SPK_LRCK_PIN  GPIO_NUM_8
#define I2S_SPK_DOUT_PIN  GPIO_NUM_18

// Configurable LED GPIO Pins
#define WS2812_GPIO_PIN GPIO_NUM_48 // Built-in WS2812 RGB LED on ESP32-S3 DevKit
#define LED_RED_PIN     GPIO_NUM_38
#define LED_GREEN_PIN   GPIO_NUM_39
#define LED_BLUE_PIN    GPIO_NUM_40

static led_strip_handle_t g_led_strip = NULL;

typedef enum {
    STATE_IDLE = 0,
    STATE_INIT,
    STATE_STREAMING,
    STATE_STOP,
    STATE_PLAYBACK,
    STATE_DEBUG_STREAMING
} satellite_state_t;

typedef enum {
    LED_MODE_PROVISIONING = 0, // Blinking YELLOW / PURPLE (Initial setup mode)
    LED_MODE_CONNECTING,       // Blinking RED (connecting to Wi-Fi/WS)
    LED_MODE_IDLE,             // Blinking GREEN (idle waiting for wake word)
    LED_MODE_ARBITRATING,      // Fast Blinking BLUE (wake word detected, awaiting server arbitration)
    LED_MODE_STREAMING         // Solid BLUE (winner granted streaming audio to server)
} led_mode_t;

static volatile satellite_state_t g_current_state = STATE_IDLE;
static volatile led_mode_t g_led_mode = LED_MODE_CONNECTING;
static volatile uint32_t s_cooldown_counter = 0; // Cooldown frames (in 100ms units) after rejection or streaming completion
static esp_websocket_client_handle_t g_ws_client = NULL;

static i2s_chan_handle_t rx_chan = NULL;
static i2s_chan_handle_t tx_chan = NULL;

/* Initialize LED Hardware (WS2812 RMT Strip & Discrete GPIOs) */
static void init_leds(void) {
    // 1. Discrete GPIOs
    gpio_reset_pin(LED_RED_PIN);
    gpio_reset_pin(LED_GREEN_PIN);
    gpio_reset_pin(LED_BLUE_PIN);
    gpio_set_direction(LED_RED_PIN, GPIO_MODE_OUTPUT);
    gpio_set_direction(LED_GREEN_PIN, GPIO_MODE_OUTPUT);
    gpio_set_direction(LED_BLUE_PIN, GPIO_MODE_OUTPUT);

    // 2. WS2812 Addressable RGB LED
    led_strip_config_t strip_config = {
        .strip_gpio_num = WS2812_GPIO_PIN,
        .max_leds = 1,
    };
    led_strip_rmt_config_t rmt_config = {
        .resolution_hz = 10 * 1000 * 1000,
    };
    esp_err_t err = led_strip_new_rmt_device(&strip_config, &rmt_config, &g_led_strip);
    if (err == ESP_OK && g_led_strip) {
        led_strip_clear(g_led_strip);
        ESP_LOGI(TAG, "WS2812 RGB LED initialized on GPIO %d", WS2812_GPIO_PIN);
    } else {
        ESP_LOGW(TAG, "WS2812 initialization bypassed (using discrete GPIOs)");
    }
}

/* Set RGB Color (0-255 brightness per channel) */
static void set_rgb(uint8_t red, uint8_t green, uint8_t blue) {
    float scale = (float)g_led_brightness / 255.0f;
    uint8_t r = (uint8_t)(red * scale);
    uint8_t g = (uint8_t)(green * scale);
    uint8_t b = (uint8_t)(blue * scale);

    // Discrete GPIO output
    gpio_set_level(LED_RED_PIN, r > 0 ? 1 : 0);
    gpio_set_level(LED_GREEN_PIN, g > 0 ? 1 : 0);
    gpio_set_level(LED_BLUE_PIN, b > 0 ? 1 : 0);

    // WS2812 RGB Strip output
    if (g_led_strip) {
        if (r == 0 && g == 0 && b == 0) {
            led_strip_clear(g_led_strip);
        } else {
            led_strip_set_pixel(g_led_strip, 0, r, g, b);
            led_strip_refresh(g_led_strip);
        }
    }
}

/* Background LED Indicator Task */
static void led_indicator_task(void *pvParameters) {
    init_leds();

    while (1) {
        switch (g_led_mode) {
            case LED_MODE_PROVISIONING:
                set_rgb(255, 165, 0);
                vTaskDelay(pdMS_TO_TICKS(300));
                set_rgb(168, 85, 247);
                vTaskDelay(pdMS_TO_TICKS(300));
                break;

            case LED_MODE_CONNECTING:
                set_rgb(255, 0, 0);
                vTaskDelay(pdMS_TO_TICKS(500));
                set_rgb(0, 0, 0);
                vTaskDelay(pdMS_TO_TICKS(500));
                break;

            case LED_MODE_IDLE:
                set_rgb(0, 255, 0);
                vTaskDelay(pdMS_TO_TICKS(150));
                set_rgb(0, 0, 0);
                vTaskDelay(pdMS_TO_TICKS(1850));
                break;

            case LED_MODE_ARBITRATING:
                set_rgb(0, 0, 255);
                vTaskDelay(pdMS_TO_TICKS(80));
                set_rgb(0, 0, 0);
                vTaskDelay(pdMS_TO_TICKS(80));
                break;

            case LED_MODE_STREAMING:
                set_rgb(0, 0, 255);
                vTaskDelay(pdMS_TO_TICKS(100));
                break;
        }
    }
}

/* Load Wi-Fi and Server Configuration from NVS Memory */
static void load_nvs_config(void) {
    nvs_handle_t my_handle;
    esp_err_t err = nvs_open("satellite_cfg", NVS_READONLY, &my_handle);
    if (err == ESP_OK) {
        size_t size = sizeof(g_wifi_ssid);
        if (nvs_get_str(my_handle, "wifi_ssid", g_wifi_ssid, &size) != ESP_OK) g_wifi_ssid[0] = '\0';
        
        size = sizeof(g_wifi_pass);
        if (nvs_get_str(my_handle, "wifi_pass", g_wifi_pass, &size) != ESP_OK) g_wifi_pass[0] = '\0';

        size = sizeof(g_server_uri);
        if (nvs_get_str(my_handle, "server_uri", g_server_uri, &size) != ESP_OK) g_server_uri[0] = '\0';

        size = sizeof(g_device_id);
        if (nvs_get_str(my_handle, "device_id", g_device_id, &size) != ESP_OK) g_device_id[0] = '\0';

        size = sizeof(g_area_id);
        if (nvs_get_str(my_handle, "area_id", g_area_id, &size) != ESP_OK) g_area_id[0] = '\0';

        uint32_t val32 = 0;
        if (nvs_get_u32(my_handle, "mic_gain_u32", &val32) == ESP_OK) {
            g_mic_gain = (float)val32 / 1000.0f;
        }
        if (nvs_get_u32(my_handle, "spk_vol_u32", &val32) == ESP_OK) {
            g_speaker_volume = (float)val32 / 1000.0f;
        }
        if (nvs_get_u32(my_handle, "led_bright", &val32) == ESP_OK) {
            g_led_brightness = (int)val32;
        }
        if (nvs_get_u32(my_handle, "vad_mult_u32", &val32) == ESP_OK) {
            g_vad_multiplier = (float)val32 / 1000.0f;
        }

        if (nvs_get_u32(my_handle, "dc_rem_u32", &val32) == ESP_OK) g_dc_removal_enabled = (val32 != 0);
        if (nvs_get_u32(my_handle, "noise_tr_u32", &val32) == ESP_OK) g_noise_tracking_enabled = (val32 != 0);
        if (nvs_get_u32(my_handle, "mg_en_u32", &val32) == ESP_OK) g_mic_gain_enabled = (val32 != 0);
        if (nvs_get_u32(my_handle, "spk_en_u32", &val32) == ESP_OK) g_speaker_volume_enabled = (val32 != 0);
        if (nvs_get_u32(my_handle, "wn_thresh", &val32) == ESP_OK) g_wakenet_threshold = (int)val32;
        if (nvs_get_u32(my_handle, "mic_shift", &val32) == ESP_OK) g_mic_shift = (int)val32;
        if (nvs_get_u32(my_handle, "str_max", &val32) == ESP_OK) g_stream_max_ms = (int)val32;
        if (nvs_get_u32(my_handle, "sil_end", &val32) == ESP_OK) g_stream_silence_end_ms = (int)val32;
        if (nvs_get_u32(my_handle, "mic_slot", &val32) == ESP_OK) g_mic_slot = (int)val32;

        nvs_close(my_handle);
    }

    if (strlen(g_wifi_ssid) > 0 && strlen(g_server_uri) > 0 && strlen(g_device_id) > 0) {
        g_is_provisioned = true;
        ESP_LOGI(TAG, "NVS Config Loaded: SSID='%s', Server='%s', Device='%s', Area='%s', MicGain=%.2f, SpkVol=%.2f",
                 g_wifi_ssid, g_server_uri, g_device_id, g_area_id, g_mic_gain, g_speaker_volume);
    } else {
        g_is_provisioned = false;
        ESP_LOGW(TAG, "No valid configuration found in NVS! SoftAP Provisioning required.");
    }
}

/* Save Full Configuration (Network & Hardware) to NVS Memory */
static void save_full_nvs_config(void) {
    nvs_handle_t my_handle;
    esp_err_t err = nvs_open("satellite_cfg", NVS_READWRITE, &my_handle);
    if (err == ESP_OK) {
        nvs_set_str(my_handle, "wifi_ssid", g_wifi_ssid);
        nvs_set_str(my_handle, "wifi_pass", g_wifi_pass);
        nvs_set_str(my_handle, "server_uri", g_server_uri);
        nvs_set_str(my_handle, "device_id", g_device_id);
        nvs_set_str(my_handle, "area_id", g_area_id);
        nvs_set_u32(my_handle, "mic_gain_u32", (uint32_t)(g_mic_gain * 1000.0f));
        nvs_set_u32(my_handle, "spk_vol_u32", (uint32_t)(g_speaker_volume * 1000.0f));
        nvs_set_u32(my_handle, "led_bright", (uint32_t)g_led_brightness);
        nvs_set_u32(my_handle, "vad_mult_u32", (uint32_t)(g_vad_multiplier * 1000.0f));
        nvs_set_u32(my_handle, "dc_rem_u32", g_dc_removal_enabled ? 1 : 0);
        nvs_set_u32(my_handle, "noise_tr_u32", g_noise_tracking_enabled ? 1 : 0);
        nvs_set_u32(my_handle, "mg_en_u32", g_mic_gain_enabled ? 1 : 0);
        nvs_set_u32(my_handle, "spk_en_u32", g_speaker_volume_enabled ? 1 : 0);
        nvs_set_u32(my_handle, "wn_thresh", (uint32_t)g_wakenet_threshold);
        nvs_set_u32(my_handle, "mic_shift", (uint32_t)g_mic_shift);
        nvs_set_u32(my_handle, "str_max", (uint32_t)g_stream_max_ms);
        nvs_set_u32(my_handle, "sil_end", (uint32_t)g_stream_silence_end_ms);
        nvs_set_u32(my_handle, "mic_slot", (uint32_t)g_mic_slot);
        nvs_commit(my_handle);
        nvs_close(my_handle);
        ESP_LOGI(TAG, "Full NVS Config Saved: SSID='%s', Server='%s', Device='%s', Area='%s'",
                 g_wifi_ssid, g_server_uri, g_device_id, g_area_id);
    } else {
        ESP_LOGE(TAG, "Failed to open NVS for writing!");
    }
}

static void save_nvs_config(void) {
    save_full_nvs_config();
}

// ----------------------------------------------------------------------------
// SoftAP Provisioning WebServer
// ----------------------------------------------------------------------------
static httpd_handle_t g_http_server = NULL;

static void url_decode(char *dst, const char *src) {
    char a, b;
    while (*src) {
        if ((*src == '%') && ((a = src[1]) && (b = src[2])) && (isxdigit((unsigned char)a) && isxdigit((unsigned char)b))) {
            if (a >= 'a' && a <= 'f') a -= 'a' - 'A';
            if (a >= 'A' && a <= 'F') a = a - 'A' + 10;
            else a -= '0';
            if (b >= 'a' && b <= 'f') b -= 'a' - 'A';
            if (b >= 'A' && b <= 'F') b = b - 'A' + 10;
            else b -= '0';
            *dst++ = 16 * a + b;
            src += 3;
        } else if (*src == '+') {
            *dst++ = ' ';
            src++;
        } else {
            *dst++ = *src++;
        }
    }
    *dst = '\0';
}

static void parse_query_param(const char *buf, const char *key, char *out, size_t max_len) {
    char key_eq[64];
    snprintf(key_eq, sizeof(key_eq), "%s=", key);
    const char *p = strstr(buf, key_eq);
    if (!p) {
        if (buf == strstr(buf, key)) p = buf;
        else return;
    }
    p += strlen(key_eq);
    char raw[256] = {0};
    size_t i = 0;
    while (*p && *p != '&' && i < sizeof(raw) - 1) {
        raw[i++] = *p++;
    }
    raw[i] = '\0';
    url_decode(out, raw);
}

static const char SETUP_HTML[] = 
"<!DOCTYPE html><html lang='ru'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
"<title>ESP32 Satellite Setup</title>"
"<style>"
"body{background:#0f172a;color:#f8fafc;font-family:sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0;padding:16px;box-sizing:border-box}"
".card{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:24px;width:100%;max-width:420px;box-shadow:0 10px 30px rgba(0,0,0,0.5)}"
"h2{margin-top:0;color:#38bdf8;font-size:20px;text-align:center}"
"p{color:#94a3b8;font-size:13px;text-align:center;margin-bottom:20px}"
"label{display:block;font-size:12px;font-weight:600;color:#cbd5e1;margin-bottom:4px}"
"input{width:100%;padding:10px;border-radius:8px;border:1px solid #475569;background:#0f172a;color:#fff;font-size:14px;box-sizing:border-box;margin-bottom:14px}"
"input:focus{outline:none;border-color:#38bdf8}"
"button{width:100%;padding:12px;border-radius:10px;border:none;background:#10b981;color:#fff;font-size:15px;font-weight:700;cursor:pointer;transition:background 0.2s}"
"button:hover{background:#059669}"
"</style></head><body>"
"<div class='card'>"
"<h2>🎙️ Стартовая настройка Сателлита</h2>"
"<p>Укажите параметры сети и сервера для первичной инициализации</p>"
"<form action='/save' method='POST'>"
"<label>Wi-Fi SSID (Имя сети):</label>"
"<input type='text' name='wifi_ssid' placeholder='Ваша Wi-Fi сеть' required>"
"<label>Wi-Fi Пароль:</label>"
"<input type='password' name='wifi_pass' placeholder='Пароль'>"
"<label>Сервер (WebSocket URI):</label>"
"<input type='text' name='server_uri' value='ws://192.168.1.100:8765' placeholder='ws://192.168.1.X:8765' required>"
"<label>ID Сателлита (Device ID):</label>"
"<input type='text' name='device_id' value='esp32_satellite_bedroom' placeholder='esp32_satellite_bedroom' required>"
"<label>Локация (Area ID):</label>"
"<input type='text' name='area_id' value='bedroom' placeholder='bedroom' required>"
"<button type='submit'>💾 Сохранить и Перезагрузить</button>"
"</form></div></body></html>";

static esp_err_t get_setup_handler(httpd_req_t *req) {
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    return httpd_resp_send(req, SETUP_HTML, HTTPD_RESP_USE_STRLEN);
}

static void reboot_task(void *pvParameters) {
    vTaskDelay(pdMS_TO_TICKS(1500));
    esp_restart();
}

static esp_err_t post_save_handler(httpd_req_t *req) {
    char buf[512] = {0};
    int ret, remaining = req->content_len;
    if (remaining >= (int)sizeof(buf)) remaining = sizeof(buf) - 1;
    
    ret = httpd_req_recv(req, buf, remaining);
    if (ret <= 0) {
        if (ret == HTTPD_SOCK_ERR_TIMEOUT) {
            httpd_resp_send_408(req);
        }
        return ESP_FAIL;
    }
    buf[ret] = '\0';

    parse_query_param(buf, "wifi_ssid", g_wifi_ssid, sizeof(g_wifi_ssid));
    parse_query_param(buf, "wifi_pass", g_wifi_pass, sizeof(g_wifi_pass));
    parse_query_param(buf, "server_uri", g_server_uri, sizeof(g_server_uri));
    parse_query_param(buf, "device_id", g_device_id, sizeof(g_device_id));
    parse_query_param(buf, "area_id", g_area_id, sizeof(g_area_id));

    save_full_nvs_config();

    const char *resp = "<!DOCTYPE html><html><body style='background:#0f172a;color:#10b981;font-family:sans-serif;text-align:center;padding-top:50px'>"
                       "<h2>✓ Настройки успешно сохранены!</h2>"
                       "<p style='color:#94a3b8'>Сателлит перезагружается для подключения к сети...</p>"
                       "</body></html>";
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    httpd_resp_send(req, resp, HTTPD_RESP_USE_STRLEN);

    xTaskCreate(reboot_task, "reboot_task", 2048, NULL, 5, NULL);
    return ESP_OK;
}

static void start_provisioning_webserver(void) {
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.stack_size = 8192;
    if (httpd_start(&g_http_server, &config) == ESP_OK) {
        httpd_uri_t setup_uri = {
            .uri      = "/",
            .method   = HTTP_GET,
            .handler  = get_setup_handler,
            .user_ctx = NULL
        };
        httpd_register_uri_handler(g_http_server, &setup_uri);

        httpd_uri_t save_uri = {
            .uri      = "/save",
            .method   = HTTP_POST,
            .handler  = post_save_handler,
            .user_ctx = NULL
        };
        httpd_register_uri_handler(g_http_server, &save_uri);
        ESP_LOGI(TAG, "Provisioning WebServer active at http://192.168.4.1/");
    }
}

static void wifi_init_softap(void) {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_ap();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    wifi_config_t wifi_config = {
        .ap = {
            .ssid = "ESP32-Satellite-Setup",
            .ssid_len = strlen("ESP32-Satellite-Setup"),
            .channel = 1,
            .password = "",
            .max_connection = 4,
            .authmode = WIFI_AUTH_OPEN
        },
    };

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "Wi-Fi SoftAP started. SSID: 'ESP32-Satellite-Setup', IP: 192.168.4.1");
    start_provisioning_webserver();
}

/* Initialize Station Mode Wi-Fi Connection */
static void wifi_init_sta(void) {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    wifi_config_t wifi_config = { 0 };
    snprintf((char *)wifi_config.sta.ssid, sizeof(wifi_config.sta.ssid), "%s", g_wifi_ssid);
    snprintf((char *)wifi_config.sta.password, sizeof(wifi_config.sta.password), "%s", g_wifi_pass);
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_connect());

    ESP_LOGI(TAG, "Wi-Fi Connecting to SSID: '%s'...", g_wifi_ssid);
}

/* Initialize Dual I2S Channels (SPH0645 32-bit Slot Mic Rx & MAX98357A Speaker Tx) */
static void init_i2s(void) {
    // 1. Microphone Rx Channel (SPH0645 32-bit slot)
    i2s_chan_config_t rx_chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    i2s_new_channel(&rx_chan_cfg, NULL, &rx_chan);

    i2s_std_config_t rx_std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(16000),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = I2S_MIC_SCK_PIN,
            .ws = I2S_MIC_WS_PIN,
            .dout = I2S_GPIO_UNUSED,
            .din = I2S_MIC_SD_PIN,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    rx_std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_BOTH;
    i2s_channel_init_std_mode(rx_chan, &rx_std_cfg);
    i2s_channel_enable(rx_chan);

    // 2. Speaker Tx Channel (MAX98357A)
    i2s_chan_config_t tx_chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    i2s_new_channel(&tx_chan_cfg, &tx_chan, NULL);

    i2s_std_config_t tx_std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(16000),
        .slot_cfg = I2S_STD_MSB_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = I2S_SPK_BCLK_PIN,
            .ws = I2S_SPK_LRCK_PIN,
            .dout = I2S_SPK_DOUT_PIN,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    i2s_channel_init_std_mode(tx_chan, &tx_std_cfg);
    i2s_channel_enable(tx_chan);

    ESP_LOGI(TAG, "I2S Rx SPH0645 (32-bit slot SCK=%d, WS=%d, SD=%d) and Tx (Speaker BCLK=%d) initialized.",
             I2S_MIC_SCK_PIN, I2S_MIC_WS_PIN, I2S_MIC_SD_PIN, I2S_SPK_BCLK_PIN);
}

#if CONFIG_APP_USE_ESP_SR
static void init_wakenet(void) {
    srmodel_list_t *models = esp_srmodel_init("model");
    if (!models) {
        ESP_LOGE(TAG, "Failed to initialize srmodel partition 'model'");
        return;
    }
    char *model_name = esp_srmodel_filter(models, ESP_WN_PREFIX, NULL);
    if (model_name) {
        ESP_LOGI(TAG, "Initializing ESP-SR WakeNet model: '%s'", model_name);
        
        afe_config_t *afe_config = afe_config_init("M", models, AFE_TYPE_SR, AFE_MODE_HIGH_PERF);
        if (afe_config) {
            afe_config->wakenet_mode = DET_MODE_90; // Balanced detection mode (prevents false positives)
            g_afe_handle = esp_afe_handle_from_config(afe_config);
            if (g_afe_handle) {
                g_afe_data = g_afe_handle->create_from_config(afe_config);
            }
        }

        if (g_afe_data) {
            ESP_LOGI(TAG, "ESP-SR Audio Front-End (AFE) pipeline initialized with NS & DET_MODE_90!");
        } else {
            ESP_LOGW(TAG, "AFE init failed. Falling back to direct WakeNet model.");
            g_wakenet = (esp_wn_iface_t *)esp_wn_handle_from_name(model_name);
            if (g_wakenet) {
                g_wn_model_data = g_wakenet->create(model_name, DET_MODE_90);
                if (g_wn_model_data) {
                    g_wn_chunksize = g_wakenet->get_samp_chunksize(g_wn_model_data);
                    ESP_LOGI(TAG, "Direct WakeNet loaded. Chunk size = %d samples", g_wn_chunksize);
                }
            }
        }
    } else {
        ESP_LOGW(TAG, "No WakeNet model found in 'model' partition. Fallback energy detector active.");
    }
}
#endif

static float calculate_rms(const int16_t *samples, size_t count) {
    if (count == 0) return 0.0f;
    double sum = 0.0;
    for (size_t i = 0; i < count; i++) {
        double val = (double)samples[i];
        sum += val * val;
    }
    return (float)sqrt(sum / (double)count);
}

static void send_init_message(float rms) {
    if (!g_ws_client || !esp_websocket_client_is_connected(g_ws_client)) {
        return;
    }
    cJSON *init_json = cJSON_CreateObject();
    if (!init_json) return;
    cJSON_AddStringToObject(init_json, "event", "init");
    cJSON_AddStringToObject(init_json, "device_id", g_device_id);
    cJSON_AddStringToObject(init_json, "area_id", g_area_id);
    cJSON_AddNumberToObject(init_json, "rms", rms);
    cJSON_AddNumberToObject(init_json, "mic_gain", g_mic_gain);
    cJSON_AddNumberToObject(init_json, "speaker_volume", g_speaker_volume);
    cJSON_AddNumberToObject(init_json, "led_brightness", g_led_brightness);
    cJSON_AddNumberToObject(init_json, "vad_multiplier", g_vad_multiplier);
    cJSON_AddBoolToObject(init_json, "dc_removal_enabled", g_dc_removal_enabled);
    cJSON_AddBoolToObject(init_json, "noise_tracking_enabled", g_noise_tracking_enabled);
    cJSON_AddBoolToObject(init_json, "mic_gain_enabled", g_mic_gain_enabled);
    cJSON_AddBoolToObject(init_json, "speaker_volume_enabled", g_speaker_volume_enabled);
    cJSON_AddNumberToObject(init_json, "wakenet_threshold", g_wakenet_threshold);
    cJSON_AddNumberToObject(init_json, "mic_shift", g_mic_shift);
    cJSON_AddNumberToObject(init_json, "stream_max_ms", g_stream_max_ms);
    cJSON_AddNumberToObject(init_json, "stream_silence_end_ms", g_stream_silence_end_ms);
    cJSON_AddNumberToObject(init_json, "mic_slot", g_mic_slot);

    char *payload = cJSON_PrintUnformatted(init_json);
    if (payload) {
        esp_websocket_client_send_text(g_ws_client, payload, strlen(payload), portMAX_DELAY);
        free(payload);
    }
    cJSON_Delete(init_json);
}

static void websocket_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
    esp_websocket_event_data_t *data = (esp_websocket_event_data_t *)event_data;
    switch (event_id) {
        case WEBSOCKET_EVENT_CONNECTED:
            ESP_LOGI(TAG, "Connected to Central Voice Server -> LED mode: IDLE (Blinking Green)");
            g_led_mode = LED_MODE_IDLE;
            send_init_message(0.0f);
            break;
        case WEBSOCKET_EVENT_DISCONNECTED:
            ESP_LOGW(TAG, "Disconnected from Server -> LED mode: CONNECTING (Blinking Red)");
            g_current_state = STATE_IDLE;
            g_led_mode = LED_MODE_CONNECTING;
            break;
        case WEBSOCKET_EVENT_DATA:
            if (data->op_code == 0x01) { // Text JSON frame
                cJSON *root = cJSON_ParseWithLength(data->data_ptr, data->data_len);
                if (root) {
                    cJSON *action = cJSON_GetObjectItem(root, "action");
                    if (cJSON_IsString(action) && (action->valuestring != NULL)) {
                        if (strcmp(action->valuestring, "continue") == 0) {
                            ESP_LOGI(TAG, "Arbiter Granted CONTINUE -> STREAMING active");
                            g_current_state = STATE_STREAMING;
                            g_led_mode = LED_MODE_STREAMING;
                        } else if (strcmp(action->valuestring, "stop") == 0) {
                            ESP_LOGI(TAG, "Arbiter Rejected STOP -> Returning to IDLE (cooldown active)");
                            g_current_state = STATE_IDLE;
                            g_led_mode = LED_MODE_IDLE;
                            s_cooldown_counter = 30; // 3 second cooldown after rejection
                        } else if (strcmp(action->valuestring, "start_debug") == 0) {
                            ESP_LOGI(TAG, "Server Command START_DEBUG -> Entering continuous audio stream debug mode");
                            g_current_state = STATE_DEBUG_STREAMING;
                            g_led_mode = LED_MODE_STREAMING;
                        } else if (strcmp(action->valuestring, "stop_debug") == 0) {
                            ESP_LOGI(TAG, "Server Command STOP_DEBUG -> Exiting debug mode, returning to IDLE");
                            g_current_state = STATE_IDLE;
                            g_led_mode = LED_MODE_IDLE;
                        } else if (strcmp(action->valuestring, "update_config") == 0) {
                            cJSON *cfg = cJSON_GetObjectItem(root, "config");
                            if (cJSON_IsObject(cfg)) {
                                cJSON *area = cJSON_GetObjectItem(cfg, "area_id");
                                if (cJSON_IsString(area) && area->valuestring) {
                                    snprintf(g_area_id, sizeof(g_area_id), "%s", area->valuestring);
                                }
                                cJSON *mg = cJSON_GetObjectItem(cfg, "mic_gain");
                                if (cJSON_IsNumber(mg)) g_mic_gain = (float)mg->valuedouble;
                                cJSON *sv = cJSON_GetObjectItem(cfg, "speaker_volume");
                                if (cJSON_IsNumber(sv)) g_speaker_volume = (float)sv->valuedouble;
                                cJSON *lb = cJSON_GetObjectItem(cfg, "led_brightness");
                                if (cJSON_IsNumber(lb)) g_led_brightness = lb->valueint;
                                cJSON *vm = cJSON_GetObjectItem(cfg, "vad_multiplier");
                                if (cJSON_IsNumber(vm)) g_vad_multiplier = (float)vm->valuedouble;

                                cJSON *dcrem = cJSON_GetObjectItem(cfg, "dc_removal_enabled");
                                if (cJSON_IsBool(dcrem)) g_dc_removal_enabled = cJSON_IsTrue(dcrem);
                                cJSON *noisetr = cJSON_GetObjectItem(cfg, "noise_tracking_enabled");
                                if (cJSON_IsBool(noisetr)) g_noise_tracking_enabled = cJSON_IsTrue(noisetr);
                                cJSON *mgnen = cJSON_GetObjectItem(cfg, "mic_gain_enabled");
                                if (cJSON_IsBool(mgnen)) g_mic_gain_enabled = cJSON_IsTrue(mgnen);
                                cJSON *spkvolen = cJSON_GetObjectItem(cfg, "speaker_volume_enabled");
                                if (cJSON_IsBool(spkvolen)) g_speaker_volume_enabled = cJSON_IsTrue(spkvolen);
                                cJSON *wnth = cJSON_GetObjectItem(cfg, "wakenet_threshold");
                                if (cJSON_IsNumber(wnth)) g_wakenet_threshold = wnth->valueint;
                                cJSON *msh = cJSON_GetObjectItem(cfg, "mic_shift");
                                if (cJSON_IsNumber(msh)) g_mic_shift = msh->valueint;
                                cJSON *stmax = cJSON_GetObjectItem(cfg, "stream_max_ms");
                                if (cJSON_IsNumber(stmax)) g_stream_max_ms = stmax->valueint;
                                cJSON *silend = cJSON_GetObjectItem(cfg, "stream_silence_end_ms");
                                if (cJSON_IsNumber(silend)) g_stream_silence_end_ms = silend->valueint;
                                cJSON *midslot = cJSON_GetObjectItem(cfg, "mic_slot");
                                if (cJSON_IsNumber(midslot)) g_mic_slot = midslot->valueint;

                                save_nvs_config();
                                ESP_LOGI(TAG, "Applied Remote Config: DCRem=%d, NoiseTr=%d, MicGainEn=%d (%.2f), SpkVolEn=%d (%.2f)",
                                         g_dc_removal_enabled, g_noise_tracking_enabled, g_mic_gain_enabled, g_mic_gain, g_speaker_volume_enabled, g_speaker_volume);
                            }
                        }
                    }
                    cJSON_Delete(root);
                }
            } else if (data->op_code == 0x02) { // Binary PCM playback
                g_current_state = STATE_PLAYBACK;
                if (tx_chan && data->data_len > 0) {
                    if (g_speaker_volume_enabled && fabsf(g_speaker_volume - 1.0f) > 0.01f) {
                        int16_t *spk_samples = (int16_t *)malloc(data->data_len);
                        if (spk_samples) {
                            const int16_t *src_samples = (const int16_t *)data->data_ptr;
                            size_t num_samples = data->data_len / sizeof(int16_t);
                            for (size_t s = 0; s < num_samples; s++) {
                                int32_t val = (int32_t)(src_samples[s] * g_speaker_volume);
                                if (val > 32767) val = 32767;
                                if (val < -32768) val = -32768;
                                spk_samples[s] = (int16_t)val;
                            }
                            size_t written = 0;
                            i2s_channel_write(tx_chan, spk_samples, data->data_len, &written, portMAX_DELAY);
                            free(spk_samples);
                        } else {
                            size_t written = 0;
                            i2s_channel_write(tx_chan, data->data_ptr, data->data_len, &written, portMAX_DELAY);
                        }
                    } else {
                        size_t written = 0;
                        i2s_channel_write(tx_chan, data->data_ptr, data->data_len, &written, portMAX_DELAY);
                    }
                }
            }
            break;
        default:
            break;
    }
}

static void process_i2s_to_pcm16(
    const int32_t *raw32_buf,
    int16_t *pcm16_buf,
    size_t stereo_samples,
    float *s_dc_offset_l,
    float *s_dc_offset_r,
    int s_active_slot,
    int32_t *out_left_max,
    int32_t *out_right_max
) {
    float effective_gain = g_mic_gain_enabled ? g_mic_gain : 1.0f;
    int32_t left_max = 0;
    int32_t right_max = 0;
    int target_slot = (g_mic_slot >= 0) ? g_mic_slot : s_active_slot;

    for (size_t i = 0; i < stereo_samples; i++) {
        int32_t raw_l = raw32_buf[i * 2];
        int32_t raw_r = raw32_buf[i * 2 + 1];

        float ac_l, ac_r;
        if (g_dc_removal_enabled) {
            *s_dc_offset_l = 0.995f * (*s_dc_offset_l) + 0.005f * (float)raw_l;
            *s_dc_offset_r = 0.995f * (*s_dc_offset_r) + 0.005f * (float)raw_r;
            ac_l = (float)raw_l - *s_dc_offset_l;
            ac_r = (float)raw_r - *s_dc_offset_r;
        } else {
            ac_l = (float)raw_l;
            ac_r = (float)raw_r;
        }

        float abs_l = fabsf(ac_l);
        float abs_r = fabsf(ac_r);
        if ((int32_t)abs_l > left_max) left_max = (int32_t)abs_l;
        if ((int32_t)abs_r > right_max) right_max = (int32_t)abs_r;

        float ac_selected;
        if (target_slot == -1) {
            ac_selected = (abs_l >= abs_r) ? ac_l : ac_r;
        } else {
            ac_selected = (target_slot == 1) ? ac_r : ac_l;
        }

        int32_t sample = (int32_t)((ac_selected / 2048.0f) * effective_gain);
        if (sample > 32767) sample = 32767;
        if (sample < -32768) sample = -32768;
        pcm16_buf[i] = (int16_t)sample;
    }

    if (out_left_max) *out_left_max = left_max;
    if (out_right_max) *out_right_max = right_max;
}

static void satellite_task(void *pvParameters) {
    int32_t *raw32_buf = (int32_t *)malloc(3200 * sizeof(int32_t));
    int16_t *pcm16_buf = (int16_t *)malloc(1600 * sizeof(int16_t));
    
    if (!raw32_buf || !pcm16_buf) {
        ESP_LOGE(TAG, "Failed to allocate audio buffers!");
        vTaskDelete(NULL);
        return;
    }

    size_t bytes_read = 0;
    uint32_t loop_count = 0;
    uint32_t init_state_ticks = 0;
    uint32_t consecutive_speech_frames = 0;

    static float s_dc_offset_l = 0.0f;
    static float s_dc_offset_r = 0.0f;
    static float s_noise_floor_rms = 100.0f;

    static int s_active_slot = -1; // -1: auto-detecting mic slot, 0: Left slot, 1: Right slot
    static double s_l_energy_acc = 0.0;
    static double s_r_energy_acc = 0.0;

    while (1) {
        if (g_current_state == STATE_IDLE) {
            if (s_cooldown_counter > 0) {
                s_cooldown_counter--;
            }
            if (rx_chan && i2s_channel_read(rx_chan, raw32_buf, 3200 * sizeof(int32_t), &bytes_read, pdMS_TO_TICKS(100)) == ESP_OK) {
                size_t stereo_samples = bytes_read / (2 * sizeof(int32_t));
                
                int32_t left_max = 0;
                int32_t right_max = 0;
                process_i2s_to_pcm16(raw32_buf, pcm16_buf, stereo_samples, &s_dc_offset_l, &s_dc_offset_r, s_active_slot, &left_max, &right_max);

                if (g_mic_slot < 0 && s_active_slot == -1) {
                    s_l_energy_acc += (double)left_max;
                    s_r_energy_acc += (double)right_max;
                    if (loop_count >= 25) {
                        s_active_slot = (s_l_energy_acc >= s_r_energy_acc) ? 0 : 1;
                        ESP_LOGI(TAG, "I2S Mic Active Slot Locked: %s (L_Energy=%.0f, R_Energy=%.0f)",
                                 s_active_slot == 0 ? "LEFT (Slot 0)" : "RIGHT (Slot 1)",
                                 s_l_energy_acc, s_r_energy_acc);
                    }
                }

                float rms = calculate_rms(pcm16_buf, stereo_samples);
                
                if (g_noise_tracking_enabled && rms < 500.0f) {
                    s_noise_floor_rms = 0.98f * s_noise_floor_rms + 0.02f * rms;
                }

                float dynamic_threshold = s_noise_floor_rms * g_vad_multiplier;
                if (dynamic_threshold < 350.0f) dynamic_threshold = 350.0f;

                bool wake_detected = false;

#if CONFIG_APP_USE_ESP_SR
                if (g_afe_handle && g_afe_data) {
                    int feed_chunk = g_afe_handle->get_feed_chunksize(g_afe_data);
                    int offset = 0;
                    while (offset + feed_chunk <= (int)stereo_samples) {
                        g_afe_handle->feed(g_afe_data, pcm16_buf + offset);
                        afe_fetch_result_t *res = g_afe_handle->fetch(g_afe_data);
                        if (res && res->wakeup_state == WAKENET_DETECTED) {
                            ESP_LOGI(TAG, "[WAKE] ESP-SR AFE Neural Model DETECTED Wake Word! (index=%d, RMS=%.1f)", res->wake_word_index, rms);
                            wake_detected = true;
                            break;
                        }
                        offset += feed_chunk;
                    }
                } else if (g_wakenet && g_wn_model_data) {
                    int offset = 0;
                    while (offset + g_wn_chunksize <= (int)stereo_samples) {
                        int res = g_wakenet->detect(g_wn_model_data, pcm16_buf + offset);
                        if (res > 0) {
                            ESP_LOGI(TAG, "[WAKE] WakeNet Neural Model DETECTED Wake Word! (res=%d, RMS=%.1f)", res, rms);
                            wake_detected = true;
                            break;
                        }
                        offset += g_wn_chunksize;
                    }
                }
#endif
                // RMS Energy Fallback removed! Only neural WakeNet triggers wake_detected.

                loop_count++;
                if (loop_count % 20 == 0 || wake_detected) {
                    ESP_LOGI(TAG, "[Audio AC Probe] RMS=%.1f (NoiseFloor=%.1f DynThresh=%.1f Cooldown=%u AFEActive=%d Connected=%d Warmup=%u/50)",
                             rms, s_noise_floor_rms, dynamic_threshold, s_cooldown_counter,
                             (g_afe_handle && g_afe_data) ? 1 : 0,
                             (g_ws_client && esp_websocket_client_is_connected(g_ws_client)) ? 1 : 0, loop_count);
                }

                // Trigger wake word ONLY after WakeNet detection, past warm-up, no active cooldown, and WS connected
                if (wake_detected && loop_count > 50 && s_cooldown_counter == 0 && g_ws_client && esp_websocket_client_is_connected(g_ws_client)) {
                    ESP_LOGI(TAG, "Wake trigger confirmed. Sending WS INIT, waiting for server arbitration...");
                    g_current_state = STATE_INIT;
                    g_led_mode = LED_MODE_ARBITRATING;
                    init_state_ticks = 0;
                    consecutive_speech_frames = 0;

                    send_init_message(rms);
                }
            }
        } else if (g_current_state == STATE_INIT) {
            // Timeout after 20 ticks (2 seconds) if server response is not received
            init_state_ticks++;
            if (init_state_ticks > 20) {
                ESP_LOGW(TAG, "STATE_INIT timeout (no arbiter response). Reverting to IDLE");
                g_current_state = STATE_IDLE;
                g_led_mode = LED_MODE_IDLE;
                s_cooldown_counter = 20;
            }
            vTaskDelay(pdMS_TO_TICKS(100));
        } else if (g_current_state == STATE_STREAMING) {
            if (rx_chan && i2s_channel_read(rx_chan, raw32_buf, 3200 * sizeof(int32_t), &bytes_read, pdMS_TO_TICKS(100)) == ESP_OK) {
                size_t stereo_samples = bytes_read / (2 * sizeof(int32_t));
                process_i2s_to_pcm16(raw32_buf, pcm16_buf, stereo_samples, &s_dc_offset_l, &s_dc_offset_r, s_active_slot, NULL, NULL);

                if (g_ws_client && esp_websocket_client_is_connected(g_ws_client)) {
#if CONFIG_APP_USE_ESP_SR
                    if (g_afe_handle && g_afe_data) {
                        int feed_chunk = g_afe_handle->get_feed_chunksize(g_afe_data);
                        int offset = 0;
                        while (offset + feed_chunk <= (int)stereo_samples) {
                            g_afe_handle->feed(g_afe_data, pcm16_buf + offset);
                            afe_fetch_result_t *res = g_afe_handle->fetch(g_afe_data);
                            if (res && res->data && res->data_size > 0) {
                                esp_websocket_client_send_bin(g_ws_client, (const char *)res->data, res->data_size, portMAX_DELAY);
                            } else {
                                esp_websocket_client_send_bin(g_ws_client, (const char *)(pcm16_buf + offset), feed_chunk * sizeof(int16_t), portMAX_DELAY);
                            }
                            offset += feed_chunk;
                        }
                    } else
#endif
                    {
                        esp_websocket_client_send_bin(g_ws_client, (const char *)pcm16_buf, stereo_samples * sizeof(int16_t), portMAX_DELAY);
                    }
                }
            }
        } else if (g_current_state == STATE_DEBUG_STREAMING) {
            if (rx_chan && i2s_channel_read(rx_chan, raw32_buf, 3200 * sizeof(int32_t), &bytes_read, pdMS_TO_TICKS(100)) == ESP_OK) {
                size_t stereo_samples = bytes_read / (2 * sizeof(int32_t));
                process_i2s_to_pcm16(raw32_buf, pcm16_buf, stereo_samples, &s_dc_offset_l, &s_dc_offset_r, s_active_slot, NULL, NULL);

                if (g_ws_client && esp_websocket_client_is_connected(g_ws_client)) {
                    esp_websocket_client_send_bin(g_ws_client, (const char *)pcm16_buf, stereo_samples * sizeof(int16_t), portMAX_DELAY);
                }
            }
        } else if (g_current_state == STATE_PLAYBACK) {
            vTaskDelay(pdMS_TO_TICKS(50));
        } else {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }

    free(raw32_buf);
    free(pcm16_buf);
}

void app_main(void) {
    ESP_LOGI(TAG, "Starting ESP32-S3 Voice Satellite (wake_word_test)");
    
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    load_nvs_config();

    // Start LED indicator task (WS2812 & Discrete GPIOs)
    xTaskCreate(led_indicator_task, "led_task", 4096, NULL, 4, NULL);

    if (!g_is_provisioned) {
        g_led_mode = LED_MODE_PROVISIONING;
        ESP_LOGI(TAG, "==========================================================");
        ESP_LOGI(TAG, "  SATELLITE INITIAL PROVISIONING MODE ACTIVE!");
        ESP_LOGI(TAG, "  1. Connect Wi-Fi to AP: 'ESP32-Satellite-Setup'");
        ESP_LOGI(TAG, "  2. Open Web Portal at: http://192.168.4.1");
        ESP_LOGI(TAG, "==========================================================");
        wifi_init_softap();
        return;
    }

    g_led_mode = LED_MODE_CONNECTING;

    // Initialize I2S Microphone & Speaker Channels
    init_i2s();

#if CONFIG_APP_USE_ESP_SR
    // Initialize ESP-SR Audio Front-End (AFE) Neural Network Detector
    init_wakenet();
#endif

    // Initialize Wi-Fi Station
    wifi_init_sta();

    // Configure WebSocket Client
    esp_websocket_client_config_t ws_cfg = {
        .uri = g_server_uri,
    };
    g_ws_client = esp_websocket_client_init(&ws_cfg);
    esp_websocket_register_events(g_ws_client, ESP_EVENT_ANY_ID, websocket_event_handler, (void *)g_ws_client);
    esp_websocket_client_start(g_ws_client);

    // Start audio satellite task on Core 1 with 16KB stack size
    xTaskCreatePinnedToCore(satellite_task, "sat_task", 16384, NULL, 5, NULL, 1);
}

