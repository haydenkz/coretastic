#pragma once
#include "coretastic_layout.h" // Generated per board from its partitions.csv.
#include <cstddef>
#include <cstdint>
namespace coretastic {
constexpr bool contains(uint32_t start, uint32_t size, uint32_t address, size_t length) {
  return address >= start && address - start <= size && length <= size - (address - start);
}
constexpr bool storage_write_allowed(bool meshcore, uint32_t address, size_t size) {
  return meshcore ? contains(kMcNvsOffset, kMcNvsSize, address, size) ||
                        contains(kMcFsOffset, kMcFsSize, address, size)
                  : contains(kMtNvsOffset, kMtNvsSize, address, size) ||
                        contains(kMtFsOffset, kMtFsSize, address, size);
}
} // namespace coretastic
