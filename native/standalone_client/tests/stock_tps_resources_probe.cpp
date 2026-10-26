#include "../bigworld/stock_tps_resources.hpp"
#include <cassert>
#include <cstring>
#include <fstream>
#include <iostream>
#include <vector>

template<class Value> void write( std::ostream & output, const Value & value )
{ output.write( reinterpret_cast<const char *>(&value), sizeof(value) ); }
int main( int argc, char ** argv )
{
    assert( argc == 3 );
    std::ifstream input( argv[1], std::ios::binary );
    std::ofstream output( argv[2], std::ios::binary );
    if (!input || !output) return 2;
    unsigned resources = 0, effects = 0, emitters = 0;
    std::uint32_t size;
    while (input.read( reinterpret_cast<char *>(&size), sizeof(size) ))
    {
        if (size > 32 * 1024 * 1024) return 2;
        std::vector<char> bytes( size );
        if (!input.read( bytes.data(), size )) return 2;
        wot::StockTPSResource owner;
        std::string error;
        const std::uint32_t valid = owner.read( bytes.data(), size, error );
        write( output, valid );
        if (!valid) continue;
        ++resources;
        output.write( reinterpret_cast<const char *>(owner.descriptor().data()), owner.descriptor().size() );
        write( output, std::uint32_t(owner.effects().size()) );
        for (const auto & effect : owner.effects())
        {
            ++effects;
            output.write( reinterpret_cast<const char *>(effect.descriptor.data()), effect.descriptor.size() );
            write( output, std::uint32_t(effect.emitters.size()) );
            for (const auto & emitter : effect.emitters)
            {
                ++emitters;
                output.write( reinterpret_cast<const char *>(emitter.descriptor.data()), emitter.descriptor.size() );
                for (const auto & curve : emitter.curves)
                {
                    write( output, std::uint32_t(curve.components) );
                    write( output, std::uint32_t(curve.times.size()) );
                    output.write( reinterpret_cast<const char *>(curve.times.data()), curve.times.size() * sizeof(float) );
                    output.write( reinterpret_cast<const char *>(curve.values.data()), curve.values.size() * sizeof(float) );
                }
            }
        }
        // Failed reloads must preserve the previous owner's leases and data.
        const auto count = owner.effects().size();
        assert( !owner.read( nullptr, 0, error ) && owner.effects().size() == count );
        assert( !owner.read( nullptr, 1, error ) && owner.effects().size() == count );
        if (!owner.effects().empty())
        {
            bool caught = false;
            try { owner.effects().front().scalar( 139 ); }
            catch (const std::out_of_range &) { caught = true; }
            assert(caught);
        }
    }
    std::cout << "tPS resources=" << resources << " effects=" << effects << " emitters=" << emitters << '\n';
    return output ? 0 : 2;
}
