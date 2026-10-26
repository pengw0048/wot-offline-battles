#include "../bigworld/stock_tps_emitter.hpp"
#include <cassert>
#include <cstring>
#include <iostream>
#include <vector>

struct Event { unsigned type;float elapsed; };
struct Sink
{
    std::vector<Event> events;
    void advanceChildren( float elapsed ) { events.push_back( { 0, elapsed } ); }
    void advanceBase( float elapsed ) { events.push_back( { 1, elapsed } ); }
    void emit() { events.push_back( { 2, 0.f } ); }
};
std::uint32_t bits( float value )
{ std::uint32_t result;std::memcpy( &result, &value, 4 );return result; }
int main( int argc, char ** argv )
{
    if (argc > 1 && std::strcmp( argv[1], "--curve" ) == 0)
    {
        unsigned components, count;
        float time;
        while (std::cin >> components >> count >> time)
        {
            wot::StockTPSCurve curve;
            curve.components = components;
            curve.times.resize( count );
            curve.values.resize( std::size_t(count) * components );
            for (auto & value : curve.times) std::cin >> value;
            for (auto & value : curve.values) std::cin >> value;
            std::array<float, 4> output{};
            std::cout << curve.sample( time, output ) << ' ';
            for (unsigned index = 0; index < components; ++index)
                std::cout << bits( output[index] ) << ' ';
            std::cout << '\n';
        }
        return 0;
    }
    if (argc > 1)
    {
        float rate, remaining, elapsed, density;
        while (std::cin >> rate >> remaining >> elapsed >> density)
        {
            wot::StockTPSEmitterResource resource;
            std::memcpy( resource.descriptor.data() + 0x74, &rate, 4 );
            wot::StockTPSEmissionClock clock( resource );
            clock.remaining = remaining;
            Sink sink;
            clock.advance( elapsed, density, sink );
            std::cout << bits( clock.remaining ) << ' ';
            for (const auto & event : sink.events)
                std::cout << event.type << ':' << bits( event.elapsed ) << ' ';
            std::cout << '\n';
        }
        return 0;
    }
    wot::StockTPSEmitterResource resource;
    const float rate = 100.f;
    std::memcpy( resource.descriptor.data() + 0x74, &rate, 4 );
    assert( wot::StockTPSEmissionClock::burstCount( resource, .01f ) == 1 );
    resource.descriptor[9] = 1;
    assert( wot::StockTPSEmissionClock::burstCount( resource, .01f ) == 1 );
    assert( wot::StockTPSEmissionClock::burstCount( resource, .5f ) == 50 );
    wot::StockTPSEmissionClock clock( resource );
    Sink sink;
    clock.advance( .015f, 1.f, sink );
    assert( sink.events.size() == 4 && sink.events[0].type == 1 &&
        sink.events[1].type == 2 && sink.events[2].type == 0 && sink.events[3].type == 1 );
    std::cout << "Original tPS emission clock and burst budget passed\n";
}
