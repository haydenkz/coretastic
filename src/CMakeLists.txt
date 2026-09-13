idf_component_register(SRCS "selector.cpp" "oled.cpp"
    INCLUDE_DIRS "../include"
    REQUIRES driver nvs_flash app_update esp_timer)
