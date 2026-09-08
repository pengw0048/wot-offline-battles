#ifndef OFFLINE_EXPERIMENT_NAVIGATION_SNAPSHOT_H
#define OFFLINE_EXPERIMENT_NAVIGATION_SNAPSHOT_H
#include "navigation_state.h"
#include <iomanip>
#include <sstream>

// Diagnostic projection is explicit and outside the native navigation flow.
// It exists to compare every persistent field with the Python reference.
namespace offline_nav {
inline std::string quoted(const std::string &value){
    std::string out="\"";
    const char *digits="0123456789abcdef";
    for(unsigned char c:value){
        if(c=='"'||c=='\\'){out+='\\';out+=c;}
        else if(c<32){out+="\\u00";out+=digits[c>>4];out+=digits[c&15];}
        else out+=c;
    }
    return out+'"';
}
inline std::string number(double v){std::ostringstream out;out<<std::setprecision(17)<<v;return out.str();}
inline std::string boolean(bool v){return v?"true":"false";}
inline std::string key_value(const std::string &v){return v.empty()?"null":quoted(v);}
inline std::string point_value(Point p){return "["+number(p.x)+","+number(p.y)+","+number(p.z)+"]";}
inline std::string cell_value(Cell c){return "["+std::to_string(c.first)+","+std::to_string(c.second)+"]";}
inline std::string edge_value(Edge e){return "["+cell_value(e.first)+","+cell_value(e.second)+"]";}
inline std::string point_value(Optional<Point> p){return p.has?point_value(p.value):"null";}
inline std::string number(Optional<double> v){return v.has?number(v.value):"null";}
inline std::string path_value(const Path &path){std::string out="[";for(Point p:path){if(out.size()>1)out+=',';out+=point_value(p);}return out+"]";}
struct Object {
    std::string text="{";
    void field(const std::string &key,const std::string &value){if(text.size()>1)text+=',';text+=quoted(key)+":"+value;}
    std::string done()const{return text+"}";}
};
inline std::string state_value(const State &s){
    Object out;
    out.field("last_position",point_value(s.last_position));out.field("progress_time",number(s.progress_time));
    out.field("path_key",key_value(s.path_key));out.field("index",number(s.index));out.field("recovery",number(s.recovery));
    out.field("recovery_until",number(s.recovery_until));out.field("recovery_key","null");out.field("recovery_start",point_value(s.recovery_start));
    out.field("request_key",key_value(s.request_key));out.field("request_path_key",key_value(s.request_path_key));out.field("planned_goal",point_value(s.planned_goal));
    out.field("planned_at",number(s.planned_at));out.field("navigation_status",quoted(s.status==0?"pending":s.status==1?"safe":"blocked"));
    out.field("target_is_terminal",boolean(s.terminal));out.field("replan_generation",number(s.generation));out.field("replan_active",boolean(s.replan_active));
    out.field("macro_replan_active",boolean(s.macro_active));out.field("macro_progress_replans",number(s.macro_replans));out.field("macro_progress_at",number(s.macro_at));
    out.field("macro_progress_position",point_value(s.macro_position));out.field("macro_progress_target",point_value(s.macro_target));
    out.field("macro_progress_path_key",key_value(s.macro_path_key));out.field("macro_progress_index",number(s.macro_index));
    out.field("blocked_step_replans",number(s.blocked_replans));out.field("blocked_step_escalated_until",number(s.blocked_until));
    if(s.tracker.has){
        const BlockedTracker &t=s.tracker.value;Object tracker;
        tracker.field("key",edge_value(t.key));tracker.field("count",number(t.count));tracker.field("first_at",number(t.first));
        tracker.field("last_at",number(t.last));tracker.field("origin",point_value(t.origin));out.field("blocked_step_tracker",tracker.done());
    }else out.field("blocked_step_tracker","null");
    if(s.last_target.has)out.field("last_target",point_value(s.last_target));
    if(s.shallow.has)out.field("controlled_shallow_target",point_value(s.shallow));
    if(s.pending_since.has)out.field("pending_since",number(s.pending_since));
    if(s.escape.has)out.field("macro_escape_target",point_value(s.escape));
    if(s.escape_until.has)out.field("macro_escape_until",number(s.escape_until));
    return out.done();
}
inline std::string timed_value(const TimedPenalties &values){
    std::string out="[";for(auto item:values){if(out.size()>1)out+=',';out+="["+edge_value(item.first)+","+number(item.second.first)+","+number(item.second.second)+"]";}return out+"]";
}
inline std::string penalties_value(const std::map<int,TimedPenalties> &values){
    Object out;for(const auto &item:values)out.field(std::to_string(item.first),timed_value(item.second));return out.done();
}
inline std::string snapshot(const Navigator &nav,bool full){
    Object out,paths,searches,times,revisions,search_times;
    for(const auto &item:nav.paths)paths.field(item.first,path_value(*item.second));
    for(const auto &item:nav.searches){
        Object job;job.field("last_frame",number(item.second.last_frame));job.field("hull_revision",number(item.second.revision));
        job.field("done",boolean(item.second.search->done()));job.field("expansions",number(item.second.search->expansions()));
        searches.field(item.first,job.done());
    }
    out.field("paths",paths.done());out.field("searches",searches.done());
    out.field("search_next_key",key_value(nav.next_key));out.field("search_credit",number(nav.credit));out.field("search_frame_budget",number(nav.budget));
    out.field("search_completed",number(nav.completed));out.field("search_failed",number(nav.failed_count));
    if(!full)return out.done();
    for(const auto &item:nav.path_times)times.field(item.first,number(item.second));
    for(const auto &item:nav.revisions)revisions.field(item.first,number(item.second));
    for(const auto &item:nav.search_times)search_times.field(item.first,number(item.second));
    out.field("path_times",times.done());out.field("path_hull_revisions",revisions.done());out.field("search_times",search_times.done());
    out.field("search_frame_time",number(nav.frame_time));out.field("housekeeping_time",number(nav.housekeeping));
    out.field("search_frame_serial",number(nav.serial));out.field("search_processed_frame",number(nav.processed));out.field("search_frame_open",boolean(nav.frame_open));
    out.field("search_auto_time",number(nav.auto_time));out.field("search_now",number(nav.now));out.field("search_max_expansions",number(nav.maximum));
    Object states,direct,modes,totals;
    for(const auto &item:nav.states)states.field(std::to_string(item.first),state_value(item.second));
    for(const auto &item:nav.direct){
        const DirectState &s=item.second;Object value;
        value.field("path_key",key_value(s.path_key));value.field("target",point_value(s.target));value.field("position",point_value(s.position));
        value.field("progress_at",number(s.progress_at));value.field("replans",number(s.replans));
        if(s.escape.has)value.field("escape_target",point_value(s.escape));
        if(s.until.has)value.field("escape_until",number(s.until));
        direct.field(std::to_string(item.first),value.done());
    }
    const char *names[]={"pending","safe_direct","safe_local","reactive"};
    for(const auto &item:nav.modes)modes.field(std::to_string(item.first),quoted(names[item.second]));
    for(int i=0;i<4;++i)totals.field(names[i],number(nav.totals[i]));
    out.field("bot_states",states.done());out.field("bot_direct_progress",direct.done());out.field("fallback_modes",modes.done());out.field("fallback_totals",totals.done());
    out.field("fallback_recovered",number(nav.recovered));out.field("bot_failed_edges",penalties_value(nav.failed));out.field("bot_macro_edges",penalties_value(nav.macro));
    out.field("grid_failed_edges",timed_value(nav.grid->failed));
    return out.done();
}
}
#endif
