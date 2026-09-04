from pathlib import Path
from ._base_section import *



class Base_Binary_Section(Base_Section):
    def unp_to_dir(self, unp_dir: Path):
        with (unp_dir / f'{self.header}.bin').open('wb') as fw:
            fw.write(self._data)

    def from_dir(self, unp_dir: Path):
        binary_file = unp_dir / f'{self.header}.bin'
        self._exist = binary_file.is_file()
        if not self._exist:
            return
        with binary_file.open('rb') as fr:
            self._data = fr.read()
