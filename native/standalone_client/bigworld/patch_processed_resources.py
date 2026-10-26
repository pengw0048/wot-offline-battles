#!/usr/bin/env python3
"""Read original #1513 processed resources without rewriting their asset bytes."""
import argparse
from pathlib import Path


# Native storage table 0x01a1afb8; conversion selector 0x009d3540.
# The MULT3 names describe stored palette-row offsets, not an instruction to
# multiply an already processed vertex again. These types must remain distinct
# from public BW's historical SC_DIV3/SC_REVERSE types.
NATIVE_STORAGE_TYPES = r'''
	// #1513 native conversion kernels 0x009cf350..0x009cf711.
	inline uint8 native1513TruncatedByte( float value )
	{
		// CVTTSS2SI returns INT_MIN for NaN and values outside signed int32.
		if (!(value >= -2147483648.0f && value < 2147483648.0f))
			return 0;
		return static_cast<uint8>(static_cast<int32>(value));
	}

	struct ElementMult3I
	{
		float index_;
		ElementMult3I( const Float1 & value ) : index_(value.x * 3.0f) {}
		operator Float1() const
		{
			Float1 result;
			result.x = index_ * 0.3333333432674408f;
			return result;
		}
	};

	struct ElementMult3ReversePaddedIIIP;
	struct ElementMult3III
	{
		uint8 index1_, index2_, index3_;
		ElementMult3III( const Ubyte3 & value )
			: index1_(static_cast<uint8>(value.x * 3)),
			  index2_(static_cast<uint8>(value.y * 3)),
			  index3_(static_cast<uint8>(value.z * 3)) {}
		ElementMult3III( const ElementMult3I & value )
			: index1_(native1513TruncatedByte(value.index_)),
			  index2_(index1_), index3_(index1_) {}
		ElementMult3III( const ElementMult3ReversePaddedIIIP & value );
		operator Ubyte3() const
		{
			Ubyte3 result;
			result.x = index1_ / 3;
			result.y = index2_ / 3;
			result.z = index3_ / 3;
			return result;
		}
	};

	struct ElementMult3ReversePaddedIIIP
	{
		uint8 index1_, index2_, index3_, pad_;
		ElementMult3ReversePaddedIIIP( const Ubyte3 & value )
			: index1_(static_cast<uint8>(value.z * 3)),
			  index2_(static_cast<uint8>(value.y * 3)),
			  index3_(static_cast<uint8>(value.x * 3)), pad_(0) {}
		ElementMult3ReversePaddedIIIP( const ElementMult3III & value )
			: index1_(value.index3_), index2_(value.index2_),
			  index3_(value.index1_), pad_(0) {}
		ElementMult3ReversePaddedIIIP( const Float1 & value )
			: index1_(native1513TruncatedByte(value.x * 3.0f)),
			  index2_(index1_), index3_(index1_) {}
		// 0x009cf640 leaves the unused padding byte indeterminate.
		operator Ubyte3() const
		{
			Ubyte3 result;
			result.x = index3_ / 3;
			result.y = index2_ / 3;
			result.z = index1_ / 3;
			return result;
		}
	};

	inline ElementMult3III::ElementMult3III(
		const ElementMult3ReversePaddedIIIP & value )
		: index1_(value.index3_), index2_(value.index2_), index3_(value.index1_) {}

	struct ElementReversePaddedPWWP
	{
		uint8 pad1_, weight1_, weight2_, pad2_;
		// 0x009d32c0 writes the weights only; padding is not initialized.
		ElementReversePaddedPWWP() : weight1_(0), weight2_(255) {}
		ElementReversePaddedPWWP( const Ubyte2 & value )
			: pad1_(0), weight1_(value.y), weight2_(value.x), pad2_(0) {}
		ElementReversePaddedPWWP( const Float2 & value )
			: pad1_(0), weight1_(native1513TruncatedByte(value.y * 256.0f)),
			  weight2_(native1513TruncatedByte(value.x * 256.0f)), pad2_(0) {}
		operator Ubyte2() const
		{
			Ubyte2 result;
			result.x = weight2_;
			result.y = weight1_;
			return result;
		}
		operator Float2() const
		{
			Float2 result;
			result.x = weight2_ * 0.00390625f;
			result.y = weight1_ * 0.00390625f;
			return result;
		}
	};

'''

VISUAL_BEFORE = '''\t\tactualResourceName = resourceBaseName + ".visual.processed";
\t\tdataSection = BWResource::openSection( actualResourceName );'''
VISUAL_AFTER = '''\t\tactualResourceName = resourceBaseName + ".visual_processed";
\t\tdataSection = BWResource::openSection( actualResourceName );
\t\tif (!dataSection)
\t\t{
\t\t\tactualResourceName = resourceBaseName + ".visual.processed";
\t\t\tdataSection = BWResource::openSection( actualResourceName );
\t\t}'''
PRIMITIVE_BEFORE = '''\tDataSectionPtr pNew =
\t\tBWResource::instance().openSection( resourceID + ".processed" );'''
PRIMITIVE_AFTER = '''\tDataSectionPtr pNew =
\t\tBWResource::instance().openSection( resourceID + "_processed" );
\tif (!pNew)
\t{
\t\tpNew = BWResource::instance().openSection( resourceID + ".processed" );
\t}'''

STORAGES = (
    ('SC_FLOAT1_MULT3_I', 'ElementMult3I', 1),
    ('SC_UBYTE3_MULT3_III', 'ElementMult3III', 3),
    ('SC_UBYTE4_MULT3_REVERSE_PADDED_IIIP', 'ElementMult3ReversePaddedIIIP', 3),
    ('SC_UBYTE4_REVERSE_PADDED_PWWP', 'ElementReversePaddedPWWP', 2),
)


