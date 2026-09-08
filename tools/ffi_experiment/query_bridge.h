#ifndef OFFLINE_QUERY_BRIDGE_H
#define OFFLINE_QUERY_BRIDGE_H
#ifdef __cplusplus
extern "C" {
#endif
/* The callback is borrowed for this synchronous call only. It replaces the
 * request packet with its answer; neither side may resize its owned buffer. */
typedef int (*OfflineQueryCallback)(void *owner, double *packet, int count);
int offline_query_dispatch(double *buffer, int count,
                           double *query_buffer, int query_count,
                           OfflineQueryCallback callback, void *owner);
int offline_query_buffers_available(const double *buffer,int count,
                                    const double *query_buffer,int query_count);
int offline_query_active(void);
int offline_query_can_enter(const double *buffer, int count);
int offline_query(double *packet, int count);
#ifdef __cplusplus
}
#endif
#endif
