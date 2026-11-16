#pragma once
// Stand-in for PyTorch's <ATen/cuda/cub.h> (importing only): the declarations are in torch/extension.h.
#include <torch/extension.h>

namespace at { namespace cuda { namespace cub {
// ATen/cuda/cub.h: CUB's radix sort as PyTorch wraps it (gsplat sorts its batch keys with it)
template <typename key_t, typename value_t, int value_size = sizeof(value_t)>
void radix_sort_pairs(const key_t* keys_in, key_t* keys_out, const value_t* values_in, value_t* values_out,
                      int64_t n, bool descending = false, int64_t begin_bit = 0, int64_t end_bit = sizeof(key_t) * 8);
template <typename key_t>
void radix_sort_keys(const key_t* keys_in, key_t* keys_out, int64_t n, bool descending = false,
                     int64_t begin_bit = 0, int64_t end_bit = sizeof(key_t) * 8);
}}}  // namespace at::cuda::cub
