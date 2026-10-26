#include "pch.hpp"
#include "vehicle_appearance.hpp"

#include "pyscript/script_math.hpp"
#include "cstdmf/bgtask_manager.hpp"
#include "duplo/py_attachment.hpp"
#include "duplo/pymodel.hpp"
#include "duplo/py_transform_fashion.hpp"
#include "model/fashion.hpp"
#include "model/model.hpp"
#include "model/super_model.hpp"
#include "model/super_model_node_tree.hpp"
#include "moo/node.hpp"
#include "moo/render_context.hpp"
#include "romp/py_resource_refs.hpp"
#include "romp/resource_ref.hpp"

#include <map>
#include <memory>
#include <vector>

BW_BEGIN_NAMESPACE

int StockVehicleAppearance_token = 1;

// #1513's appearance owns these providers for its entire lifetime. Model
// refresh rebinds their targets rather than replacing providers held by nodes.
class WGAdaptiveMatrixProvider : public MatrixProvider
{
    Py_Header( WGAdaptiveMatrixProvider, MatrixProvider )

public:
    WGAdaptiveMatrixProvider() :
        MatrixProvider( false, &s_type_ ),
        staticTransform_( Matrix::identity )
    {
    }

    virtual void matrix( Matrix & result ) const
    {
        // Exact #1513 0x006dde30: delegate to the live target, or return the
        // explicit static transform. There is no implicit interpolation here.
        if (target_)
            target_->matrix( result );
        else
            result = staticTransform_;
    }

    void setStaticTransform( MatrixProviderPtr value )
    {
        // Exact #1513 0x006de930 samples the supplied provider once. It does
        // not retain the provider or change the live target.
        value->matrix( staticTransform_ );
    }

    PY_RW_ATTRIBUTE_DECLARE( target_, target )
    PY_AUTO_METHOD_DECLARE( RETVOID, setStaticTransform,
        NZARG( MatrixProviderPtr, END ) )

    static WGAdaptiveMatrixProvider * New()
    {
        return new WGAdaptiveMatrixProvider();
    }
    PY_AUTO_FACTORY_DECLARE( WGAdaptiveMatrixProvider, END )

private:
    MatrixProviderPtr target_;
    Matrix staticTransform_;
};

PY_TYPEOBJECT( WGAdaptiveMatrixProvider )
PY_BEGIN_METHODS( WGAdaptiveMatrixProvider )
    PY_METHOD( setStaticTransform )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( WGAdaptiveMatrixProvider )
    PY_ATTRIBUTE( target )
PY_END_ATTRIBUTES()
PY_FACTORY( WGAdaptiveMatrixProvider, Math )

namespace
{
enum AssemblyOperationKind { ROOT_PART, ADD_PART, EMPLACE_PART, ADD_NODE, NODE_ALIAS };

struct AssemblyOperation
{
    AssemblyOperationKind kind;
    BW::string model;
    BW::string name;
    BW::string parent;
    BW::string prefix;
    Matrix local;
};

struct CompoundNode
{
    BW::string name;
    BW::string parentName;
    int parent;
    Matrix local;
    Matrix world;
    MatrixProviderPtr source;
};

struct CompoundPart
{
    PyModelPtr model;
    BW::string mountName;
    size_t mount;
    // Original model node identifier -> compound node. Scene Root is omitted
    // by #1513 0x008e21e0; the model mounts on its declared compound parent.
    std::vector<std::pair<BW::string, size_t> > bones;
};

struct CompoundState
{
    std::vector<CompoundNode> nodes;
    std::vector<CompoundPart> parts;
    std::map<BW::string, size_t> names;
    MatrixProviderPtr matrix;
    bool active;
    uint32 frame;

    CompoundState() : active( true ), frame( 0 ) {}

    bool evaluate( size_t index, std::vector<unsigned char> & visiting )
    {
        if (visiting[index] == 2)
            return true;
        if (visiting[index] == 1)
        {
            PyErr_SetString( PyExc_ValueError, "Compound node hierarchy contains a cycle" );
            return false;
        }
        visiting[index] = 1;
        CompoundNode & node = nodes[index];
        // #1513 0x008e3f00 writes providers directly into local transforms.
        // Applying a provider as an extra transform doubles authored offsets.
        if (index == 0 && matrix)
            matrix->matrix( node.local );
        else if (node.source)
            node.source->matrix( node.local );
        if (node.parent >= 0)
        {
            if (!evaluate( static_cast<size_t>( node.parent ), visiting ))
                return false;
            node.world.multiply( node.local, nodes[node.parent].world );
        }
        else
            node.world = node.local;
        visiting[index] = 2;
        return true;
    }

