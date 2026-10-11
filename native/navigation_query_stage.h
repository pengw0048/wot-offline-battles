#ifndef WOT_NAVIGATION_QUERY_STAGE_H
#define WOT_NAVIGATION_QUERY_STAGE_H
// Same-thread complete navigation oracle. Callbacks are engine capabilities,
// never Python navigation wrappers; nothing is retained or sent to workers.
struct NavigationQuerySnapshot {
    double start[3], end[3];
    double cell_size, grade_up, grade_down, water_limit;
};
struct NavigationQueryEngine {
    void *context;
    // ground=true requires hit height/normal. Horizontal only needs hit/miss.
    bool (*ray)(void *, const double *, const double *, bool, double &, double &);
    double (*water)(void *, double, double, double);
};
// 1 proved clear, 0 proved hard, -1 unknown support. Exceptions unwind locally.
int navigation_query_run(const NavigationQuerySnapshot &, const NavigationQueryEngine &);
#endif
