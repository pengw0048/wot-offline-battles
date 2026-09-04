""" Versioning - as in res_mods/ """

from dataclasses import dataclass

from .sections import g_all_sections_by_version, CompiledSpaceSections, Version, Realm


@dataclass(init=False)
class WoTVersion:
    ver_str: str
    ver: Version

    def __init__(self, ver_str: str, realm_str: str = 'RU'):
        self.ver_str = ver_str

        # remove extra info
        if 'Supertest v.ST ' in ver_str:
            ver_str = ver_str.replace('Supertest v.ST ', 'v.')
        elif ' Common Test' in ver_str:
            ver_str = ver_str.replace(' Common Test', '')
        if 'v.' in ver_str:
            ver_str = ver_str.split('v.')[1]
        if '#' in ver_str:
            ver_str = ver_str.split('#')[0]
        ver_str = ver_str.strip()
        ver_tuple = tuple(map(int, ver_str.split('.')))

        if realm_str.upper() == 'RU':
            realm = Realm.RU
        elif realm_str.upper() == 'EU':
            realm = Realm.EU
        else:
            assert False, f'Unknown realm {realm_str}'

        self.ver = Version(ver_tuple, realm)

    @property
    def has_compiled_space(self) -> bool:
        return self.ver.version >= (0, 9, 12)

    def get_nearest(self) -> Version | None:
        for it in reversed(g_all_sections_by_version):
            if self.ver.realm & it.realm and self.ver.version >= it.version:
                return it
        return None

    def get_sections(self) -> CompiledSpaceSections | None:
        if nearest_ver := self.get_nearest():
            return g_all_sections_by_version[nearest_ver]
        return None
