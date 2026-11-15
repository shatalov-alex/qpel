#pragma once
// Stand-in for PyTorch's c10::complex (importing only): a pair of values. qpel has no complex
// arithmetic, so kernels that compute with it report their own blockers.
#include <torch/extension.h>
namespace c10 {
template <typename T> struct complex {
  T real_, imag_;
  __host__ __device__ constexpr complex(T re = T(), T im = T()) : real_(re), imag_(im) {}
  __host__ __device__ constexpr T real() const { return real_; }
  __host__ __device__ constexpr T imag() const { return imag_; }
};
template <typename T> __host__ __device__ complex<T> operator+(complex<T> a, complex<T> b) {
  return complex<T>(a.real_ + b.real_, a.imag_ + b.imag_);
}
template <typename T> __host__ __device__ complex<T> operator-(complex<T> a, complex<T> b) {
  return complex<T>(a.real_ - b.real_, a.imag_ - b.imag_);
}
template <typename T> __host__ __device__ complex<T> operator*(complex<T> a, complex<T> b) {
  return complex<T>(a.real_ * b.real_ - a.imag_ * b.imag_, a.real_ * b.imag_ + a.imag_ * b.real_);
}
// with a real value on either side, as c10::complex has
template <typename T> __host__ __device__ complex<T> operator*(complex<T> a, T b) { return complex<T>(a.real_ * b, a.imag_ * b); }
template <typename T> __host__ __device__ complex<T> operator*(T a, complex<T> b) { return complex<T>(a * b.real_, a * b.imag_); }
template <typename T> __host__ __device__ complex<T> operator+(complex<T> a, T b) { return complex<T>(a.real_ + b, a.imag_); }
template <typename T> __host__ __device__ complex<T> operator+(T a, complex<T> b) { return complex<T>(a + b.real_, b.imag_); }
template <typename T> __host__ __device__ complex<T> operator-(complex<T> a, T b) { return complex<T>(a.real_ - b, a.imag_); }
template <typename T> __host__ __device__ complex<T>& operator+=(complex<T>& a, complex<T> b) { return a = a + b; }
template <typename T> __host__ __device__ complex<T>& operator*=(complex<T>& a, complex<T> b) { return a = a * b; }
template <typename T> __host__ __device__ complex<T> conj(complex<T> a) { return complex<T>(a.real_, -a.imag_); }
template <typename T> __host__ __device__ complex<T> exp(complex<T> a);
template <typename T> struct scalar_value_type { using type = T; };
template <typename T> struct scalar_value_type<complex<T>> { using type = T; };
}  // namespace c10
namespace std {  // PyTorch adds these for c10::complex
template <typename T> __host__ __device__ c10::complex<T> conj(c10::complex<T> a) { return c10::conj(a); }
template <typename T> __host__ __device__ c10::complex<T> exp(c10::complex<T> a) { return c10::exp(a); }
}  // namespace std
