#pragma once
#include <cuda_runtime.h>
// Driver API names llama.cpp's common.cuh mentions.
typedef int CUresult;
typedef int CUdevice;
enum { CUDA_SUCCESS = 0 };
extern "C" CUresult cuGetErrorString(CUresult, const char**);
