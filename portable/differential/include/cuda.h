#pragma once
#include "cuda_runtime.h"
// Driver API names library headers mention (declarations only).
typedef int CUresult;
typedef int CUdevice;
enum { CUDA_SUCCESS = 0 };
extern "C" CUresult cuGetErrorString(CUresult, const char**);
