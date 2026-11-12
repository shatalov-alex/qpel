// Declaration-only stand-in for PyTorch's C++ extension API (torch/extension.h), so clang can
// parse CUDA extensions offline. Only for importing: host functions are parsed, never imported,
// so this declares the common host API (Tensor, TensorOptions, dtypes, factory functions,
// TORCH_CHECK, pybind11 module macros) with simplified signatures. Device code sees the same
// scalar types as real PyTorch (c10::Half and c10::BFloat16 map to CUDA's half types).
#pragma once
#include <cuda_runtime.h>
#include <cuda_fp16.h>
#include <cuda_bf16.h>
#include <cstdint>
#include <cstddef>
#include <initializer_list>
#include <limits>
#include <string>
#include <tuple>
#include <vector>

namespace c10 {
enum class ScalarType { Byte, Char, Short, Int, Long, Half, Float, Double, ComplexHalf, ComplexFloat, ComplexDouble,
                        Bool, QInt8, QUInt8, QInt32, BFloat16, UInt16 = 27, UInt32, UInt64, Undefined = 255 };
enum class DeviceType : int8_t { CPU = 0, CUDA = 1, MPS = 13, Meta = 9 };
enum class MemoryFormat : int8_t { Contiguous, Preserve, ChannelsLast, ChannelsLast3d };
using Half = __half;
using BFloat16 = __nv_bfloat16;
struct Device {
  Device(DeviceType type, int8_t index = -1);
  Device(const std::string& name);
  DeviceType type() const;
  int8_t index() const;
  bool is_cuda() const;
  bool is_cpu() const;
};
template <typename T> struct ArrayRef {
  ArrayRef();
  ArrayRef(const T&);
  ArrayRef(const std::vector<T>&);
  ArrayRef(std::initializer_list<T>);
  template <size_t N> ArrayRef(const T (&)[N]);
  const T* data() const;
  size_t size() const;
  const T& operator[](size_t) const;
  const T* begin() const;
  const T* end() const;
  std::vector<T> vec() const;
};
using IntArrayRef = ArrayRef<int64_t>;
template <typename T> struct optional {
  optional();
  optional(const T&);
  bool has_value() const;
  const T& value() const;
  explicit operator bool() const;
  const T& operator*() const;
  const T* operator->() const;
};
struct Scalar {
  Scalar(double);
  Scalar(int64_t);
  Scalar(int);
  Scalar(bool);
  template <typename T> T to() const;
};
}  // namespace c10

