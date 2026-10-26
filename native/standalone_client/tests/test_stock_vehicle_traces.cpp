#ifdef NDEBUG
#undef NDEBUG
#endif
#include "wot/stock_vehicle_traces.hpp"
#include <cassert>
#include <cmath>
#include <vector>

using namespace wot;
static bool near(float a,float b) { return std::fabs(a-b)<.00001f; }
int main()
{
    StockVehicleTraceSide side;
    side.width=.6f;side.length=1.f;
    side.textures={21,22,23};side.previous_albedo=11;side.blend_distance=8.f;
    std::vector<StockTraceStamp> stamps;
    auto emit=[&](const StockTraceStamp& stamp){stamps.push_back(stamp);};
    const StockTraceVector right(1,0,0),forward(0,0,1);
    side.update({0,0,3.5f},right,forward,3.5f,3.5f,false,true,emit);
    assert(stamps.size()==3);
    for(unsigned i=0;i<3;++i)
    {
        assert(near(stamps[i].position.z,float(i+1)));
        assert(stamps[i].textures.albedo==21&&stamps[i].previous_albedo==11);
        assert(near(stamps[i].blend,3.5f/8.f)&&stamps[i].right);
        assert(near(stamps[i].yaw,-1.570796f));
    }
    side.update({0,0,4.1f},right,forward,.6f,.6f,false,true,emit);
    assert(stamps.size()==4&&near(stamps.back().position.z,4.f));
    side.update({0,0,10.f},right,forward,5.9f,5.9f,false,true,emit);
    assert(side.previous_albedo==21&&near(stamps.back().blend,1.f));
    // Reverse motion keeps spatial spacing; a sideways skid cuts the strip.
    const auto beforeReverse=stamps.size();
    side.update({0,0,7.f},right,forward,-3.f,-3.f,false,true,emit);
    assert(stamps.size()==beforeReverse+3&&near(stamps.back().position.z,7.f));
    const auto beforeSkid=stamps.size();
    side.update({4,0,7.f},right,forward,4.f,4.f,false,true,emit);
    assert(stamps.size()==beforeSkid&&near(side.last_stamp.x,4.f));
    side.update({4,0,9.f},right,forward,2.f,2.f,true,true,emit);
    assert(stamps.size()==beforeSkid&&near(side.last_stamp.z,9.f));
    side.update({4,0,11.f},right,forward,2.f,2.f,false,false,emit);
    assert(stamps.size()==beforeSkid); // Retail forward rendering submits none.
    side.update({4,0,100.f},right,forward,89.f,89.f,false,true,emit);
    assert(stamps.size()==beforeSkid&&near(side.last_stamp.z,100.f));
    // Each side uses its own world offset and movement delta for material blend.
    StockVehicleTraceSide left;
    left.left=true;left.center_offset=1.5f;left.width=.6f;left.length=1.f;
    left.textures={1,2,3};left.previous_albedo=1;left.blend_distance=10.f;
    left.last_stamp={-1.5f,0,0};
    stamps.clear();
    left.update({0,0,2},right,forward,2.f,7.f,false,true,emit);
    assert(stamps.size()==2&&!stamps[0].right&&near(stamps[0].position.x,-1.5f));
    assert(near(left.blend_movement,2.f));
    left.update({0,0,4},right,forward,101.f,0.f,false,true,emit);
    assert(stamps.size()==2&&near(left.last_chassis.z,4.f));
    // Material transition throttle and authored material-zero fallback.
    StockVehicleTraceSide material;
    std::map<int,StockTraceTextureSet> sets{{0,{1,2,3}},{7,{4,5,6}},{8,{9,10,11}}};
    material.setTerrain(7,sets);
    assert(material.previous_albedo==4&&material.textures.normal==5);
    material.setTerrain(8,sets);assert(material.textures.albedo==4);
    material.blend_movement=12.f;material.setTerrain(8,sets);
    assert(material.textures.albedo==9&&material.previous_albedo==4&&material.blend_movement==0.f);
    // #1513 retains the first ANM/GMM while switching albedo sources.
    assert(material.textures.normal==5&&material.textures.material==6);
    material.blend_movement=12.f;material.setTerrain(123,sets);
    assert(material.textures.albedo==1&&material.terrain_kind==123);
    left.length=0.f;
    left.update({0,0,5},right,forward,1.f,1.f,false,true,emit);
    assert(stamps.size()==2);

    StockTraceSegment segment;
    segment.scale={.6f,1.f,.5f};segment.direction={0,0,1};
    segment.lifetime=3.f;segment.alpha=.75f;segment.layout=0;segment.blend_layout=1;
    StockVehicleTraceManager manager;
    std::uint32_t token=~0u;
    manager.add(token,segment);
    segment.position.z=1.f;
    manager.add(token,segment);
    assert(manager.strips().size()==1&&manager.strips()[0].segments.size()==1);
    const auto& merged=manager.strips()[0].segments.front();
    assert(near(merged.scale.y,2.f)&&near(merged.position.z,.5f)&&near(merged.repeat,2.f));
    assert(near(merged.lifetime,12.f)); // MAX quality lifetime multiplier is four.
    manager.tick(3.f,{0,0,0},2.f);
    assert(near(manager.strips()[0].segments.front().distance_squared,1.f));
    auto packets=manager.packets(2,[](float){return 1.f;});
    assert(packets.size()==2&&packets[0].layout==0&&packets[1].layout==1);
    assert(near(packets[0].alpha_begin,0.f)&&near(packets[0].alpha_end,.75f*.75f));
    assert(near(packets[1].alpha_end,.75f*.25f));
    manager.tick(9.f,{0,0,0},1.f);
    assert(manager.strips()[0].segments.empty()&&manager.packets(2,[](float){return 1.f;}).empty());

    // A reset invalidates old component tokens; IDs cannot alias fresh strips.
    const std::uint32_t old_token=token;
    manager.clear();manager.add(token,segment);
    assert(token!=old_token&&manager.strips().size()==1);
    assert((token&0xffff0000u)==0x10000u);

    // Ring overwrite and timeout barriers preserve the original bounded owner.
    StockVehicleTraceManager ring;
    ring.qualities[0].capacity=2;
    ring.optimise=false;
    token=~0u;segment.position={0,0,1};segment.blend_layout=-1;segment.alpha=1.f;
    ring.add(token,segment);
    segment.position.z=2.f;ring.add(token,segment);
    segment.position.z=3.f;ring.add(token,segment);
    assert(ring.strips()[0].segments.size()==2);
    assert(near(ring.strips()[0].segments.front().position.z,2.f));
    packets=ring.packets(1,[](float){return 1.f;});
    assert(packets.size()==2&&near(packets[0].alpha_begin,0.f)&&near(packets[1].alpha_begin,1.f));
    ring.clear();ring.optimise=true;segment.position.z=0;ring.add(token,segment);
    ring.tick(.5f,{0,0,0},1.f);segment.position.z=1;ring.add(token,segment);
    assert(ring.strips()[0].segments.size()==2);
    // Crossing material layouts is a merge barrier even on straight tracks.
    ring.clear();segment.position.z=0;ring.add(token,segment);
    segment.layout=1;segment.position.z=1;ring.add(token,segment);
    assert(ring.strips()[0].segments.size()==2);
    // Reverse endpoint alpha follows travel direction, with no extra stamp.
    ring.optimise=false;segment.position.z=0;ring.add(token,segment);
    assert(ring.strips()[0].segments.back().reversed);
    packets=ring.packets(2,[](float){return .5f;});
    assert(packets.size()==2&&near(packets.back().alpha_begin,.5f));

    // Corner crop grows adjoining geometry only by the native capped amount.
    StockVehicleTraceManager corner;
    token=~0u;segment.layout=0;segment.position={0,0,0};segment.direction={0,0,1};
    corner.add(token,segment);
    segment.position={1,0,1};segment.direction={1,0,0};corner.add(token,segment);
    assert(corner.strips()[0].segments.size()==2);
    assert(corner.strips()[0].segments[0].scale.y>1.f&&corner.strips()[0].segments[0].scale.y<=1.25f);
    assert(corner.strips()[0].segments[1].crop[3]>0.f);

    manager.setQuality(2);
    assert(manager.strips().empty()&&near(manager.time(),0.f));
    assert(near(manager.fade_start_squared,(100.f*.6f)*(100.f*.6f))&&
        near(manager.fade_end_squared,(200.f*.6f)*(200.f*.6f)));
    manager.setFadeDistances(150.f,300.f);
    assert(near(manager.fade_start_squared,8100.f)&&near(manager.fade_end_squared,32400.f));
    segment.position={0,0,0};segment.blend_layout=-1;segment.lifetime=2;segment.alpha=1;
    manager.add(token,segment);
    manager.tick(0.f,{0,0,150.f},1.f);
    packets=manager.packets(1);
    assert(packets.size()==1&&near(packets[0].alpha_end,1.f-(22500.f-8100.f)/(32400.f-8100.f)));
    manager.tick(0.f,{0,0,180.f},1.f);
    assert(manager.packets(1).empty());

    StockTraceFramePacket packet;
    packet.segment.position={10,20,30};packet.segment.scale={.6f,2.f,.5f};
    packet.segment.rotation={{.1f,.2f,.3f,-.9f}};packet.segment.repeat=4.f;
    packet.segment.crop={{.1f,.2f,.3f,.4f}};
    packet.alpha_begin=.25f;packet.alpha_end=.75f;
    StockTraceTextureRectangle rectangle;
    rectangle.width=256;rectangle.height=128;rectangle.max_x=256;rectangle.max_y=128;
    auto gpu=stockTraceGpuInstance(packet,rectangle);
    static_assert(sizeof(gpu)==96,"Native decal instance stride changed");
    assert(near(gpu[3],-2.f)&&near(gpu[7],1.f)&&near(gpu[11],4.f));
    assert(near(gpu[14],257.f/256.f)&&near(gpu[15],129.f/128.f));
    assert(near(gpu[16],63.f+.1f*.499f)&&near(gpu[19],191.f+.4f*.499f));
    assert(near(gpu[20],1.f)&&near(gpu[21],1.f)&&near(gpu[22],.0001f));
    rectangle.min_x=16;rectangle.max_x=80;rectangle.min_y=8;rectangle.max_y=72;rectangle.border=2;
    gpu=stockTraceGpuInstance(packet,rectangle);
    assert(near(gpu[12],18.f/256.f)&&near(gpu[13],10.f/128.f));
    assert(near(gpu[14],61.f/256.f)&&near(gpu[15],61.f/128.f));
}
