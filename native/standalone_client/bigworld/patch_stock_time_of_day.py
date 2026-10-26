#!/usr/bin/env python3
"""Restore the #1513 day/night reader and native sun transform order."""

import argparse
from pathlib import Path


SUN_TRANSFORM = '''\t// #1513 a1f370: the field named angleZ rotates about world Y.
\tnow_.sunTransform.setRotateY( sunAngleZ_ * 0.017453292f );
\tnow_.sunTransform.preRotateX( sunAngle_ * -0.017453292f );
\tnow_.sunTransform.preRotateY( 6.2831855f - time_ * 0.2617994f );'''

SUN_READER = '''\t\t// #1513 a7c3f0 and a1f070 choose the active renderer's fields.
\t\tconst bool forward = Renderer::instance().pipelineType() ==
\t\t\tIRendererPipeline::TYPE_FORWARD_SHADING;
\t\tconst BW::string mode = forward ? "forward/" : "deferred/";
\t\tsunAngle_ = dayNightSection->readFloat( mode + "angle",
\t\t\tdayNightSection->readFloat( "angle", 80.f ) );
\t\tsunAngleZ_ = dayNightSection->readFloat( mode + "angleZ",
\t\t\tdayNightSection->readFloat( "angleZ", 0.f ) );'''

LIGHT_KEYS = '''\t\t// First nonempty list wins: current pipeline, other pipeline, root.
\t\tdayNightSection->openSections( mode + "lightkey", lightKeys );
\t\tif (lightKeys.empty())
\t\t\tdayNightSection->openSections(
\t\t\t\tforward ? "deferred/lightkey" : "forward/lightkey", lightKeys );
\t\tif (lightKeys.empty())
\t\t\tdayNightSection->openSections( "lightkey", lightKeys );'''


def replace(source, old, new, label):
    if new in source:
        return source
    if source.count(old) != 1:
        raise ValueError('Pinned time-of-day anchor changed: ' + label)
    return source.replace(old, new, 1)


def patch(root):
    folder = Path(root) / 'lib/romp'
    header = folder / 'time_of_day.hpp'
    source = header.read_text()
    source = replace(source, '\tfloat\t\t\t\t\t\tsunAngle_;',
                     '\tfloat\t\t\t\t\t\tsunAngle_;\n'
                     '\tfloat\t\t\t\t\t\tsunAngleZ_;', 'sun angle storage')
    header.write_text(source)
    implementation = folder / 'time_of_day.cpp'
    source = implementation.read_text()
    source = replace(source, '#include "moo/render_context.hpp"',
                     '#include "moo/render_context.hpp"\n#include "moo/renderer.hpp"',
                     'renderer owner')
    source = replace(source, '\tsunAngle_( 80.f ),',
                     '\tsunAngle_( 80.f ),\n\tsunAngleZ_( 0.f ),', 'sun constructor')
    source = replace(source,
        '\tnow_.sunTransform.setRotateX( DEG_TO_RAD( - sunAngle_ ) );\n'
        '\tnow_.sunTransform.preRotateY( 2.f * MATH_PI - DEG_TO_RAD( ( time_ / HOURS_IN_DAY ) * 360 ) );',
        SUN_TRANSFORM, 'sun transform')
    source = replace(source,
        '\tnow_.useMoonColour(moonPos.y > sunPos.y);',
        '\t// The original #1513 lighting owner has one sun transform.\n'
        '\tnow_.useMoonColour(false);', 'original directional light owner')
    # Remove the now-unused selection vectors, retaining existing public moon
    # presentation fields without allowing them to replace the original sun.
    source = source.replace('\tVector3 localDir( 0, 0, -1 );\n'
        '\tVector3 sunPos( now_.sunTransform.applyPoint( localDir ) );\n'
        '\tVector3 moonPos( now_.moonTransform.applyPoint( localDir ) );\n', '')
    source = replace(source,
        '\t\tsunAngle_ = dayNightSection->readFloat( "angle", 80.f );',
        SUN_READER, 'renderer sun angles')
    source = replace(source,
        '\t\tdayNightSection->openSections( "lightkey", lightKeys );',
        LIGHT_KEYS, 'renderer light keys')
    implementation.write_text(source)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch(parser.parse_args().engine_root)