namespace at {
using c10::ScalarType;
using c10::DeviceType;
using c10::Device;
using c10::IntArrayRef;
using c10::Half;
using c10::BFloat16;
using c10::Scalar;
using c10::MemoryFormat;
template <typename T> using optional = c10::optional<T>;
constexpr ScalarType kByte = ScalarType::Byte, kChar = ScalarType::Char, kShort = ScalarType::Short,
    kInt = ScalarType::Int, kLong = ScalarType::Long, kHalf = ScalarType::Half, kFloat = ScalarType::Float,
    kDouble = ScalarType::Double, kBool = ScalarType::Bool, kBFloat16 = ScalarType::BFloat16,
    kUInt8 = ScalarType::Byte, kInt8 = ScalarType::Char, kInt16 = ScalarType::Short, kInt32 = ScalarType::Int,
    kInt64 = ScalarType::Long, kFloat16 = ScalarType::Half, kFloat32 = ScalarType::Float,
    kFloat64 = ScalarType::Double, kUInt16 = ScalarType::UInt16, kUInt32 = ScalarType::UInt32,
    kUInt64 = ScalarType::UInt64;
constexpr DeviceType kCPU = DeviceType::CPU, kCUDA = DeviceType::CUDA, kMPS = DeviceType::MPS;
struct TensorOptions {
  TensorOptions();
  TensorOptions(ScalarType);
  TensorOptions(Device);
  TensorOptions(DeviceType);
  TensorOptions dtype(ScalarType) const;
  template <typename T> TensorOptions dtype() const;
  TensorOptions device(Device) const;
  TensorOptions device(DeviceType, int8_t = -1) const;
  TensorOptions layout(int) const;
  TensorOptions requires_grad(bool) const;
  ScalarType dtype() const;
  Device device() const;
};
struct Tensor;
struct TensorIndex;
struct Tensor {
  Tensor();
  int64_t size(int64_t) const;
  IntArrayRef sizes() const;
  int64_t stride(int64_t) const;
  IntArrayRef strides() const;
  int64_t numel() const;
  int64_t dim() const;
  int64_t ndimension() const;
  int64_t element_size() const;
  int64_t nbytes() const;
  ScalarType scalar_type() const;
  ScalarType dtype() const;
  TensorOptions options() const;
  Device device() const;
  bool is_cuda() const;
  bool is_cpu() const;
  bool is_contiguous() const;
  bool defined() const;
  bool requires_grad() const;
  template <typename T> T* data_ptr() const;
  void* data_ptr() const;
  template <typename T> T* mutable_data_ptr() const;
  void* mutable_data_ptr() const;
  template <typename T> const T* const_data_ptr() const;
  template <typename T> T item() const;
  Scalar item() const;
  Tensor contiguous(MemoryFormat = MemoryFormat::Contiguous) const;
  Tensor to(ScalarType, bool = false, bool = false) const;
  Tensor to(Device, bool = false, bool = false) const;
  Tensor to(Device, ScalarType, bool = false, bool = false) const;
  Tensor to(TensorOptions, bool = false, bool = false) const;
  Tensor cuda() const;
  Tensor cpu() const;
  Tensor clone() const;
  Tensor detach() const;
  Tensor view(IntArrayRef) const;
  Tensor reshape(IntArrayRef) const;
  Tensor flatten(int64_t = 0, int64_t = -1) const;
  Tensor permute(IntArrayRef) const;
  Tensor transpose(int64_t, int64_t) const;
  Tensor unsqueeze(int64_t) const;
  Tensor squeeze(int64_t) const;
  Tensor squeeze() const;
  Tensor expand(IntArrayRef) const;
  Tensor slice(int64_t = 0, int64_t = 0, int64_t = 0, int64_t = 1) const;
  Tensor select(int64_t, int64_t) const;
  Tensor narrow(int64_t, int64_t, int64_t) const;
  Tensor index_select(int64_t, const Tensor&) const;
  Tensor index(std::initializer_list<TensorIndex>) const;
  Tensor& index_put_(std::initializer_list<TensorIndex>, const Tensor&);
  Tensor& index_put_(std::initializer_list<TensorIndex>, const Scalar&);
  Tensor& zero_();
  Tensor& fill_(const Scalar&);
  Tensor& copy_(const Tensor&, bool = false);
  Tensor& resize_(IntArrayRef);
  Tensor sum() const;
  Tensor sum(int64_t, bool = false) const;
  Tensor max() const;
  Tensor min() const;
  Tensor abs() const;
  Tensor cumsum(int64_t) const;
  Tensor operator[](int64_t) const;
  Tensor operator+(const Tensor&) const;
  Tensor operator-(const Tensor&) const;
  Tensor operator*(const Tensor&) const;
  Tensor operator/(const Tensor&) const;
  Tensor operator*(const Scalar&) const;
  Tensor operator==(const Scalar&) const;
  Tensor operator!=(const Scalar&) const;
  Tensor operator<(const Scalar&) const;
  Tensor operator>(const Scalar&) const;
  template <typename T, size_t N> struct Accessor { T* data() const; };
  template <typename T, size_t N> Accessor<T, N> accessor() const;
};
struct TensorIndex {
  TensorIndex(int64_t);
  TensorIndex(int);
  TensorIndex(const Tensor&);
  TensorIndex(const char*);
};
TensorOptions dtype(ScalarType);
template <typename T> TensorOptions dtype();
TensorOptions device(Device);
TensorOptions device(DeviceType);
Tensor empty(IntArrayRef, TensorOptions = {});
Tensor zeros(IntArrayRef, TensorOptions = {});
Tensor ones(IntArrayRef, TensorOptions = {});
Tensor full(IntArrayRef, const Scalar&, TensorOptions = {});
Tensor arange(const Scalar&, TensorOptions = {});
Tensor arange(const Scalar&, const Scalar&, TensorOptions = {});
Tensor empty_like(const Tensor&, TensorOptions = {});
Tensor zeros_like(const Tensor&, TensorOptions = {});
Tensor ones_like(const Tensor&, TensorOptions = {});
Tensor full_like(const Tensor&, const Scalar&, TensorOptions = {});
Tensor from_blob(void*, IntArrayRef, TensorOptions = {});
Tensor cat(std::initializer_list<Tensor>, int64_t = 0);
Tensor cat(const std::vector<Tensor>&, int64_t = 0);
Tensor stack(const std::vector<Tensor>&, int64_t = 0);
Tensor cumsum(const Tensor&, int64_t);
Tensor index_select(const Tensor&, int64_t, const Tensor&);
Tensor where(const Tensor&, const Tensor&, const Tensor&);
Tensor clamp(const Tensor&, const Scalar&, const Scalar&);
std::tuple<Tensor, Tensor> sort(const Tensor&, int64_t = -1, bool = false);
std::tuple<Tensor, Tensor> unique_consecutive(const Tensor&, bool = false, bool = false);
std::tuple<Tensor, Tensor, Tensor> _unique2(const Tensor&, bool = true, bool = false, bool = false);
namespace indexing { struct Slice { Slice(); Slice(int64_t, int64_t = 0, int64_t = 1); }; extern const char* None; }
}  // namespace at

