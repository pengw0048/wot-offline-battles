#ifndef OFFLINE_EXPERIMENT_MOTION_CORE_H
#define OFFLINE_EXPERIMENT_MOTION_CORE_H
#ifdef __cplusplus
extern "C" {
#endif
int offline_motion_dispatch(double *buffer,int count);
void offline_motion_reset(void);
#ifdef __cplusplus
}
#endif
#endif
