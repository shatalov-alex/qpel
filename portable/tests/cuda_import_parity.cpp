// CPU execution of the unchanged generated CUDA body; no CUDA runtime validation.
#include <algorithm>
#include <iostream>
#include <numeric>
#include <random>
#include <stdexcept>
#include <vector>
// CUDA uint3 in the source has x/y/z; generated host ABI uses padded QpelShape.
struct uint3 { unsigned x,y,z; };
#define apply_threshold original_threshold
#include "original.cu"
#undef apply_threshold
#include "cuda_threshold.cu"
void check(unsigned w,unsigned h,unsigned n,unsigned mode,unsigned block,std::mt19937& rng) {
    unsigned size=w*h*n;
    std::vector<unsigned char> input(size),threshold(size);
    for(unsigned i=0;i<size;i++){
        if(mode==0){input[i]=i/256;threshold[i]=i%256;}
        else if(mode==1){input[i]=127;threshold[i]=128;}
        else if(mode==2){input[i]=128;threshold[i]=128;}
        else if(mode==3){input[i]=255;threshold[i]=0;}
        else if(mode==4){input[i]=0;threshold[i]=255;}
        else {input[i]=rng()%256;threshold[i]=rng()%256;}
    }
    std::vector<unsigned> output(size+256,0xDEADBEEF), original(size+256,0xDEADBEEF);
    auto* intensity=input.data();auto* cutoff=threshold.data();auto* binary=output.data();
    QpelShape dimensions{w,h,n,0};
    // Execute padded logical threads in shuffled order, including out-of-range indices.
#define QPEL_STAGE(kernel, grid, modelBlock, ...) \
    {unsigned bs=block?block:modelBlock;unsigned padded=((size+bs-1)/bs)*bs; \
    std::vector<unsigned> order(padded);std::iota(order.begin(),order.end(),0); \
    std::shuffle(order.begin(),order.end(),rng);blockDim={bs,1,1}; \
    for(unsigned id:order){blockIdx={id/bs,0,0};threadIdx={id%bs,0,0};kernel(__VA_ARGS__);original_threshold(intensity,cutoff,uint3{w,h,n},original.data());}}
#include "pipeline.inc"
#undef QPEL_STAGE
    for(unsigned i=0;i<size;i++)if(output[i]!=unsigned(input[i]>=threshold[i]) || original[i]!=output[i])throw std::runtime_error("Threshold mismatch");
    for(unsigned i=size;i<output.size();i++)if(output[i]!=0xDEADBEEF || original[i]!=0xDEADBEEF)throw std::runtime_error("Padded thread wrote outside output");
}
int main(int argc,char** argv){
    try{
        std::mt19937 rng(20260925);unsigned passed=0;
        std::vector<unsigned> blocks=argc>1?std::vector<unsigned>{0}:std::vector<unsigned>{1,0,256};
        for(unsigned block:blocks){
            check(256,256,1,0,block,rng);passed++;
            for(unsigned mode=1;mode<=4;mode++){check(1,1,1,mode,block,rng);passed++;}
            check(7,9,3,5,block,rng);passed++;
            for(unsigned k=0;k<128;k++){check(1+rng()%65,1+rng()%35,1+rng()%4,5,block,rng);passed++;}
        }
        std::cout<<"PASS: "<<passed<<" CUDA import parity cases; original CUDA = generated CUDA = independent oracle; all 65536 uint8 pairs, boundaries, batches, odd sizes and padded-thread guards\n";
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