namespace torch {
using namespace at;
using at::Tensor;
}  // namespace torch

// Checks: evaluated for parsing only.
#define TORCH_CHECK(condition, ...) ((void)(condition))
#define TORCH_CHECK_VALUE(condition, ...) ((void)(condition))
#define TORCH_INTERNAL_ASSERT(condition, ...) ((void)(condition))
#define AT_ASSERTM(condition, ...) ((void)(condition))
#define AT_ASSERT(condition, ...) ((void)(condition))
#define AT_ERROR(...) ((void)0)
#define C10_CUDA_KERNEL_LAUNCH_CHECK() ((void)0)
#define C10_CUDA_CHECK(expression) ((void)(expression))

// Type dispatch: the stand-in instantiates float (and the half types where the real macro
// adds them). The real macros also instantiate double, which Metal cannot run.
#define AT_PRIVATE_CASE(scalar, ...) [&] { using scalar_t = scalar; return __VA_ARGS__(); }()
#define AT_DISPATCH_FLOATING_TYPES(TYPE, NAME, ...) AT_PRIVATE_CASE(float, __VA_ARGS__)
#define AT_DISPATCH_FLOATING_TYPES_AND_HALF(TYPE, NAME, ...) AT_PRIVATE_CASE(float, __VA_ARGS__)
#define AT_DISPATCH_FLOATING_TYPES_AND(EXTRA, TYPE, NAME, ...) AT_PRIVATE_CASE(float, __VA_ARGS__)
#define AT_DISPATCH_FLOATING_TYPES_AND2(E1, E2, TYPE, NAME, ...) AT_PRIVATE_CASE(float, __VA_ARGS__)
#define AT_DISPATCH_INTEGRAL_TYPES(TYPE, NAME, ...) AT_PRIVATE_CASE(int, __VA_ARGS__)
#define AT_DISPATCH_ALL_TYPES(TYPE, NAME, ...) AT_PRIVATE_CASE(float, __VA_ARGS__)

namespace pybind11 {
struct module_ {
  template <typename F, typename... Extra> module_& def(const char*, F&&, const Extra&...);
};
using module = module_;
struct gil_scoped_release {};
template <typename... T> struct call_guard {};
struct arg { arg(const char*); template <typename T> arg& operator=(T&&); };
}  // namespace pybind11
namespace py = pybind11;
#define PYBIND11_MODULE(name, m) void pybind11_init_##name(pybind11::module_& m)
#define TORCH_LIBRARY(ns, m) void torch_library_init_##ns(pybind11::module_& m)
#define TORCH_LIBRARY_IMPL(ns, k, m) void torch_library_impl_##ns##_##k(pybind11::module_& m)
#ifndef TORCH_EXTENSION_NAME
#define TORCH_EXTENSION_NAME qpel_import
#endif