    bool evaluate()
    {
        std::vector<unsigned char> visiting( nodes.size(), 0 );
        for (size_t index = 0; index < nodes.size(); ++index)
            if (!evaluate( index, visiting ))
                return false;
        return true;
    }
};

typedef std::shared_ptr<CompoundState> CompoundStatePtr;

// A real pre-transform fashion writes the original skeleton's local pose
// before Model::dress computes the skin palette. PyModelNode.local only
// transforms attachments and cannot implement the stock Gun bone contract.
class CompoundPose : public TransformFashion
{
public:
    CompoundPose( const CompoundStatePtr & state, size_t part ) :
        state_( state ), part_( part ) {}

    virtual void dress( SuperModel & model, Matrix & /* world */ )
    {
        CompoundStatePtr state = state_.lock();
        if (!state || !state->active)
            return;
        const CompoundPart & part = state->parts[part_];
        for (size_t index = 0; index < part.bones.size(); ++index)
        {
            const std::pair<BW::string, size_t> & bone = part.bones[index];
            Moo::NodePtr node = model.findNode( bone.first );
            if (node)
                node->blendClobber( Model::blendCookie(), state->nodes[bone.second].local );
        }
    }
    virtual bool needsRedress() const { return false; }

private:
    std::weak_ptr<CompoundState> state_;
    size_t part_;
};
}

class PyCompoundPose : public PyTransformFashion
{
    Py_Header( PyCompoundPose, PyTransformFashion )
public:
    explicit PyCompoundPose( TransformFashionPtr fashion ) :
        PyTransformFashion( &s_type_ ), fashion_( fashion ) {}
    virtual const TransformFashionPtr transformFashion() const { return fashion_; }
    virtual TransformFashionPtr transformFashion() { return fashion_; }
private:
    TransformFashionPtr fashion_;
};
PY_TYPEOBJECT( PyCompoundPose )
PY_BEGIN_METHODS( PyCompoundPose )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( PyCompoundPose )
PY_END_ATTRIBUTES()

class PyCompoundNode : public MatrixProvider
{
    Py_Header( PyCompoundNode, MatrixProvider )
public:
    PyCompoundNode( const CompoundStatePtr & state, size_t node ) :
        MatrixProvider( false, &s_type_ ), state_( state ), node_( node ) {}
    virtual void matrix( Matrix & result ) const { result = state_->nodes[node_].world; }
    PY_RO_ATTRIBUTE_DECLARE( state_->nodes[node_].name, name )
private:
    CompoundStatePtr state_;
    size_t node_;
};
PY_TYPEOBJECT( PyCompoundNode )
PY_BEGIN_METHODS( PyCompoundNode )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( PyCompoundNode )
    PY_ATTRIBUTE( name )
PY_END_ATTRIBUTES()

class PyCompoundModel : public PyAttachment
{
    Py_Header( PyCompoundModel, PyAttachment )
public:
    explicit PyCompoundModel( const CompoundStatePtr & state ) :
        PyAttachment( &s_type_ ), state_( state ), visible_( true ) {}

    virtual ~PyCompoundModel()
    {
        this->reset();
    }

