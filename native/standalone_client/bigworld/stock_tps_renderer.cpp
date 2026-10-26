#include "pch.hpp"
#include "stock_tps_renderer.hpp"
#include "stock_effect_adapter.hpp"
#include "moo/device_callback.hpp"
#include "moo/effect_material.hpp"
#include "moo/moo_utilities.hpp"
#include "moo/render_context.hpp"
#include "moo/texture_manager.hpp"
#include "moo/vertex_declaration.hpp"
#include <cstring>

BW_BEGIN_NAMESPACE
namespace
{
class TPSDrawResource : public wot::StockTPSDrawResource, public Moo::DeviceCallback
{
public:
    explicit TPSDrawResource( const BW::string & texture ) : textureID_( texture ) {}
    ~TPSDrawResource() { deleteUnmanagedObjects();deleteManagedObjects(); }
    bool load( std::string & error ) override
    {
        if (!Moo::rc().device())
        {
            error = "Original tPS renderer requires a live D3D device";
            return false;
        }
        if (!material_) createManagedObjects();
        if (!material_ || !texture_ || !declaration_)
        {
            error = "Cannot load original tPS shader, texture or lit_particles declaration";
            return false;
        }
        return true;
    }
    void createManagedObjects() override
    {
        // fa9f50 always binds this original BigWorld material, including
        // descriptors whose historical name starts with Data/Shaders/.
        if (!Moo::createEffect( material_, "shaders/wg_particles/ps.fx" )) material_ = NULL;
        texture_ = Moo::TextureManager::instance()->get( textureID_, true, true, true, "Art/Particles/" );
        declaration_ = Moo::VertexDeclaration::get( "lit_particles" );
    }
    void deleteManagedObjects() override
    {
        clearTextureBindings();
        material_ = NULL;
        texture_ = NULL;
        declaration_ = NULL;
    }
    void deleteUnmanagedObjects() override
    {
        clearTextureBindings();
        common_ = NULL;
        offsets_ = NULL;
        indices_ = NULL;
        vertexCapacity_ = indexCapacity_ = 0;
    }
    bool recreateForD3DExDevice() const override { return true; }

