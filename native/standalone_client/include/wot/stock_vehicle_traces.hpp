#pragma once

#include <algorithm>
#include <cmath>
#include <array>
#include <cstdint>
#include <deque>
#include <map>
#include <vector>

namespace wot
{
struct StockTraceVector
{
    float x = 0.f, y = 0.f, z = 0.f;
    StockTraceVector() = default;
    StockTraceVector(float a, float b, float c) : x(a), y(b), z(c) {}
    StockTraceVector operator+(const StockTraceVector& v) const { return {x+v.x,y+v.y,z+v.z}; }
    StockTraceVector operator-(const StockTraceVector& v) const { return {x-v.x,y-v.y,z-v.z}; }
    StockTraceVector operator*(float scale) const { return {x*scale,y*scale,z*scale}; }
    float length() const { return std::sqrt(x*x+y*y+z*z); }
    float dot(const StockTraceVector& v) const { return x*v.x+y*v.y+z*v.z; }
    StockTraceVector cross(const StockTraceVector& v) const
    {
        return {y*v.z-z*v.y,z*v.x-x*v.z,x*v.y-y*v.x};
    }
    StockTraceVector normalised() const
    {
        const float size=length();
        return size>0.f?*this*(1.f/size):StockTraceVector();
    }
};
struct StockTraceTextureSet
{
    int albedo = -1;
    int normal = -1;
    int material = -1;
};
struct StockTraceStamp
{
    StockTraceVector position;
    float width;
    float length;
    float yaw;
    StockTraceTextureSet textures;
    int previous_albedo;
    float blend;
    bool right;
};
struct StockVehicleTraceSide
{
    bool left = false;
    float center_offset = 0.f;
    float width = 0.f;
    float length = 0.f;
    float signed_movement = 0.f;
    float blend_movement = 0.f;
    float blend_distance = 5.f;
    int terrain_kind = -1;
    StockTraceVector last_stamp;
    StockTraceVector last_chassis;
    StockTraceTextureSet textures;
    int previous_albedo = -1;

    void setTerrain(int kind, const std::map<int, StockTraceTextureSet>& texture_sets)
    {
        // 007908a0 accepts the first material immediately, then requires 12m
        // of movement before accepting another change. Material zero is the
        // authored fallback; absent entries leave the current texture intact.
        if (terrain_kind == kind || (terrain_kind != -1 && blend_movement < 12.f)) return;
        terrain_kind = kind;
        auto found = texture_sets.find(kind);
        if (found == texture_sets.end()) found = texture_sets.find(0);
        if (found == texture_sets.end() || textures.albedo == found->second.albedo) return;
        textures.albedo = found->second.albedo;
        blend_movement = 0.f;
        if (previous_albedo == -1)
        {
            previous_albedo = textures.albedo;
            textures.normal = found->second.normal;
            textures.material = found->second.material;
        }
    }