    virtual void tick( float elapsed )
    {
        if (state_->active)
            for (size_t index = 0; index < state_->parts.size(); ++index)
                state_->parts[index].model->tick( elapsed );
    }
    virtual bool needsUpdate() const { return state_->active; }
    virtual bool isLodVisible() const { return state_->active && visible_; }
#if ENABLE_TRANSFORM_VALIDATION
    virtual bool isTransformFrameDirty( uint32 frame ) const
    {
        return state_->active && state_->frame != frame;
    }
    virtual bool isVisibleTransformFrameDirty( uint32 frame ) const
    {
        return this->isTransformFrameDirty( frame );
    }
#endif
    virtual void updateAnimations( const Matrix & /* world */, float lod )
    {
        if (!state_->active || !state_->evaluate())
            return;
        for (size_t index = 0; index < state_->parts.size(); ++index)
        {
            CompoundPart & part = state_->parts[index];
            const Matrix & pose = state_->nodes[part.mount].world;
            part.model->worldTransform( pose );
            part.model->updateAnimations( pose, lod );
        }
        state_->frame = Moo::rc().frameTimestamp();
    }
    virtual void draw( Moo::DrawContext & context, const Matrix & /* world */ )
    {
        if (state_->active && visible_)
            for (size_t index = 0; index < state_->parts.size(); ++index)
            {
                CompoundPart & part = state_->parts[index];
                part.model->draw( context, state_->nodes[part.mount].world );
            }
    }
    virtual void enterWorld()
    {
        PyAttachment::enterWorld();
        for (size_t index = 0; index < state_->parts.size(); ++index)
            state_->parts[index].model->enterWorld();
    }
    virtual void leaveWorld()
    {
        for (size_t index = 0; index < state_->parts.size(); ++index)
            if (state_->parts[index].model->isInWorld())
                state_->parts[index].model->leaveWorld();
        if (this->isInWorld())
            PyAttachment::leaveWorld();
    }
    virtual void tossed( bool outside )
    {
        for (size_t index = 0; index < state_->parts.size(); ++index)
            state_->parts[index].model->tossed( outside );
    }
    virtual void localBoundingBox( BoundingBox & bounds, bool skinny = false )
    {
        Matrix inverse;
        if (!state_->active || !inverse.invert( state_->nodes[0].world ))
            return;
        for (size_t index = 0; index < state_->parts.size(); ++index)
        {
            CompoundPart & part = state_->parts[index];
            BoundingBox local;
            part.model->localBoundingBox( local, skinny );
            Matrix relative;
            relative.multiply( state_->nodes[part.mount].world, inverse );
            local.transformBy( relative );
            bounds.addBounds( local );
        }
    }
    virtual void localVisibilityBox( BoundingBox & bounds, bool skinny = false )
    {
        this->localBoundingBox( bounds, skinny );
    }
    virtual void worldBoundingBox( BoundingBox & bounds, const Matrix &, bool skinny = false )
    {
        if (state_->active)
            for (size_t index = 0; index < state_->parts.size(); ++index)
            {
                CompoundPart & part = state_->parts[index];
                part.model->worldBoundingBox( bounds, state_->nodes[part.mount].world, skinny );
            }
    }
    virtual void worldVisibilityBox( BoundingBox & bounds, const Matrix & world, bool skinny = false )
    {
        this->worldBoundingBox( bounds, world, skinny );
    }

    PyObject * node( const BW::string & name, MatrixProviderPtr source )
    {
        if (!state_->active)
        {
            PyErr_SetString( PyExc_ValueError, "CompoundModel has been reset" );
            return NULL;
        }
        std::map<BW::string, size_t>::const_iterator found = state_->names.find( name );
        const size_t index = name.empty() ? 0 :
            (found == state_->names.end() ? state_->nodes.size() : found->second);
        if (index >= state_->nodes.size())
        {
            PyErr_Format( PyExc_ValueError, "CompoundModel has no node named %s", name.c_str() );
            return NULL;
        }
        if (source)
        {
            if (index == 0)
                state_->matrix = source;
            else
                state_->nodes[index].source = source;
        }
        // The stock node adapter is cached by node index. Its identity is
        // independent of which local source was most recently installed.
        PyObjectPtr & handle = handles_[index];
        if (!handle)
            handle = PyObjectPtr( new PyCompoundNode( state_, index ), PyObjectPtr::STEAL_REFERENCE );
        Py_INCREF( handle.get() );
        return handle.get();
    }
    PY_AUTO_METHOD_DECLARE( RETOWN, node,
        ARG( BW::string, OPTARG( MatrixProviderPtr, NULL, END ) ) )

    MatrixProviderPtr matrix() const { return state_->matrix; }
    void matrix( MatrixProviderPtr value ) { state_->matrix = value; }
    PY_RW_ACCESSOR_ATTRIBUTE_DECLARE( MatrixProviderPtr, matrix, matrix )
    PY_RW_ATTRIBUTE_DECLARE( visible_, visible )