def patches():
    enum_anchor = '\t\t\tSC_REVERSE_PADDED_WW_,'
    names_anchor = '\tStorageEntry( Stor::SC_REVERSE_PADDED_WW_, "SC_REVERSE_PADDED_WW_" )'
    switch_anchor = '\tX( SC_REVERSE_PADDED_WW_, ElementReversePaddedWW_ ) \\'
    mapping_anchor = 'DECLARE_VERTEX_TYPE_SIZE( UBYTE4N, 4, 4 );'
    declaration_anchor = '\tDeclTypeEntry( Stor::SC_REVERSE_PADDED_WW_, D3DDECLTYPE_D3DCOLOR )'
    conversions_anchor = '\tENABLE_CONVERSION_TEMPLATE_FUNC( Ubyte3, ElementDiv3III, \n\t\tVertexConversions::assignmentConvert );'
    pairs = (
        ('ElementMult3I', 'Float1', True),
        ('ElementMult3III', 'Ubyte3', True),
        ('ElementMult3III', 'ElementMult3I', False),
        ('ElementMult3ReversePaddedIIIP', 'Ubyte3', True),
        ('ElementMult3ReversePaddedIIIP', 'ElementMult3III', True),
        ('ElementMult3ReversePaddedIIIP', 'Float1', False),
        ('ElementReversePaddedPWWP', 'Ubyte2', True),
        ('ElementReversePaddedPWWP', 'Float2', True),
    )
    conversions = ''
    for destination, source, two_way in pairs:
        macro = 'ENABLE_CONVERSION_TEMPLATE_FUNC' + ('_2WAY' if two_way else '')
        conversions += '\n\t%s( %s, %s,\n\t\tVertexConversions::assignmentConvert );' % (
            macro, destination, source)
    return {
        'lib/moo/visual_common.hpp': [(VISUAL_BEFORE, VISUAL_AFTER, 1)],
        'lib/resmgr/primitive_file.cpp': [(PRIMITIVE_BEFORE, PRIMITIVE_AFTER, 1)],
        'lib/moo/vertex_element.hpp': [(enum_anchor, enum_anchor + ''.join(
            '\n\t\t\t%s,' % name for name, unused, count in STORAGES), 1)],
        'lib/moo/vertex_element.cpp': [(names_anchor, names_anchor + ',' + ''.join(
            '\n\tStorageEntry( Stor::%s, "%s" )%s' % (
                name, name, ',' if index + 1 < len(STORAGES) else '')
            for index, (name, unused, count) in enumerate(STORAGES)), 1)],
        'lib/moo/vertex_element_special_cases.hpp': [(
            '\t// Just an initialisation helper for Ubyte2 in blend weights case',
            NATIVE_STORAGE_TYPES + '\t// Just an initialisation helper for Ubyte2 in blend weights case', 1)],
        'lib/moo/vertex_format.hpp': [(mapping_anchor, ''.join(
            'DECLARE_VERTEX_TYPE_MAPPING( VertexElement::%s, %s, %d );\n' % (
                runtime_type, name, count) for name, runtime_type, count in STORAGES)
            + '\n' + mapping_anchor, 1)],
        'lib/moo/vertex_format_conversions.cpp': [
            (switch_anchor, switch_anchor + ''.join(
                '\n\tX( %s, %s ) \\' % (name, runtime_type)
                for name, runtime_type, count in STORAGES), 1),
            ('ENABLE_CTOR_CREATION_FUNC( ElementReversePaddedWW_ );',
             'ENABLE_CTOR_CREATION_FUNC( ElementReversePaddedWW_ );\n'
             'ENABLE_CTOR_CREATION_FUNC( ElementReversePaddedPWWP );', 1),
            (conversions_anchor, conversions_anchor + conversions, 1)],
        'lib/moo/vertex_declaration.cpp': [
            (declaration_anchor, declaration_anchor + ',\n'
             '\tDeclTypeEntry( Stor::SC_FLOAT1_MULT3_I, D3DDECLTYPE_FLOAT1 ),\n'
             '\tDeclTypeEntry( Stor::SC_UBYTE4_MULT3_REVERSE_PADDED_IIIP, D3DDECLTYPE_D3DCOLOR ),\n'
             '\tDeclTypeEntry( Stor::SC_UBYTE4_REVERSE_PADDED_PWWP, D3DDECLTYPE_D3DCOLOR )', 1),
            ('\t\tDECLARE_UNSUPPORTED_TYPE( SC_DIV3_III );',
             '\t\tDECLARE_UNSUPPORTED_TYPE( SC_DIV3_III );\n'
             '\t\tDECLARE_UNSUPPORTED_TYPE( SC_UBYTE3_MULT3_III );', 1)],
    }


def patch_engine(root):
    root = Path(root)
    updates = []
    for name, replacements in patches().items():
        path = root / name
        source = path.read_text(encoding='utf-8-sig')
        for before, after, count in replacements:
            if source.count(after) == count:
                continue
            if source.count(before) != count:
                raise ValueError('Unrecognized pinned processed-resource seam: ' + name)
            source = source.replace(before, after)
        updates.append((path, source))
    for path, source in updates:
        path.write_text(source, encoding='utf-8')
    print('Original #1513 processed suffixes and vertex storage conversions installed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch_engine(parser.parse_args().engine_root)
