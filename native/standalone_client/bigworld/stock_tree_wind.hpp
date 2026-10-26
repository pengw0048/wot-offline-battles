#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace wot
{
struct StockTreeWindParameters
{
    std::int32_t matrixCount = 0;
    std::int32_t leafAngleCount = 0;
    float response = 0.f;
    float responseLimit = 0.f;
    float maximumBend = 0.f;
    float branchExponent = 0.f;
    float leafExponent = 0.f;
    float gustMinimum = 0.f;
    float gustMaximum = 0.f;
    float gustDurationMinimum = 0.f;
    float gustDurationMaximum = 0.f;
    float gustFrequency = 0.f;
    // Low amplitude, high amplitude, low speed, high speed, in the original
    // X/Y/leaf-rock/leaf-rustle oscillator order.
    std::array<std::array<float, 4>, 4> oscillation = {};
};

bool decodeStockTreeWindParameters( const char * data, std::size_t size,
    StockTreeWindParameters & parameters, std::string & error );

class StockTreeWind
{
public:
    using Matrix = std::array<float, 16>;
    // The scene uses the actual Win32 CRT rand owner. Tests supply an explicit
    // input sequence; values obey its real inclusive 0..32767 domain.
    using RandomSource = std::uint32_t (*)( void * context );
    bool initialise( const StockTreeWindParameters & parameters, std::string & error );
    void reset();
    bool setStrengthAndDirection( float strength, const std::array<float, 3> & direction,
        std::string & error );
    bool advance( float time, RandomSource random, void * context, std::string & error );
    const std::array<Matrix, 6> & matrices() const { return matrices_; }
    const std::vector<std::array<float, 4>> & leafAngles() const { return leafAngles_; }

private:
    struct Response
    {
        float target = 0.f, value = 0.f, previousDelta = 0.f, previousTime = 0.f;
        void update( float time, float response, float limit );
    };
    bool step( float time, RandomSource random, void * context, std::string & error );
    StockTreeWindParameters parameters_;
    bool initialised_ = false;
    float lastTime_ = 0.f;
    float strength_ = 0.f;
    float gustStrength_ = 0.f;
    float gustEnd_ = 0.f;
    std::array<Response, 4> responses_ = {};
    std::array<float, 4> phase_ = {};
    std::array<Matrix, 6> matrices_ = {};
    std::vector<std::array<float, 4>> leafAngles_;
};
}