    void reset()
    {
        if (!state_->active)
            return;
        this->leaveWorld();
        state_->active = false;
        state_->parts.clear();
        state_->matrix = NULL;
        for (size_t index = 0; index < state_->nodes.size(); ++index)
            state_->nodes[index].source = NULL;
        handles_.clear();
    }
    PY_AUTO_METHOD_DECLARE( RETVOID, reset, END )

private:
    CompoundStatePtr state_;
    std::map<size_t, PyObjectPtr> handles_;
    bool visible_;
};
PY_TYPEOBJECT( PyCompoundModel )
PY_BEGIN_METHODS( PyCompoundModel )
    PY_METHOD( node )
    PY_METHOD( reset )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( PyCompoundModel )
    PY_ATTRIBUTE( matrix )
    PY_ATTRIBUTE( visible )
PY_END_ATTRIBUTES()

namespace
{
PyModelPtr loadPartModel( const BW::string & resource )
{
    PyObjectPtr args( Py_BuildValue( "(s)", resource.c_str() ),
        PyObjectPtr::STEAL_REFERENCE );
    if (!args)
        return PyModelPtr();
    PyObject * object = PyModel::pyNew( args.get() );
    if (!object)
        return PyModelPtr();
    return PyModelPtr( static_cast<PyModel *>( object ), ScriptObject::FROM_NEW_REFERENCE );
}

size_t appendNode( CompoundState & state, const BW::string & name,
    const BW::string & parent, const Matrix & local )
{
    CompoundNode node;
    node.name = name;
    node.parentName = parent;
    node.parent = -2;
    node.local = local;
    node.world = local;
    const size_t index = state.nodes.size();
    state.nodes.push_back( node );
    state.names[name] = index;
    return index;
}

PyObject * buildCompound( const std::vector<AssemblyOperation> & operations )
{
    CompoundStatePtr state( new CompoundState() );
    std::vector<std::pair<BW::string, BW::string> > aliases;
    bool haveRoot = false;
    for (size_t operationIndex = 0; operationIndex < operations.size(); ++operationIndex)
    {
        const AssemblyOperation & operation = operations[operationIndex];
        if (operation.kind == NODE_ALIAS)
        {
            aliases.push_back( std::make_pair( operation.parent, operation.name ) );
            continue;
        }
        if (operation.kind == ROOT_PART)
        {
            if (haveRoot)
            {
                PyErr_SetString( PyExc_ValueError,
                    "A vehicle compound requires exactly one root part" );
                return NULL;
            }
            haveRoot = true;
        }
        else if (!haveRoot)
        {
            PyErr_SetString( PyExc_ValueError, "Compound operation precedes its root part" );
            return NULL;
        }
        if (operation.kind != EMPLACE_PART)
        {
            const size_t node = appendNode( *state, operation.name,
                operation.parent, operation.local );
            if (operation.kind == ROOT_PART)
                state->nodes[node].parent = -1;
        }
        else if (!operation.name.empty())
            aliases.push_back( std::make_pair( operation.parent, operation.name ) );
        if (operation.kind == ADD_NODE)
            continue;

        CompoundPart part;
        part.mountName = operation.kind == EMPLACE_PART ? operation.parent : operation.name;
        part.model = loadPartModel( operation.model );
        if (!part.model)
            return NULL;
        SuperModel * superModel = part.model->pSuperModel();
        if (!superModel || (!superModel->getInstanceNodeTree() &&
                !superModel->regenerateInstanceNodeTree()))
        {
            PyErr_Format( PyExc_ValueError, "Compound part has no skeleton: %s",
                operation.model.c_str() );
            return NULL;
        }
        const SuperModelNodeTree * tree = superModel->getInstanceNodeTree();
        const int count = tree->getNumNodes();
        std::vector<size_t> indices( count, 0 );
        // #1513 0x008e21e0 discards Scene Root and grafts its children onto
        // the named mount, retaining their actual parent and local transforms.
        for (int index = 1; index < count; ++index)
        {
            const SuperModelNode & bone = tree->getNode( index );
            const size_t node = appendNode( *state,
                operation.prefix + bone.getIdentifier(), part.mountName,
                superModel->getNodeRelativeTransform( index ) );
            indices[index] = node;
            if (bone.getParentIndex() > 0)
                state->nodes[node].parent = static_cast<int>( indices[bone.getParentIndex()] );
            part.bones.push_back( std::make_pair( bone.getIdentifier(), node ) );
        }
        state->parts.push_back( part );
    }
    if (!haveRoot)
    {
        PyErr_SetString( PyExc_ValueError, "Compound has no root part" );
        return NULL;
    }
    // Aliases are resolved after model trees have been loaded, matching the
    // second pass in #1513 0x008e2620. Hull aliases must retain the V node.
    for (size_t index = 0; index < aliases.size(); ++index)
    {
        std::map<BW::string, size_t>::const_iterator source = state->names.find( aliases[index].first );
        if (source == state->names.end())
        {
            PyErr_Format( PyExc_ValueError, "Compound alias refers to missing node %s",
                aliases[index].first.c_str() );
            return NULL;
        }
        state->names[aliases[index].second] = source->second;
    }
    for (size_t index = 0; index < state->nodes.size(); ++index)
    {
        CompoundNode & node = state->nodes[index];
        if (node.parent != -2)
            continue;
        std::map<BW::string, size_t>::const_iterator parent = state->names.find( node.parentName );
        if (node.parentName.empty())
            node.parent = 0;
        else if (parent != state->names.end())
            node.parent = static_cast<int>( parent->second );
        else
        {
            PyErr_Format( PyExc_ValueError, "Compound refers to missing parent %s",
                node.parentName.c_str() );
            return NULL;
        }
    }
    PyMatrix * root = new PyMatrix();
    root->set( state->nodes[0].local );
    state->matrix = MatrixProviderPtr( root, true );
    for (size_t index = 0; index < state->parts.size(); ++index)
    {
        CompoundPart & part = state->parts[index];
        std::map<BW::string, size_t>::const_iterator mount = state->names.find( part.mountName );
        if (!part.mountName.empty() && mount == state->names.end())
        {
            PyErr_Format( PyExc_ValueError, "Compound part refers to missing mount %s",
                part.mountName.c_str() );
            return NULL;
        }
        part.mount = part.mountName.empty() ? 0 : mount->second;
        PyObjectPtr fashion( new PyCompoundPose( new CompoundPose( state, index ) ),
            PyObjectPtr::STEAL_REFERENCE );
        if (PyObject_SetAttrString( part.model.get(), "_stockCompoundPose", fashion.get() ) < 0)
            return NULL;
    }
    if (!state->evaluate())
        return NULL;
    return new PyCompoundModel( state );
}
}

