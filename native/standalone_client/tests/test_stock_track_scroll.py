"""Compile the actual track residual producer with reviewed #1513 receipts.

This checks the scalar visual kernel. It does not prove material dressing,
the public engine scheduler, or Windows rendering.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


class StockTrackScrollTest(unittest.TestCase):
    def test_original_residual_receipts(self):
        source = (Path(__file__).resolve().parents[1] /
                  'bigworld/stock_vehicle_visuals.cpp').read_text()
        begin = source.index('struct TrackScrollChannel\n')
        end = source.index('\n};', begin) + len('\n};')
        program = '#include <cassert>\n#include <cmath>\n' + source[begin:end] + r'''
int main() {
    TrackScrollChannel channel;
    channel.external = 6.f;
    channel.update(2.f, 0.05f);
    assert(channel.residual == 4.f && channel.external == 3.f);
    channel.update(2.f, 0.05f);
    assert(channel.residual == 1.f && channel.external == 1.5f);

    // A forward hull overtaking the external visual scroll removes the
    // residual. Reversing the track preserves a signed difference instead.
    channel.external = 6.f;
    channel.residual = 2.f;
    channel.update(8.f, 0.05f);
    assert(channel.residual == 0.f && channel.external == 3.f);
    channel.external = -6.f;
    channel.residual = 2.f;
    channel.update(2.f, 0.05f);
    assert(channel.residual == -8.f && channel.external == -3.f);

    // The original threshold applies to pending scroll, not hull speed.
    channel.external = 0.1f;
    channel.residual = 3.f;
    channel.update(0.f, 0.05f);
    assert(channel.residual == 0.f && channel.external == 0x1.999998p-5f);
    channel.external = 6.f;
    channel.residual = 0.f;
    channel.update(0.f, 0.2f);
    assert(channel.residual == 6.f && channel.external == 0.f);
}
'''
        with tempfile.TemporaryDirectory(prefix='stock-track-scroll-') as directory:
            path = Path(directory)
            (path / 'probe.cpp').write_text(program)
            subprocess.run([
                os.environ.get('CXX', 'clang++'), '-std=c++17', '-Wall',
                '-Wextra', '-Werror', '-ffp-contract=off', str(path / 'probe.cpp'),
                '-o', str(path / 'probe')], check=True)
            subprocess.run([str(path / 'probe')], check=True)


if __name__ == '__main__':
    unittest.main()
