#ifndef OFFLINE_EXPERIMENT_NAVIGATION_FLOW_H
#define OFFLINE_EXPERIMENT_NAVIGATION_FLOW_H
#ifdef __cplusplus
extern "C" {
#endif
int offline_navigation_dispatch(double *buffer,int count);
void offline_navigation_reset(void);
/* 0 blocked, 1 clear, 2 requires the real native direction probe. */
int offline_navigation_local_query(int owner,int bot,int kind,
    double x,double y,double z,double yaw,double length,double width,
    int wet_escape,int bake_admitted,int has_distance,double maximum_distance);
#ifdef __cplusplus
}
namespace offline_nav {struct Navigator;}
offline_nav::Navigator &offline_runtime_navigation(int owner);
#endif
#endif
