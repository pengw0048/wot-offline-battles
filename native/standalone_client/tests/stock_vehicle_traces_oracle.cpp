#include "wot/stock_vehicle_traces.hpp"
#include <cstring>
using namespace wot;
static StockTraceSegment read(const unsigned char* p){
 float v[28];std::memcpy(v,p,112);StockTraceSegment s;
 s.position={v[0],v[1],v[2]};s.scale={v[3],v[4],v[5]};
 for(unsigned i=0;i<4;++i){s.rotation[i]=v[6+i];s.crop[i]=v[19+i];}
 s.up={v[10],v[11],v[12]};s.direction={v[13],v[14],v[15]};
 s.created=v[16];s.lifetime=v[17];s.alpha=v[18];s.distance_squared=v[23];s.repeat=v[24];
 std::memcpy(&s.layout,p+100,4);std::memcpy(&s.blend_layout,p+104,4);s.reversed=p[108]!=0;return s;
}
static void write(unsigned char* p,const StockTraceSegment& s){
 float v[28]={s.position.x,s.position.y,s.position.z,s.scale.x,s.scale.y,s.scale.z,
 s.rotation[0],s.rotation[1],s.rotation[2],s.rotation[3],s.up.x,s.up.y,s.up.z,
 s.direction.x,s.direction.y,s.direction.z,s.created,s.lifetime,s.alpha,s.crop[0],s.crop[1],s.crop[2],s.crop[3],s.distance_squared,s.repeat};
 std::memcpy(p,v,112);std::memcpy(p+100,&s.layout,4);std::memcpy(p+104,&s.blend_layout,4);p[108]=s.reversed;
}
extern "C" unsigned fixture(const unsigned char* a,const unsigned char*b,unsigned char* output){
 auto first=read(a),last=read(b);StockVehicleTraceManager owner;owner.qualities[0].lifetime_multiplier=1;
 std::uint32_t token=~0u;owner.add(token,first);owner.tick(last.created,{0,0,0},1);owner.add(token,last);
 unsigned i=0;for(auto&s:owner.strips()[0].segments)write(output+112*i++,s);return i;
}
