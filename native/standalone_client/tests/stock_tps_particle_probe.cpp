#include "../bigworld/stock_tps_particle.hpp"
#include <cstring>
#include <iostream>

std::uint32_t bits( float value )
{ std::uint32_t result;std::memcpy( &result, &value, 4 );return result; }
int main()
{
    float elapsed;
    unsigned special, paused, hasSource;
    wot::StockTPSEmitterResource source;
    for (unsigned index : { 2u, 3u, 4u })
    {
        source.curves[index].times = { 0.f, .5f, 1.f };
        source.curves[index].values = index == 2 ? std::vector<float>{ .2f, -1.f, .4f } :
            index == 3 ? std::vector<float>{ .5f, 1.2f, .4f } : std::vector<float>{ 1.f, .5f, 2.f };
    }
    wot::StockTPSParticle particle;
    std::int64_t identity;
    wot::StockTPSVector3 wind;
    while (std::cin >> elapsed >> special >> paused >> hasSource >> particle.flags >>
        particle.ownerIdentity >> identity >> particle.children >>
        particle.velocity.x >> particle.velocity.y >> particle.velocity.z >>
        particle.position.x >> particle.position.y >> particle.position.z >>
        particle.speedMultiplier >> particle.windMultiplier >> particle.delay >> particle.lifetime >> particle.age >>
        particle.gravity >> particle.windResponse >> particle.inheritedVelocityDuration >>
        particle.inheritedVelocity.x >> particle.inheritedVelocity.y >> particle.inheritedVelocity.z >>
        particle.additionalVelocity.x >> particle.additionalVelocity.y >> particle.additionalVelocity.z >>
        wind.x >> wind.y >> wind.z)
    {
        particle.source = hasSource ? &source : nullptr;
        particle.tick( elapsed, wind, identity, special != 0, paused != 0 );
        std::cout << particle.flags << ' ';
        for (float value : { particle.velocity.x, particle.velocity.y, particle.velocity.z,
            particle.position.x, particle.position.y, particle.position.z,
            particle.speedMultiplier, particle.windMultiplier, particle.delay, particle.age,
            particle.inheritedVelocityDuration }) std::cout << bits( value ) << ' ';
        std::cout << '\n';
    }
}
