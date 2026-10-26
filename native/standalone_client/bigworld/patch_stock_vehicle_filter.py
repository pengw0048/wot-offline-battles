#!/usr/bin/env python3
"""Restore the stock filter's weak entity-owner association in public BigWorld."""

import argparse
from pathlib import Path


def patch_engine(root):
    path = Path(root) / 'client' / 'py_entity.cpp'
    source = path.read_text(encoding='utf-8')
    replacements = [(
        '#include "py_entity.hpp"',
        '#include "py_entity.hpp"\n#include "stock_vehicle_filter.hpp"'), (
        '\tpEntity_->pFilter( pPyFilter_->getFilter() );',
        '\tbindStockVehicleFilterOwner( pPyFilter_.get(), this );\n'
        '\tpEntity_->pFilter( pPyFilter_->getFilter() );'), (
        '\t\tpPyFilter_->isAttached( false );',
        '\t\tbindStockVehicleFilterOwner( pPyFilter_.get(), NULL );\n'
        '\t\tpPyFilter_->isAttached( false );')]
    for before, after in replacements:
        if source.count(after) == 1:
            continue
        if source.count(before) != 1:
            raise ValueError('Unrecognized pinned PyEntity filter seam')
        source = source.replace(before, after)
    path.write_text(source, encoding='utf-8')
    print('Stock filter weak owner association installed in PyEntity')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch_engine(parser.parse_args().engine_root)
