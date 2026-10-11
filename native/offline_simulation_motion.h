#ifndef WOT_OFFLINE_SIMULATION_MOTION_H
#define WOT_OFFLINE_SIMULATION_MOTION_H
#include "offline_simulation_types.h"
#include "offline_math_batch.h"
#include "offline_contact_roster.h"
#include "world_stage.h"
#include <map>
#include <vector>
namespace offline_simulation {
    namespace motion {
        struct OperationFailed {
        };
        struct Tuning {
            double gravity=0.,gravity_factor=0.,cohesion=0.,power_factor=0.,reverse_power=0.,engine_min_v=0.;
            double drive_traction=0.,lng_full_y=0.,lng_min_y=0.,lng_full=0.,lng_min=0.;
            double side_full_y=0.,side_min_y=0.,side_full=0.,side_min=0.;
            double coh_decay_y=0.,coh_decay_factor=0.,coh_decay_pow=0.,slope_coh_y=0.,slope_coh=0.,coh_bound=0.;
            double steer_resist=0.,coast_share=0.,hold_tan=0.,slide_kinetic=0.,slip_tan=0.,slip_drag=0.;
            double overspeed=0.,angular_time=0.,speed_rot_cost=0.;
            double follow_base=0.,follow_min=0.,follow_max=0.,pitch_limit=0.;
            double hard_brake=0.,hard_stop=0.,hard_entry=0.,hard_slide=0.;
            int hard_ticks=0, constraint_iterations=0;
            std::vector<double> glancing_yaws;
            double drown_probe=0.,drown_seconds=0.,overturn_ignore=0.,overturn_death=0.,warning_cos=0.,danger_cos=0.;
        };
        struct Descriptor {
            std::int64_t revision=0;
            double mass=0.,power=0.,forward_limit=0.,reverse_limit=0.,rotation_speed=0.;
            std::array<double,3> resistance={
            };
            double friction=0.,brake=0.,track_center=0.,native_power_ratio=0.;
            bool around_center=true,detailed_suspension=false;
            std::array<double,4> shape={
            }
            ,bounds={
            };
            std::array<double,2> heights={
            };
            Point water_sensor={
            };
            double siege_limit=0.,kinetic_speed=0.;
        };
        struct State {
            std::uint64_t presence=0;
            // Python-owned optional field presence, not a schema hash.
            Pose pose;
            double terrain_pitch=0.,speed=0.,turn_speed=0.,vertical_speed=0.,soft_contact_speed=0.;
            double push_x=0.,push_z=0.,push_yaw=0.,contact_forward=0.,last_drive_pitch=0.,direction_command=0.;
            double drown_check=0.,drown_time=0.,water_depth=-1.,overturn_check=0.,overturn_time=0.;
            double siege_remaining=0.,siege_total=0.;
            int movement_dir=0,rotation_dir=0,motor_turn=0,hard_grind=0,siege_state=0,overturn_level=0;
            bool alive=true,airborne=false,grounded=false,service_brake=false,contact_dynamics=false;
            bool drowning=false,overturned=false,rotation_blocked=false,query_failed=false,has_soft_contact=false;
            int death_reason=0;
            std::int64_t team=0;
        };
        struct Command {
            double throttle=0.,turn=0.,slope_pitch=0.,travel_sign=1.;
            bool path_clear=true,pose_frozen=false,generic_collision=false,exact_collision=true;
            bool allow_shallow=false,wet_escape=false,siege_locked=false,diagnostics=false;
        };
        struct CatalogResult {
            int status=0;
            bool accepted=false;
        };
        // 0 clear,1 hard,2 soft,3 crushed
        struct Support {
            bool present=false;
            double height=0.;
        };
        struct EffectReceipt {
            State state;
        };
        struct Effect {
            ActorKey actor;
            int kind=0;
            double value=0.;
        };
        // 1 landing,2 world impact,3 drowning,4 overturn,5 invalidate
        struct DriveTrace {
            double speed=0.,pitch=0.,throttle=0.,world_speed=0.;
            bool baked_veto=false,path_clear=true,frozen=false,hard_contact=false;
            int world_status=0;
            Pose integrated;
            Support centre,highest;
            bool grounded_before=false,rise_obstacle=false,rise_continuous=false;
            double support_limit=0.;
            bool has_support=false,has_limit=false;
        };
        struct Entry {
            State state;
            Descriptor descriptor,normal_descriptor,siege_descriptor;
            bool has_siege=false,drive_pending=false;
            offline_math::Vec drive_move={0.,0.};
            Pose tick_pose,settled_pose,after_contacts;
            DriveTrace trace;
            bool diagnostics=false,vertical_cohort=false,pose_rollback=false;
            State rollback;
            double passive_forward=0.,attempted_yaw=0.;
            bool prepared=false,advanced=false,siege_locked=false,tick_safe=false,support_blocked=false,ballistic=false;
        };
        // Every engine operation is synchronous on the main thread. This interface
        // never retains Python pointers. Native world request batches use the existing
        // WorldDispatch schema directly, without a Python snapshot round trip.
        class Engine {
            public:     virtual ~Engine() {
            }
            virtual bool begin_world(ActorKey,const State&,const Descriptor&,const WorldSnapshot&,bool commit,bool active,bool receipt_allowed)=0;
            virtual int dispatch_world(std::uint32_t,const WorldRequest*,std::uint32_t,WorldResponse*)=0;
            virtual void end_world()=0;
            virtual CatalogResult catalog(ActorKey,const State&,double speed,double motion_yaw,double dt,bool passive,bool commit)=0;
            virtual bool pose_guard(ActorKey,const Pose&,const Pose&,bool hazards)=0;
            virtual bool turret_guard(ActorKey,const Pose&,const Pose&)=0;
            virtual bool corridor(ActorKey,const State&,double travel_yaw,bool wet_escape,bool shallow)=0;
            virtual std::vector<Support> ground(ActorKey,const std::vector<Point>&)=0;
            virtual bool support_path(ActorKey,const Pose&,const Pose&,double support_y,double dt)=0;
            virtual double water(ActorKey,const Pose&)=0;
            virtual bool wreck_rotation(ActorKey,const Pose&,const Pose&,double dt,offline_math::Vec translation)=0;
            virtual bool safe(ActorKey,const Pose&)=0;
            virtual EffectReceipt effect(const Effect&)=0;
            virtual void motion_finished(ActorKey,int status,double initial_speed,double final_speed,bool hard,const Pose&,double contact_yaw,bool resolved)=0;
            virtual void begin_contacts(const std::vector<offline_contact::Body>&)=0;
            virtual std::vector<std::pair<std::int64_t,std::int64_t>> contact_finalize(ActorKey,const offline_contact::Row&)=0;
        };
        class Store {
            std::map<ActorKey,Entry> actors_;
            Tuning tuning_;
            TickToken token_;
            double dt_=0.,now_=0.;
            bool configured_=false,in_slice_=false,in_call_=false;
            std::vector<ActorKey> order_,settle_order_;
            std::vector<offline_contact::Body> humans_;
            std::vector<std::int64_t> contact_order_;
            std::vector<std::pair<std::int64_t,std::int64_t>> ram_contacts_;
            std::vector<Effect> effects_;
            std::vector<offline_contact::Row> contact_rows_;
            std::size_t next_actor_=0;
            Entry &entry(ActorKey);
            void fail(ActorKey);
            const State &prepare_impl(ActorKey,Engine&);
            const State &advance_impl(ActorKey,const Command&,Engine&);
            void emit(Engine&,ActorKey,int,double=0.);
            int world(ActorKey,Entry&,double,double,double,bool,bool,Engine&);
            bool vertical(ActorKey,Entry&,double,const Pose&,Engine&);
            void push(ActorKey,Entry&,const offline_contact::Row&,double,bool,bool,Engine&);
            std::vector<offline_math::Body> peers(ActorKey,bool drive=false) const;
            public:     void configure(Tuning);
            void install(ActorKey,State,Descriptor,const Descriptor* siege=nullptr);
            void patch(ActorKey,const State&);
            void patch_fields(ActorKey,const State&,unsigned mask);
            void descriptor_patch(ActorKey,Descriptor,const Descriptor*,int siege_state,double remaining,double total);
            // Explicit owner/authority/turret rebase; no hidden mirrored writes.
            const State &snapshot(ActorKey) const;
            const Entry &observation(ActorKey) const;
            const Descriptor &descriptor(ActorKey) const;
            void begin_slice(TickToken,double dt,double now,const std::vector<ActorKey>&,                      std::vector<offline_contact::Body> humans, std::vector<std::int64_t> contact_order, const std::vector<ActorKey>& settle_order);
            const State &prepare_actor(ActorKey,Engine&);
            const State &advance_actor(ActorKey,const Command&,Engine&);
            void settle_roster(Engine&);
            const std::map<ActorKey,Entry> &actors() const {
                return actors_;
            }
            const std::vector<Effect> &effects() const {
                return effects_;
            }
            const std::vector<offline_contact::Row> &contact_rows() const {
                return contact_rows_;
            }
        };
    }
}
#endif