class PyCompoundAssembler : public PyObjectPlus
{
    Py_Header( PyCompoundAssembler, PyObjectPlus )
public:
    PyCompoundAssembler() : PyObjectPlus( &s_type_ ), spaceID_( 0 ) {}

    void addRootPart( const BW::string & model, const BW::string & name,
        const Matrix & local, const BW::string & prefix )
    {
        this->append( ROOT_PART, model, name, "", local, prefix );
    }
    PY_AUTO_METHOD_DECLARE( RETVOID, addRootPart,
        ARG( BW::string, OPTARG( BW::string, "",
        OPTARG( Matrix, Matrix::identity, OPTARG( BW::string, "", END ) ) ) ) )

    void addPart( const BW::string & model, const BW::string & parent,
        const BW::string & name, const Matrix & local, const BW::string & prefix )
    {
        this->append( ADD_PART, model, name, parent, local, prefix );
    }
    PY_AUTO_METHOD_DECLARE( RETVOID, addPart,
        ARG( BW::string, OPTARG( BW::string, "", OPTARG( BW::string, "",
        OPTARG( Matrix, Matrix::identity, OPTARG( BW::string, "", END ) ) ) ) ) )

    void emplacePart( const BW::string & model, const BW::string & parent,
        const BW::string & alias, const BW::string & prefix )
    {
        this->append( EMPLACE_PART, model, alias, parent, Matrix::identity, prefix );
    }
    PY_AUTO_METHOD_DECLARE( RETVOID, emplacePart,
        ARG( BW::string, OPTARG( BW::string, "", OPTARG( BW::string, "",
        OPTARG( BW::string, "", END ) ) ) ) )

    void addNode( const BW::string & name, const BW::string & parent, const Matrix & local )
    {
        this->append( ADD_NODE, "", name, parent, local, "" );
    }
    PY_AUTO_METHOD_DECLARE( RETVOID, addNode,
        ARG( BW::string, OPTARG( BW::string, "", OPTARG( Matrix, Matrix::identity, END ) ) ) )

