#ifndef OFFLINE_DRIVER_CORE_H
#define OFFLINE_DRIVER_CORE_H
int offline_driver_dispatch(double *buffer, int count);
void offline_driver_reset();
void offline_driver_remember(int owner,int bot,double yaw,bool has_ttl,double ttl);
bool offline_driver_waiting(int owner,int bot);
#endif
