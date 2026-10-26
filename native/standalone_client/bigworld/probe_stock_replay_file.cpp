#if !defined(WOT_REPLAY_HOST_BUILD)
#include "cstdmf/cstdmf_windows.hpp"
#include "cstdmf/allocator.hpp"
#include "cstdmf/log_msg.hpp"
#endif
#include "stock_replay_file.hpp"
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>

namespace {
void require(bool value,std::string message){if(!value)throw std::runtime_error(message);}
void inspect(const wot::StockReplayStream& stream){
    const auto& packets=stream.packets();require(packets.size()==7,"Packet count differs from the independent fixture");
    require(packets[0].type==0x18&&packets[1].type==0x2d&&packets[2].type==0x30,"Version packet types changed");
    std::string text,error;require(stream.packetString(0,text,error)&&text=="v.0.9.22.0 #1513","Client version packet payload changed");
    require(stream.packetString(1,text,error)&&text=="0.9.22","Protocol packet payload changed");
    require(stream.packetString(2,text,error)&&text=="HD","Asset version packet payload changed");
    require(stream.timeIndex()==std::vector<std::size_t>({4,0,1,2,3,5,6}),"Timestamp index lost packet order or stable ties");
    require(stream.lowerBoundTime(-2)==4&&stream.lowerBoundTime(1.5f)==3&&stream.lowerBoundTime(2)==6&&stream.lowerBoundTime(10)==7,"Timestamp lower bounds are incorrect");
    require(stream.duration()==9,"Replay duration is not the maximum timestamp");
    require(packets[3].size==9&&stream.bytes()[packets[3].payloadOffset+1]==0xff,"Binary packet payload changed");
    require(!stream.packetString(3,text,error)&&!error.empty(),"Invalid string packet was accepted");
}
}
int main(int argc,char** argv){
#if !defined(WOT_REPLAY_HOST_BUILD)
    BW_SYSTEMSTAGE_MAIN();BW::LogMsg::automatedTest(true);SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX);
#endif
    int outcome=1;
    try{
        require(argc==2,"Usage: probe_stock_replay_file <fixture-directory>");const std::string root=argv[1];std::string error;
        wot::StockReplayFile file;
        for(const auto* name:{"raw.wotreplay","legacy.wotreplay",u8"\u5f53\u524d\u56de\u653e.wotreplay"}){
            require(file.open(root+"/"+name,error),error.c_str());inspect(file.stream());
            const bool current=std::string(name)==u8"\u5f53\u524d\u56de\u653e.wotreplay";
            require(file.stream().metadata().size()==(current?2u:0u),"Replay metadata block count changed");
            if(current)require(file.stream().metadata()[0]==u8"{\"playerName\":\"\u6d4b\u8bd5\",\"clientVersionFromExe\":\"0.9.22.0\"}","UTF-8 metadata changed");
            require(file.seekPacket(7),"Cannot seek to packet EOF");wot::ReplayPacketIndex packet;
            require(!file.next(packet)&&!file.seekPacket(8)&&file.cursor()==7,"EOF or failed seek changed cursor");
            require(file.seekPacket(3)&&file.next(packet)&&packet.type==2&&packet.time==1.5f,"Packet cursor returned the wrong record");
        }
        const auto bytes=file.stream().bytes();const auto metadata=file.stream().metadata();
        for(const auto* name:{"bad-count","bad-meta-length","bad-raw-limit","bad-packed-size","bad-alignment","bad-cipher","bad-zlib","bad-raw-size","bad-packet-header","bad-packet-size","bad-time","bad-padding","bad-zlib-trailing"}){
            require(!file.open(root+"/"+name,error)&&!error.empty(),"Malformed replay was accepted");
            require(file.stream().bytes()==bytes&&file.stream().metadata()==metadata&&file.cursor()==4,"Failed replay load mutated the active owner");
        }
        require(!file.save(root+"/protected-directory",wot::ReplayContainer::EncryptedZlib,error)&&!error.empty(),"Failed publish was not reported");
        require(file.save(root+u8"/\u8f93\u51fa\u5f53\u524d.wotreplay",wot::ReplayContainer::EncryptedZlib,error),error.c_str());
        require(!file.save(root+"/metadata-must-survive",wot::ReplayContainer::LegacyZlib,error),"Legacy output silently dropped metadata");
        file.stream().metadata({});
        require(file.save(root+"/output-legacy.wotreplay",wot::ReplayContainer::LegacyZlib,error),error.c_str());
        require(file.save(root+"/output-raw.wotreplay",wot::ReplayContainer::PacketStream,error),error.c_str());
        file.close();file.close();require(file.cursor()==0&&file.stream().packets().empty(),"Repeated close did not reset the owner");
        require(file.open(root+u8"/\u8f93\u51fa\u5f53\u524d.wotreplay",error),error.c_str());inspect(file.stream());
        require(file.open(root+"/terminated.wotreplay",error),error.c_str());inspect(file.stream());
        require(file.stream().terminalOffset()+19==file.stream().bytes().size(),"Terminal marker/trailer ownership is incorrect");
        require(file.save(root+"/output-terminated.wotreplay",wot::ReplayContainer::EncryptedZlib,error),error.c_str());
        require(file.seekPacket(7),"Cannot seek before clearing the exposed stream");
        file.stream().clear();wot::ReplayPacketIndex absent;
        require(!file.next(absent),"A cleared stream must not dereference the previous cursor");
        wot::StockReplayStream recording;require(recording.append(99,-1,{},error),error.c_str());
        for(unsigned packet=0;packet<10000;++packet)require(recording.append(2,static_cast<float>(packet),{0,1,2},error),error.c_str());
        const auto recorded=recording.bytes();require(!recording.append(1,std::numeric_limits<float>::quiet_NaN(),{},error)&&recording.bytes()==recorded,"Invalid recording append changed the stream");
        require(recording.packets().size()==10001&&recording.duration()==9999,"Recording index or duration is inconsistent");
        require(recording.append(0xffffffff,10000,{},error)&&recording.packets().size()==10001,"Replay end marker entered the playback index");
        require(!recording.append(2,10001,{},error),"Recording continued after the explicit end marker");
        for(const auto format:{wot::ReplayContainer::PacketStream,wot::ReplayContainer::LegacyZlib,wot::ReplayContainer::EncryptedZlib}){
            wot::StockReplayStream empty,decoded;std::vector<std::uint8_t> encoded;
            require(empty.encode(format,encoded,error)&&decoded.decode(encoded,error)&&decoded.packets().empty(),"Empty replay stream round trip failed");
        }
        std::cout<<"Real replay container codecs, Blowfish feedback, packet/time indices, metadata, Unicode paths and failure ownership passed"<<std::endl;outcome=0;
    }catch(const std::exception& exception){std::cerr<<"Replay file probe failed: "<<exception.what()<<std::endl;}
#if !defined(WOT_REPLAY_HOST_BUILD)
    BW::LogMsg::fini();
#endif
    return outcome;
}
