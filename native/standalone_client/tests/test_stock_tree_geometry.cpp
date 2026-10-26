#include "stock_tree_geometry.hpp"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <cstring>
#include <fstream>
#include <iostream>

namespace
{
void word(std::vector<std::uint8_t>& bytes,std::uint32_t value){for(unsigned i=0;i<4;++i)bytes.push_back(static_cast<std::uint8_t>(value>>(i*8)));}
void scalar(std::vector<std::uint8_t>& bytes,float value){std::uint32_t bits;std::memcpy(&bits,&value,4);word(bytes,bits);}
void path(std::vector<std::uint8_t>& bytes,const std::string& value){word(bytes,static_cast<std::uint32_t>(value.size()));bytes.insert(bytes.end(),value.begin(),value.end());}
std::vector<std::uint8_t> serialise(const wot::StockTreeGeometry& geometry){
    std::vector<std::uint8_t> bytes;word(bytes,106);
    for(float value:geometry.minimumBounds)scalar(bytes,value);
    for(float value:geometry.maximumBounds)scalar(bytes,value);
    scalar(bytes,geometry.leafRockScalar);scalar(bytes,geometry.leafRustleScalar);
    for(const auto& part:geometry.parts){
        word(bytes,part.vertexCount);bytes.insert(bytes.end(),part.vertices.begin(),part.vertices.end());
        word(bytes,static_cast<std::uint32_t>(part.lodIndices.size()));
        for(const auto& lod:part.lodIndices){word(bytes,static_cast<std::uint32_t>(lod.size()));for(auto index:lod)word(bytes,index);}
        path(bytes,part.diffuseMap);path(bytes,part.normalMap);
    }
    return bytes;
}
}
int main(int argc,char** argv){
    if(argc==3){
        std::ifstream input(argv[1],std::ios::binary);std::ofstream output(argv[2],std::ios::binary);
        if(!input||!output)return 2;
        std::uint32_t size;
        while(input.read(reinterpret_cast<char*>(&size),4)){
            if(size>16*1024*1024)return 2;
            std::vector<std::uint8_t> source(size);if(!input.read(reinterpret_cast<char*>(source.data()),size))return 2;
            wot::StockTreeGeometry geometry;std::string error;
            if(!wot::decodeStockTreeGeometry(source.data(),source.size(),geometry,error)){std::cerr<<error<<std::endl;return 1;}
            const auto decoded=serialise(geometry);const std::uint32_t count=static_cast<std::uint32_t>(decoded.size());
            output.write(reinterpret_cast<const char*>(&count),4);output.write(reinterpret_cast<const char*>(decoded.data()),count);
        }
        return input.eof()&&output?0:2;
    }
    assert(argc==1);wot::StockTreeGeometry original={};
    original.minimumBounds={{-1,-2,-3}};original.maximumBounds={{1,2,3}};
    original.leafRockScalar=.5f;original.leafRustleScalar=.2f;
    const unsigned strides[]={52,52,88,68};
    for(unsigned i=0;i<4;++i){
        auto& part=original.parts[i];part.vertexCount=1;part.vertexStride=strides[i];
        part.vertices.assign(strides[i],static_cast<std::uint8_t>(i));part.lodIndices={{0}};
        part.diffuseMap=u8"speedtree/\u6811/actual.dds";part.normalMap=i%2?"":"speedtree/actual_Normal.dds";
    }
    const auto source=serialise(original);std::string error;wot::StockTreeGeometry result;
    assert(wot::decodeStockTreeGeometry(source.data(),source.size(),result,error)&&error.empty());
    assert(serialise(result)==source);
    for(std::size_t size=0;size<source.size();++size){
        assert(!wot::decodeStockTreeGeometry(source.data(),size,result,error)&&!error.empty());assert(serialise(result)==source);
    }
    assert(!wot::decodeStockTreeGeometry(NULL,source.size(),result,error));
    auto reject=[&](std::vector<std::uint8_t> bad){assert(!wot::decodeStockTreeGeometry(bad.data(),bad.size(),result,error)&&!error.empty());assert(serialise(result)==source);};
    auto bad=source;bad[0]=105;reject(bad);
    bad=source;bad.push_back(0);reject(bad);
    for(unsigned offset:{36u,92u,96u}){bad=source;std::memset(bad.data()+offset,0xff,4);reject(bad);}
    bad=source;bad[100]=1;reject(bad); // The first LOD has only vertex zero.
    bad=source;const float inverted=10;std::memcpy(bad.data()+4,&inverted,4);reject(bad);
    bad=source;const std::uint32_t nan=0x7fc00000;std::memcpy(bad.data()+28,&nan,4);reject(bad);
    bad=source;bad[108]=0;reject(bad); // Embedded NUL in the first texture path.
    wot::StockTreeGeometry empty={};for(unsigned i=0;i<4;++i)empty.parts[i].vertexStride=strides[i];
    auto emptyBytes=serialise(empty);assert(wot::decodeStockTreeGeometry(emptyBytes.data(),emptyBytes.size(),result,error));
    assert(serialise(result)==emptyBytes);
    std::cout<<"Version-106 geometry bounds, vertex/LOD/index ownership, Unicode maps and strict EOF passed"<<std::endl;
}