    // Exact #1513 00791090: movement deltas are supplied by movementInfo,
    // while segment placement follows the evaluated compound world pose.
    // elapsed time is deliberately not used to integrate another motion owner.
    template<class Emit>
    void update(const StockTraceVector& chassis, const StockTraceVector& right_axis,
        const StockTraceVector& forward, float left_movement, float right_movement,
        bool flying, bool deferred, Emit emit)
    {
        if (left_movement > 100.f)
        {
            signed_movement = 0.f;
            last_chassis.x = chassis.x;
            last_chassis.z = chassis.z;
            return;
        }
        const float movement = left ? left_movement : right_movement;
        signed_movement += movement;
        blend_movement += std::fabs(movement);
        if (textures.albedo != previous_albedo && blend_distance <= blend_movement)
            previous_albedo = textures.albedo;
        const StockTraceVector track = chassis + right_axis * (left ? -center_offset : center_offset);
        if (!(length <= -.0004f || length >= .0004f)) return;
        const StockTraceVector difference = track - last_stamp;
        float segments = difference.length() * (1.f / length);
        if (!(segments >= 1.f)) return;
        const StockTraceVector step = difference * (1.f / segments);
        const float forward_length = std::sqrt(forward.x*forward.x + forward.z*forward.z);
        const float dx = chassis.x - last_chassis.x;
        const float dz = chassis.z - last_chassis.z;
        const float displacement_length = std::sqrt(dx*dx + dz*dz);
        const float alignment = forward_length > 0.f && displacement_length > 0.f ?
            (dx*forward.x + dz*forward.z) / (forward_length*displacement_length) : 0.f;
        last_chassis.x = chassis.x;
        last_chassis.z = chassis.z;
        if (std::fabs(alignment) < .7f || segments > 20.f)
        {
            signed_movement = 0.f;
            last_stamp = track;
            return;
        }
        if (flying)
        {
            signed_movement = 0.f;
            last_stamp = track;
            segments = 0.f;
        }
        if (!deferred) return;
        while (segments >= 1.f)
        {
            if (previous_albedo != -1)
            {
                last_stamp = last_stamp + step;
                const float blend = std::min(blend_movement, blend_distance) / blend_distance;
                StockTraceStamp stamp = {last_stamp, width, length,
                    std::atan2(forward.x, forward.z) - 1.570796f,
                    textures, previous_albedo, blend, !left};
                emit(stamp);
            }
            signed_movement = 0.f;
            segments -= 1.f;
        }
    }
};

// TraceManager's 0x70-byte record (00a60f60), expressed without pointer or
// float/integer aliasing. Layout IDs refer to the renderer's three strong
// texture references; they are not resource names or newly allocated IDs.
struct StockTraceSegment
{
    StockTraceVector position;
    StockTraceVector scale;
    std::array<float,4> rotation{{0.f,0.f,0.f,1.f}};
    StockTraceVector up{0.f,-1.f,0.f};
    StockTraceVector direction;
    float created=0.f;
    float lifetime=0.f;
    float alpha=0.f;
    std::array<float,4> crop{{0.f,0.f,0.f,0.f}};
    float distance_squared=0.f;
    float repeat=1.f;
    int layout=-1;
    int blend_layout=-1;
    bool reversed=false;
};

struct StockTraceQuality
{
    float lod_bias;
    unsigned capacity;
    float lifetime_multiplier;
};

struct StockTraceFramePacket
{
    StockTraceSegment segment;
    unsigned type=1;
    int layout=-1;
    // a61310 keeps the preceding segment's alpha separately for each texture
    // layout. Reversing travel exchanges the two ends, not the texture set.
    float alpha_begin=0.f;
    float alpha_end=0.f;
};

struct StockTraceTextureRectangle
{
    unsigned width=0,height=0;
    unsigned min_x=0,min_y=0,max_x=0,max_y=0;
    unsigned border=0;
};

// Six FLOAT4 TEXCOORD4..9 values on stream 9, matching the original
// shaders/formats/decal_instancing.xml and a8fd60. Public light instancing
// uses a different stride and must not own or reinterpret these records.
using StockTraceGpuInstance=std::array<float,24>;
inline StockTraceGpuInstance stockTraceGpuInstance(const StockTraceFramePacket& packet,
    const StockTraceTextureRectangle& texture)
{
    StockTraceGpuInstance result{};
    const auto& segment=packet.segment;
    const float sign=segment.rotation[3]>=0.f?1.f:-1.f;
    result[0]=segment.rotation[0];result[1]=segment.rotation[1];result[2]=segment.rotation[2];
    result[3]=2.f*sign; // APPLY_TO_TERRAIN, subtype 2 occupies the removed high byte.
    result[4]=segment.position.x;result[5]=segment.position.y;result[6]=segment.position.z;
    result[7]=1.f;
    result[8]=segment.scale.x;result[9]=segment.scale.y;result[10]=segment.scale.z;
    result[11]=segment.repeat;
    const float width=static_cast<float>(texture.width),height=static_cast<float>(texture.height);
    const float border=static_cast<float>(texture.border);
    result[12]=(texture.min_x+border)/width;
    result[13]=(texture.min_y+border)/height;
    result[14]=(texture.max_x-texture.min_x-2.f*border+1.f)/width;
    result[15]=(texture.max_y-texture.min_y-2.f*border+1.f)/height;
    result[16]=std::floor(packet.alpha_begin*255.f)+segment.crop[0]*.499f;
    result[17]=std::floor(packet.alpha_begin*255.f)+segment.crop[1]*.499f;
    result[18]=std::floor(packet.alpha_end*255.f)+segment.crop[2]*.499f;
    result[19]=std::floor(packet.alpha_end*255.f)+segment.crop[3]*.499f;
    result[20]=(texture.max_x-texture.min_x)/width;
    result[21]=(texture.max_y-texture.min_y)/height;
    result[22]=.0001f;
    return result;
}

class StockVehicleTraceManager
{
public:
    struct Strip
    {
        unsigned type=1;
        unsigned capacity=0;
        std::deque<StockTraceSegment> segments;
    };
    std::array<StockTraceQuality,5> qualities{{
        {1.f,250,4.f},{.8f,125,2.f},{.6f,62,1.f},{.4f,41,.5f},{0.f,31,.5f}}};
    unsigned quality=0;
    bool optimise=true;
    float addition_timeout=.5f;
    float addition_angle_cos=.99939084f;
    float offset_squared=.05f*.05f;
    float max_segment_length=33.333332f;
    float fade_start_squared=10000.f;
    float fade_end_squared=40000.f;

