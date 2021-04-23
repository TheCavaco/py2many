#include "py14/runtime/builtins.h"
#include "py14/runtime/sys.h"
#include <cassert>
#include <iostream>

inline void foo() {
  int a = 10;
  int b = 20;
  int c1 = a + b;
  int c2 = a - b;
  int c3 = a * b;
  double c4 = a / b;
  int c5 = -(a);
  double d = 2.0;
  double e1 = a + d;
  double e2 = a - d;
  double e3 = a * d;
  double e4 = a / d;
  double f = -3.0;
  int g = -(a);
}

inline int16_t add1(int8_t x, int8_t y) { return x + y; }

inline int32_t add2(int16_t x, int16_t y) { return x + y; }

inline int64_t add3(int32_t x, int32_t y) { return x + y; }

inline int64_t add4(int64_t x, int64_t y) { return x + y; }

inline uint16_t add5(uint8_t x, uint8_t y) { return x + y; }

inline uint32_t add6(uint16_t x, uint16_t y) { return x + y; }

inline uint64_t add7(uint32_t x, uint32_t y) { return x + y; }

inline uint64_t add8(uint64_t x, uint64_t y) { return x + y; }

inline uint32_t add9(int8_t x, uint16_t y) { return x + y; }

inline int8_t sub(int8_t x, int8_t y) { return x - y; }

inline int16_t mul(int8_t x, int8_t y) { return x * y; }

inline double fadd1(int8_t x, double y) { return x + y; }

inline void show() {
  double rv = fadd1(6, 6.0);
  assert(rv == 12);
  std::cout << std::string{"OK"};
  std::cout << std::endl;
}

int main(int argc, char **argv) {
  py14::sys::argv = std::vector<std::string>(argv, argv + argc);
  foo();
  show();
}
