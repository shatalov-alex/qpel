#pragma once
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>
struct Case { std::string name; unsigned w,h,n; std::vector<unsigned char> mask; };
struct Answer { std::vector<unsigned> labels, areas; };
inline Answer oracle(const Case& c) {
    Answer a{std::vector<unsigned>(c.mask.size()), std::vector<unsigned>(c.mask.size())};
    unsigned plane=c.w*c.h;
    for(unsigned b=0;b<c.n;b++) for(unsigned j=0;j<plane;j++) {
        unsigned start=b*plane+j;
        if(!c.mask[start] || a.labels[start]) continue;
        std::vector<unsigned> queue{start}; a.labels[start]=j+1;
        for(size_t k=0;k<queue.size();k++) {
            unsigned i=queue[k], local=i-b*plane;
            int x=local%c.w, y=local/c.w;
            for(int dy=-1;dy<=1;dy++) for(int dx=-1;dx<=1;dx++) {
                int nx=x+dx,ny=y+dy;
                if(nx<0||ny<0||nx>=int(c.w)||ny>=int(c.h))continue;
                unsigned next=b*plane+ny*c.w+nx;
                if(c.mask[next]&&!a.labels[next]) {a.labels[next]=j+1;queue.push_back(next);}
            }
        }
        for(unsigned i:queue)a.areas[i]=unsigned(queue.size());
    }
    return a;
}
inline std::vector<Case> cases() {
    std::vector<Case> v;
    auto add=[&](std::string name,unsigned w,unsigned h,unsigned n,unsigned char fill) -> Case& {
        v.push_back({name,w,h,n,std::vector<unsigned char>(w*h*n,fill)});return v.back();
    };
    add("empty",8,8,1,0);add("full",8,8,1,1);
    auto& diagonal=add("diagonal",8,8,1,0);for(int i=0;i<8;i++)diagonal.mask[i*9]=1;
    auto& islands=add("islands",9,9,1,0);for(int y=0;y<9;y+=2)for(int x=0;x<9;x+=2)islands.mask[y*9+x]=1;
    add("batch isolation",7,5,3,1);add("nonbinary",3,5,2,255);
    add("odd dimensions",13,11,1,1);add("single pixel",1,1,1,1);
    std::mt19937 rng(20260924);
    for(unsigned k=0;k<125;k++) {
        auto& c=add("random-"+std::to_string(k),1+rng()%35,1+rng()%29,1+rng()%4,0);
        unsigned density=(k*13)%101;
        for(auto& x:c.mask)x=(rng()%100<density)?static_cast<unsigned char>(1+rng()%255):0;
    }
    return v;
}
template<class Run> int verify(Run run) {
    auto all=cases();
    for(const auto& c:all) {
        auto expected=oracle(c),actual=run(c);
        if(actual.labels!=expected.labels || actual.areas!=expected.areas)throw std::runtime_error("Mismatch: "+c.name);
    }
    std::cout<<"PASS: "<<all.size()<<" cases; exact labels/areas against independent CPU flood-fill\n";
    return 0;
}