    void addNodeAlias( const BW::string & source, const BW::string & alias )
    {
        this->append( NODE_ALIAS, "", alias, source, Matrix::identity, "" );
    }
    PY_AUTO_METHOD_DECLARE( RETVOID, addNodeAlias, ARG( BW::string, ARG( BW::string, END ) ) )

    PY_RW_ATTRIBUTE_DECLARE( assemblerName_, assemblerName )
    PY_RW_ATTRIBUTE_DECLARE( spaceID_, spaceID )

    const std::vector<AssemblyOperation> & operations() const { return operations_; }
    const BW::string & resourceID() const { return assemblerName_; }
    PyObject * product( const std::vector<AssemblyOperation> & operations )
    {
        // #1513 0x008d8630 caches the compound product on its assembler.
        if (!product_)
            product_ = PyObjectPtr( buildCompound( operations ), PyObjectPtr::STEAL_REFERENCE );
        if (!product_)
            return NULL;
        Py_INCREF( product_.get() );
        return product_.get();
    }
    static PyCompoundAssembler * New() { return new PyCompoundAssembler(); }
    PY_AUTO_FACTORY_DECLARE( PyCompoundAssembler, END )

private:
    void append( AssemblyOperationKind kind, const BW::string & model,
        const BW::string & name, const BW::string & parent, const Matrix & local,
        const BW::string & prefix )
    {
        AssemblyOperation operation;
        operation.kind = kind;
        operation.model = model;
        operation.name = name;
        operation.parent = parent;
        operation.local = local;
        operation.prefix = prefix;
        operations_.push_back( operation );
    }
    std::vector<AssemblyOperation> operations_;
    BW::string assemblerName_;
    uint32 spaceID_;
    PyObjectPtr product_;
};
PY_TYPEOBJECT( PyCompoundAssembler )
PY_BEGIN_METHODS( PyCompoundAssembler )
    PY_METHOD( addRootPart )
    PY_METHOD( addPart )
    PY_METHOD( emplacePart )
    PY_METHOD( addNode )
    PY_METHOD( addNodeAlias )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( PyCompoundAssembler )
    PY_ATTRIBUTE( assemblerName )
    PY_ATTRIBUTE( spaceID )
PY_END_ATTRIBUTES()
PY_FACTORY_NAMED( PyCompoundAssembler, "CompoundAssembler", BigWorld )

namespace
{
void compoundModRef( void * data, int delta )
{
    PyObject * object = static_cast<PyObject *>( data );
    if (delta > 0)
        Py_INCREF( object );
    else
        Py_DECREF( object );
}

PyObject * compoundPyInstance( ResourceRef & resource )
{
    PyObject * object = static_cast<PyObject *>( resource.data() );
    Py_INCREF( object );
    return object;
}

struct CompoundLoadRequest
{
    BW::string id;
    PyObjectPtr assembler;
    std::vector<AssemblyOperation> operations;
    ResourceRefs retainedModels;
    bool loaded;
};

class StockResourceRefs : public PyResourceRefs
{
public:
    StockResourceRefs() : PyResourceRefs( ResourceRefs() ) {}
    const ResourceRefs & loadedResources() const { return resourceRefs_; }
    const BW::vector<BW::string> & failedResources() const { return pFailedIDs_; }
};

class CompoundResourceLoader : public BackgroundTask
{
public:
    CompoundResourceLoader( const std::vector<CompoundLoadRequest> & requests,
        PyObject * callback, int priority ) :
        BackgroundTask( "StockCompoundResourceLoader" ), requests_( requests ),
        callback_( callback ), result_( new StockResourceRefs(),
            PyObjectPtr::STEAL_REFERENCE )
    {
        FileIOTaskManager::instance().addBackgroundTask( this, priority );
    }

    virtual void doBackgroundTask( TaskManager & manager )
    {
        // Only resource objects are touched in this thread. Python model
        // wrappers, node providers and the callback are created on the main
        // thread, as in the public engine's ResourceRefs loader.
        for (size_t index = 0; index < requests_.size(); ++index)
        {
            CompoundLoadRequest & request = requests_[index];
            request.loaded = true;
            if (!request.assembler)
            {
                ResourceRef resource = ResourceRef::getOrLoad( request.id );
                request.loaded = static_cast<bool>( resource );
                request.retainedModels.push_back( resource );
            }
            else
            {
                for (size_t operation = 0; operation < request.operations.size(); ++operation)
                {
                    const BW::string & model = request.operations[operation].model;
                    if (model.empty())
                        continue;
                    ResourceRef resource = ResourceRef::getOrLoad( model );
                    if (!resource)
                        request.loaded = false;
                    request.retainedModels.push_back( resource );
                }
            }
        }
        manager.addMainThreadTask( this );
    }

