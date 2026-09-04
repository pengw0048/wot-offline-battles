from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntFlag
from itertools import groupby
from pathlib import Path
from typing import Any, Generator, Self
from collections import defaultdict
from zipfile import ZipFile
from ctypes import c_int16

# local imports
from .versioning import WoTVersion
from . import CompiledSpace
from .xml_utils.XmlUnpacker import XmlUnpacker


class VisbilityFlags(IntFlag):
    CAPTURE_THE_FLAG = 1


@dataclass
class UniversalResMgr:
    """Tiny version of resource manager used for universal space"""

    pkgs: dict[str, tuple[ZipFile, str]]
    unp_dir: Path

    def exists(self, name: str) -> bool:
        return (name.lower() in self.pkgs) or (self.unp_dir / name).is_file()

    @contextmanager
    def open(self, name: str) -> Generator:
        if item := self.pkgs.get(name.lower()):
            try:
                f = item[0].open(item[1], 'r')
                yield f
            finally:
                f.close()
        elif (self.unp_dir / name).is_file():
            try:
                f = (self.unp_dir / name).open('rb')
                yield f
            finally:
                f.close()
        return None

    def open_xml(self, name: str):
        with self.open(name) as f:
            return XmlUnpacker().read(f)


@dataclass
class UniversalMesh:
    rset_id: int
    pg_idx: int
    fx_name: str
    props: dict[str, Any]


@dataclass
class UniversalModelInstances:
    transforms: list[list[float]]
    meshes: list[UniversalMesh]


@dataclass
class UniversalModel:
    prims_name: str
    verts_dataname: str
    prims_dataname: str
    instances: list[UniversalModelInstances]


@dataclass
class UniversalTerrain:
    chunk_size: float
    bounds: tuple[int, int, int, int]
    global_map: str

    @property
    def num_chunks(self) -> tuple[int, int]:
        return (self.bounds[1] - self.bounds[0] + 1, self.bounds[3] - self.bounds[2] + 1)


