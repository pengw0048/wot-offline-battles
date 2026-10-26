#include "wot/vehicle_constraint_solver.hpp"

#include <cmath>
#include <stdexcept>

#if defined(_M_IX86) || defined(_M_X64) || defined(__i386__) || defined(__x86_64__)
#include <xmmintrin.h>
#endif

namespace wot {
namespace {

struct Four { float v[4]; };
Four load(const float* p) { return {{p[0], p[1], p[2], p[3]}}; }
void store(float* p, Four v) { for (int i=0; i<4; ++i) p[i]=v.v[i]; }
Four splat(float f) { return {{f,f,f,f}}; }
Four add(Four a, Four b) {
    for (int i=0; i<4; ++i) a.v[i] += b.v[i];
    return a;
}
Four sub(Four a, Four b) {
    for (int i=0; i<4; ++i) a.v[i] -= b.v[i];
    return a;
}
Four mul(Four a, Four b) {
    for (int i=0; i<4; ++i) a.v[i] *= b.v[i];
    return a;
}
Four shuffle(Four a, Four b, unsigned mask) {
    return {{a.v[mask&3], a.v[(mask>>2)&3],
             b.v[(mask>>4)&3], b.v[(mask>>6)&3]}};
}
Four reduce(Four v) {
    Four t=add(shuffle(v,v,0x4e),v);
    return add(shuffle(t,t,0xb1),t);
}
// SSE MINSS/MAXSS select the second operand on equality or unordered input.
float minimum(float a,float b) { return a < b ? a : b; }
float maximum(float a,float b) { return a > b ? a : b; }

void one_body(VehicleSolverRow& row, VehicleSolverBody& body, bool split) {
    auto& c=row.coefficients;
    auto& d=body.delta;
    for (int channel=0; channel<(split?2:1); ++channel) {
        const int base=channel*8;
        const float previous=c[34+channel];
        const Four linear=load(d.data()+base);
        const Four angular={{d[base+4],d[base+5],1.0f,previous}};
        const Four j=channel ? load(c.data()+28) : load(c.data()+4);
        // The second channel reuses the six physical Jacobian coefficients,
        // with its own bias/decay in cached floats 28/29.
        const Four jacobian=channel ? Four{{c[4],c[5],j.v[0],j.v[1]}} : j;
        const Four residual=reduce(add(mul(jacobian,angular),
                                       mul(load(c.data()),linear)));
        float next=maximum(residual.v[0],c[24]);
        next=minimum(next,c[25]);
        c[34+channel]=next;
        const Four change=splat(next-previous);
        store(d.data()+base,add(mul(change,load(c.data()+12)),linear));
        store(d.data()+base+4,add(mul(change,load(c.data()+16)),angular));
    }
}

void two_body(VehicleSolverRow& row, VehicleSolverBody& a,
              VehicleSolverBody& b, bool split, bool unbounded) {
    auto& c=row.coefficients;
    for (int channel=0;channel<(split?2:1);++channel) {
        const int base=channel*8;
        auto& da=a.delta;
        auto& db=b.delta;
        const float previous=c[34+channel];
        const Four v0=load(da.data()+base);
        const Four v1={{da[base+4],da[base+5],db[base],db[base+1]}};
        const Four v2={{db[base+2],db[base+3],db[base+4],db[base+5]}};
        Four residual=reduce(add(add(mul(load(c.data()+8),v2),
                                         mul(load(c.data()),v0)),
                                    mul(load(c.data()+4),v1)));
        const float bias=channel ? c[28] : c[32];
        const float decay=channel ? 0.0f : c[33];
        Four next=add(splat(bias+previous*decay),residual);
        if (!unbounded) {
            next=splat(maximum(minimum(next.v[0],c[25]),c[24]));
        }
        c[34+channel]=next.v[0];
        const Four change=sub(next,splat(previous));
        const Four n0=add(mul(change,load(c.data()+12)),v0);
        const Four n1=add(mul(change,load(c.data()+16)),v1);
        const Four n2=add(mul(change,load(c.data()+20)),v2);
        store(da.data()+base,n0);
        store(da.data()+base+4,n1);
        store(db.data()+base,shuffle(n1,n2,0x4e));
        // MOVHLPS leaves the upper two lanes unchanged.
        store(db.data()+base+4,Four{{n2.v[2],n2.v[3],n2.v[2],n2.v[3]}});
    }
}


Vector3 inertia_transform(Vector3 v, const VehicleMatrix& matrix, bool pair) {
    Vector3 result;
    float* output[3]={&result.x,&result.y,&result.z};
    for (int column=0;column<3;++column) {
        if (pair)
            *output[column]=(v.z*matrix.rows[2][column]+
                             v.y*matrix.rows[1][column])+v.x*matrix.rows[0][column];
        else
            *output[column]=(v.x*matrix.rows[0][column]+
                             v.y*matrix.rows[1][column])+v.z*matrix.rows[2][column];
    }
    return result;
}
Four approximate_reciprocal(Four v) {
#if defined(_M_IX86) || defined(_M_X64) || defined(__i386__) || defined(__x86_64__)
    Four result;
    _mm_storeu_ps(result.v,_mm_rcp_ps(_mm_loadu_ps(v.v)));
    return result;
#else
    (void)v;
    // The supported client is x86. An ARM reciprocal estimate has a different
    // instruction contract; do not silently replace the original RCPPS.
    throw std::runtime_error("Paired constraint preparation requires x86 SSE RCPPS");
#endif
}
std::uint32_t register_body(VehicleConstraintContext& ctx,VehicleRigidBody* body) {
    if (!body) throw std::invalid_argument("Constraint body is missing");
    if (body->solver_id!=-1) {
        const auto id=static_cast<std::uint32_t>(body->solver_id);
        if (id>=ctx.bodies.size() || ctx.bodies[id].body!=body)
            throw std::invalid_argument("Constraint body belongs to another solve");
        return id;
    }
    VehicleSolverBody scratch;
    scratch.body=body;
    scratch.delta[6]=scratch.delta[14]=1;
    auto& p=scratch.acceleration_prediction;
    p[0]=body->linear_velocity.x*ctx.inverse_elapsed;
    p[1]=body->linear_velocity.y*ctx.inverse_elapsed;
    p[2]=body->linear_velocity.z*ctx.inverse_elapsed;
    p[0]=body->loads.force.x*body->inverse_mass+p[0];
    p[1]=body->loads.force.y*body->inverse_mass+p[1];
    p[2]=body->loads.force.z*body->inverse_mass+p[2];
    const auto angular=inertia_transform(body->loads.torque,body->inverse_world_inertia,false);
    p[3]=body->angular_velocity.x*ctx.inverse_elapsed+angular.x;
    p[4]=body->angular_velocity.y*ctx.inverse_elapsed+angular.y;
    p[5]=body->angular_velocity.z*ctx.inverse_elapsed+angular.z;
    body->solver_id=static_cast<std::int32_t>(ctx.bodies.size());
    ctx.bodies.push_back(scratch);
    return static_cast<std::uint32_t>(body->solver_id);
}
float error_correction(const VehicleConstraintContext& ctx,
                       const VehicleConstraintDescription& desc,float error) {
    return ((ctx.inverse_elapsed*ctx.inverse_elapsed)*desc.correction)*error;
}
Four prediction_segment(const VehicleSolverBody& a,const VehicleSolverBody& b,int part) {
    const auto& ap=a.acceleration_prediction;
    const auto& bp=b.acceleration_prediction;
    if (part==0) return load(ap.data());
    if (part==1) return {{ap[4],ap[5],bp[0],bp[1]}};
    return {{bp[2],bp[3],bp[4],bp[5]}};
}
std::uint32_t prepare_constraint(VehicleConstraintContext& ctx,
    const VehicleConstraintDescription& desc,float error,
    std::int32_t friction_reference,bool contact) {
    if (!(ctx.elapsed>0 && ctx.inverse_elapsed>0))
        throw std::invalid_argument("Constraint timestep is not initialized");
    const bool pair=desc.body_b!=nullptr;
    if (friction_reference!=-1 &&
        (friction_reference<0 || static_cast<std::size_t>(friction_reference)>=ctx.sources.size()))
        throw std::invalid_argument("Friction source constraint is missing");
#if !defined(_M_IX86) && !defined(_M_X64) && !defined(__i386__) && !defined(__x86_64__)
    if (pair) throw std::runtime_error("Paired constraint preparation requires x86 SSE RCPPS");
#endif
    const auto a_id=register_body(ctx,desc.body_a);
    const auto b_id=pair ? register_body(ctx,desc.body_b) : 0;
    unsigned group=pair ? 2u : 0u;
    if (friction_reference!=-1) group=pair ? 6u : 5u;
    else if (contact && desc.error_mode==2) group=pair ? 3u : 1u;
    else if (pair && desc.kind==2) group=8;
    VehicleSolverSource source;
    source.lambda=desc.warm_start;
    source.friction_reference=friction_reference;
    source.body_a=desc.body_a;
    source.body_b=desc.body_b;
    source.cache_group=group;
    source.cache_index=static_cast<std::uint32_t>(ctx.groups[group].size());
    source.contact=contact;
    source.geometry=desc.geometry;
    auto& j=source.jacobian;
    if (!contact) {
        for (int i=0;i<6;++i) j[i]=-desc.geometry[i];
        if (pair) for (int i=6;i<12;++i) j[i]=desc.geometry[i];
    } else {
        const auto& v=desc.geometry;
        const auto& m=desc.body_a->transform.rows;
        const float ax=v[4]-m[3][0], ay=v[5]-m[3][1], az=v[6]-m[3][2];
        j[0]=-v[0];j[1]=-v[1];j[2]=-v[2];
        j[3]=az*v[1]-ay*v[2];j[4]=ax*v[2]-az*v[0];j[5]=ay*v[0]-ax*v[1];
        if (pair) {
            const auto& n=desc.body_b->transform.rows;
            const float bx=v[8]-n[3][0],by=v[9]-n[3][1],bz=v[10]-n[3][2];
            j[6]=v[0];j[7]=v[1];j[8]=v[2];
            j[9]=by*v[2]-bz*v[1];j[10]=bz*v[0]-bx*v[2];j[11]=bx*v[1]-by*v[0];
        }
    }
    VehicleSolverRow row;
    row.body_a=a_id;row.body_b=b_id;
    row.source_index=static_cast<std::uint32_t>(ctx.sources.size());
    auto& c=row.coefficients;
    c[24]=desc.lower;c[25]=desc.upper;c[34]=desc.warm_start;
    for (int body_index=0;body_index<(pair?2:1);++body_index) {
        auto& body=body_index ? *desc.body_b : *desc.body_a;
        const int base=body_index*6;
        c[12+base]=j[base]*body.inverse_mass;
        c[13+base]=j[base+1]*body.inverse_mass;
        c[14+base]=j[base+2]*body.inverse_mass;
        const auto angular=inertia_transform({j[base+3],j[base+4],j[base+5]},body.inverse_world_inertia,pair);
        c[15+base]=angular.x;c[16+base]=angular.y;c[17+base]=angular.z;
    }
    auto& a=ctx.bodies[a_id];
    // Warm start changes only the first channel. The original SIMD stores
    // also preserve its two padding lanes after a paired body update.
    if (pair) {
        auto& b=ctx.bodies[b_id];
        const Four v0=load(a.delta.data());
        const Four v1={{a.delta[4],a.delta[5],b.delta[0],b.delta[1]}};
        const Four v2={{b.delta[2],b.delta[3],b.delta[4],b.delta[5]}};
        const Four n0=add(mul(load(c.data()+12),splat(desc.warm_start)),v0);
        const Four n1=add(mul(load(c.data()+16),splat(desc.warm_start)),v1);
        const Four n2=add(mul(load(c.data()+20),splat(desc.warm_start)),v2);
        store(a.delta.data(),n0);store(a.delta.data()+4,n1);
        store(b.delta.data(),shuffle(n1,n2,0x4e));
        store(b.delta.data()+4,Four{{n2.v[2],n2.v[3],n2.v[2],n2.v[3]}});
        Four predicted=reduce(add(add(mul(prediction_segment(a,b,2),load(j.data()+8)),
                                      mul(prediction_segment(a,b,0),load(j.data()))),
                                 mul(prediction_segment(a,b,1),load(j.data()+4))));
        for (auto& value:predicted.v) value=-value;
        if ((!contact && desc.error_mode!=0) || (contact && desc.error_mode==1))
            if (std::fabs(desc.dead_zone)<std::fabs(error))
                predicted=add(predicted,splat(error_correction(ctx,desc,error)));
        const float softness=desc.softness*ctx.inverse_elapsed;
        Four denominator=add(reduce(add(add(mul(load(c.data()+20),load(j.data()+8)),
                                               mul(load(c.data()+12),load(j.data()))),
                                          mul(load(c.data()+16),load(j.data()+4)))),splat(softness));
        Four inverse=approximate_reciprocal(denominator);
        for (int i=0;i<4;++i) if (!(0.0001f<std::fabs(denominator.v[i]))) inverse.v[i]=0;
        c[32]=inverse.v[0]*predicted.v[0];
        c[33]=1.0f-inverse.v[0]*softness;
        if (contact && desc.error_mode==2) {
            c[28]=std::fabs(error)<=std::fabs(desc.dead_zone) ? 0 : error_correction(ctx,desc,error)*inverse.v[0];
            c[29]=c[33];
        }
        for (int i=0;i<12;++i) c[i]=-inverse.v[i%4]*j[i];
    } else {
        for (int i=0;i<6;++i) a.delta[i]=c[12+i]*desc.warm_start+a.delta[i];
        const auto& p=a.acceleration_prediction;
        float predicted=-(((((p[1]*j[1]+p[0]*j[0])+p[2]*j[2])+p[3]*j[3])+p[4]*j[4])+p[5]*j[5]);
        if ((!contact && desc.error_mode!=0) || (contact && desc.error_mode==1))
            if (std::fabs(desc.dead_zone)<std::fabs(error)) predicted += error_correction(ctx,desc,error);
        const float softness=desc.softness*ctx.inverse_elapsed;
        const float denominator=(((((c[13]*j[1]+c[12]*j[0])+c[14]*j[2])+c[15]*j[3])+c[16]*j[4])+c[17]*j[5])+softness;
        const float inverse=std::fabs(denominator)<=0.0001f ? 0 : 1.0f/denominator;
        c[6]=inverse*predicted;c[7]=1.0f-inverse*softness;
        if (contact && desc.error_mode==2) {
            c[28]=std::fabs(error)<=std::fabs(desc.dead_zone) ? 0 : error_correction(ctx,desc,error)*inverse;
            c[29]=c[7];
        }
        for (int i=0;i<6;++i) c[i]=j[i]*(-inverse);
    }
    ctx.sources.push_back(source);
    ctx.groups[group].push_back(row);
    ctx.nonempty=true;
    return row.source_index;
}

void publish_sources(VehicleConstraintContext& ctx, VehicleSolverRow& row) {
    ctx.sources.at(row.source_index).lambda=row.coefficients[34];
}
void run_group(VehicleConstraintContext& ctx, unsigned group, bool publish,
               bool refresh_friction) {
    for (auto& row:ctx.groups[group]) {
        if (refresh_friction && (group==5 || group==6)) {
            const auto& source=ctx.sources.at(row.source_index);
            const float normal=std::fabs(ctx.sources.at(source.friction_reference).lambda);
            row.coefficients[24]=-(source.negative_friction*normal);
            row.coefficients[25]=source.positive_friction*normal;
        }
        auto& a=ctx.bodies.at(row.body_a);
        if (group==0 || group==1 || group==5)
            one_body(row,a,group==1);
        else
            two_body(row,a,ctx.bodies.at(row.body_b),group==3,group==8);
        if (publish) publish_sources(ctx,row);
    }
}
void run_pass(VehicleConstraintContext& ctx, bool friction, bool publish,
              bool refresh_friction=false) {
    // This is the native order, including the unbounded pair before split
    // impulse rows and both friction groups after all normal constraints.
    for (unsigned group : {0u,2u,8u,1u,3u})
        run_group(ctx,group,publish,false);
    if (friction) {
        run_group(ctx,5,publish,refresh_friction);
        run_group(ctx,6,publish,refresh_friction);
    }
}
void aggregate(VehicleConstraintContext& ctx) {
    for (std::size_t i=0;i<ctx.active_impulse_groups;++i) {
        auto& group=ctx.impulse_groups.at(i);
        const auto& sources=group.sources ? *group.sources : ctx.sources;
        group.absolute_impulse=0;
        for (std::size_t j=0;j<group.active_count;++j)
            group.absolute_impulse += std::fabs(sources.at(group.source_indices.at(j)).lambda);
    }
}
void publish_bodies(VehicleConstraintContext& ctx,bool motion) {
    for (auto& scratch:ctx.bodies) {
        if (!scratch.body) throw std::invalid_argument("Solver body is missing");
        auto& body=*scratch.body;
        const auto& d=scratch.delta;
        body.loads.linear_acceleration.x += d[0];
        body.loads.linear_acceleration.y += d[1];
        body.loads.linear_acceleration.z += d[2];
        body.loads.angular_acceleration.x += d[3];
        body.loads.angular_acceleration.y += d[4];
        body.loads.angular_acceleration.z += d[5];
        if (motion) {
            body.linear_motion_acceleration.x += d[8];
            body.linear_motion_acceleration.y += d[9];
            body.linear_motion_acceleration.z += d[10];
            body.angular_motion_acceleration.x += d[11];
            body.angular_motion_acceleration.y += d[12];
            body.angular_motion_acceleration.z += d[13];
        }
    }
}
} // namespace

std::uint32_t add_vehicle_constraint(VehicleConstraintContext& ctx,
    const VehicleConstraintDescription& desc,float error,std::int32_t friction_reference) {
    return prepare_constraint(ctx,desc,error,friction_reference,false);
}
std::uint32_t add_vehicle_contact(VehicleConstraintContext& ctx,
    const VehicleConstraintDescription& desc,float error,std::int32_t friction_reference) {
    return prepare_constraint(ctx,desc,error,friction_reference,true);
}

std::array<std::uint32_t, 2> add_vehicle_contact_friction(
    VehicleConstraintContext& ctx,std::uint32_t normal_constraint,
    const VehicleConstraintDescription& contact,float coefficient) {
    if (normal_constraint>=ctx.sources.size())
        throw std::invalid_argument("Friction normal constraint is missing");
    const auto& n=contact.geometry;
    unsigned largest=std::fabs(n[0])<std::fabs(n[1]) ? 1u : 0u;
    if (std::fabs(n[largest])<std::fabs(n[2])) largest=2;
    const unsigned next=(largest+1)%3;
    std::array<float,3> first{};
    first[largest]=-n[next];first[next]=n[largest];
    const float length=std::sqrt((first[0]*first[0]+first[1]*first[1])+first[2]*first[2]);
    if (!(length>0)) throw std::invalid_argument("Friction contact normal is invalid");
    for (auto& value:first) value/=length;
    const std::array<float,3> second{{n[2]*first[1]-n[1]*first[2],
        n[0]*first[2]-first[0]*n[2],first[0]*n[1]-n[0]*first[1]}};
    VehicleConstraintDescription tangent=contact;
    tangent.lower=-3.402823466e+38f;tangent.upper=3.402823466e+38f;
    tangent.error_mode=0;tangent.correction=0.8f;tangent.dead_zone=0.02f;
    tangent.warm_start=0;tangent.softness=0;tangent.kind=0;
    std::array<std::uint32_t,2> result;
    for (int i=0;i<2;++i) {
        const auto& direction=i ? second : first;
        for (int j=0;j<3;++j) tangent.geometry[j]=direction[j];
        const auto id=add_vehicle_contact(ctx,tangent,0,static_cast<std::int32_t>(normal_constraint));
        ctx.sources[id].negative_friction=coefficient;
        ctx.sources[id].positive_friction=coefficient;
        result[i]=id;
    }
    return result;
}

std::array<std::int32_t, 4> add_vehicle_axis_constraints(
    VehicleConstraintContext& ctx,const VehicleConstraintAxisDescription& desc) {
    const float distance=(desc.axis.x*(desc.endpoint.x-desc.origin.x)+
                          desc.axis.y*(desc.endpoint.y-desc.origin.y))+
                         desc.axis.z*(desc.endpoint.z-desc.origin.z);
    const Vector3 projected{desc.origin.x+desc.axis.x*distance,
                           desc.origin.y+desc.axis.y*distance,
                           desc.origin.z+desc.axis.z*distance};
    VehicleConstraintDescription row;
    row.body_a=desc.body_a;row.body_b=desc.body_b;
    row.lower=-3.402823466e+38f;row.upper=3.402823466e+38f;
    row.error_mode=desc.error_mode;row.correction=desc.correction;
    row.dead_zone=desc.dead_zone;row.kind=2;
    row.geometry[4]=projected.x;row.geometry[5]=projected.y;row.geometry[6]=projected.z;
    row.geometry[8]=desc.endpoint.x;row.geometry[9]=desc.endpoint.y;row.geometry[10]=desc.endpoint.z;
    std::array<std::int32_t,4> result{{-1,-1,-1,-1}};
    for (int i=0;i<2;++i) {
        const auto normal=desc.tangents[i];
        row.geometry[0]=normal.x;row.geometry[1]=normal.y;row.geometry[2]=normal.z;
        row.warm_start=desc.tangent_warm_start[i];
        const float error=(normal.x*(projected.x-desc.endpoint.x)+
                           normal.y*(projected.y-desc.endpoint.y))+
                          normal.z*(projected.z-desc.endpoint.z);
        result[i]=static_cast<std::int32_t>(add_vehicle_contact(ctx,row,error));
    }
    // Keep the original strict comparisons: touching either authored limit
    // does not add a stopping row. Only one of the two limit rows is present.
    const float axial=((desc.endpoint.x-desc.origin.x)*desc.axis.x+
                       (desc.endpoint.y-desc.origin.y)*desc.axis.y)+
                      (desc.endpoint.z-desc.origin.z)*desc.axis.z;
    if (desc.upper<axial || axial<desc.lower) {
        const bool upper=desc.upper<axial;
        row.geometry[0]=upper ? -desc.axis.x : desc.axis.x;
        row.geometry[1]=upper ? -desc.axis.y : desc.axis.y;
        row.geometry[2]=upper ? -desc.axis.z : desc.axis.z;
        row.lower=0;row.dead_zone=desc.limit_dead_zone;
        row.warm_start=desc.limit_warm_start;row.kind=1;
        const float error=upper ? axial-desc.upper : desc.lower-axial;
        result[upper?3:2]=static_cast<std::int32_t>(add_vehicle_contact(ctx,row,error));
    }
    return result;
}

void solve_vehicle_constraints(VehicleConstraintContext& ctx) {
    if (!ctx.nonempty) return;
    if (ctx.iterations<2) throw std::invalid_argument("Solver requires at least two iterations");
    if (ctx.mid_iterations) {
        for (std::uint32_t i=1;i<ctx.mid_iterations;++i) run_pass(ctx,false,false);
        run_pass(ctx,false,true);
        aggregate(ctx);
        publish_bodies(ctx,false);
        for (const auto& callback:ctx.mid_solve_callbacks) callback();
        for (auto& scratch:ctx.bodies) {
            scratch.body->loads.linear_acceleration={};
            scratch.body->loads.angular_acceleration={};
        }
    }
    run_pass(ctx,true,false,true);
    for (std::uint32_t i=1;i<ctx.iterations-1;++i) run_pass(ctx,true,false);
    run_pass(ctx,true,true);
    publish_bodies(ctx,true);
    aggregate(ctx);
}

void finish_vehicle_constraints(VehicleConstraintContext& ctx) {
    ctx.active_impulse_groups=0;
    for (auto& group:ctx.impulse_groups) {
        group.active_count=0;
        group.absolute_impulse=0;
    }
    for (auto& scratch:ctx.bodies)
        if (scratch.body) scratch.body->solver_id=-1;
    ctx.bodies.clear();
    ctx.sources.clear();
    ctx.nonempty=false;
    for (auto& group:ctx.groups) group.clear();
}
} // namespace wot