    virtual void doMainThreadTask( TaskManager & /* manager */ )
    {
        PyResourceRefs * result = static_cast<PyResourceRefs *>( result_.get() );
        for (size_t index = 0; index < requests_.size(); ++index)
        {
            CompoundLoadRequest & request = requests_[index];
            if (!request.loaded)
            {
                result->addFailedResourceID( request.id );
                continue;
            }
            if (!request.assembler)
            {
                result->addLoadedResourceRef( request.retainedModels[0] );
                continue;
            }
            PyCompoundAssembler * assembler =
                static_cast<PyCompoundAssembler *>( request.assembler.get() );
            PyObjectPtr product( assembler->product( request.operations ),
                PyObjectPtr::STEAL_REFERENCE );
            if (!product)
            {
                PyErr_Print();
                result->addFailedResourceID( request.id );
                continue;
            }
            ResourceRef resource( product.get(), compoundModRef, request.id, compoundPyInstance );
            result->addLoadedResourceRef( resource );
        }
        PyObjectPtr returned( PyObject_CallFunctionObjArgs( callback_.get(), result_.get(), NULL ),
            PyObjectPtr::STEAL_REFERENCE );
        if (!returned)
            PyErr_Print();
    }

private:
    std::vector<CompoundLoadRequest> requests_;
    PyObjectPtr callback_;
    PyObjectPtr result_;
};
}

static bool isStockVehicleResource( ScriptObject object )
{
    return PyCompoundAssembler::Check( object );
}
PY_AUTO_MODULE_FUNCTION( RETDATA, isStockVehicleResource, ARG( ScriptObject, END ), BigWorld )

PyObject * py_loadStockVehicleResourcesBG( PyObject * args )
{
    PyObject * resources;
    PyObject * callback;
    int priority = BgTaskManager::DEFAULT;
    if (!PyArg_ParseTuple( args, "OO|i:loadResourceListBG", &resources, &callback, &priority ))
        return NULL;
    if (!PyCallable_Check( callback ))
    {
        PyErr_SetString( PyExc_TypeError, "loadResourceListBG requires a callable callback" );
        return NULL;
    }
    PyObjectPtr sequence( PySequence_Fast( resources,
        "loadResourceListBG requires a resource sequence" ), PyObjectPtr::STEAL_REFERENCE );
    if (!sequence)
        return NULL;
    std::vector<CompoundLoadRequest> requests;
    const Py_ssize_t count = PySequence_Fast_GET_SIZE( sequence.get() );
    for (Py_ssize_t index = 0; index < count; ++index)
    {
        PyObject * object = PySequence_Fast_GET_ITEM( sequence.get(), index );
        CompoundLoadRequest request;
        request.loaded = false;
        if (PyCompoundAssembler::Check( object ))
        {
            PyCompoundAssembler * assembler = static_cast<PyCompoundAssembler *>( object );
            request.assembler = object;
            request.id = assembler->resourceID();
            request.operations = assembler->operations();
        }
        else if (PyString_Check( object ))
        {
            if (Script::setData( object, request.id ) != 0)
                return NULL;
        }
        else
        {
            PyErr_SetString( PyExc_TypeError,
                "loadResourceListBG requires resource strings or CompoundAssembler objects" );
            return NULL;
        }
        requests.push_back( request );
    }
    new CompoundResourceLoader( requests, callback, priority );
    Py_RETURN_NONE;
}
PY_MODULE_FUNCTION( loadStockVehicleResourcesBG, BigWorld )

