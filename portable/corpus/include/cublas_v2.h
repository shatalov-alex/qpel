// Declaration-only stub: the cuBLAS names llama.cpp's common.cuh mentions.
#pragma once
typedef struct cublasContext* cublasHandle_t;
typedef enum { CUBLAS_STATUS_SUCCESS = 0, CUBLAS_STATUS_NOT_INITIALIZED = 1, CUBLAS_STATUS_ALLOC_FAILED = 3,
  CUBLAS_STATUS_INVALID_VALUE = 7, CUBLAS_STATUS_ARCH_MISMATCH = 8, CUBLAS_STATUS_MAPPING_ERROR = 11,
  CUBLAS_STATUS_EXECUTION_FAILED = 13, CUBLAS_STATUS_INTERNAL_ERROR = 14, CUBLAS_STATUS_NOT_SUPPORTED = 15 } cublasStatus_t;
enum { CUBLAS_DEFAULT_MATH = 0, CUBLAS_TENSOR_OP_MATH = 1, CUBLAS_TF32_TENSOR_OP_MATH = 3 };
extern "C" const char* cublasGetStatusString(cublasStatus_t);
extern "C" cublasStatus_t cublasCreate_v2(cublasHandle_t*);
extern "C" cublasStatus_t cublasSetMathMode(cublasHandle_t, int);
#define cublasCreate cublasCreate_v2
