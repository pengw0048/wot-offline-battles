// Persistent navigation and its complete baked geometry. The numeric bridge
// also exposes geometry entry points for existing runtime safety consumers.
#include "navigation_flow.h"
#include "navigation_grid.h"
#include "navigation_snapshot.h"

namespace {
using namespace offline_nav;
struct Reader {
    double *b;int n,i;
    double number(){if(i>=n||!std::isfinite(b[i]))throw std::invalid_argument("navigation packet");return b[i++];}
    int integer(int low=-1000000000,int high=1000000000){
        double v=number();if(v<low||v>high||v!=std::floor(v))throw std::invalid_argument("navigation integer");return static_cast<int>(v);
    }
    Point point(){double x=number(),y=number(),z=number();return Point(x,y,z);}
    Cell cell(){int x=integer(),z=integer();return Cell(x,z);}
    Edge edge(){Cell a=cell(),b=cell();return offline_nav::edge(a,b);}
    Path path(){Path out;int size=integer(0,1000000);for(int j=0;j<size;++j)out.push_back(point());return out;}
    Penalties penalties(){Penalties out;int size=integer(0,1000000);for(int j=0;j<size;++j){Edge e=edge();out[e]=number();}return out;}
    std::string string(){std::string out;int size=integer(0,65536);for(int j=0;j<size;++j)out+=static_cast<char>(integer(0,255));return out;}
    void end(){if(i!=n)throw std::invalid_argument("navigation packet width");}
};
struct Writer {
    double *b;int n,i;
    void number(double v){if(i>=n)throw std::invalid_argument("navigation result capacity");b[i++]=v;}
    void point(Point p){number(p.x);number(p.y);number(p.z);}
    void cell(Cell c){number(c.first);number(c.second);}
    void path(const Path &path){number(path.size());for(Point p:path)point(p);}
};
std::map<int,std::shared_ptr<Grid> > grids;
int next_grid=1;
Grid &grid(int id){auto it=grids.find(id);if(it==grids.end())throw std::invalid_argument("navigation owner");return *it->second;}
struct NavigationOwner {
    Navigator nav;
    std::map<int,Identity> identities;
    int next_identity=1;
    std::string snapshot;
    explicit NavigationOwner(std::shared_ptr<Grid> value):nav(value){}
};
std::map<int,std::unique_ptr<NavigationOwner> > navigators;
int next_navigator=1;
NavigationOwner &navigator(int id){auto it=navigators.find(id);if(it==navigators.end())throw std::invalid_argument("navigator owner");return *it->second;}
void projection(const Navigator &nav,int id,Writer &w){
    auto found=nav.states.find(id);w.number(found!=nav.states.end());if(found==nav.states.end())return;
    const State &s=found->second;w.number(s.status);w.number(s.planned_goal.has);w.point(s.planned_goal.value);
    w.number(s.shallow.has);w.point(s.shallow.value);w.number(s.terminal);
}
void navigate(NavigationOwner &owner,int kind,Reader &r,Writer &w){
    Navigator &nav=owner.nav;
    if(kind==0){double elapsed=r.number();r.end();nav.begin(elapsed);}
    else if(kind==1){r.end();nav.frame_open=false;}
    else if(kind==2||kind==11){double now=r.number();r.end();if(kind==2)nav.tick(now);else nav.advance(now);}
    else if(kind==3||kind==4||kind==13){
        int id=r.integer(0),identity=r.integer(1);Point current=r.point(),goal=r.point();double now=r.number();bool movement=r.integer(0,1)!=0;
        auto found=owner.identities.find(identity);if(found==owner.identities.end())throw std::invalid_argument("navigation identity");
        if(kind==13){
            Optional<Point> anchor;if(r.integer(0,1))anchor=r.point();
            double horizon=r.number();bool stop=r.integer(0,1)!=0;r.end();
            bool direct=distance(current,goal)<=15.0&&!nav.penalized(id,current,goal,now)&&nav.grid->dry(current,goal,now);
            Point target;
            if(direct){auto escape=nav.observe_direct(id,current,goal,found->second,now,movement);target=escape.has?escape.value:goal;}
            else target=nav.next_target(id,current,goal,found->second,now,anchor,Path(),horizon,movement);
            auto state=nav.states.find(id);
            bool terminal=state!=nav.states.end()&&state->second.terminal;
            w.number(stop&&((direct&&target==goal)||(!direct&&terminal)));w.point(target);projection(nav,id,w);
        }else if(kind==3){
            Optional<Point> anchor;if(r.integer(0,1))anchor=r.point();
            Optional<double> horizon;if(r.integer(0,1))horizon=r.number();
            Path avoid=r.path();r.end();Point target=nav.next_target(id,current,goal,found->second,now,anchor,avoid,horizon,movement);
            w.number(1);w.point(target);projection(nav,id,w);
        }else{r.end();auto target=nav.observe_direct(id,current,goal,found->second,now,movement);w.number(target.has);w.point(target.value);projection(nav,id,w);}
    }
    else if(kind==5){int id=r.integer(0);Point current=r.point();Optional<Point> target;if(r.integer(0,1))target=r.point();double now=r.number();r.end();w.number(nav.report_blocked(id,current,target,now));projection(nav,id,w);}
    else if(kind==6){int id=r.integer(0);Point a=r.point(),b=r.point();double now=r.number();r.end();w.number(nav.penalized(id,a,b,now));}
    else if(kind==7||kind==8){int id=r.integer(0);Point p=r.point();double yaw=r.number(),maximum=r.number();r.end();w.number(nav.shallow_step(id,p,yaw,maximum,kind==8));}
    else if(kind==9){int id=r.integer(0);r.end();auto found=nav.states.find(id);w.number(found!=nav.states.end()&&found->second.terminal);}
    else if(kind==10){int maximum=r.integer(0,1000000);r.end();nav.maximum=maximum;}
    else if(kind==12){
        std::set<int> active;int count=r.integer(0,10000);for(int i=0;i<count;++i)active.insert(r.integer(0));r.end();
        for(auto it=nav.modes.begin();it!=nav.modes.end();)if(!active.count(it->first))it=nav.modes.erase(it);else ++it;
    }
    else throw std::invalid_argument("navigator operation");
}

void geometry(Grid &g,int kind,Reader &r,Writer &w){
    if(kind==0){Point p=r.point();r.end();double y=0;bool has=g.ground(p,y);w.number(has);w.number(y);}
    else if(kind==1||kind==2||kind==3||kind==4||kind==10){
        Point a=r.point(),b=r.point();double extra=kind==1?0:(kind==3||kind==4?r.integer(0,255):r.number());r.end();
        if(kind==1)w.number(g.segment_clear(a,b));
        if(kind==2)w.number(g.dry(a,b,extra));
        if(kind==3)w.number(g.hazard(a,b,static_cast<int>(extra)));
        if(kind==4)w.number(g.motion_hazard(a,b,static_cast<int>(extra)));
        if(kind==10)w.number(g.segment_penalty(a,b,extra));
    }
    else if(kind==5||kind==6||kind==7){Point p=r.point();int value=r.integer(0,10000);r.end();
        if(kind==5)w.number(g.point_hazard(p,value));
        if(kind==6)w.number(g.hazard_near(p,value));
        if(kind==7){Cell c;w.number(g.nearest(g.cell(p),value,c));}
    }
    else if(kind==8){Point p=r.point();double yaw=r.number(),length=r.number(),width=r.number();r.end();w.number(g.pose(p,yaw,length,width));}
    else if(kind==9){Point p=r.point();r.end();std::pair<double,double> result;w.number(g.local_corridor(p,result));w.number(result.first);w.number(result.second);}
    else if(kind==11||kind==12){
        Point a=r.point(),b=r.point();int value=r.integer(0,255);r.end();std::vector<Cell> cells;
        if(kind==11){cells=g.segment_cells(a,b,value!=0);w.number(cells.size());}
        else {bool valid=g.hazard_cells(a,b,value,cells);w.number(valid?static_cast<int>(cells.size()):-1);}
        for(Cell c:cells)w.cell(c);
    }
    else if(kind==13){
        Path path=r.path();double now=r.number();bool prefer=r.integer(0,1)!=0;Penalties hard=r.penalties();r.end();w.path(g.smooth(path,now,prefer,hard));
    }
    else if(kind==14){
        Point a=r.point(),b=r.point();double now=r.number(),side=r.number(),minimum=r.number();Path avoid=r.path();Penalties hard=r.penalties();r.end();
        Point result;w.number(g.safe_local(a,b,now,avoid,side,hard,minimum,result));w.point(result);
    }
    else if(kind==15){Path p=r.path();Penalties hard=r.penalties();r.end();w.number(g.path_penalty(p,hard));}
    else if(kind==16){Path p=r.path();r.end();double value=0;w.number(g.exposure(p,value));w.number(value);}
    else if(kind==17||kind==18||kind==19){
        Point current;if(kind==18)current=r.point();Path p=r.path();int a=r.integer(0,static_cast<int>(p.size())),b=r.integer(-1,static_cast<int>(p.size())-1);
        double grade=0,turn=0,exposure=0;
        if(kind==17){grade=r.number();turn=r.number();}
        if(kind==19)exposure=r.number();
        r.end();
        if(kind==17)w.number(climb(p,a,b,grade,turn));
        if(kind==18)w.number(live_climb(current,p,a,b));
        if(kind==19)w.number(g.clearance(p,a,b,exposure));
    }
    else if(kind==20){Path p=r.path();double now=r.number();r.end();w.number(g.path_timed(p,now));}
    else if(kind==21){Path p=r.path();r.end();w.number(g.path_penalty(p,g.hulls));}
    else if(kind==22){Point a=r.point(),b=r.point();r.end();auto edges=g.edges(a,b);w.number(edges.size());for(Edge e:edges){w.cell(e.first);w.cell(e.second);}}
    else if(kind==23){Point a=r.point(),b=r.point();r.end();auto result=g.corridor(a,b);w.number(result.first);w.number(result.second);}
    else throw std::invalid_argument("navigation geometry operation");
}
}
offline_nav::Navigator &offline_runtime_navigation(int owner){return navigator(owner).nav;}
extern "C" void offline_navigation_reset(void){navigators.clear();grids.clear();}
extern "C" int offline_navigation_local_query(int owner,int bot,int kind,
        double x,double y,double z,double yaw,double length,double width,
        int wet_escape,int bake_admitted,int has_distance,double maximum_distance){
    Navigator &nav=navigator(owner).nav;Grid &g=*nav.grid;Point start(x,y,z);
    if(kind==2)return g.pose(start,yaw,length,width)?1:0;
    if(wet_escape||g.point_hazard(start,4)||!bake_admitted||g.index(g.cell(start))<0)return 2;
    bool shallow=nav.shallow_step(bot,start,yaw);
    double distance=std::max(1.0,g.data->cell);
    if(has_distance)distance=std::min(distance,std::max(0.0,maximum_distance));
    Point end(x+std::sin(yaw)*distance,y,z+std::cos(yaw)*distance);
    if(g.motion_hazard(start,end,shallow?9:13))return 0;
    return g.segment_clear(start,end)?1:2;
}
extern "C" int offline_navigation_dispatch(double *b,int n){
    Reader r{b,n,1};int op=static_cast<int>(b[0]);
    if(op==500){
        auto owner=std::make_shared<Grid>(offline_navigation_graph(r.integer(1)));
        owner->bounded=r.integer(0,1)!=0;
        for(int j=0;j<4;++j)owner->bounds[j]=r.number();
        r.end();int id=next_grid++;grids[id]=owner;b[0]=id;return 0;
    }
    if(op==510){int handle=r.integer(1);r.end();grid(handle);int id=next_navigator++;navigators[id].reset(new NavigationOwner(grids.at(handle)));b[0]=id;return 0;}
    if(op>=511){
        int handle=r.integer(1);NavigationOwner &owner=navigator(handle);
        if(op==511){
            Identity key;key.repr=r.string();key.kind=r.string();key.owner=r.integer(-1);key.prefer=r.integer(0,1)!=0;r.end();
            int id=owner.next_identity++;owner.identities[id]=key;b[0]=id;return 0;
        }
        if(op==512){int kind=r.integer(0,13),size=r.integer(0,n-4);r.n=4+size;Writer w{b,n,0};navigate(owner,kind,r,w);return 0;}
        if(op==513){bool full=r.integer(0,1)!=0;r.end();owner.snapshot=offline_nav::snapshot(owner.nav,full);b[0]=owner.snapshot.size();return 0;}
        if(op==514){
            if(n<static_cast<int>(owner.snapshot.size()+1))throw std::invalid_argument("navigation snapshot capacity");
            b[0]=owner.snapshot.size();for(size_t i=0;i<owner.snapshot.size();++i)b[i+1]=static_cast<unsigned char>(owner.snapshot[i]);owner.snapshot.clear();return 0;
        }
        if(op==515){r.end();navigators.erase(handle);return 0;}
        if(op==516){
            Navigator &nav=owner.nav;int needed=6+6*static_cast<int>(nav.searches.size());
            if(n<needed){b[0]=needed;b[1]=-1;return 0;}
            Writer w{b,n,0};w.number(nav.searches.size());w.number(nav.credit);w.number(nav.budget);w.number(nav.completed);w.number(nav.failed_count);
            w.number(nav.next_key.empty()?0:nav.key_ids.at(nav.next_key));
            for(const auto &item:nav.searches){
                w.number(nav.key_ids.at(item.first));w.number(item.second.last_frame.has);w.number(item.second.last_frame.value);
                w.number(item.second.revision);w.number(item.second.search->done());w.number(item.second.search->expansions());
            }
            return 0;
        }
        throw std::invalid_argument("navigator bridge operation");
    }
    int handle=r.integer(1);Grid &g=grid(handle);
    if(op==501){
        int kind=r.integer(0,23),size=r.integer(0,n-4);r.n=4+size;
        Writer w{b,n,0};geometry(g,kind,r,w);return 0;
    }
    if(op==502){
        int revision=r.integer(0);TimedPenalties failed;int size=r.integer(0,1000000);
        for(int j=0;j<size;++j){Edge e=r.edge();double expiry=r.number(),cost=r.number();failed[e]=std::make_pair(expiry,cost);}
        Penalties hulls=r.penalties();r.end();
        g.failed.swap(failed);g.hulls.swap(hulls);g.hull_revision=revision;
        return 0;
    }
    if(op==503){r.end();grids.erase(handle);return 0;}
    throw std::invalid_argument("navigation operation");
}
