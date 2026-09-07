// Persistent visibility/cache/scheduler/lease state with ordered query yields.
#include "perception_core.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <map>
#include <set>
#include <stdexcept>
#include <vector>

namespace {
const int OUTPUT=64,PACKET=576;
typedef std::array<int,2> Identity;
typedef std::array<int,3> TeamKey;
typedef std::array<int,4> Key;
struct Reader {
    double *b;int n,i;
    Reader(double *v,int size):b(v),n(size),i(1){}
    double next(){if(i>=n||!std::isfinite(b[i]))throw std::invalid_argument("perception buffer");return b[i++];}
    int integer(){double v=next();if(v!=std::floor(v)||std::abs(v)>1000000000)throw std::invalid_argument("perception int");return static_cast<int>(v);}
    void end(){if(i!=n)throw std::invalid_argument("perception width");}
};
struct Record {int kind,id,team,fire;bool alive;double x,y,z;};
struct Cached {double now;bool value;int fire;};
struct Projection {double view,base0,base1,shot,add,multiply;bool moving;};
struct Frame {
    bool active,prepared;double now;int budget;size_t next;
    std::vector<Key> order,prior,parked;
    std::set<Key> allowed,completed,requested,admitted,deferred;
    std::map<Key,int> classification;
    Frame():active(false),prepared(false),now(0),budget(0),next(0){}
};
struct Job {
    bool active,single,fired;int cursor,phase,stage;
    Record source,target;double now,distance;Key key;
    std::vector<bool> processed,shared;
    std::vector<std::array<int,4> > results;
    Projection projection;
    Job():active(false),single(false),fired(false),cursor(0),phase(0),stage(0),source(),target(),now(0),distance(0),key(),projection(){}
};
struct Owner {
    double ttl,shot_seconds,proximity,maximum,memory,designated;
    int budget;Frame frame;Job job;
    std::map<Key,Cached> cache;
    std::map<Identity,std::pair<int,double> > fire;
    std::map<TeamKey,double> spot;
    std::map<TeamKey,bool> remembered;
    std::map<Key,double> deferred;
    std::vector<Key> waiting;
    std::vector<Record> roster;
    std::map<std::pair<int,int>,Record> templates;
    double diagnostic_now;long admitted,completed,deferred_count,services[4];
    size_t max_depth;double max_age;
    Owner():diagnostic_now(0),admitted(0),completed(0),deferred_count(0),max_depth(0),max_age(0){for(int i=0;i<4;++i)services[i]=0;}
};
std::map<int,Owner> owners;int next_owner=1;
double clamp(double v,double low,double high){return std::max(low,std::min(high,v));}
Owner &owner(Reader &r){auto it=owners.find(r.integer());if(it==owners.end())throw std::invalid_argument("unknown perception owner");return it->second;}
Key pair_key(Record s,Record t){return Key{{s.kind,s.id,t.kind,t.id}};}
TeamKey team_key(int team,Record t){return TeamKey{{team,t.kind,t.id}};}
void renew(Owner &o,TeamKey key,double now,double duration){
    double deadline=now+clamp(duration,0,o.designated);
    auto it=o.spot.find(key);double prior=it==o.spot.end()?0:it->second;
    o.spot[key]=std::max(prior,deadline);
}
double remaining(Owner &o,TeamKey key,double now){
    auto it=o.spot.find(key);double value=(it==o.spot.end()?0:it->second)-now;
    if(value<=0){if(it!=o.spot.end())o.spot.erase(it);return 0;}
    return std::min(o.designated,value);
}
void complete(Owner &o,Key key){
    Frame &f=o.frame;if(!f.active)return;
    if(f.completed.insert(key).second)++o.completed;
    o.deferred.erase(key);f.allowed.erase(key);
    while(static_cast<int>(f.allowed.size())<f.budget && f.next<f.order.size()){
        Key candidate=f.order[f.next++];if(!f.completed.count(candidate))f.allowed.insert(candidate);
    }
}
bool allowed(const Owner &o,Key key){return !o.frame.active||(o.frame.allowed.count(key)&&o.frame.budget>0);}
void defer(Owner &o,Key key){
    Frame &f=o.frame;if(!f.active)return;
    f.requested.insert(key);if(f.deferred.insert(key).second)++o.deferred_count;
    o.deferred.insert(std::make_pair(key,f.now));
}
bool admit(Owner &o,Key key){
    Frame &f=o.frame;if(!f.active)return true;
    f.requested.insert(key);if(!allowed(o,key)){defer(o,key);return false;}
    --f.budget;
    if(f.admitted.insert(key).second){++o.admitted;auto it=f.classification.find(key);++o.services[it==f.classification.end()?3:it->second];}
    return true;
}
void store(Owner &o,bool value){
    Job &j=o.job;o.cache[j.key]=Cached{j.now,value,j.target.fire};complete(o,j.key);
    if(o.cache.size()>1024){
        // Supported rooms have at most thirty actors, hence at most 450
        // opposing live pairs. Reject an unbounded synthetic identity stream
        // instead of inventing Python-dict tie ordering for its prune branch.
        throw std::invalid_argument("perception room identity capacity");
    }
}
void result(Owner &o,bool value){
    Job &j=o.job;
    if(j.single){j.results.push_back(std::array<int,4>{{0,value,0,0}});j.active=false;return;}
    TeamKey key=team_key(j.source.team,j.target);
    if(value){renew(o,key,j.now,o.memory);o.remembered[key]=true;j.shared[j.cursor]=true;}
    if(!o.remembered.count(key))o.remembered[key]=remaining(o,key,j.now)>0;
    bool fresh=value||j.shared[j.cursor],visible=value||o.remembered[key]||j.shared[j.cursor];
    j.results.push_back(std::array<int,4>{{j.cursor,visible,value,fresh}});
    ++j.cursor;j.stage=0;
}
double camouflage(Projection p,bool fired,double foliage,bool upper){
    double value=p.moving?p.base0:p.base1;
    value=(value+(upper?0:p.add))*std::max(0.0,upper?1:p.multiply);
    if(fired)value*=clamp(p.shot,0,1);
    value+=clamp(foliage,0,0.60);return clamp(value,0,0.95);
}
bool detected(const Owner &o,double distance,double view,double camouflage,bool los){
    distance=std::max(0.0,distance);if(distance<=o.proximity)return true;
    view=std::max(o.proximity,view);camouflage=clamp(camouflage,0,0.95);
    return los&&distance<=clamp(view-(view-o.proximity)*camouflage,o.proximity,o.maximum);
}
// Continue all cache hits and pure rejections together; stop at each Python
// descriptor or engine leaf, preserving cache/fire/lease mutation order.
void advance(Owner &o,double *b){
    Job &j=o.job;
    while(j.active){
        if(j.stage!=0)break;
        if(!j.single){
            while(j.cursor<static_cast<int>(o.roster.size())){
                Record r=o.roster[j.cursor];
                if(r.alive&&r.team!=j.source.team&&!(r.kind==0&&r.id==j.source.id))break;
                ++j.cursor;
            }
            if(j.cursor==static_cast<int>(o.roster.size())){j.active=false;break;}
            j.phase=j.processed[j.cursor]?1:0;
            auto it=o.templates.find(std::make_pair(j.cursor,j.phase));
            if(it==o.templates.end()){j.stage=1;break;}
            j.target=it->second;
        }
        j.key=pair_key(j.source,j.target);j.fired=false;bool changed=false;
        if(j.target.fire>=0){
            Identity id={{j.target.kind,j.target.id}};auto it=o.fire.find(id);
            if(it==o.fire.end()||j.target.fire<it->second.first)o.fire[id]=std::make_pair(j.target.fire,0.0);
            else if(j.target.fire>it->second.first){o.fire[id]=std::make_pair(j.target.fire,j.now+o.shot_seconds);j.fired=true;changed=true;}
            else j.fired=j.now<it->second.second;
        }
        auto cached=o.cache.find(j.key);
        if(!changed&&cached!=o.cache.end()&&cached->second.fire==j.target.fire&&j.now-cached->second.now<o.ttl){result(o,cached->second.value);continue;}
        double dx=j.source.x-j.target.x,dz=j.source.z-j.target.z;
        j.distance=std::sqrt(dx*dx+dz*dz);
        if(j.distance<=o.proximity){store(o,true);result(o,true);continue;}
        if(j.distance>o.maximum){store(o,false);result(o,false);continue;}
        if(!allowed(o,j.key)){defer(o,j.key);result(o,false);continue;}
        j.stage=2;
    }
    b[OUTPUT]=j.active?j.stage:0;b[OUTPUT+1]=j.cursor;b[OUTPUT+2]=j.phase;b[OUTPUT+3]=j.fired;
    if(!j.active){
        b[OUTPUT+1]=j.results.size();int k=OUTPUT+2;
        for(auto r:j.results)for(int v:r)b[k++]=v;
    }
}
struct Source {Record record;Identity selected;bool eligible;};
void prepare(Owner &o,const std::vector<Source> &sources,double now){
    Frame &f=o.frame;f.prepared=true;f.now=now;
    std::map<Key,bool> valid;std::map<Key,std::array<bool,4> > candidates;
    for(const Source &s:sources){
        if(s.record.team!=1&&s.record.team!=2)continue;
        for(const Source &t:sources){
            if(t.record.team==s.record.team||(t.record.team!=1&&t.record.team!=2))continue;
            Key key=pair_key(s.record,t.record);auto it=o.cache.find(key);
            bool fresh=it==o.cache.end(),fire=!fresh&&it->second.fire!=t.record.fire;
            bool stale=fresh||fire||now-it->second.now>=o.ttl;valid[key]=stale;
            if(stale&&s.eligible)candidates[key]=std::array<bool,4>{{s.record.kind==1,s.selected==Identity{{t.record.kind,t.record.id}},fire,fresh}};
        }
    }
    for(auto it=o.deferred.begin();it!=o.deferred.end();){if(!valid[it->first])it=o.deferred.erase(it);else ++it;}
    std::vector<Key> waiting;std::set<Key> waiting_set;
    for(Key key:o.waiting)if(valid[key]){
        f.prior.push_back(key);
        if(candidates.count(key)){waiting.push_back(key);waiting_set.insert(key);}else f.parked.push_back(key);
    }
    std::vector<Key> cohorts[7];
    for(Key key:waiting){
        auto flags=candidates[key];
        if(flags[0])cohorts[0].push_back(key);
        if(!flags[0]&&!flags[1]&&!flags[2]&&!flags[3])cohorts[5].push_back(key);
    }
    for(auto entry:candidates){
        Key key=entry.first;auto flags=entry.second;
        if(flags[0]&&!waiting_set.count(key))cohorts[1].push_back(key);
        if(flags[1]&&!flags[0])cohorts[2].push_back(key);
        if(flags[2]&&!flags[0]&&!flags[1])cohorts[3].push_back(key);
        if(flags[3]&&!flags[0]&&!flags[1]&&!flags[2])cohorts[4].push_back(key);
        if(!waiting_set.count(key)&&!flags[0]&&!flags[1]&&!flags[2]&&!flags[3])cohorts[6].push_back(key);
    }
    std::set<Key> seen;
    for(const auto &cohort:cohorts)for(Key key:cohort)if(seen.insert(key).second)f.order.push_back(key);
    for(Key key:waiting)if(seen.insert(key).second)f.order.push_back(key);
    for(Key key:f.order){auto flags=candidates[key];f.classification[key]=flags[1]?0:(flags[2]?1:(flags[3]?2:3));}
    f.next=std::min(f.order.size(),static_cast<size_t>(f.budget));
    f.allowed.insert(f.order.begin(),f.order.begin()+f.next);
}
void finish(Owner &o){
    Frame &f=o.frame;
    std::set<Key> unfinished(f.parked.begin(),f.parked.end()),seen;
    for(Key key:f.order)if(!f.completed.count(key))unfinished.insert(key);
    o.waiting.clear();
    for(Key key:f.prior)if(unfinished.count(key)&&seen.insert(key).second)o.waiting.push_back(key);
    for(Key key:f.order)if(unfinished.count(key)&&seen.insert(key).second)o.waiting.push_back(key);
    for(Key key:o.waiting)o.deferred.insert(std::make_pair(key,f.now));
    o.diagnostic_now=f.now;o.max_depth=std::max(o.max_depth,o.waiting.size());
    for(auto item:o.deferred)o.max_age=std::max(o.max_age,std::max(0.0,f.now-item.second));
    f.active=false;
}
Record record(Reader &r,bool position){
    Record t={};t.kind=r.integer();t.id=r.integer();t.team=r.integer();t.alive=true;
    if(t.kind<0||t.kind>1)throw std::invalid_argument("actor kind");
    if(position){t.x=r.next();t.y=r.next();t.z=r.next();t.fire=r.integer();}
    return t;
}
}
void offline_perception_reset(){owners.clear();}
int offline_perception_dispatch(double *b,int n){
    Reader r(b,n);int op=static_cast<int>(b[0]);
    if(op==300){
        Owner o;o.ttl=r.next();o.shot_seconds=r.next();o.proximity=r.next();o.maximum=r.next();o.memory=r.next();o.designated=r.next();o.budget=r.integer();r.end();
        if(o.ttl<0||o.budget<0||o.proximity<0||o.maximum<o.proximity)throw std::invalid_argument("perception parameters");
        int id=next_owner++;owners[id]=o;b[0]=id;return 0;
    }
    Owner &o=owner(r);
    if(op==301){r.end();if(o.job.active)throw std::invalid_argument("unfinished perception operation");o.frame=Frame();o.frame.active=true;o.frame.budget=o.budget;return 0;}
    if(op==302){
        double now=r.next();int count=r.integer();if(count<0||count>30)throw std::invalid_argument("room size");
        std::vector<Source> sources;
        for(int i=0;i<count;++i){Source s;s.record=record(r,false);s.record.fire=r.integer();s.selected[0]=r.integer();s.selected[1]=r.integer();s.eligible=r.integer()!=0;sources.push_back(s);}
        r.end();if(!o.frame.active||o.frame.prepared){b[0]=0;return 0;}
        prepare(o,sources,now);b[0]=1;return 0;
    }
    if(op==303){r.end();b[0]=o.frame.active;if(o.frame.active)finish(o);return 0;}
    if(op==305){
        int count=r.integer();if(count<0||count>30||o.job.active)throw std::invalid_argument("roster size/state");
        std::vector<Record> roster;
        for(int i=0;i<count;++i){Record t=record(r,false);t.alive=r.integer()!=0;roster.push_back(t);}
        r.end();o.roster.swap(roster);o.templates.clear();o.remembered.clear();return 0;
    }
    if(op==304||op==307){
        if(n!=PACKET||o.job.active)throw std::invalid_argument("perception operation state/width");
        o.job=Job();Job &j=o.job;j.active=true;j.single=op==307;j.source=record(r,true);j.now=r.next();
        if(j.single)j.target=record(r,true);
        else for(size_t i=0;i<o.roster.size();++i){
            j.processed.push_back(r.integer()!=0);j.shared.push_back(r.integer()!=0);
            o.roster[i].alive=r.integer()!=0;o.roster[i].team=r.integer();
        }
        advance(o,b);return 0;
    }
    if(op==306){
        if(n!=PACKET||!o.job.active)throw std::invalid_argument("perception resume state/width");
        Job &j=o.job;int stage=r.integer();if(stage!=j.stage)throw std::invalid_argument("perception resume tag");
        if(stage==1){
            Record t=record(r,true),expected=o.roster[j.cursor];
            if(t.kind!=expected.kind||t.id!=expected.id)throw std::invalid_argument("template identity");
            o.templates[std::make_pair(j.cursor,j.phase)]=t;j.stage=0;
        }else if(stage==2){
            Projection p;p.view=r.next();p.base0=r.next();p.base1=r.next();p.shot=r.next();p.moving=r.integer()!=0;p.add=r.next();p.multiply=r.next();j.projection=p;
            if(!detected(o,j.distance,p.view,camouflage(p,j.fired,0,true),true)){store(o,false);result(o,false);}
            else if(!admit(o,j.key))result(o,false);
            else j.stage=3;
        }else if(stage==3){
            bool los=r.integer()!=0;double foliage=r.next();
            bool value=detected(o,j.distance,j.projection.view,camouflage(j.projection,j.fired,foliage,false),los);
            store(o,value);result(o,value);
        }else throw std::invalid_argument("perception query tag");
        advance(o,b);return 0;
    }
    if(op==308||op==309){
        TeamKey key;key[0]=r.integer();key[1]=r.integer();key[2]=r.integer();double now=r.next();
        if(op==308){double duration=r.next();r.end();renew(o,key,now,duration);b[0]=1;}
        else {r.end();b[0]=remaining(o,key,now);}
        return 0;
    }
    if(op==310){
        if(n!=20)throw std::invalid_argument("perception stats width");
        double age=0;for(auto item:o.deferred)age=std::max(age,std::max(0.0,o.diagnostic_now-item.second));
        b[0]=o.waiting.size();b[1]=o.max_depth;b[2]=age;b[3]=o.max_age;
        b[4]=o.admitted;b[5]=o.completed;b[6]=o.deferred_count;
        for(int i=0;i<4;++i)b[7+i]=o.services[i];
        return 0;
    }
    if(op==311){r.end();o.job=Job();return 0;}
    if(op==312){r.end();int id=static_cast<int>(b[1]);owners.erase(id);return 0;}
    throw std::invalid_argument("perception opcode");
}
