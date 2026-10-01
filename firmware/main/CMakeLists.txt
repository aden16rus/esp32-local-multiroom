set(priv_requires log led_strip)

if(CONFIG_APP_USE_ESP_SR)
    list(APPEND priv_requires esp-sr)
endif()

list(APPEND priv_requires esp_wifi esp_netif esp_event nvs_flash esp_http_server)
list(APPEND priv_requires esp_websocket_client)

idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES driver esp_timer esp_common
    PRIV_REQUIRES ${priv_requires}
)