class StockPrerequisitesRequest : public PyObjectPlus
{
    Py_Header( StockPrerequisitesRequest, PyObjectPlus )
public:
    StockPrerequisitesRequest() : PyObjectPlus( &s_type_ ), cancelled_( false ), ready_( false ) {}
    PY_METHOD_DECLARE( pyCall )
    void cancel() { cancelled_ = true; result_ = NULL; failures_.clear(); }
    void fail( PyObject * resources )
    {
        const Py_ssize_t count = PySequence_Size( resources );
        for (Py_ssize_t index = 0; index < count; ++index)
        {
            PyObjectPtr item( PySequence_GetItem( resources, index ), PyObjectPtr::STEAL_REFERENCE );
            BW::string id;
            if (item && PyCompoundAssembler::Check( item.get() ))
                id = static_cast<PyCompoundAssembler *>( item.get() )->resourceID();
            else if (item && PyString_Check( item.get() ))
                id = PyString_AsString( item.get() );
            else
            {
                PyErr_Clear();
                continue;
            }
            failures_.push_back( ResourceRef( NULL, NULL, id, NULL ) );
        }
        ready_ = true;
    }
    bool collect( ResourceRefs & resources ) const
    {
        if (!ready_)
            return false;
        if (!result_)
        {
            resources = failures_;
            return true;
        }
        const StockResourceRefs * result = static_cast<StockResourceRefs *>( result_.get() );
        resources = result->loadedResources();
        const BW::vector<BW::string> & failures = result->failedResources();
        for (size_t index = 0; index < failures.size(); ++index)
            resources.push_back( ResourceRef( NULL, NULL, failures[index], NULL ) );
        return true;
    }
private:
    bool cancelled_;
    bool ready_;
    PyObjectPtr result_;
    ResourceRefs failures_;
};

PyObject * StockPrerequisitesRequest::pyCall( PyObject * args )
{
    PyObject * result;
    if (!PyArg_ParseTuple( args, "O:prerequisites", &result ))
        return NULL;
    if (!PyResourceRefs::Check( result ))
    {
        PyErr_SetString( PyExc_TypeError, "Expected native prerequisite resource refs" );
        return NULL;
    }
    if (!cancelled_ && !ready_)
    {
        result_ = result;
        ready_ = true;
    }
    Py_RETURN_NONE;
}
PY_TYPEOBJECT_WITH_CALL( StockPrerequisitesRequest )
PY_BEGIN_METHODS( StockPrerequisitesRequest )
PY_END_METHODS()
PY_BEGIN_ATTRIBUTES( StockPrerequisitesRequest )
PY_END_ATTRIBUTES()

bool beginStockVehiclePrerequisites( PyObject * resources, PyObjectPtr & request )
{
    if (!resources || !PySequence_Check( resources ))
        return false;
    const Py_ssize_t count = PySequence_Size( resources );
    bool containsAssembler = false;
    for (Py_ssize_t index = 0; index < count; ++index)
    {
        PyObjectPtr value( PySequence_GetItem( resources, index ), PyObjectPtr::STEAL_REFERENCE );
        if (!value)
            return false;
        if (PyCompoundAssembler::Check( value.get() ))
            containsAssembler = true;
    }
    if (!containsAssembler)
        return false;
    // Publish the request before scheduling: the callback owns only this
    // request and never captures an Entity or an unguarded native pointer.
    request = PyObjectPtr( new StockPrerequisitesRequest(), PyObjectPtr::STEAL_REFERENCE );
    PyObjectPtr args( Py_BuildValue( "(OO)", resources, request.get() ),
        PyObjectPtr::STEAL_REFERENCE );
    if (!args)
    {
        PyErr_Print();
        static_cast<StockPrerequisitesRequest *>( request.get() )->fail( resources );
        return true;
    }
    PyObjectPtr result( py_loadStockVehicleResourcesBG( args.get() ), PyObjectPtr::STEAL_REFERENCE );
    if (!result)
    {
        // An identified assembler request must not fall through the public
        // string-only path and become a successful empty prerequisite list.
        PyErr_Print();
        static_cast<StockPrerequisitesRequest *>( request.get() )->fail( resources );
        return true;
    }
    return true;
}

bool pollStockVehiclePrerequisites( PyObject * request, ResourceRefs & resources )
{
    return static_cast<StockPrerequisitesRequest *>( request )->collect( resources );
}

void cancelStockVehiclePrerequisites( PyObject * request )
{
    if (request)
        static_cast<StockPrerequisitesRequest *>( request )->cancel();
}

bool stockVehicleModelBounds( PyObject * model, BoundingBox & bounds )
{
    if (PyCompoundModel::Check( model ))
        static_cast<PyCompoundModel *>( model )->localBoundingBox( bounds );
    else if (PyModel::Check( model ))
        static_cast<PyModel *>( model )->localBoundingBox( bounds );
    else
        return false;
    return true;
}

BW_END_NAMESPACE
