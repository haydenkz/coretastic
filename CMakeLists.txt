cmake_minimum_required(VERSION 3.16)
if(NOT CORETASTIC_BOARD)
    message(FATAL_ERROR "Set CORETASTIC_BOARD to a directory under boards/")
endif()
# Board settings (flash size, partition table) override the shared defaults.
set(SDKCONFIG_DEFAULTS
    "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults"
    "${CMAKE_CURRENT_LIST_DIR}/boards/${CORETASTIC_BOARD}/sdkconfig.defaults")
set(EXTRA_COMPONENT_DIRS src)
if(EXISTS "$ENV{IDF_PATH}/components/__pio_env")
    set(COMPONENTS src __pio_env)
endif()
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(coretastic)
