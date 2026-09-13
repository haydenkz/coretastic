"""PlatformIO post-script copied into an isolated upstream build checkout."""

Import("env")
from SCons.Script import COMMAND_LINE_TARGETS
from pathlib import Path

symbols = [
    "app_main",
    "nvs_flash_init",
    "nvs_flash_init_partition",
    "nvs_flash_erase",
    "nvs_flash_erase_partition",
    "nvs_flash_deinit",
    "nvs_flash_deinit_partition",
    "nvs_open",
    "nvs_open_from_partition",
    "nvs_get_stats",
    "esp_flash_write",
    "esp_flash_erase_region",
    "esp_flash_erase_chip",
    "esp_ota_begin",
    "esp_ota_set_boot_partition",
    "spi_flash_write",
    "spi_flash_erase_range",
    "spi_flash_erase_sector",
    "esp_flash_write_encrypted",
    "spi_flash_write_encrypted",
]
env.Append(LINKFLAGS=["-Wl,--wrap=" + symbol for symbol in symbols])
env.Append(CPPPATH=[str(Path(env["PROJECT_DIR"]) / "coretastic")])
# The --wrap entry points are reached through Xtensa call8, which requires a
# 4-byte aligned target. Meshtastic links with LTO, which ignores the aligned(4)
# function attribute, so build this library without LTO and with explicit
# 4-byte function alignment. PlatformIO's BuildLibrary applies BUILD_UNFLAGS
# (the inherited -fno-lto would otherwise strip the flag), so clear it first.
coretastic_env = env.Clone()
coretastic_env.Replace(BUILD_UNFLAGS=[])
coretastic_env.Append(CCFLAGS=["-fno-lto", "-falign-functions=4"])
coretastic_env.Append(CXXFLAGS=["-fno-lto", "-falign-functions=4"])
env.Prepend(LIBS=[coretastic_env.BuildLibrary("$BUILD_DIR/coretastic", "$PROJECT_DIR/coretastic")])

# Upstream upload targets write shared boot files using their original layout.
# Only the manifest-driven USB flasher may program the integrated device.
if any("upload" in target for target in COMMAND_LINE_TARGETS):
    raise RuntimeError(
        "Use scripts/flash.py with the release manifest; upstream upload targets are disabled"
    )

if env["PIOENV"] == "coretastic-meshtastic":
    env.Append(LINKFLAGS=["--specs=nano.specs", "-u", "_printf_float"])
