# Generated CUDA host (initial implementation)

The package builder emits cuda_host.py alongside metal_host.py from the same checked Pipeline. No MPS model changes are needed. Both interfaces accept inputs and named (W,H,N) shapes and return flat output arrays. CUDA uses CuPy arrays and a nonnegative integer device ID (default 0); Metal uses PyTorch tensors. Automatic interchange between the frameworks is not implemented.

CUDA requires CuPy, NumPy, an NVIDIA device and a compatible CUDA/NVRTC installation. Nothing is installed automatically. CuPy RawModule compiles the generated .cu with C++17 on first use. Modules are cached per device, and source hashes are checked before creating each cached module. Cached modules retain their source until the process restarts or the cache is cleared.

Input names, shape dimensions/product, array type, device, dtype and element count are checked before allocation/compilation. Strided inputs are copied to contiguous storage on the selected device's current stream. Callers must establish dependencies for input writes on other streams. Each stage launches ceil(element_count/blockSize) blocks and synchronizes that stream before continuing. Exceptions stop the pipeline. This is synchronous and deliberately untuned.

QpelShape is passed by value as a NumPy structured scalar with uint32 fields at offsets 0,4,8,12 and total size 16. The fourth field is zero padding. Unlike Metal, this is not a device-buffer pointer. u32/atomic buffers retain the existing host convention of int32 backing storage; CUDA kernels interpret those bits as unsigned. Float16/float32 and uint8 storage follow the common resource map.

The compiler still does not prove per-element bounds or race freedom. CUDA pads the last group, so qpel programs must guard accesses; collective kernels must contribute zero for padded lanes and reach every barrier.

Validation: 113 tests passed using the project Python environment (NumPy available), including six new host/package tests. Recorders verify ABI bytes, bindings, scratch reuse between stages, launch sizes, stream completion, input rejection, per-device cache, hash rejection and failure propagation. These are not CUDA execution tests. Both host packages were generated for all 12 native fixtures. Actual NVRTC compilation and NVIDIA execution remain pending; no performance or production-readiness claim is made.

Optional GPU runner: outputs/qpel-cuda-host/test_cuda.py in the task workspace. Run on an NVIDIA machine with the adjacent workgroup/generated package. It covers five counts including partial groups and strided input. Real NVIDIA testing remains deferred at the user's request.

API references: https://docs.cupy.dev/en/stable/user_guide/kernel.html (Raw kernels, Custom user types); https://docs.cupy.dev/en/stable/reference/generated/cupy.RawModule.html.
