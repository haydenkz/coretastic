idf_component_register(SRCS "selector.cpp" "oled.cpp"
    INCLUDE_DIRS "../include"
    REQUIRES driver nvs_flash app_update esp_timer)
# board_profile.h selects pins with CORETASTIC_BOARD_<ID>, e.g. CORETASTIC_BOARD_HELTEC_V3.
string(MAKE_C_IDENTIFIER "${CORETASTIC_BOARD}" board_id)
string(TOUPPER "${board_id}" board_id)
target_compile_definitions(${COMPONENT_LIB} PRIVATE "CORETASTIC_BOARD_${board_id}=1")