    void setQuality(unsigned option)
    {
        quality=option;
        updateFade();
        // a62c40 changes quality and discards both strips and layout cache.
        // The render owner clears its corresponding strong layout references.
        clear();
    }
    void setFadeDistances(float first,float last)
    {
        if (first>0.f && last>0.f && first<last)
        {
            fade_start_=first;fade_end_=last;updateFade();
        }
    }

    float time() const { return time_; }
    const std::vector<Strip>& strips() const { return strips_; }
    void clear()
    {
        strips_.clear();
        // a61280 invalidates component-owned tokens before reusing strip slots.
        generation_+=0x10000u;
        time_=0.f;
    }
    void add(std::uint32_t& token, StockTraceSegment segment, unsigned type=1)
    {
        if (segment.layout==-1) return;
        if (token==~0u || (token&0xffff0000u)!=generation_)
        {
            token=generation_|static_cast<unsigned>(strips_.size());
            Strip strip;
            strip.type=type;strip.capacity=qualities.at(quality).capacity;
            strips_.push_back(strip);
        }
        Strip& strip=strips_.at(token-generation_);
        segment.created=time_;
        segment.lifetime*=qualities.at(quality).lifetime_multiplier;
        if (!strip.segments.empty())
            segment.reversed=segment.direction.dot(segment.position-strip.segments.back().position)<0.f;
        if (strip.segments.size()==strip.capacity) strip.segments.pop_front();
        strip.segments.push_back(segment);
        if (strip.segments.size()>1)
        {
            const auto& previous=strip.segments[strip.segments.size()-2];
            if (previous.layout==segment.layout && previous.blend_layout==segment.blend_layout)
                optimiseStrip(strip);
        }
    }
    void tick(float elapsed,const StockTraceVector& camera,float zoom)
    {
        time_+=elapsed;
        for (auto& strip:strips_)
        {
            while (!strip.segments.empty() &&
                (time_-strip.segments.front().created)-strip.segments.front().lifetime>=0.f)
                strip.segments.pop_front();
            for (auto& segment:strip.segments)
            {
                const StockTraceVector relative=(segment.position-camera)*zoom;
                segment.distance_squared=relative.dot(relative);
            }
        }
    }
    template<class Fade> std::vector<StockTraceFramePacket> packets(unsigned layout_count,Fade fade) const
    {
        std::vector<StockTraceFramePacket> result;
        if (!layout_count) return result;
        for (const auto& strip:strips_)
        {
            // The native scratch array is cleared once for each strip. Its
            // eight-bit hash is part of a61310, including layout collisions.
            std::vector<float> previous(layout_count,0.f);
            for (const auto& segment:strip.segments)
            {
                const float lifetime_alpha=1.f-(time_-segment.created)/segment.lifetime;
                const float distance_alpha=fade(segment.distance_squared);
                const float alpha=lifetime_alpha*segment.alpha*distance_alpha;
                if (!(alpha>.003921569f || segment.blend_layout!=-1)) continue;
                const auto append=[&](int layout,float current)
                {
                    const unsigned hash=static_cast<unsigned>(layout)%layout_count&0xffu;
                    StockTraceFramePacket packet;
                    packet.segment=segment;packet.type=strip.type;packet.layout=layout;
                    packet.alpha_begin=segment.reversed?current:previous[hash];
                    packet.alpha_end=segment.reversed?previous[hash]:current;
                    if (layout!=-1) result.push_back(packet);
                    previous[hash]=current;
                };
                append(segment.layout,alpha);
                if (segment.blend_layout!=-1)
                    append(segment.blend_layout,lifetime_alpha*(1.f-segment.alpha)*distance_alpha);
            }
        }
        return result;
    }
    std::vector<StockTraceFramePacket> packets(unsigned layout_count) const
    {
        return packets(layout_count,[this](float distance)
        {
            // a545b0 clamps the squared-distance ramp, not world distance.
            const float factor=1.f-(distance-fade_start_squared)/
                (fade_end_squared-fade_start_squared);
            if (factor<0.f) return 0.f;
            if (factor>=1.f) return 1.f;
            return factor;
        });
    }
private:
    void updateFade()
    {
        const float bias=qualities.at(quality).lod_bias;
        const float first=fade_start_*bias,last=fade_end_*bias;
        fade_start_squared=first*first;fade_end_squared=last*last;
    }
    static std::array<float,4> slerp(std::array<float,4> a,std::array<float,4> b,float t)
    {
        float dot=0.f;
        for (unsigned i=0;i!=4;++i) dot+=a[i]*b[i];
        if (dot<0.f) { for(auto& value:b)value=-value;dot=-dot; }
        float first=1.f-t,second=t;
        if (1.f-dot>.001f)
        {
            const float angle=std::acos(dot);
            const float sine=std::sin(angle);
            first=std::sin((1.f-t)*angle)/sine;
            second=std::sin(t*angle)/sine;
        }
        for (unsigned i=0;i!=4;++i) a[i]=a[i]*first+b[i]*second;
        return a;
    }
    static StockTraceSegment merge(const StockTraceSegment& first,const StockTraceSegment& last)
    {
        const float weight=first.scale.y/(last.scale.y+first.scale.y);
        const float other=1.f-weight;
        StockTraceSegment result=last;
        result.position=first.position*weight+last.position*other;
        result.direction=first.direction*weight+last.direction*other;
        result.up=first.up*weight+last.up*other;
        result.rotation=slerp(first.rotation,last.rotation,other);
        result.scale={first.scale.x*weight+last.scale.x*other,
            last.scale.y+first.scale.y,first.scale.z*weight+last.scale.z*other};
        result.repeat=result.scale.y*first.repeat/first.scale.y;
        result.crop={{first.crop[0]*weight,first.crop[1]*weight,0.f,0.f}};
        return result;
    }
    static std::array<float,4> rotation(const StockTraceVector& up,const StockTraceVector& direction)
    {
        // a54950: D3DXMatrixLookAtLH(Zero, up, direction), followed by
        // D3DXQuaternionRotationMatrix. The matrix is not inverted here.
        const StockTraceVector z=up.normalised();
        const StockTraceVector x=direction.cross(z).normalised();
        const StockTraceVector y=z.cross(x);
        const float m[3][3]={{x.x,y.x,z.x},{x.y,y.y,z.y},{x.z,y.z,z.z}};
        std::array<float,4> q;
        const float trace=m[0][0]+m[1][1]+m[2][2]+1.f;
        if (trace>1.f)
        {
            const float root=std::sqrt(trace);
            q[3]=root*.5f;
            const float inv=.5f/root;
            q[0]=(m[1][2]-m[2][1])*inv;
            q[1]=(m[2][0]-m[0][2])*inv;
            q[2]=(m[0][1]-m[1][0])*inv;
        }
        else
        {
            unsigned i=0;
            if (m[1][1]>m[0][0]) i=1;
            if (m[2][2]>m[i][i]) i=2;
            const unsigned j=(i+1)%3,k=(i+2)%3;
            const float root=std::sqrt(m[i][i]-m[j][j]-m[k][k]+1.f);
            q[i]=root*.5f;
            const float inv=root==0.f?0.f:.5f/root;
            q[j]=(m[i][j]+m[j][i])*inv;
            q[k]=(m[i][k]+m[k][i])*inv;
            q[3]=(m[j][k]-m[k][j])*inv;
        }
        return q;
    }
    void optimiseStrip(Strip& strip)
    {
        if (!optimise || strip.segments.size()<2) return;
        auto& first=strip.segments[strip.segments.size()-2];
        auto& last=strip.segments.back();
        if (!(first.scale.y<=max_segment_length)) return;
        const bool timed_out=addition_timeout<=last.created-first.created;
        const float sign=last.reversed?-1.f:1.f;
        const StockTraceVector edge=first.position+first.direction*(first.scale.y*.5f*sign);
        const StockTraceVector target=edge+last.direction*(last.scale.y*.5f*sign);
        const StockTraceVector gap=target-last.position;
        const float gap_squared=gap.dot(gap);
        if (gap_squared>.0001f && gap_squared<.25f && !timed_out)
        {
            const float original_y=last.direction.y;
            last.position.x=last.position.x*.25f+target.x*.75f;
            last.position.z=last.position.z*.25f+target.z*.75f;
            last.direction=(last.position-edge)*sign;
            // a624c9 saves the old basis Y before replacing the direction.
            // Preserve that slope rather than using the positional Y delta.
            last.direction.y=original_y*last.direction.length();
            last.direction=last.direction.normalised();
            last.rotation=rotation(last.up,last.direction);
        }
        const StockTraceVector separation=first.position-last.position;
        const StockTraceVector offset=first.position+
            first.direction*((last.scale.y+first.scale.y)*.5f*sign)-last.position;
        if (last.direction.dot(first.direction)>=addition_angle_cos &&
            offset.dot(offset)<=offset_squared &&
            separation.dot(separation)>=first.scale.y*first.scale.y*.25f && !timed_out)
        {
            StockTraceSegment combined=merge(first,last);
            strip.segments.pop_back();strip.segments.pop_back();
            strip.segments.push_back(combined);
            return;
        }
        const float side_x=first.direction.z+last.direction.z;
        const float side_z=-last.direction.x-first.direction.x;
        const float size=std::sqrt(side_x*side_x+side_z*side_z);
        const float corner=size>0.f?
            (side_x*last.direction.x+side_z*last.direction.z)*last.scale.x/size:0.f;
        const float a=std::min(.5f,std::max(0.f,std::fabs(corner)/first.scale.y));
        const float b=std::min(.5f,std::max(0.f,std::fabs(corner)/last.scale.y));
        first.scale.y*=1.f+a*.5f;
        last.scale.y*=1.f+b*.5f;
        if (last.reversed?corner<0.f:corner>=0.f)
        {
            first.crop[2]=a;last.crop[3]=b;
        }
        else
        {
            first.crop[0]=a;last.crop[1]=b;
        }
    }
    float time_=0.f;
    float fade_start_=100.f,fade_end_=200.f;
    std::uint32_t generation_=0;
    std::vector<Strip> strips_;
};
}
