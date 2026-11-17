// Declaration-only stub: the cuBLASLt types bitsandbytes' compat.cuh names (its kernels do not call cuBLASLt).
#pragma once
#include "cublas_v2.h"
typedef struct cublasLtContext* cublasLtHandle_t;
typedef struct cublasLtMatmulDescOpaque_t* cublasLtMatmulDesc_t;
typedef struct cublasLtMatrixLayoutOpaque_t* cublasLtMatrixLayout_t;
extern "C" cublasStatus_t cublasLtCreate(cublasLtHandle_t*);
