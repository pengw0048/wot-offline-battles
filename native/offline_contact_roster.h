#ifndef WOT_OFFLINE_CONTACT_ROSTER_H
#define WOT_OFFLINE_CONTACT_ROSTER_H
#include <array>
#include <cstdint>
#include <utility>
#include <vector>
namespace offline_contact {
enum Flags {Alive=1,Immovable=2,Impulse=4,PositionFixed=8,Player=16,HasY=32,HasGrip=64};
struct Body {
    int64_t id=0,team=0;
    unsigned flags=Alive|Impulse;
    double x=0.,y=0.,z=0.,yaw=0.,mass=1.,vx=0.,vy=0.,vz=0.,pitch=0.,roll=0.;
    std::array<double,4> shape={};
    double grip[2]={},push_yaw=0.,traverse_speed=0.,traverse_torque=0.;
    bool has(unsigned bit) const {return (flags&bit)!=0;}
};
struct RamCandidate {
    int64_t other_id=0;
    std::array<double,3> impact={};
    double closing_speed=0.,relative_speed=0.;
    bool moving_self=false,moving_other=false;
};
struct Row {
    int64_t id=0;
    std::array<double,2> correction={},delta_velocity={};
    double delta_yaw=0.;
    bool has_peers=false,has_active_peer=false;
    std::vector<std::pair<int64_t,int64_t>> retained_contacts;
    std::vector<RamCandidate> ram_candidates;
};
// Input body order is the caller's existing integer-key dictionary iteration
// order, preserving spatial-bucket peer order. Physics sorts a private copy by
// actor ID; owner/ram publication follows the separate current owner order.
std::vector<Row> resolve(const std::vector<Body> &bodies,
                         const std::vector<int64_t> &owners,double dt,
                         const std::vector<std::pair<int64_t,int64_t>> &previous);
}
#endif
