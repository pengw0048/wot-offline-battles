#include "pch.hpp"
#include "stock_scene_identity.hpp"

#include "chunk/chunk.hpp"
#include "chunk/chunk_model.hpp"
#include "chunk/geometry_mapping.hpp"
#include "physics2/collision_obstacle.hpp"

#include <cmath>
#include <limits>

BW_BEGIN_NAMESPACE

namespace
{
bool readIdentityNumber( DataSectionPtr section, const char * name, uint32 & value )
{
    const BW::string text = section->readString( name );
    if (text.empty()) return false;
    uint64 parsed = 0;
    for (BW::string::const_iterator it = text.begin(); it != text.end(); ++it)
    {
        if (*it < '0' || *it > '9') return false;
        parsed = parsed * 10u + static_cast<unsigned>( *it - '0' );
        if (parsed > std::numeric_limits<uint32>::max()) return false;
    }
    value = static_cast<uint32>( parsed );
    return true;
}

// The public engine's chunk loader owns this instance and its collision BSP.
// Identity is authored by the #1513 WGDE decoder, never inferred from a native
// address, a model filename, or the order in which chunks happen to stream in.
class StockChunkModel : public ChunkModel
{
    DECLARE_CHUNK_ITEM( StockChunkModel )
public:
    StockChunkModel() : hasIdentity_( false ) {}

    bool load( DataSectionPtr section, Chunk * chunk, BW::string * error ) override
    {
        hasIdentity_ = false;
        StockObstacleIdentity parsed;
        DataSectionPtr source = section->openSection( "stockIdentity" );
        if (source)
        {
            parsed.filename = source->readString( "filename" );
            if (!readIdentityNumber( source, "chunkID", parsed.chunkID ) ||
                !readIdentityNumber( source, "itemIndex", parsed.itemIndex ) ||
                parsed.filename.empty())
            {
                if (error) *error = "Invalid original WGDE static-item identity";
                return false;
            }
        }
        if (!ChunkModel::load( section, chunk, error )) return false;
        identity_ = parsed;
        hasIdentity_ = source != NULL;
        return true;
    }

    bool identity( StockObstacleIdentity & output ) const
    {
        if (!hasIdentity_ || !this->chunk()) return false;
        output = identity_;
        return true;
    }

private:
    bool hasIdentity_;
    StockObstacleIdentity identity_;
};

#undef IMPLEMENT_CHUNK_ITEM_ARGS
#define IMPLEMENT_CHUNK_ITEM_ARGS (pSection, pChunk, &errorString)
IMPLEMENT_CHUNK_ITEM( StockChunkModel, wotModel, 0 )
#undef IMPLEMENT_CHUNK_ITEM_ARGS

} // namespace

bool readStockObstacleIdentity( const CollisionObstacle & obstacle,
    StockObstacleIdentity & output )
{
    const SceneObject & object = obstacle.sceneObject();
    if (!object.isType<ChunkItem>()) return false;
    const StockChunkModel * model = dynamic_cast<const StockChunkModel *>(
        object.getAs<ChunkItem>() );
    StockObstacleIdentity identity;
    if (!model || !model->identity( identity )) return false;
    // Exact prediction callback 0x006efa80 uses the world X-basis length.
    // Compute it from the same transform used for this collision, including
    // any mapping transform, instead of caching a scale from another pose.
    identity.scale = obstacle.transform().applyVector( Vector3( 1.f, 0.f, 0.f ) ).length();
    if (!std::isfinite( identity.scale ) || identity.scale <= 0.f) return false;
    output = identity;
    return true;
}

BW_END_NAMESPACE