@dataclass
class UniversalSpace:
    terrain: UniversalTerrain
    models: list[UniversalModel]

    @classmethod
    def from_space_dir(cls, space_dir: Path, wot_version: str, wot_realm: str, res_mgr: UniversalResMgr) -> Self | None:
        if WoTVersion(wot_version).has_compiled_space:
            space_bin_path = space_dir / 'space.bin'
            if not space_bin_path.is_file():
                return None
            with space_bin_path.open('rb') as fr:
                comp_space = CompiledSpace(fr, wot_version, wot_realm, ['BSMO', 'BSMI', 'BSMA', 'BWST', 'BWT2', 'BWSV'])
                return cls._from_compiled_space(comp_space)
        else:
            return cls._from_uncompiled_space(space_dir, res_mgr)

    @classmethod
    def _from_uncompiled_space(cls, space_dir: Path, res_mgr: UniversalResMgr) -> Self:
        terrain = cls.__load_terrain_from_uncompiled_space(space_dir, res_mgr)
        models = cls.__load_models_from_uncompiled_space(space_dir, res_mgr, terrain)
        return cls(terrain, models)

    @classmethod
    def _from_compiled_space(cls, comp_space: CompiledSpace) -> Self:
        terrain = cls.__load_terrain_from_compiled_space(comp_space)
        models = cls.__load_models_from_compiled_space(comp_space)
        return cls(terrain, models)

    @staticmethod
    def __load_terrain_from_uncompiled_space(space_dir: Path, res_mgr: UniversalResMgr) -> UniversalTerrain:
        space_settings_path = space_dir / 'space.settings'
        with space_settings_path.open('rb') as f:
            space_settings_xml = XmlUnpacker().read(f)

        chunk_size = 100.0
        minX = int(space_settings_xml.findtext('bounds/minX').strip())
        maxX = int(space_settings_xml.findtext('bounds/maxX').strip())
        minY = int(space_settings_xml.findtext('bounds/minY').strip())
        maxY = int(space_settings_xml.findtext('bounds/maxY').strip())
        global_map = None

        return UniversalTerrain(chunk_size, (minX, maxX, minY, maxY), global_map)

    @staticmethod
    def __load_terrain_from_compiled_space(comp_space: CompiledSpace) -> UniversalTerrain:
        bwt2 = comp_space.sections['BWT2']._data
        bwst = comp_space.sections['BWST']

        chunk_size = bwt2['settings']['chunk_size']
        assert chunk_size == 100.0, chunk_size

        bounds = bwt2['settings']['bounds']
        if 'global_map_fnv' in bwt2['settings']:
            global_map = bwst.get(bwt2['settings']['global_map_fnv'])
        else:
            global_map = None

        return UniversalTerrain(chunk_size, bounds, global_map)

    @staticmethod
    def __load_models_from_uncompiled_space(
        space_dir: Path, res_mgr: UniversalResMgr, terrain: UniversalTerrain
    ) -> list[UniversalModel]:
        model_path_to_transform = defaultdict(list)
        for chunk_name in space_dir.glob('*.chunk'):
            chunk_pos_x, chunk_pos_y = chunk_name.name[0:4], chunk_name.name[4:8]
            chunk_pos_x = c_int16(int(chunk_pos_x, 16)).value
            chunk_pos_y = c_int16(int(chunk_pos_y, 16)).value

            with chunk_name.open('rb') as f:
                chunk = XmlUnpacker().read(f)
                for chunk_model in chunk.iterfind('model'):
                    visibility_mask = int(chunk_model.findtext('visibilityMask', '4294967295').strip())
                    if not visibility_mask & VisbilityFlags.CAPTURE_THE_FLAG:
                        continue

                    model_path = chunk_model.findtext('resource').strip().lower()
                    transform_row0 = list(map(float, chunk_model.findtext('transform/row0').strip().split()))
                    transform_row1 = list(map(float, chunk_model.findtext('transform/row1').strip().split()))
                    transform_row2 = list(map(float, chunk_model.findtext('transform/row2').strip().split()))
                    transform_row3 = list(map(float, chunk_model.findtext('transform/row3').strip().split()))

                    # TODO: check this
                    transform_row3[0] += terrain.chunk_size * chunk_pos_x
                    transform_row3[2] += terrain.chunk_size * chunk_pos_y

                    model_path_to_transform[model_path].append(
                        [*transform_row0, 0.0, *transform_row1, 0.0, *transform_row2, 0.0, *transform_row3, 0.0]
                    )

        models = []
        for model_path, transforms in model_path_to_transform.items():
            # TODO: parse nodelessVisual from .model
            # if res_mgr.exists(model_path):
            #     model_xml = res_mgr.open_xml(model_path)

            visual_name = model_path.replace('.model', '.visual')
            if not res_mgr.exists(visual_name):
                continue

            visual_xml = res_mgr.open_xml(visual_name)
            prims_name = visual_xml.findtext('primitivesName', model_path.replace('.model', '')) + '.primitives'
            for renderSet in visual_xml.iterfind('renderSet'):
                verts_dataname = renderSet.findtext('geometry/vertices')
                prims_dataname = renderSet.findtext('geometry/primitive')

                meshes = []
                for pg in renderSet.iterfind('geometry/primitiveGroup'):
                    if not pg.findtext('material/fx'):
                        continue
                    if not pg.findtext('material/identifier'):
                        continue
                    identifier = pg.findtext('material/identifier').strip()
                    if identifier.startswith('d_'):
                        # destroyed, skip
                        continue
                    fx_name = pg.findtext('material/fx').strip()
                    props = {}
                    for prop_elem in pg.iterfind('material/property'):
                        if prop_elem.text.strip() == 'diffuseMap':
                            props['diffuseMap'] = prop_elem.findtext('Texture').strip()
                    meshes.append(UniversalMesh(0, int(pg.text), fx_name, props))

                models.append(
                    UniversalModel(
                        prims_name, verts_dataname, prims_dataname, [UniversalModelInstances(transforms, meshes)]
                    )
                )

        return models

    @staticmethod
    def __load_models_from_compiled_space(comp_space: CompiledSpace) -> list[UniversalModel]:
        bsmo = comp_space.sections['BSMO']._data
        bsmi = comp_space.sections['BSMI']
        bsma = comp_space.sections['BSMA']._data
        bwst = comp_space.sections['BWST']

        if 'BWSV' in comp_space.sections:
            visibility_masks = comp_space.sections['BWSV']._data['visibility_masks']
        else:
            visibility_masks = bsmi._data['visibility_masks']

        models_id_to_transform: dict[int, list[list[float]]] = defaultdict(list)
        for i, model_id in enumerate(bsmi.model_ids()):
            if not visibility_masks[i] & VisbilityFlags.CAPTURE_THE_FLAG:
                continue
            models_id_to_transform[model_id].append(bsmi._data['transforms'][i])

        grouped_render_sets = defaultdict(list)

        # grouping by prims_name
        for i in models_id_to_transform:
            lod0_id = bsmo['models_loddings'][i]['lod_begin']
            render_set_begin = bsmo['lod_renders'][lod0_id]['render_set_begin']
            render_set_end = bsmo['lod_renders'][lod0_id]['render_set_end']

            for rset_id in range(render_set_begin, render_set_end + 1):
                rset = bsmo['renders'][rset_id]
                if bsma['materials'][rset['material_index']]['key_fx'] == -1:
                    # there is no shader
                    continue

                verts_name = bwst.get(rset['verts_name_fnv'])
                verts_name, verts_dataname = (
                    verts_name[: verts_name.rindex('/')],
                    verts_name[verts_name.rindex('/') + 1 :],
                )
                verts_name = verts_name.replace('.primitives', '.primitives_processed')

                prims_name = bwst.get(rset['prims_name_fnv'])
                prims_name, prims_dataname = (
                    prims_name[: prims_name.rindex('/')],
                    prims_name[prims_name.rindex('/') + 1 :],
                )
                prims_name = prims_name.replace('.primitives', '.primitives_processed')

                assert verts_name == prims_name
                grouped_render_sets[(prims_name, verts_dataname, prims_dataname)].append((i, rset_id))

        models = []

        for (prims_name, verts_dataname, prims_dataname), render_set_ids in grouped_render_sets.items():
            instances = []

            for k, g in groupby(render_set_ids, key=lambda x: x[0]):
                meshes = []
                for _, rset_id in g:
                    rset = bsmo['renders'][rset_id]
                    pg_idx = rset['primitive_index']
                    material_index = rset['material_index']

                    mat = bsma['materials'][material_index]

                    props = {}
                    for i in range(mat['key_from'], mat['key_to'] + 1):
                        prop = bsma['props'][i]
                        prop_name = bwst.get(prop['prop_fnv'])
                        match prop['value_type']:
                            case 5:
                                props[prop_name] = bsma['vectors'][prop['value']]
                            case 6:
                                props[prop_name] = bwst.get(prop['value'])
                            case _:
                                props[prop_name] = prop['value']

                    fx_name = bwst.get(bsma['fx'][mat['key_fx']])

                    meshes.append(UniversalMesh(rset_id, pg_idx, fx_name, props))
                instances.append(UniversalModelInstances(models_id_to_transform[k], meshes))

            models.append(UniversalModel(prims_name, verts_dataname, prims_dataname, instances))

        return models
