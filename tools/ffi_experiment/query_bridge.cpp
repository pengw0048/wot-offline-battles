#include "query_bridge.h"
#include "astar_core.h"
#include <cstdint>
#include <cstddef>
using std::size_t;

namespace {
// The bridge keeps the GIL and runs only on its caller's thread. Only reviewed
// synchronous query operations may nest; no callback can reset an active owner.
OfflineQueryCallback active_callback = 0;
void *active_owner = 0;
struct Borrowed {
    uintptr_t command,query;size_t command_bytes,query_bytes;Borrowed *previous;
};
Borrowed *borrowed=0;
bool overlaps(uintptr_t a,size_t an,uintptr_t b,size_t bn){
    return a<=b?b-a<an:a-b<bn;
}
bool available(const double *buffer,int count){
    if(!buffer||count<1||count>12000012)return false;
    uintptr_t at=reinterpret_cast<uintptr_t>(buffer);size_t size=static_cast<size_t>(count)*sizeof(double);
    if(at>UINTPTR_MAX-size)return false;
    for(Borrowed *p=borrowed;p;p=p->previous)
        if(overlaps(at,size,p->command,p->command_bytes)||overlaps(at,size,p->query,p->query_bytes))return false;
    return true;
}
}
extern "C" int offline_query_buffers_available(const double *buffer,int count,const double *query,int query_count){
    return available(buffer,count)&&available(query,query_count)&&
        !overlaps(reinterpret_cast<uintptr_t>(buffer),static_cast<size_t>(count)*sizeof(double),
                  reinterpret_cast<uintptr_t>(query),static_cast<size_t>(query_count)*sizeof(double));
}
extern "C" int offline_query_active(void) { return active_callback != 0; }
extern "C" int offline_query_can_enter(const double *buffer,int count) {
    if(!active_callback)return 1;
    if(!available(buffer,count))return 0;
    if(buffer[0]==405)return 1; // Stack-owned horizontal sweep; no persistent job.
    if(buffer[0]==501)return 1; // Geometry query on immutable map data.
    if(buffer[0]==512&&count>=4)return buffer[2]==6||buffer[2]==7||buffer[2]==8||buffer[2]==9;
    return 0;
}
extern "C" int offline_query(double *packet, int count) {
    return active_callback ? active_callback(active_owner, packet, count) : 18;
}
OfflineQueryRoute::OfflineQueryRoute(OfflineQueryCallback callback,void *owner):previous(active_callback),previous_owner(active_owner){active_callback=callback;active_owner=owner;}
OfflineQueryRoute::~OfflineQueryRoute(){active_callback=previous;active_owner=previous_owner;}
int OfflineQueryRoute::forward(double *packet,int count)const{return previous?previous(previous_owner,packet,count):18;}
extern "C" int offline_query_dispatch(double *buffer, int count,
                                     double *query_buffer,int query_count,
                                     OfflineQueryCallback callback, void *owner) {
    if (!callback || !offline_query_can_enter(buffer,count)||
            !offline_query_buffers_available(buffer,count,query_buffer,query_count)) return 18;
    Borrowed local{reinterpret_cast<uintptr_t>(buffer),reinterpret_cast<uintptr_t>(query_buffer),
        static_cast<size_t>(count)*sizeof(double),static_cast<size_t>(query_count)*sizeof(double),borrowed};
    borrowed=&local;
    OfflineQueryCallback previous_callback=active_callback;
    void *previous_owner=active_owner;
    active_callback = callback;
    active_owner = owner;
    int status = offline_astar_dispatch(buffer, count);
    active_callback = previous_callback;
    active_owner = previous_owner;
    borrowed=local.previous;
    return status;
}
