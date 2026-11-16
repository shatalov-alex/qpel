// Declaration-only stand-in for PyTorch's C++ extension API (torch/extension.h), so clang can
// parse CUDA extensions offline. Only for importing: host functions are parsed, never imported,
// so this declares the common host API (Tensor, TensorOptions, dtypes, factory functions,
// TORCH_CHECK, pybind11 module macros) with simplified signatures. Device code sees the same
// scalar types as real PyTorch (c10::Half and c10::BFloat16 map to CUDA's half types).
#pragma once
#include <cuda_runtime.h>
#include <cuda_fp16.h>
#include <cuda_bf16.h>
#include <cfloat>  // FLT_MAX: ATen's headers bring it in, and extensions use it
#include <cstdint>
#include <cstddef>
#include <functional>
#include <initializer_list>
#include <limits>
#include <string>
#include <tuple>
#include <unordered_map>
#include <optional>
#include <type_traits>
#include <vector>

namespace c10 {
enum class ScalarType { Byte, Char, Short, Int, Long, Half, Float, Double, ComplexHalf, ComplexFloat, ComplexDouble,
                        Bool, QInt8, QUInt8, QInt32, BFloat16, UInt16 = 27, UInt32, UInt64, Undefined = 255 };
enum class DeviceType : int8_t { CPU = 0, CUDA = 1, MPS = 13, Meta = 9, COMPILE_TIME_MAX_DEVICE_TYPES = 21 };
enum class MemoryFormat : int8_t { Contiguous, Preserve, ChannelsLast, ChannelsLast3d };
#if defined(QPEL_SIM_HALF_IS_FLOAT16) || defined(__CUDA_FP16_H__)
// Running an original (the CPU simulator, whose __half is _Float16, or nvcc's own cuda_fp16.h):
// c10::Half is a class of its own, which libraries convert by name (`h.operator __half()`)
struct Half {
  __half x;
  Half() = default;
  __host__ __device__ Half(__half v) : x(v) {}
  __host__ __device__ Half(float f) : x((__half)f) {}
  __host__ __device__ operator __half() const { return x; }
};
#else
using Half = __half;
#endif
using BFloat16 = __nv_bfloat16;
struct Device {
  Device(DeviceType type, int8_t index = -1);
  Device(const std::string& name);
  DeviceType type() const;
  int8_t index() const;
  bool is_cuda() const;
  bool has_index() const;
  bool operator==(const Device&) const;
  bool operator!=(const Device&) const;
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
  ArrayRef slice(size_t, size_t) const;
  ArrayRef slice(size_t) const;
};
using IntArrayRef = ArrayRef<int64_t>;
using SymInt = int64_t;
using SymIntArrayRef = ArrayRef<int64_t>;
template <typename T> using optional = std::optional<T>;  // as in PyTorch 2.x
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
using c10::SymIntArrayRef;
using c10::SymInt;
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
  explicit TensorOptions(DeviceType);
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
using TensorList = c10::ArrayRef<Tensor>;
// Accessors (ATen/core/TensorAccessor.h), as device code indexes them.
template <typename T> struct DefaultPtrTraits { typedef T* PtrType; };
template <typename T> struct RestrictPtrTraits { typedef T* __restrict__ PtrType; };
template <typename T, size_t N, template <typename> class Traits = DefaultPtrTraits, typename Index = int64_t>
struct TensorAccessor {
  __host__ __device__ TensorAccessor<T, N - 1, Traits, Index> operator[](Index) const;
  __host__ __device__ Index size(Index) const;
  __host__ __device__ Index stride(Index) const;
  __host__ __device__ T* data() const;
};
template <typename T, template <typename> class Traits, typename Index> struct TensorAccessor<T, 1, Traits, Index> {
  __host__ __device__ T& operator[](Index) const;
  __host__ __device__ Index size(Index) const;
  __host__ __device__ Index stride(Index) const;
  __host__ __device__ T* data() const;
};
template <typename T, size_t N, template <typename> class Traits = DefaultPtrTraits, typename Index = int64_t>
struct PackedTensorAccessor : TensorAccessor<T, N, Traits, Index> {};
template <typename T, size_t N, template <typename> class Traits = DefaultPtrTraits>
using PackedTensorAccessor32 = PackedTensorAccessor<T, N, Traits, int32_t>;
template <typename T, size_t N, template <typename> class Traits = DefaultPtrTraits>
using PackedTensorAccessor64 = PackedTensorAccessor<T, N, Traits, int64_t>;
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
  const void* const_data_ptr() const;
  SymIntArrayRef sym_sizes() const;
  template <typename T> T item() const;
  Scalar item() const;
  Tensor contiguous(MemoryFormat = MemoryFormat::Contiguous) const;
  Tensor to(ScalarType, bool = false, bool = false) const;
  Tensor to(Device, bool = false, bool = false) const;
  Tensor to(Device, ScalarType, bool = false, bool = false) const;
  template <class O, class = typename std::enable_if<std::is_same<O, TensorOptions>::value>::type>
  Tensor to(O, bool = false, bool = false) const;  // (a template: `.to({kCPU})` picks Device)
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
  template <typename T, size_t N, template <typename> class Traits = DefaultPtrTraits, typename Index = int64_t>
  PackedTensorAccessor<T, N, Traits, Index> packed_accessor() const;
  template <typename T, size_t N, template <typename> class Traits = DefaultPtrTraits>
  PackedTensorAccessor<T, N, Traits, int32_t> packed_accessor32() const;
  template <typename T, size_t N, template <typename> class Traits = DefaultPtrTraits>
  PackedTensorAccessor<T, N, Traits, int64_t> packed_accessor64() const;
  int64_t get_device() const;
  Tensor t() const;
  Tensor lt(const Scalar&) const;
  Tensor gt(const Scalar&) const;
  Tensor nonzero() const;
  Tensor new_empty(IntArrayRef, TensorOptions = {}) const;
  Tensor new_zeros(IntArrayRef, TensorOptions = {}) const;
  Tensor masked_fill(const Tensor&, const Scalar&) const;
  Tensor masked_select(const Tensor&) const;
  Tensor& masked_fill_(const Tensor&, const Scalar&);
  std::tuple<Tensor, Tensor> sort(int64_t = -1, bool = false) const;
  Tensor argsort(int64_t = -1, bool = false) const;
  Tensor operator-() const;
  Tensor operator~() const;
  Tensor operator+(const Scalar&) const;
  Tensor operator-(const Scalar&) const;
  Tensor operator/(const Scalar&) const;
  Tensor operator&(const Tensor&) const;
  Tensor operator|(const Tensor&) const;
  Tensor operator>=(const Scalar&) const;
  Tensor operator<=(const Scalar&) const;
  Tensor& operator+=(const Tensor&);
  Tensor& operator*=(const Scalar&);
  Tensor& add_(const Tensor&, const Scalar& = 1);
  Tensor& mul_(const Scalar&);
  Tensor& sub_(const Tensor&);
  Tensor mm(const Tensor&) const;
  Tensor view(ScalarType) const;
  Tensor type_as(const Tensor&) const;
  Tensor any(int64_t = 0, bool = false) const;
  Tensor all(int64_t = 0, bool = false) const;
  Tensor& index_fill_(int64_t, const Tensor&, const Scalar&);
  MemoryFormat suggest_memory_format() const;
  bool is_non_overlapping_and_dense() const;
  Tensor& set_(const Tensor&);
  Tensor index_add(int64_t, const Tensor&, const Tensor&) const;
};
struct indexing_Slice { indexing_Slice(); indexing_Slice(int64_t, int64_t = 0, int64_t = 1); };
struct TensorIndex {
  TensorIndex(int64_t);
  TensorIndex(int);
  TensorIndex(const Tensor&);
  TensorIndex(const char*);
  TensorIndex(indexing_Slice);
};
TensorOptions dtype(ScalarType);
template <typename T> TensorOptions dtype();
TensorOptions device(Device);
TensorOptions device(DeviceType);
Tensor empty(IntArrayRef, TensorOptions = {}, std::optional<MemoryFormat> = {});
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
Tensor from_blob(void*, IntArrayRef, const std::function<void(void*)>&, TensorOptions = {});
Tensor rand(IntArrayRef, TensorOptions = {});
Tensor sparse_coo_tensor(const Tensor&, const Tensor&, IntArrayRef, TensorOptions = {});
template <class... A> Tensor sparse_coo_tensor(const Tensor&, const Tensor&, IntArrayRef, A&&...);
Tensor randint(int64_t, IntArrayRef, TensorOptions = {});
Tensor randn(IntArrayRef, TensorOptions = {});
Tensor cat(std::initializer_list<Tensor>, int64_t = 0);
Tensor cat(const std::vector<Tensor>&, int64_t = 0);
Tensor stack(const std::vector<Tensor>&, int64_t = 0);
Tensor cumsum(const Tensor&, int64_t);
Tensor index_select(const Tensor&, int64_t, const Tensor&);
Tensor where(const Tensor&, const Tensor&, const Tensor&);
Tensor clamp(const Tensor&, const Scalar&, const Scalar&);
std::tuple<Tensor, Tensor> sort(const Tensor&, int64_t = -1, bool = false);
std::tuple<Tensor, Tensor, Tensor> unique_dim(const Tensor&, int64_t, bool = true, bool = false, bool = false);
std::tuple<Tensor, Tensor> _unique(const Tensor&, bool = true, bool = false);
Tensor& mm_out(Tensor&, const Tensor&, const Tensor&);
Tensor mm(const Tensor&, const Tensor&);
Tensor matmul(const Tensor&, const Tensor&);
Tensor nonzero(const Tensor&);
Tensor sum(const Tensor&, int64_t, bool = false);
Device device_of(const Tensor&);
namespace cuda {
template <typename T> __host__ __device__ T ATenCeilDiv(T a, T b) { return (a + b - 1) / b; }
inline void MaybeSetDevice(int) {}
}
std::tuple<Tensor, Tensor> unique_consecutive(const Tensor&, bool = false, bool = false);
std::tuple<Tensor, Tensor, Tensor> _unique2(const Tensor&, bool = true, bool = false, bool = false);
namespace indexing { using Slice = indexing_Slice; extern const char* None; }
}  // namespace at

