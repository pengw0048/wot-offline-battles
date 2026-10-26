#include "stock_tree_materials.hpp"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <cstring>
#include <fstream>
#include <iostream>
#include <vector>

namespace
{
void word(std::vector<std::uint8_t>& bytes,std::uint32_t value){for(unsigned i=0;i<4;++i)bytes.push_back(static_cast<std::uint8_t>(value>>(i*8)));}
void scalar(std::vector<std::uint8_t>& bytes,float value){std::uint32_t bits;std::memcpy(&bits,&value,4);word(bytes,bits);}
std::vector<std::uint8_t> section(){
    std::vector<std::uint8_t> bytes;word(bytes,8000);
    for(unsigned token=8002;token<=8009;++token){
        word(bytes,token);
        if(token==8003||token==8005||token==8009)for(unsigned i=0;i<13;++i)scalar(bytes,(float(token-8003)+float(i))*.1f);
        else if(token==8006)scalar(bytes,.75f);
        else word(bytes,token-8002);
    }
    word(bytes,8001);return bytes;
}
void writeFields(std::ofstream& output,const wot::StockTreeMaterials& material){
    const auto write=[&](const void* bytes,std::size_t size){output.write(static_cast<const char*>(bytes),static_cast<std::streamsize>(size));};
    write(&material.lightingStyles[0],4);write(material.branch.data(),52);
    write(&material.lightingStyles[1],4);write(material.leaf.data(),52);
    write(&material.leafLightingAdjustment,4);write(&material.lightingStyles[2],8);write(material.frond.data(),52);
}
}
int main(int argc,char** argv){
    if(argc==3){
        std::ifstream input(argv[1],std::ios::binary);std::ofstream output(argv[2],std::ios::binary);
        if(!input||!output)return 2;
        std::uint32_t length;
        while(input.read(reinterpret_cast<char*>(&length),4)){
            if(length>16*1024*1024)return 2;
            std::vector<std::uint8_t> bytes(length);if(!input.read(reinterpret_cast<char*>(bytes.data()),length))return 2;
            wot::StockTreeMaterials material;std::size_t used=0;std::string error;
            const bool file=bytes.size()>=4&&bytes[0]==0xe8&&bytes[1]==3&&bytes[2]==0&&bytes[3]==0;
            const bool accepted=file?wot::decodeStockTreeMaterialPrefix(bytes.data(),bytes.size(),material,used,error):
                wot::decodeStockTreeLightingSection(bytes.data(),bytes.size(),material,used,error);
            if(!accepted){std::cerr<<error<<std::endl;return 1;}
            const std::uint32_t consumed=static_cast<std::uint32_t>(used);output.write(reinterpret_cast<const char*>(&consumed),4);
            writeFields(output,material);const auto packed=wot::packStockTreeMaterials(material);
            output.write(reinterpret_cast<const char*>(packed.data()),static_cast<std::streamsize>(packed.size()));
        }
        return input.eof()&&output?0:2;
    }
    assert(argc==1);auto bytes=section();wot::StockTreeMaterials material;std::string error;std::size_t consumed=0;
    assert(wot::decodeStockTreeLightingSection(bytes.data(),bytes.size(),material,consumed,error)&&consumed==bytes.size()&&error.empty());
    const auto before=material;
    for(std::size_t size=0;size<bytes.size();++size){
        consumed=1234;assert(!wot::decodeStockTreeLightingSection(bytes.data(),size,material,consumed,error));
        assert(!error.empty()&&consumed==1234&&std::memcmp(&before,&material,sizeof(material))==0);
    }
    consumed=1234;assert(!wot::decodeStockTreeLightingSection(NULL,bytes.size(),material,consumed,error)&&consumed==1234);
    auto wrong=bytes;wrong[0]=0;assert(!wot::decodeStockTreeLightingSection(wrong.data(),wrong.size(),material,consumed,error));
    wrong=bytes;wrong[4]=0xff;assert(!wot::decodeStockTreeLightingSection(wrong.data(),wrong.size(),material,consumed,error));
    std::vector<std::uint8_t> incomplete;word(incomplete,8000);word(incomplete,8001);
    assert(!wot::decodeStockTreeLightingSection(incomplete.data(),incomplete.size(),material,consumed,error));
    auto repeated=bytes;repeated.resize(bytes.size()-4);word(repeated,8006);scalar(repeated,.25f);word(repeated,8001);
    assert(wot::decodeStockTreeLightingSection(repeated.data(),repeated.size(),material,consumed,error)&&material.leafLightingAdjustment==.25f);
    const auto packed=wot::packStockTreeMaterials(material);
    for(std::uint32_t handle:{0u,1u,31u,32u,0xffffffffu}){
        std::array<std::uint8_t,1024> atlas;atlas.fill(0xcc);wot::writeStockTreeMaterialAtlasSlot(material,handle,atlas);
        const auto slot=handle<32?handle:31;
        for(unsigned i=0;i<1024;++i)assert(atlas[i]==(i>=slot*32&&i<(slot+1)*32?packed[i-slot*32]:0xcc));
    }
    bytes.push_back(1);bytes.push_back(2);
    assert(wot::decodeStockTreeLightingSection(bytes.data(),bytes.size(),material,consumed,error)&&consumed+2==bytes.size());
    std::vector<std::uint8_t> file;word(file,1000);word(file,12);
    const char version[]="__IdvSpt_02_";file.insert(file.end(),version,version+12);
    word(file,1002);word(file,2000);
    // A complete lighting block inside a profile/string is not a section.
    word(file,static_cast<std::uint32_t>(bytes.size()));file.insert(file.end(),bytes.begin(),bytes.end());
    word(file,1014);word(file,0);word(file,1015);word(file,1003);
    word(file,1004);word(file,3003);file.push_back(1);word(file,1009);word(file,1006);word(file,1);
    word(file,1007);word(file,4001);for(float f:{1.f,2.f,3.f})scalar(file,f);word(file,1008);word(file,1010);word(file,1005);
    word(file,1011);word(file,5006);file.push_back(0);word(file,1012);word(file,1001);
    const auto prefix=file.size();file.insert(file.end(),bytes.begin(),bytes.end());
    assert(wot::decodeStockTreeMaterialPrefix(file.data(),file.size(),material,consumed,error)&&consumed+2==file.size());
    for(std::size_t size=0;size<consumed;++size){
        std::size_t unchanged=1234;
        assert(!wot::decodeStockTreeMaterialPrefix(file.data(),size,material,unchanged,error)&&unchanged==1234&&!error.empty());
    }
    auto unsupported=file;unsupported[prefix]=0x58;unsupported[prefix+1]=0x1b; // Optional 7000 leaf LOD.
    assert(!wot::decodeStockTreeMaterialPrefix(unsupported.data(),unsupported.size(),material,consumed,error));
    auto malformed=file;malformed[8]='X';
    assert(!wot::decodeStockTreeMaterialPrefix(malformed.data(),malformed.size(),material,consumed,error));
    std::cout<<"Strict SPT lighting section, failure ownership and actual-handle atlas packing passed"<<std::endl;
}
