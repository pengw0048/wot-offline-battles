#ifdef NDEBUG
#undef NDEBUG
#endif
#include "wot/stock_fog.hpp"

#include <cassert>
#include <cmath>
#include <cstring>

int main() {
    wot::StockFogSettings settings;
    assert(settings.deferred[0] == 200 && settings.forward[0] == 0);
    assert(settings.deferred[4] == 500 && settings.forward[4] == 300);
    auto fog = wot::stock_fog_parameters(settings.deferred);
    assert(fog[20] == 0.25f && fog[23] == 1);
    assert(fog[31] == 1 && fog[35] == 4);
    assert(fog[12] < 0 && fog[14] > 0);
    settings.deferred[37] = 0;
    settings.deferred[23] = 0.2f;
    settings.deferred[27] = 0.8f;
    settings.deferred[32] = 0.75f;
    const auto selected = wot::stock_fog_selected(settings, false);
    assert(selected[1] == selected[0] && selected[5] == selected[4]);
    assert(selected[27] == 0.2f && selected[23] == 0.2f);
    assert(selected[32] == settings.deferred[28]);
    settings.forward[37] = 0;
    const auto forward = wot::stock_fog_selected(settings, true);
    assert(forward[23] == 1 && forward[27] == 1);
    settings.deferred[38] = 0;
    assert(std::isinf(wot::stock_fog_parameters(settings.deferred)[35]));
    // No semantic/gamma correction or safety clamp is introduced by the port.
    settings.deferred[20] = -2;
    settings.deferred[23] = 3;
    assert(wot::stock_fog_parameters(settings.deferred)[20] == -6);
}
