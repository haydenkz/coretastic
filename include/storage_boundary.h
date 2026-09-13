#pragma once
#include <cstddef>
#include <cstdint>
namespace coretastic {
constexpr bool contains(uint32_t start, uint32_t size, uint32_t address, size_t length) {
  return address >= start && address - start <= size && length <= size - (address - start);
}
constexpr bool storage_write_allowed(bool meshcore, uint32_t address, size_t size) {
  return meshcore ? contains(0xa00000, 0x10000, address, size) ||
                        contains(0xa20000, 0x100000, address, size)
                  : contains(0xa10000, 0x10000, address, size) ||
                        contains(0xb20000, 0x4e0000, address, size);
}
} // namespace coretastic