    bool draw( const std::vector<wot::StockTPSCommonVertex> & common,
        const std::vector<wot::StockTPSOffsetVertex> & offsets,
        const std::vector<std::uint16_t> & indices,
        const wot::StockTPSDrawParameters & parameters, std::string & error ) override
    {
        if (common.empty() || common.size() != offsets.size() || common.size() > 65536 ||
            indices.empty() || indices.size() % 3 || indices.size() > 0x7fffffffu / sizeof(std::uint16_t))
        {
            error = "Invalid tPS draw stream sizes";
            return false;
        }
        for (auto index : indices)
            if (index >= common.size())
            {
                error = "tPS draw index exceeds vertex stream";
                return false;
            }
        if (!load( error ) || !reserve( common.size(), indices.size(), error )) return false;
        if (!upload( common_, common.data(), common.size() * sizeof(common[0]) ) ||
            !upload( offsets_, offsets.data(), offsets.size() * sizeof(offsets[0]) ) ||
            !upload( indices_, indices.data(), indices.size() * sizeof(indices[0]) ))
        {
            error = "Cannot upload original tPS draw streams";
            return false;
        }
        // f99e10/faa9c0 bind all five selectors and both scalar semantics.
        if (!material_->setTechnique( parameters.debugMode ? "Debug" : "Render" ) ||
            !material_->properties().select( *material_->pEffect() ))
        {
            error = "Cannot select original tPS render technique";
            return false;
        }
        ID3DXEffect * effect = material_->pEffect()->pEffect();
        D3DXHANDLE aspect = effect->GetParameterBySemantic( NULL, "USER_ASPECT_RATIO" );
        D3DXHANDLE colour = effect->GetParameterBySemantic( NULL, "COLOR_MULTIPLIER" );
        if (!aspect || !colour ||
            FAILED( effect->SetInt( "particleQuality", parameters.quality ) ) ||
            FAILED( effect->SetInt( "particleTransformMode", parameters.transformMode ) ) ||
            FAILED( effect->SetInt( "particleDebugMode", parameters.debugMode ) ) ||
            FAILED( effect->SetInt( "g_offscreenMode", parameters.offscreenMode ) ) ||
            FAILED( effect->SetInt( "particleTransparencyMode", parameters.transparencyMode ) ) ||
            FAILED( effect->SetFloat( aspect, parameters.aspectRatio ) ) ||
            FAILED( effect->SetFloat( colour, parameters.colourMultiplier ) ) ||
            FAILED( effect->SetTexture( "diffuseMap", texture_->pTexture() ) ))
        {
            error = "Cannot bind original tPS render parameters";
            return false;
        }
        IDirect3DDevice9 * device = Moo::rc().device();
        DrawState state( device );
        if (!state.capture())
        {
            error = "Cannot preserve native state before tPS draw";
            return false;
        }
        if (FAILED( device->SetStreamSourceFreq( 0, 1 ) ) ||
            FAILED( device->SetStreamSourceFreq( 1, 1 ) ) ||
            FAILED( device->SetStreamSource( 0, common_.pComObject(), 0, sizeof(common[0]) ) ) ||
            FAILED( device->SetStreamSource( 1, offsets_.pComObject(), 0, sizeof(offsets[0]) ) ) ||
            FAILED( Moo::rc().setIndices( indices_.pComObject() ) ) ||
            FAILED( HRESULT(Moo::rc().setVertexDeclaration( declaration_->declaration() )) ) ||
            !material_->begin())
        {
            error = "Cannot begin original tPS draw";
            return false;
        }
        bool drawn = material_->numPasses() != 0;
        for (uint32 pass = 0; drawn && pass < material_->numPasses(); ++pass)
        {
            if (!material_->beginPass( pass )) { drawn = false;break; }
            drawn = SUCCEEDED( HRESULT(Moo::rc().drawIndexedPrimitive( D3DPT_TRIANGLELIST,
                0, 0, UINT(common.size()), 0, UINT(indices.size() / 3) )) );
            drawn = material_->endPass() && drawn;
            if (!drawn) break;
        }
        drawn = material_->end() && drawn;
        if (!drawn) error = "Original tPS indexed draw failed";
        else error.clear();
        return drawn;
    }
private:
    struct DrawState
    {
        explicit DrawState( IDirect3DDevice9 * value ) : device( value ) {}
        IDirect3DDevice9 * device;
        ComObjectWrap<DX::VertexBuffer> streams[2];
        ComObjectWrap<DX::IndexBuffer> indices;
        ComObjectWrap<DX::VertexDeclaration> declaration;
        UINT offsets[2]{}, strides[2]{}, frequencies[2]{};
        DWORD fvf = 0;
        bool captured = false;
        bool capture()
        {
            for (UINT index = 0; index < 2; ++index)
                if (FAILED( device->GetStreamSource( index, &streams[index], &offsets[index], &strides[index] ) ) ||
                    FAILED( device->GetStreamSourceFreq( index, &frequencies[index] ) )) return false;
            captured = SUCCEEDED( device->GetIndices( &indices ) ) &&
                SUCCEEDED( device->GetVertexDeclaration( &declaration ) ) && SUCCEEDED( device->GetFVF( &fvf ) );
            return captured;
        }
        ~DrawState()
        {
            if (!captured) return;
            for (UINT index = 0; index < 2; ++index)
            {
                device->SetStreamSource( index, streams[index].pComObject(), offsets[index], strides[index] );
                device->SetStreamSourceFreq( index, frequencies[index] );
            }
            Moo::rc().setIndices( indices.pComObject() );
            if (declaration) Moo::rc().setVertexDeclaration( declaration.pComObject() );
            else Moo::rc().setFVF( fvf );
        }
    };
    void clearTextureBindings()
    {
        if (!material_ || !material_->pEffect()) return;
        ID3DXEffect * binding = material_->properties().existingStockBinding();
        if (binding) binding->SetTexture( "diffuseMap", NULL );
        ID3DXEffect * current = material_->pEffect()->pEffect();
        if (current && current != binding && !wot::is_stock_effect( current ))
            current->SetTexture( "diffuseMap", NULL );
    }
    template<class Buffer> bool upload( const ComObjectWrap<Buffer> & buffer, const void * data, std::size_t size )
    {
        void * destination = NULL;
        if (FAILED( buffer->Lock( 0, UINT(size), &destination, D3DLOCK_DISCARD ) )) return false;
        std::memcpy( destination, data, size );
        return SUCCEEDED( buffer->Unlock() );
    }
    bool reserve( std::size_t vertices, std::size_t indices, std::string & error )
    {
        IDirect3DDevice9 * device = Moo::rc().device();
        if (!common_ || vertices > vertexCapacity_)
        {
            common_ = NULL;
            offsets_ = NULL;
            vertexCapacity_ = 0;
            if (FAILED( device->CreateVertexBuffer( UINT(vertices * 96), D3DUSAGE_DYNAMIC | D3DUSAGE_WRITEONLY,
                    0, D3DPOOL_DEFAULT, &common_, NULL ) ) ||
                FAILED( device->CreateVertexBuffer( UINT(vertices * 32), D3DUSAGE_DYNAMIC | D3DUSAGE_WRITEONLY,
                    0, D3DPOOL_DEFAULT, &offsets_, NULL ) ))
            {
                common_ = NULL;
                offsets_ = NULL;
                error = "Cannot allocate original tPS vertex streams";
                return false;
            }
            vertexCapacity_ = vertices;
        }
        if (!indices_ || indices > indexCapacity_)
        {
            indices_ = NULL;
            indexCapacity_ = 0;
            if (FAILED( device->CreateIndexBuffer( UINT(indices * 2), D3DUSAGE_DYNAMIC | D3DUSAGE_WRITEONLY,
                    D3DFMT_INDEX16, D3DPOOL_DEFAULT, &indices_, NULL ) ))
            {
                error = "Cannot allocate original tPS index stream";
                return false;
            }
            indexCapacity_ = indices;
        }
        return true;
    }
    BW::string textureID_;
    Moo::BaseTexturePtr texture_;
    Moo::EffectMaterialPtr material_;
    Moo::VertexDeclaration * declaration_ = NULL;
    ComObjectWrap<DX::VertexBuffer> common_, offsets_;
    ComObjectWrap<DX::IndexBuffer> indices_;
    std::size_t vertexCapacity_ = 0, indexCapacity_ = 0;
};
}
std::shared_ptr<wot::StockTPSDrawResource> createStockTPSDrawResource( const BW::string & texture )
{
    return std::make_shared<TPSDrawResource>( texture );
}
BW_END_NAMESPACE
