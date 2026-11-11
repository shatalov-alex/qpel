#pragma once
#include <cstdint>
#include <vector>
namespace torch {
enum ScalarType { kUInt8, kInt32 };
struct Device {};
struct TensorOptions {
  TensorOptions dtype(ScalarType) const;
  TensorOptions device(Device) const;
};
struct Tensor {
  bool is_cuda() const;
  long ndimension() const;
  ScalarType scalar_type() const;
  long size(long) const;
  Device device() const;
  template <typename T> T* data_ptr() const;
};
Tensor zeros(std::vector<long>, TensorOptions);
}
#define AT_ASSERTM(condition, message) ((void)(condition))
namespace pybind11 { struct module_ { template <typename F> void def(const char*, F, const char*); }; }
#define PYBIND11_MODULE(name, m) void pybind11_init_##name(pybind11::module_& m)
#define TORCH_EXTENSION_NAME qpel_corpus
