#ifndef OFFLINE_EXPERIMENT_NAVIGATION_STATE_H
#define OFFLINE_EXPERIMENT_NAVIGATION_STATE_H

#include "navigation_grid.h"
#include "navigation_search.h"
#include "query_bridge.h"
#include <string>

namespace offline_nav {
template<class T> struct Optional {
    bool has=false;T value{};
    Optional(){}
    Optional(T v):has(true),value(v){}
    Optional &operator=(T v){has=true;value=v;return *this;}
    void reset(){has=false;}
};
struct Identity {
    std::string repr,kind;
    int owner=-1;
    bool prefer=false;
};
inline std::string cell_repr(Cell c){return "("+std::to_string(c.first)+", "+std::to_string(c.second)+")";}
inline Identity prefixed(const Identity &base,const std::string &kind,int owner,const std::string &third){
    Identity result;
    result.repr="('"+kind+"', "+std::to_string(owner)+", "+third;
    result.repr+=(base.repr=="()"?")":", "+base.repr.substr(1));
    result.kind=kind;result.owner=owner;result.prefer=kind=="continue"&&base.kind=="route";
    return result;
}
struct BlockedTracker {Edge key;int count;double first,last;Point origin;};
struct State {
    Point last_position,macro_position;
    double progress_time,planned_at=0,macro_at;
    std::string path_key,request_key,request_path_key,macro_path_key;
    Optional<Point> last_target,planned_goal,recovery_start,macro_target,shallow,escape;
    Optional<double> pending_since,escape_until;
    int index=0,recovery=0,generation=0,macro_replans=0,macro_index=0,blocked_replans=0;
    double recovery_until=0,blocked_until=0;
    bool terminal=false,replan_active=false,macro_active=false;
    int status=0; // pending, safe, blocked
    Optional<BlockedTracker> tracker;
    State(Point current,double now):last_position(current),macro_position(current),progress_time(now),macro_at(now){}
};
struct DirectState {
    std::string path_key;Point target,position;double progress_at;
    int replans=0;Optional<Point> escape;Optional<double> until;
    DirectState(const std::string &key,Point goal,Point current,double now):path_key(key),target(goal),position(current),progress_at(now){}
};
struct SearchJob {
    Identity identity;Point goal;Penalties hard;
    int revision;Optional<double> last_frame;
    std::unique_ptr<OfflineNavigationSearch> search;
};
struct Navigator {
    std::shared_ptr<Grid> grid;
    std::map<std::string,std::shared_ptr<Path> > paths;
    std::map<std::string,double> path_times,search_times;
    std::map<std::string,int> revisions;
    std::map<std::string,int> key_ids;
    int next_key_id=1;
    std::map<std::string,SearchJob> searches;
    std::map<int,State> states;
    std::map<int,DirectState> direct;
    std::map<int,TimedPenalties> failed,macro;
    std::map<int,int> modes;
    std::array<int,4> totals{{0,0,0,0}}; // pending, safe_direct, safe_local, reactive
    int recovered=0,completed=0,failed_count=0,maximum=4096;
    Optional<double> frame_time,housekeeping,auto_time;
    std::string next_key;
    double credit=0,now=0;
    int serial=0,processed=-1,budget=96;
    bool frame_open=false;
    explicit Navigator(std::shared_ptr<Grid> value):grid(value){}
    std::string cache_key(const Identity &identity,Point goal) const {
        return "("+identity.repr+", "+cell_repr(grid->cell(goal))+")";
    }
    void set_mode(int id,int mode){
        if(mode<0||mode==1){auto state=states.find(id);if(state!=states.end())state->second.pending_since.reset();}
        auto old=modes.find(id);
        if(old!=modes.end()&&old->second==mode)return;
        if(old==modes.end()&&mode<0)return;
        if(old!=modes.end()&&mode<0)++recovered;
        if(mode<0)modes.erase(id);else{modes[id]=mode;++totals[mode];}
    }
    Penalties active(std::map<int,TimedPenalties> &collection,int owner,double at){
        Penalties result;if(owner<0)return result;
        auto found=collection.find(owner);if(found==collection.end())return result;
        for(auto it=found->second.begin();it!=found->second.end();){
            if(at>=it->second.first)it=found->second.erase(it);
            else{result[it->first]=it->second.second;++it;}
        }
        if(found->second.empty())collection.erase(found);
        return result;
    }
    Penalties planning(int owner,double at){
        Penalties result=active(failed,owner,at);
        for(auto item:active(macro,owner,at))result[item.first]=item.second;
        return result;
    }
    bool penalized(int owner,Point a,Point b,double at,bool only_macro=false){
        return grid->path_penalty(Path{a,b},only_macro?active(macro,owner,at):planning(owner,at));
    }
    void cancel(int owner,const std::string &keep="",const std::string &kind=""){
        for(auto it=searches.begin();it!=searches.end();){
            if(it->second.identity.owner==owner&&it->first!=keep&&(kind.empty()||it->second.identity.kind==kind)){
                std::string retired=it->first;
                search_times.erase(retired);it=searches.erase(it);retire_key(retired);
            }else ++it;
        }
    }
    void reset_macro(State &s,Point current,Optional<Point> target,double at){
        s.macro_at=at;s.macro_position=current;s.macro_target=target;s.macro_path_key=s.path_key;s.macro_index=s.index;
    }
    void finish_macro(int id,State &s){
        macro.erase(id);s.escape.reset();s.escape_until.reset();s.macro_active=false;s.replan_active=false;
        s.recovery_start.reset();s.path_key.clear();s.index=0;cancel(id);
    }
    bool start_macro(int id,State &s,Point current,Point target,double at){
        Point escape;bool has=grid->safe_local(current,target,at,{},id%2?1:-1,{},0.42,escape);
        auto edges=grid->edges(current,target);
        if(edges.empty()&&!has)return false;
        if(has){s.escape=escape;s.escape_until=at+4.0;}
        else macro[id][edges[0]]=std::make_pair(at+4.0,240.0);
        ++s.generation;++s.macro_replans;s.path_key.clear();s.index=0;s.recovery_start=current;
        s.replan_active=!has;s.macro_active=true;s.pending_since=at-0.6;s.shallow.reset();cancel(id);
        reset_macro(s,current,target,at);return true;
    }
    bool observe_macro(int id,State &s,Point current,Point goal,double at){
        Optional<Point> target=s.last_target;
        if(s.macro_active&&!s.escape.has&&active(macro,id,at).empty()){
            finish_macro(id,s);reset_macro(s,current,target,at);return false;
        }
        if(!target.has||distance(current,target.value)<=1.5||distance(current,goal)<=1.5){reset_macro(s,current,target,at);return false;}
        bool changed=s.macro_path_key!=s.path_key||s.macro_index!=s.index;
        if(!s.macro_target.has||changed||distance(s.macro_target.value,target.value)>2.0){reset_macro(s,current,target,at);return false;}
        double progress=distance(s.macro_position,target.value)-distance(current,target.value);
        s.macro_target=target;
        if(progress>=0.20){if(s.macro_active)finish_macro(id,s);reset_macro(s,current,target,at);return false;}
        return at-s.macro_at>=12.0?start_macro(id,s,current,target.value,at):false;
    }
    Optional<Point> observe_direct(int id,Point current,Point goal,const Identity &identity,double at,bool movement){
        auto it=direct.find(id);
        if(it==direct.end())it=direct.emplace(id,DirectState(identity.repr,goal,current,at)).first;
        DirectState &s=it->second;
        if(s.path_key!=identity.repr||distance(s.target,goal)>2.0||!movement||distance(current,goal)<=1.5){
            s.path_key=identity.repr;s.target=goal;s.position=current;s.progress_at=at;s.escape.reset();s.until.reset();return {};
        }
        if(s.escape.has){
            if(at<(s.until.has?s.until.value:at)&&distance(current,s.escape.value)>1.5&&grid->dry(current,s.escape.value,at))return s.escape;
            s.target=goal;s.position=current;s.progress_at=at;s.escape.reset();s.until.reset();return {};
        }
        double progress=distance(s.position,s.target)-distance(current,s.target);
        if(progress>=0.20){s.target=goal;s.position=current;s.progress_at=at;return {};}
        if(at-s.progress_at<12.0)return {};
        Point escape;bool has=grid->safe_local(current,goal,at,{},id%2?1:-1,{},0.42,escape);
        s.target=goal;s.position=current;s.progress_at=at;
        if(!has)return {};
        s.escape=escape;s.until=at+4.0;++s.replans;return escape;
    }
    bool report_blocked(int id,Point current,Optional<Point> target,double at){
        auto found=states.find(id);if(found==states.end()||!target.has||distance(current,target.value)<=1.5)return false;
        auto edges=grid->edges(current,target.value);if(edges.empty())return false;
        State &s=found->second;if(at<s.blocked_until)return false;
        bool same=s.tracker.has&&s.tracker.value.key==edges[0]&&at-s.tracker.value.last<=0.5&&
            distance(current,s.tracker.value.origin)<=std::max(1.5,grid->data->cell*0.5);
        if(same){++s.tracker.value.count;s.tracker.value.last=at;}
        else s.tracker=BlockedTracker{edges[0],1,at,at,current};
        if(s.tracker.value.count<4||at-s.tracker.value.first<1.0)return false;
        failed[id][edges[0]]=std::make_pair(at+12.0,240.0);
        ++s.generation;++s.blocked_replans;s.blocked_until=at+2.0;s.tracker.reset();s.path_key.clear();s.index=0;
        s.recovery_start=current;s.replan_active=true;s.macro_active=false;macro.erase(id);
        s.escape.reset();s.escape_until.reset();s.shallow.reset();cancel(id);return true;
    }
    void accrue(double elapsed){credit=std::min(384.0,credit+std::max(0.0,elapsed)*960.0);budget=std::min(384,std::max(96,static_cast<int>(credit)));}
    void begin(double elapsed){++serial;frame_open=true;accrue(elapsed);}
    void automatic(double at){
        if(auto_time.has&&std::abs(at-auto_time.value)<0.000001)return;
        double elapsed=auto_time.has?std::max(0.0,at-auto_time.value):0.10;
        auto_time=at;++serial;accrue(elapsed);
    }
    void callback(std::vector<double> &packet){
        if(offline_query(packet.data(),static_cast<int>(packet.size())))throw std::runtime_error("navigation identity callback");
    }
    int key_id(const std::string &key){
        auto found=key_ids.find(key);if(found!=key_ids.end())return found->second;
        int id=next_key_id++;
        std::vector<double> packet{503.0,static_cast<double>(id),static_cast<double>(key.size())};
        for(unsigned char c:key)packet.push_back(c);
        callback(packet);key_ids[key]=id;return id;
    }
    void save_path(const std::string &key,std::shared_ptr<Path> path,double at,int revision){
        int id=key_id(key);std::vector<double> packet{500.0,static_cast<double>(id),static_cast<double>(path->size())};
        for(Point p:*path){packet.push_back(p.x);packet.push_back(p.y);packet.push_back(p.z);}
        callback(packet);
        paths[key]=path;path_times[key]=at;revisions[key]=revision;
    }
    void retire_key(const std::string &key){
        auto found=key_ids.find(key);
        if(found==key_ids.end()||paths.count(key)||searches.count(key)||next_key==key)return;
        std::vector<double> packet{504.0,static_cast<double>(found->second)};callback(packet);key_ids.erase(found);
    }
    void erase_path(const std::string &key){
        std::vector<double> packet{501.0,static_cast<double>(key_id(key))};callback(packet);
        paths.erase(key);path_times.erase(key);revisions.erase(key);retire_key(key);
    }
    void trim(){
        if(paths.size()<=96)return;
        std::vector<std::string> keys;for(auto item:path_times)keys.push_back(item.first);
        // Python's stable timestamp sort inherits the exact interpreter's
        // dictionary order on ties. Ask its tiny key-only mirror for that
        // order; no geometry, search, route or Bot state crosses this boundary.
        std::vector<double> packet(2+keys.size(),0.0);packet[0]=502;packet[1]=keys.size();callback(packet);
        std::map<int,int> rank;
        for(size_t i=0;i<keys.size();++i){
            double raw=packet[i+2];if(!std::isfinite(raw)||raw<1||raw>=next_key_id||raw!=std::floor(raw))throw std::invalid_argument("navigation cache order");
            int id=static_cast<int>(raw);if(rank.count(id))throw std::invalid_argument("duplicate cache key");rank[id]=static_cast<int>(i);
        }
        for(const std::string &key:keys)if(!rank.count(key_ids.at(key)))throw std::invalid_argument("missing cache key");
        std::sort(keys.begin(),keys.end(),[this,&rank](const std::string &a,const std::string &b){
            if(path_times.at(a)!=path_times.at(b))return path_times.at(a)<path_times.at(b);
            return rank.at(key_ids.at(a))<rank.at(key_ids.at(b));
        });
        for(size_t i=0;i<keys.size()-80;++i)erase_path(keys[i]);
    }
    void finish_search(const std::string &key,double at){
        SearchJob &job=searches.at(key);Path path;
        for(int i:job.search->path())path.push_back(grid->point(Cell(i%grid->data->width,i/grid->data->width),grid->data->heights[i]/1000.0));
        if(!path.empty()){
            Point goal=job.goal;goal.y=path.back().y;grid->ground(job.goal,goal.y);
            if(grid->segment_clear(path.back(),goal)&&!grid->path_penalty(Path{path.back(),goal},job.hard))path.push_back(goal);
            path=grid->smooth(path,job.search?search_times.at(key):at,job.identity.prefer,job.hard);
        }
        save_path(key,std::make_shared<Path>(path),at,job.revision);
        if(path.empty())++failed_count;else ++completed;
        searches.erase(key);search_times.erase(key);
    }
    void advance(double at){
        now=at;if(!frame_open)automatic(at);if(processed==serial)return;
        processed=serial;frame_time=at;
        if(searches.empty()){std::string retired=next_key;next_key.clear();retire_key(retired);return;}
        std::vector<std::string> keys;for(const auto &item:searches)keys.push_back(item.first);
        auto next=std::find(keys.begin(),keys.end(),next_key);if(next!=keys.end())std::rotate(keys.begin(),next,keys.end());
        std::deque<std::string> queue(keys.begin(),keys.end());
        int remaining=std::min(std::max(0,static_cast<int>(credit)),std::max(0,budget)),spent=0;
        grid->search_globals();
        while(remaining>0&&!queue.empty()){
            std::string key=queue.front();queue.pop_front();auto found=searches.find(key);if(found==searches.end())continue;
            found->second.search->step();found->second.last_frame=at;--remaining;++spent;
            grid->drain_search_expiry();
            if(found->second.search->done())finish_search(key,at);else queue.push_back(key);
        }
        credit=std::max(0.0,credit-spent);budget=std::max(0,budget-spent);
        std::string retired=next_key;next_key=queue.empty()?"":queue.front();retire_key(retired);trim();
    }
    void tick(double at){
        advance(at);
        if(!housekeeping.has||at-housekeeping.value>=1.0){
            housekeeping=at;
            for(auto it=grid->failed.begin();it!=grid->failed.end();)if(at>=it->second.first)it=grid->failed.erase(it);else ++it;
            if(grid->failed.size()>128){
                std::vector<Edge> edges;for(auto item:grid->failed)edges.push_back(item.first);
                std::stable_sort(edges.begin(),edges.end(),[this](Edge a,Edge b){return grid->failed.at(a).first<grid->failed.at(b).first;});
                for(size_t i=0;i<edges.size()-128;++i)grid->failed.erase(edges[i]);
            }
            std::vector<int> ids;for(auto item:failed)ids.push_back(item.first);for(int id:ids)active(failed,id,at);
            ids.clear();for(auto item:macro)ids.push_back(item.first);for(int id:ids)active(macro,id,at);
        }
    }
    std::pair<std::string,std::shared_ptr<Path> > path(const Identity &identity,Point start,Point goal,double at){
        std::string key=cache_key(identity,goal);int owner=identity.owner;
        if(owner>=0)cancel(owner,key,identity.kind);
        auto cached=paths.find(key);
        if(cached!=paths.end()){
            auto value=cached->second;Penalties hard=active(macro,owner,at);
            bool hull=revisions.at(key)!=grid->hull_revision&&grid->path_penalty(*value,grid->hulls);
            if(!value->empty()&&!hull&&!grid->path_timed(*value,at)&&!grid->path_penalty(*value,hard)){
                path_times[key]=at;revisions[key]=grid->hull_revision;return {key,value};
            }
            if(value->empty()&&at-path_times.at(key)<8.0)return {key,value};
            erase_path(key);
        }
        if(!searches.count(key)){
            Penalties local=planning(owner,at),hard=active(macro,owner,at);
            if(!grid->path_penalty(Path{start,goal},local)&&grid->dry(start,goal,at)){
                auto value=std::make_shared<Path>(Path{start,goal});save_path(key,value,at,grid->hull_revision);return {key,value};
            }
            Cell first,last;int a=grid->nearest(grid->cell(start),3,first)?grid->index(first):-1;
            int b=grid->nearest(grid->cell(goal),3,last)?grid->index(last):-1;
            SearchJob job;job.identity=identity;job.goal=goal;job.hard=hard;job.revision=grid->hull_revision;
            job.search.reset(new OfflineNavigationSearch(grid->data,a,b,maximum,at,identity.prefer));
            std::unordered_map<uint64_t,double> native_local;std::unordered_set<uint64_t> native_hard;uint64_t edge;
            for(auto item:local)if(grid->search_edge(item.first,edge))native_local[edge]=item.second;
            for(auto item:hard)if(grid->search_edge(item.first,edge))native_hard.insert(edge);
            job.search->inputs({},native_local,native_hard,!hard.empty());
            searches.emplace(key,std::move(job));search_times[key]=at;
            key_id(key);
        }
        advance(at);cached=paths.find(key);return {key,cached==paths.end()?std::shared_ptr<Path>():cached->second};
    }
    Point fallback(int id,Point current,Point goal,double at,const Path &avoid,State &s,bool safe=true){
        if(s.shallow.has&&(distance(current,s.shallow.value)<=1.5||penalized(id,current,s.shallow.value,at)||
                grid->hazard(current,s.shallow.value,3)||!grid->segment_clear(current,s.shallow.value)))s.shallow.reset();
        Point target;
        if(safe&&grid->safe_local(current,goal,at,avoid,id%2?1:-1,active(macro,id,at),0,target)){
            s.last_target=target;s.status=1;s.terminal=distance(target,goal)<=1.5;set_mode(id,2);return target;
        }
        s.last_target=goal;s.status=2;s.terminal=false;set_mode(id,3);return goal;
    }
    Point pending(int id,Point current,Point goal,double at,State &s,const Path &avoid,bool allow=true,bool immediate=false){
        if(allow&&s.last_target.has){
            Point target=s.last_target.value;bool shallow=grid->hazard(current,target,4);
            if(grid->segment_penalty(current,target,at)<=0&&!penalized(id,current,target,at,true)&&grid->segment_clear(current,target)&&
                    (!shallow||(s.shallow.has&&s.shallow.value==target))&&distance(current,target)>1.5){
                s.status=0;s.terminal=false;set_mode(id,0);return target;
            }
        }
        if(!s.pending_since.has)s.pending_since=at;
        Point target;
        if((immediate||at-s.pending_since.value>=0.6)&&
                grid->safe_local(current,goal,at,avoid,id%2?1:-1,active(macro,id,at),0,target)){
            s.last_target=target;s.status=0;s.terminal=false;set_mode(id,2);return target;
        }
        s.last_target=current;s.status=0;s.terminal=false;set_mode(id,0);return current;
    }
    bool planned_next(Point current,const Path &path,int index,double at){
        if(index+1>=static_cast<int>(path.size()))return false;
        Point target=path[index+1];bool reached=distance(current,path[index])<=1.5;
        if((!reached&&!live_climb(current,path,index,index+1))||grid->segment_penalty(current,target,at)>0||!grid->segment_clear(current,target))return false;
        return !grid->hazard(current,target,4)||grid->hazard(path[index],target,4);
    }
    bool planned_current(Point current,const Path &path,int index,double at,Optional<Point> selected){
        if(index<=0||index>=static_cast<int>(path.size()))return false;
        Point target=path[index];
        if(((!selected.has||selected.value!=target)&&!live_climb(current,path,index-1,index))||grid->segment_penalty(current,target,at)>0||!grid->segment_clear(current,target))return false;
        return !grid->hazard(current,target,4)||grid->hazard(path[index-1],target,4);
    }
    int lookahead(Point current,const Path &path,int index,const Identity &identity,double at,Optional<double> distance_limit){
        int result=index,limit=std::min(static_cast<int>(path.size()),index+(distance_limit.has?7:3));
        double horizon=distance_limit.has?std::max(grid->data->cell*2.0,distance_limit.value):0;
        Penalties hard=active(macro,identity.owner,at);
        for(int candidate=index+1;candidate<limit;++candidate){
            if(distance_limit.has&&candidate>index+1&&distance(current,path[candidate])>horizon)break;
            if((!identity.prefer||grid->clearance(path,index,candidate))&&live_climb(current,path,index,candidate)&&
                    !grid->path_penalty(Path{current,path[candidate]},hard)&&grid->dry(current,path[candidate],at))result=candidate;
            else break;
        }
        return result;
    }
    Point selected(int id,State &s,Point current,Point target,Point goal){
        s.last_target=target;if(grid->hazard(current,target,4))s.shallow=target;else s.shallow.reset();
        s.status=1;s.terminal=distance(target,goal)<=1.5;set_mode(id,-1);return target;
    }
    Point next_target(int id,Point current,Point goal,const Identity &identity,double at,
                      Optional<Point> anchor,const Path &avoid,Optional<double> horizon,bool movement){
        direct.erase(id);tick(at);
        auto found=states.find(id);if(found==states.end())found=states.emplace(id,State(current,at)).first;
        State &s=found->second;
        if(s.request_path_key==identity.repr&&s.planned_goal.has&&distance(s.planned_goal.value,goal)<grid->data->cell*2.0&&at-s.planned_at<2.0)goal=s.planned_goal.value;
        else{s.request_path_key=identity.repr;s.planned_goal=goal;s.planned_at=at;}
        std::string request=cache_key(identity,goal);bool changed=s.request_key!=request;
        bool transition=!s.request_key.empty()&&changed,allow=true;
        if(changed){
            allow=s.last_target.has&&(s.last_target.value.x-current.x)*(goal.x-current.x)+(s.last_target.value.z-current.z)*(goal.z-current.z)>0&&
                distance(s.last_target.value,goal)+0.20<distance(current,goal);
            if(!allow)s.last_target.reset();
            cancel(id);s.request_key=request;s.path_key.clear();s.index=0;
            s.last_position=current;s.progress_time=at;s.recovery=0;s.recovery_until=0;s.recovery_start.reset();
            s.replan_active=false;s.macro_active=false;macro.erase(id);s.escape.reset();s.escape_until.reset();s.pending_since.reset();s.shallow.reset();
            reset_macro(s,current,s.last_target,at);
        }else if(movement)observe_macro(id,s,current,goal,at);
        else{if(s.macro_active)finish_macro(id,s);reset_macro(s,current,s.last_target,at);}
        if(s.escape.has){
            Point escape=s.escape.value;
            if(at<(s.escape_until.has?s.escape_until.value:at)&&distance(current,escape)>1.5&&grid->dry(current,escape,at)){
                s.shallow.reset();s.last_target=escape;s.status=1;s.terminal=false;set_mode(id,-1);return escape;
            }
            finish_macro(id,s);reset_macro(s,current,s.last_target,at);
        }
        if(!s.macro_active&&distance(current,s.last_position)>=2.0){
            s.last_position=current;s.progress_time=at;s.recovery=0;s.recovery_until=0;s.replan_active=false;s.recovery_start.reset();
        }
        Point start=anchor.has?anchor.value:current;if(anchor.has)start.y=current.y;
        Identity effective=identity;
        if(s.replan_active){effective=prefixed(identity,"recovery",id,std::to_string(s.generation));start=s.recovery_start.has?s.recovery_start.value:current;}
        auto previous=paths.find(s.path_key);std::shared_ptr<Path> previous_path=previous==paths.end()?std::shared_ptr<Path>():previous->second;
        auto planned=path(effective,start,goal,at);std::string key=planned.first;auto route=planned.second;
        if(!route||route->empty()){
            if(grid->dry(current,goal,at)&&!penalized(id,current,goal,at,true)){
                s.shallow.reset();s.last_target=goal;s.status=1;s.terminal=true;set_mode(id,1);return goal;
            }
            if(!route)return pending(id,current,goal,at,s,avoid,allow,transition);
            s.path_key=key;return fallback(id,current,goal,at,avoid,s);
        }
        std::string active_key=s.path_key;
        if(!active_key.empty()&&active_key!=key){
            auto active_path=paths.find(active_key);
            if(active_path!=paths.end()&&!active_path->second->empty()&&!grid->path_timed(*active_path->second,at)){
                key=active_key;route=active_path->second;path_times[key]=at;
            }
        }
        if(s.path_key!=key){
            s.path_key=key;s.index=0;double best=1e18;
            for(size_t i=0;i<route->size();++i){double d=distance(current,(*route)[i]);if(d<best){best=d;s.index=static_cast<int>(i);}}
        }
        int index=std::min(s.index,static_cast<int>(route->size())-1);Optional<Point> selected_target;
        if(active_key==key&&previous_path==route&&s.last_target.has&&s.last_target.value==(*route)[index])selected_target=s.shallow;
        bool shallow=grid->hazard(current,(*route)[index],4);
        if(grid->segment_penalty(current,(*route)[index],at)>0||penalized(id,current,(*route)[index],at,true)||
                (shallow&&!planned_current(current,*route,index,at,selected_target))||!grid->segment_clear(current,(*route)[index])){
            Identity join=prefixed(identity,"join",id,cell_repr(grid->cell(current)));
            planned=path(join,current,goal,at);key=planned.first;
            if(!planned.second)return pending(id,current,goal,at,s,avoid,allow,transition);
            if(planned.second->empty()){s.path_key=key;return fallback(id,current,goal,at,avoid,s);}
            route=planned.second;s.path_key=key;s.index=0;index=0;
        }
        double radius=std::min(10.0,std::max(1.5,grid->data->cell*0.55));
        while(index+1<static_cast<int>(route->size())&&distance(current,(*route)[index])<radius&&planned_next(current,*route,index,at))++index;
        int look=lookahead(current,*route,index,effective,at,horizon);
        if(look==static_cast<int>(route->size())-1&&distance(current,(*route)[look])<radius&&distance((*route)[look],goal)>radius){
            Identity continuation=prefixed(identity,"continue",id,cell_repr(grid->cell(current)));
            planned=path(continuation,current,goal,at);
            if(planned.second&&!planned.second->empty()){
                route=planned.second;s.path_key=planned.first;int next=route->size()>1&&planned_next(current,*route,0,at)?1:0;
                next=lookahead(current,*route,next,continuation,at,horizon);s.index=next;
                return selected(id,s,current,(*route)[next],goal);
            }
            if(!planned.second)return pending(id,current,goal,at,s,avoid,allow,transition);
            return fallback(id,current,goal,at,avoid,s);
        }
        Point target=(*route)[look];
        if(distance(current,target)<=1.5&&distance(current,goal)>15.0)return fallback(id,current,goal,at,avoid,s);
        s.index=look;return selected(id,s,current,target,goal);
    }
    bool shallow_corridor(Point current,Point target,double yaw){
        double distance=std::max(1.0,grid->data->cell);Point end(current.x+std::sin(yaw)*distance,current.y,current.z+std::cos(yaw)*distance);
        std::vector<Cell> planned,committed;
        if(!grid->hazard_cells(current,target,4,planned)||planned.empty()||!grid->hazard_cells(current,end,4,committed)||
                grid->hazard(current,target,3)||grid->hazard(current,end,3))return false;
        std::set<Cell> allowed(planned.begin(),planned.end());for(Cell c:committed)if(!allowed.count(c))return false;return true;
    }
    bool shallow_step(int id,Point current,double yaw,double maximum_yaw=0.45,bool committed=false){
        auto found=states.find(id);if(found==states.end()||!found->second.shallow.has)return false;
        Point target=found->second.shallow.value;double dx=target.x-current.x,dz=target.z-current.z;
        if(committed){double length=std::sqrt(dx*dx+dz*dz);if(length<0.1)return false;
            return (std::sin(yaw)*dx+std::cos(yaw)*dz)/length>0.5&&shallow_corridor(current,target,yaw);
        }
        if(std::abs(dx)+std::abs(dz)<0.1)return false;
        return std::abs(wrap(yaw-std::atan2(dx,dz)))<=std::max(0.0,maximum_yaw)&&shallow_corridor(current,target,yaw);
    }
};
}
#endif
