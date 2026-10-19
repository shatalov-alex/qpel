// Serial CPU execution validates emitted control flow, not GPU concurrency.
#include <atomic>
#include "cases.hpp"
struct Shape { unsigned x,y,z; };
using std::min; using std::max;
inline unsigned atomic_min(std::atomic<unsigned>* p,unsigned v) {
    unsigned old=p->load(std::memory_order_relaxed);
    while(old>v&&!p->compare_exchange_weak(old,v,std::memory_order_relaxed)){}
    return old;
}
#define QPEL_KERNEL
#define QPEL_DEVICE
#define QPEL_U8 unsigned char
#define QPEL_U32 unsigned
#define QPEL_READ_U8 const unsigned char*
#define QPEL_ATOMIC_U32 std::atomic<unsigned>*
#define QPEL_WRITE_U32 unsigned*
#define QPEL_SHAPE Shape
#define QPEL_PARAM(type,name,binding) type name
#define QPEL_GRID_PARAM(name) , unsigned name
#define QPEL_GRID_LOCAL(name)
#define QPEL_LOAD(p) (p)->load(std::memory_order_relaxed)
#define QPEL_STORE(p,v) (p)->store((v),std::memory_order_relaxed)
#define QPEL_ADD(p,v) (p)->fetch_add((v),std::memory_order_relaxed)
#define QPEL_MIN(p,v) atomic_min((p),(v))
#include "components.qpel.h"
Answer run(const Case& c) {
    auto size=c.mask.size();
    std::vector<std::atomic<unsigned>> parentStorage(size),countsStorage(size);
    Answer a{std::vector<unsigned>(size),std::vector<unsigned>(size)};
    Shape shape{c.w,c.h,c.n};
    const unsigned char* mask=c.mask.data();
    auto* parent=parentStorage.data();auto* counts=countsStorage.data();
    auto* labels=a.labels.data();auto* output=a.areas.data();
#define QPEL_STAGE(kernel, grid, block, ...) for(unsigned i=0;i<(grid).x*(grid).y*(grid).z;i++)kernel(__VA_ARGS__,i);
#include "pipeline.inc"
#undef QPEL_STAGE
    return a;
}
int main(){try{return verify(run);}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
