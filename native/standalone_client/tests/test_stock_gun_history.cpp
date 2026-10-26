#include "wot/stock_gun_history.hpp"

#include <cmath>
#include <cstdlib>
#include <iostream>

namespace {
void require(bool condition) {
    if (!condition) std::abort();
}
void close(float actual, float expected) {
    require(std::abs(actual - expected) < 0.000001f);
}
}

int main() {
    wot::StockGunAngleHistory history;
    float yaw = 123.0f;
    float pitch = 456.0f;
    require(!history.sample(1.0, 0.9, yaw, pitch));
    require(yaw == 123.0f && pitch == 456.0f);

    history.sync(0.0f, 0.5f, 1.0);
    history.sync(1.0f, -0.5f, 1.1);
    require(history.sample(1.05, 1.0, yaw, pitch));
    close(yaw, 0.5f);
    close(pitch, 0.0f);
    require(history.sample(0.9, 0.8, yaw, pitch));
    close(yaw, 0.0f);
    close(pitch, 0.5f);
    require(history.sample(1.2, 1.1, yaw, pitch));
    close(yaw, 1.0f);
    close(pitch, -0.5f);

    // Golden outputs checked by executing #1513 006e94c0. A long update gap
    // uses the previous output time only within its original .09-.12 window.
    wot::StockGunAngleHistory delayed;
    delayed.sync(0.0f, 0.5f, 1.0);
    delayed.sync(1.0f, -0.5f, 2.0);
    require(delayed.sample(1.94, 1.89, yaw, pitch));
    close(yaw, 0.454545468f);
    close(pitch, 0.0454545319f);
    require(delayed.sample(1.94, 1.94, yaw, pitch));
    close(yaw, 0.4f);
    close(pitch, 0.1f);

    wot::StockGunAngleHistory wrapped;
    wrapped.sync(3.0f, 0.5f, 1.0);
    wrapped.sync(-3.0f, -0.5f, 1.1);
    require(wrapped.sample(1.05, 1.0, yaw, pitch));
    close(yaw, 3.141592741f);

    // sync coalesces samples less than .01 seconds apart, including a clock
    // rewind. Its bounded history shrinks to the last eight after sample 25.
    wot::StockGunAngleHistory bounded;
    for (int index = 0; index < 25; ++index)
        bounded.sync(static_cast<float>(index) * 0.1f, 0.0f, index * 0.1);
    require(bounded.size() == 8);
    require(bounded.sample(-1.0, -2.0, yaw, pitch));
    close(yaw, 1.7f);
    bounded.sync(-0.5f, -0.25f, 2.405);
    require(bounded.size() == 8);
    bounded.sync(0.2f, 0.25f, 0.0);
    require(bounded.size() == 1);
    require(bounded.sample(0.0, -1.0, yaw, pitch));
    close(yaw, 0.2f);

    std::cout << "Stock gun presentation history passed\n";
}
