#include "selector_state.h"
#include "storage_boundary.h"
#include <algorithm>
#include <cassert>
#include <cstdint>
int main() {
  using coretastic::SelectorState;
  SelectorState s(1, 0);
  assert(!s.update(false, 4999));
  assert(s.update(false, 5000));
  SelectorState bounce(0, 0);
  bounce.update(true, 100);
  bounce.update(false, 110);
  bounce.update(true, 120);
  bounce.update(true, 149);
  assert(bounce.selection() == 0);
  bounce.update(true, 150);
  assert(bounce.selection() == 1);
  assert(!bounce.update(true, 8000)); // no boot while PRG is held
  assert(!bounce.update(false, 8010));
  assert(bounce.update(false, 8040));
  SelectorState wrap(9, UINT32_MAX - 100);
  assert(wrap.selection() == 0);
  assert(!wrap.update(false, 4898));
  assert(wrap.update(false, 4899));
  // Runs once per board against its generated layout.
  using namespace coretastic;
  assert(storage_write_allowed(true, kMcNvsOffset, kMcNvsSize));
  assert(!storage_write_allowed(true, kMcNvsOffset, kMcNvsSize + 1));
  assert(!storage_write_allowed(true, kMtNvsOffset, 4096));
  assert(!storage_write_allowed(false, kMcFsOffset, 4096));
  const uint32_t last = kMtFsOffset + kMtFsSize - 1;
  assert(storage_write_allowed(false, last, 1));
  assert(!storage_write_allowed(false, last, 2));
  assert(!storage_write_allowed(false, last, SIZE_MAX));
  const uint32_t storage = std::min({kMcNvsOffset, kMcFsOffset, kMtNvsOffset, kMtFsOffset});
  // Nothing below the settings partitions (boot data and every app) is writable.
  for (uint32_t address = 0; address < storage; address += 4096) {
    assert(!storage_write_allowed(true, address, 4096));
    assert(!storage_write_allowed(false, address, 4096));
  }
}
