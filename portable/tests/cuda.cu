#include "cases.hpp"
#include "components.cu"
inline void check(cudaError_t error) {
    if(error!=cudaSuccess)throw std::runtime_error(cudaGetErrorString(error));
}
template<class T> struct Buffer {
    T* data=nullptr;
    explicit Buffer(size_t n){check(cudaMalloc(reinterpret_cast<void**>(&data),n*sizeof(T)));}
    ~Buffer(){cudaFree(data);}
    Buffer(const Buffer&)=delete;Buffer& operator=(const Buffer&)=delete;
};
Answer run(const Case& c) {
    size_t size=c.mask.size(),bytes=size*sizeof(unsigned);
    Buffer<unsigned char> mask(size);
    Buffer<unsigned> parent(size),counts(size),labels(size),output(size);
    check(cudaMemcpy(mask.data,c.mask.data(),size,cudaMemcpyHostToDevice));
    QpelShape shape{c.w,c.h,c.n,c.w*c.h*c.n};
    // Bind host allocations by resource names; every generated stage completes.
    auto* maskPtr=mask.data;auto* parentPtr=parent.data;auto* countsPtr=counts.data;
    auto* labelsPtr=labels.data;auto* outputPtr=output.data;
#define mask maskPtr
#define parent parentPtr
#define counts countsPtr
#define labels labelsPtr
#define output outputPtr
#define QPEL_STAGE(kernel, grid, block, ...) kernel<<<((grid).x*(grid).y*(grid).z+(block)-1)/(block),block>>>(__VA_ARGS__);check(cudaGetLastError());check(cudaDeviceSynchronize());
#include "pipeline.inc"
#undef QPEL_STAGE
#undef mask
#undef parent
#undef counts
#undef labels
#undef output
    Answer a{std::vector<unsigned>(size),std::vector<unsigned>(size)};
    check(cudaMemcpy(a.labels.data(),labels.data,bytes,cudaMemcpyDeviceToHost));
    check(cudaMemcpy(a.areas.data(),output.data,bytes,cudaMemcpyDeviceToHost));
    return a;
}
int main(){try{cudaDeviceProp p{};check(cudaGetDeviceProperties(&p,0));std::cout<<"CUDA GPU: "<<p.name<<'\n';return verify(run);}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
