#pragma once

#include <cmath>
#include <cstdint>
#include <array>

namespace wot {

// #1513 9c8e50 defaults; 9c8f40 replaces these from renderer/DRRAutoscaler.
struct StockDynamicResolutionConfig {
    float time_step = 1.f;
    float critical_fps = 24.f;
    float safe_fps = 30.f;
    float minimum_scale = 0.5f;
    float correction_step = 0.05f;
    float correction_big_step = 0.1f;
};

struct StockDynamicResolutionProjection {
    std::array<float, 4> parameters;
    std::array<float, 16> downsample_parameters;
};

// The renderer owns this state and consumes scale() when allocating its scene
// target. This class supplies no Python API and cannot stand in for that owner.
class StockDynamicResolution {
public:
    const StockDynamicResolutionConfig& config() const { return config_; }
    void configure(const StockDynamicResolutionConfig& value) {
        config_ = value;
        baseline_ = scale_;
    }

    float scale() const { return scale_; }
    float baseline() const { return baseline_; }
    bool enabled() const { return enabled_; }
    bool paused() const { return paused_; }

    // 952280 publishes immediately; the renderer rebuilds at a safe boundary.
    // In particular, manual controls do not replace the autoscaler's baseline.
    void set_scale(float value) {
        scale_ = value < 0.1f ? 0.1f : value < 1.f ? value : 1.f;
    }

    // 9c8ed0/9c90b0 save manual scale on enable and restore it on disable or
    // pause. Resuming starts a fresh elapsed-time/frame-count interval.
    void enable(bool value, std::uint32_t frame) {
        if (enabled_ == value) return;
        if (value) {
            reset_sample(frame);
            baseline_ = scale_;
        } else {
            set_scale(baseline_);
        }
        enabled_ = value;
    }

    void pause(bool value, std::uint32_t frame) {
        if (paused_ == value) return;
        paused_ = value;
        if (!enabled_) return;
        if (value) set_scale(baseline_);
        else reset_sample(frame);
    }

    // 9c9120 uses the renderer frame counter, not a count of tick callbacks.
    // It consumes the complete elapsed interval when a tick arrives late.
    void tick(float elapsed, std::uint32_t frame) {
        if (!enabled_ || paused_) return;
        elapsed_ += elapsed;
        if (elapsed_ < config_.time_step) return;
        const float fps = static_cast<float>(frame - previous_frame_) / elapsed_;
        reset_sample(frame);
        if (fps < config_.critical_fps) {
            const float step = std::fabs(scale_ - baseline_) < 0.0001 ?
                config_.correction_big_step : config_.correction_step;
            if (static_cast<double>(config_.minimum_scale + step) - 0.002 > scale_)
                return;
            set_scale(scale_ - step);
        } else {
            if (static_cast<double>(scale_ - baseline_) > -0.0001 ||
                fps < config_.safe_fps)
                return;
            set_scale(scale_ + config_.correction_step);
        }
    }

    // 951cb0: original scene dimensions round independently, with one pixel
    // minimum. The public RenderTarget owns matching depth and viewport state.
    int scene_dimension(float output_dimension, bool scaling_enabled) const {
        if (!scaling_enabled) return static_cast<int>(output_dimension);
        const int value = static_cast<int>(output_dimension * scale_ + 0.5f);
        return value < 1 ? 1 : value;
    }

    // 952320 writes DRRParams and the complete DRRParamsDownsample matrix.
    // Keep independent rounding at each authored downsample size, including
    // odd output dimensions; multiplying one rounded full-size ratio is wrong.
    StockDynamicResolutionProjection project(int width, int height) const {
        StockDynamicResolutionProjection value;
        const bool scaling = scale_ != 1.f;
        const float active = scaling ? 1.f : 0.f;
        const auto group = [&](int divisor, float* output) {
            const float w = static_cast<float>(width / divisor);
            const float h = static_cast<float>(height / divisor);
            output[0] = static_cast<float>(scene_dimension(w, scaling)) / w;
            output[1] = static_cast<float>(scene_dimension(h, scaling)) / h;
            output[2] = active * divisor / (width * output[0]);
            output[3] = active * divisor / (height * output[1]);
        };
        group(1, value.parameters.data());
        for (int i = 0; i < 4; ++i)
            group(2 << i, value.downsample_parameters.data() + i * 4);
        return value;
    }

private:
    void reset_sample(std::uint32_t frame) {
        elapsed_ = 0.f;
        previous_frame_ = frame;
    }

    StockDynamicResolutionConfig config_;
    float scale_ = 1.f;
    float baseline_ = 1.f;
    float elapsed_ = 0.f;
    std::uint32_t previous_frame_ = 0;
    bool enabled_ = true;
    bool paused_ = false;
};

} // namespace wot