namespace c10 {
using DeviceIndex = int8_t;
template <class C> int64_t multiply_integers(const C&);
std::string DeviceTypeName(DeviceType, bool = false);
template <class T> bool operator==(ArrayRef<T>, ArrayRef<T>);
template <class T> bool operator!=(ArrayRef<T>, ArrayRef<T>);
using std::nullopt;
using std::nullopt_t;
namespace cuda {
namespace CUDACachingAllocator {
void* raw_alloc(size_t); void* raw_alloc_with_stream(size_t, cudaStream_t); void raw_delete(void*);
struct DataPtr { void* get() const; };
struct CUDAAllocator { DataPtr allocate(size_t); void* raw_allocate(size_t); void raw_deallocate(void*); };
CUDAAllocator* get();
}
inline void MaybeSetDevice(int) {} inline DeviceIndex current_device() { return 0; } inline DeviceIndex device_count() { return 1; } }
// c10/util/irange.h
template <class T> struct integer_range { struct iterator { T v; T operator*() const { return v; } iterator& operator++() { ++v; return *this; } bool operator!=(iterator o) const { return v != o.v; } }; T b, e; iterator begin() const { return {b}; } iterator end() const { return {e}; } };
template <class T> integer_range<T> irange(T end) { return {T(0), end}; }
template <class T, class U> integer_range<T> irange(T begin, U end) { return {begin, T(end)}; }
template <class T> struct intrusive_ptr { T* operator->() const; T& operator*() const; T* get() const; };
template <class T, class... A> intrusive_ptr<T> make_intrusive(A&&...);
template <class T> struct List { size_t size() const; T get(size_t) const; };
// ATen/core/dispatch/Dispatcher.h: calling a registered operator (host code).
template <class Sig> struct TypedOperatorHandle { template <class... A> auto call(A&&...) const; };
struct OperatorHandle { template <class Sig> TypedOperatorHandle<Sig> typed() const; };
struct Dispatcher { static Dispatcher& singleton(); OperatorHandle findSchemaOrThrow(const char*, const char*); };
struct IValue {
  IValue();
  template <class T> IValue(T&&);
  template <class T> T to() const;
  at::Tensor toTensor() const;
  bool isNone() const;
  bool isTensor() const;
  int64_t toInt() const;
  double toDouble() const;
  bool toBool() const;
  std::vector<at::Tensor> toTensorVector() const;
  template <class T> c10::List<T> toList() const;
  template <class T> c10::intrusive_ptr<T> toCustomClass() const;
};
}

