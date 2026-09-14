#include "selector_state.h"
#include "storage_boundary.h"
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
  using coretastic::storage_write_allowed;
  assert(storage_write_allowed(true, 0xa00000, 0x10000));
  assert(!storage_write_allowed(true, 0xa00000, 0x10001));
  assert(!storage_write_allowed(true, 0xa10000, 4096));
  assert(!storage_write_allowed(false, 0xa20000, 4096));
  assert(storage_write_allowed(false, 0xffffff, 1));
  assert(!storage_write_allowed(false, 0xffffff, 2));
  assert(!storage_write_allowed(false, 0xffffff, SIZE_MAX));
  for (uint32_t address = 0; address < 0xa00000; address += 4096) {
    assert(!storage_write_allowed(true, address, 4096));
    assert(!storage_write_allowed(false, address, 4096));
  }
}
