#pragma once

#include <cstddef>
#include <vector>

namespace wot {

struct StockGunAngleSample {
    float yaw;
    float pitch;
    double time;
};

// #1513 006e9700 / 006e94c0. This is presentation history only: the gun
// rotator owns the angles, and the filter samples their timestamped outputs.
class StockGunAngleHistory {
public:
    void sync(float yaw, float pitch, double time);
    bool sample(double render_time, double previous_output_time,
                float& yaw, float& pitch) const;
    std::size_t size() const { return samples_.size(); }

private:
    std::vector<StockGunAngleSample> samples_;
};

} // namespace wot
