#ifndef OFFLINE_EXPERIMENT_KERNEL_CORE_H
#define OFFLINE_EXPERIMENT_KERNEL_CORE_H
#ifdef __cplusplus
extern "C" {
#endif
int offline_kernel_dispatch(double *buffer, int count);
void offline_kernel_reset(void);
#ifdef __cplusplus
}
#endif
#endif
