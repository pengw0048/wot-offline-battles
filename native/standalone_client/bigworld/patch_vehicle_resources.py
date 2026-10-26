#!/usr/bin/env python3
"""Dispatch stock assembler requests before the public string-only loader."""

import argparse
from pathlib import Path


BEFORE = '''\t//The new PyResourceRefs object is held by the background loader and
'''
DISPATCH = '''\t// Stock CompoundAssembler resources are native objects. Dispatch before
\t// the public loader drops non-string resources. This seam is installed
\t// before Script::init/EntityType imports, with no late Python monkeypatch.
\tfor (Py_ssize_t index = 0; index < PySequence_Size( resourceList ); ++index)
\t{
\t\tPyObjectPtr item( PySequence_GetItem( resourceList, index ),
\t\t\tPyObjectPtr::STEAL_REFERENCE );
\t\tif (!item)
\t\t\treturn NULL;
\t\tif (PyString_Check( item.get() ))
\t\t\tcontinue;
\t\tPyObject * module = PyImport_AddModule( "BigWorld" );
\t\tif (!module)
\t\t\treturn NULL;
\t\tPyObjectPtr predicate( PyObject_GetAttrString( module,
\t\t\t"isStockVehicleResource" ), PyObjectPtr::STEAL_REFERENCE );
\t\tif (!predicate)
\t\t\treturn NULL;
\t\tPyObjectPtr selected( PyObject_CallFunctionObjArgs( predicate.get(),
\t\t\titem.get(), NULL ), PyObjectPtr::STEAL_REFERENCE );
\t\tif (!selected)
\t\t\treturn NULL;
\t\tconst int isStockResource = PyObject_IsTrue( selected.get() );
\t\tif (isStockResource < 0)
\t\t\treturn NULL;
\t\tif (isStockResource)
\t\t{
\t\t\tPyObjectPtr handler( PyObject_GetAttrString( module,
\t\t\t\t"loadStockVehicleResourcesBG" ), PyObjectPtr::STEAL_REFERENCE );
\t\t\tif (!handler)
\t\t\t\treturn NULL;
\t\t\treturn PyObject_CallObject( handler.get(), args );
\t\t}
\t}

'''


def patch_engine(root):
    root = Path(root)
    patches = {
        root / 'lib' / 'romp' / 'py_resource_refs.cpp': [(BEFORE, DISPATCH + BEFORE)],
        root / 'client' / 'entity.hpp': [(
            '\tPrerequisitesOrderPtr\t\tprerequisitesOrder_;',
            '\tPrerequisitesOrderPtr\t\tprerequisitesOrder_;\n'
            '\tPyObjectPtr stockPrerequisitesRequest_;')],
        root / 'client' / 'entity.cpp': [(
            '#include "entity.hpp"',
            '#include "entity.hpp"\n#include "vehicle_appearance.hpp"'), (
            'Entity::~Entity()\n{\n\tBW_GUARD;\n}',
            'Entity::~Entity()\n{\n\tBW_GUARD;\n'
            '\tcancelStockVehiclePrerequisites( stockPrerequisitesRequest_.get() );\n}'), (
            '\t// if there\'s one in progress see if it\'s done\n',
            '\t// Native object-resource prerequisites use the same main-thread\n'
            '\t// completion path as loadResourceListBG.\n'
            '\tif (stockPrerequisitesRequest_)\n\t{\n'
            '\t\tif (!pollStockVehiclePrerequisites( stockPrerequisitesRequest_.get(), resourceRefs_ ))\n'
            '\t\t\treturn false;\n'
            '\t\tstockPrerequisitesRequest_ = NULL;\n'
            '\t\tloadingPrerequisites_ = false;\n'
            '\t\tcondition_.release();\n\t\treturn true;\n\t}\n\n'
            '\t// if there\'s one in progress see if it\'s done\n'), (
            '\t// and find out who is there\n',
            '\tif (beginStockVehiclePrerequisites( res.get(), stockPrerequisitesRequest_ ))\n'
            '\t{\n\t\tloadingPrerequisites_ = true;\n\t\treturn false;\n\t}\n\n'
            '\t// and find out who is there\n'), (
            '\tMF_ASSERT( prerequisitesOrder_ != NULL );\n\n\tthis->checkPrerequisites();',
            '\tif (stockPrerequisitesRequest_)\n\t{\n'
            '\t\tcancelStockVehiclePrerequisites( stockPrerequisitesRequest_.get() );\n'
            '\t\tstockPrerequisitesRequest_ = NULL;\n'
            '\t\tloadingPrerequisites_ = false;\n\t\treturn;\n\t}\n\n'
            '\tMF_ASSERT( prerequisitesOrder_ != NULL );\n\n\tthis->checkPrerequisites();')],
    }
    updates = []
    for path, replacements in patches.items():
        source = path.read_text(encoding='utf-8')
        for before, after in replacements:
            if source.count(after) == 1:
                continue
            if source.count(before) != 1:
                raise ValueError('Unrecognized pinned resource-loader seam: ' + str(path))
            source = source.replace(before, after)
        updates.append((path, source))
    for path, source in updates:
        path.write_text(source, encoding='utf-8')
    print('Stock object-resource dispatch installed in loader and entity prerequisites')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch_engine(parser.parse_args().engine_root)