namespace at {
template <class T> struct OpMathType { using type = T; };  // ATen/OpMathType.h: half types compute in float
template <> struct OpMathType<c10::Half> { using type = float; };
template <> struct OpMathType<c10::BFloat16> { using type = float; };
template <class T> using opmath_type = typename OpMathType<T>::type;
struct GradMode { static bool is_enabled(); static void set_enabled(bool); };
struct AutoGradMode { AutoGradMode(bool); };
struct NoGradGuard {};
using c10::DeviceTypeName;
}  // namespace at

namespace torch {
using namespace at;
namespace autograd {
struct AutogradContext {
  template <class... T> void save_for_backward(T&&...);
  std::vector<Tensor> get_saved_variables() const;
  std::unordered_map<std::string, c10::IValue> saved_data;
  void mark_non_differentiable(const std::vector<Tensor>&);
  void set_materialize_grads(bool);
  bool needs_input_grad(size_t) const;
};
template <class T> struct Function { template <class... A> static auto apply(A&&...); };
using variable_list = std::vector<Tensor>;
}  // namespace autograd
struct CustomClassHolder { virtual ~CustomClassHolder() = default; };  // torch/custom_class.h
template <class T> struct class_ { class_(const std::string&, const std::string&); };
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
#define C10_CUDA_CHECK_WARN(expression) ((void)(expression))
#define AT_CUDA_CHECK(expression) ((void)(expression))
#define C10_CUDA_KERNEL_LAUNCH_CHECK_() ((void)0)

// Type dispatch: the stand-in instantiates float (and the half types where the real macro
// adds them). The real macros also instantiate double, which Metal cannot run.
#define AT_PRIVATE_CASE(scalar, ...) [&] { using scalar_t = scalar; return __VA_ARGS__(); }()
// ATen/Dispatch_v2.h: AT_DISPATCH_V2(TYPE, NAME, AT_WRAP(body), types...)
#define AT_WRAP(...) __VA_ARGS__
#define AT_DISPATCH_V2(TYPE, NAME, BODY, ...) AT_PRIVATE_CASE(float, BODY)
#define AT_DISPATCH_FLOATING_TYPES(TYPE, NAME, ...) AT_PRIVATE_CASE(float, __VA_ARGS__)
#define AT_DISPATCH_FLOATING_TYPES_AND_HALF(TYPE, NAME, ...) AT_PRIVATE_CASE(float, __VA_ARGS__)
#define AT_DISPATCH_FLOATING_TYPES_AND(EXTRA, TYPE, NAME, ...) AT_PRIVATE_CASE(float, __VA_ARGS__)
#define AT_DISPATCH_FLOATING_TYPES_AND2(E1, E2, TYPE, NAME, ...) AT_PRIVATE_CASE(float, __VA_ARGS__)
#define AT_DISPATCH_INTEGRAL_TYPES(TYPE, NAME, ...) AT_PRIVATE_CASE(int, __VA_ARGS__)
#define AT_DISPATCH_ALL_TYPES(TYPE, NAME, ...) AT_PRIVATE_CASE(float, __VA_ARGS__)
#define AT_DISPATCH_ALL_TYPES_AND(E1, TYPE, NAME, ...) AT_PRIVATE_CASE(float, __VA_ARGS__)
#define AT_DISPATCH_ALL_TYPES_AND2(E1, E2, TYPE, NAME, ...) AT_PRIVATE_CASE(float, __VA_ARGS__)
#define AT_DISPATCH_ALL_TYPES_AND3(E1, E2, E3, TYPE, NAME, ...) AT_PRIVATE_CASE(float, __VA_ARGS__)
#define AT_DISPATCH_ALL_TYPES_AND_COMPLEX(TYPE, NAME, ...) AT_PRIVATE_CASE(float, __VA_ARGS__)
#define AT_DISPATCH_ALL_TYPES_AND_COMPLEX_AND2(E1, E2, TYPE, NAME, ...) AT_PRIVATE_CASE(float, __VA_ARGS__)
#define AT_DISPATCH_FLOATING_TYPES_AND3(E1, E2, E3, TYPE, NAME, ...) AT_PRIVATE_CASE(float, __VA_ARGS__)
#define AT_DISPATCH_INDEX_TYPES(TYPE, NAME, ...) [&] { using index_t = int64_t; return __VA_ARGS__(); }()

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
