#include <cassert>   // NOLINT(build/include_order)
#include <iostream>  // NOLINT(build/include_order)

#include "pycpp/runtime/sys.h"  // NOLINT(build/include_order)
inline void foo() {
  int a = 10;
  int b = a;
  assert(b == 10);
  std::cout << b;
  std::cout << std::endl;
}

int main(int argc, char** argv) {
  pycpp::sys::argv = std::vector<std::string>(argv, argv + argc);
  foo();
}
