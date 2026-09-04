from dataclasses import dataclass
from enum import IntFlag, auto

from .BWST import *
from .BSMA import *
from .WTau import *
from .BWAL import *
from .BWLC import *
from .SpTr import *
from .BWPs import *
from .BSMI import *
from .BWT2 import *
from .BSMO import *
from .BWCS import *
from .BWfr import *
from .WGSD import *
from .WTCP import *
from .UDOS import *
from .CENT import *
from .BWWa import *
from .BWSG import *
from .WGDE import *
from .BWSS import *
from .WSMI import *
from .WSMO import *
from .BWSV import *
from .WGDN import *
from .BSGD import *
from .BWEP import *
from .WGCO import *
from .WTbl import *
from .WGSH import *
from .BWS2 import *
from .BSG2 import *
from .WGMM import *
from .GOBJ import *
from .BWVL import *


class Realm(IntFlag):
    RU = auto()
    EU = auto()
    ALL = RU | EU


@dataclass(frozen=True)
class Version:
    version: tuple[int, ...]
    realm: Realm


@dataclass(init=False)
class CompiledSpaceSections:
    version: Version
    sections: list

    def __init__(self, version: str, sections: list):
        global g_all_sections_by_version

        self.version = version
        self.sections = sections
        g_all_sections_by_version[version] = self


g_all_sections_by_version: dict[Version, CompiledSpaceSections] = {}


# Tested with:
#  '0.9.12', # WoT init space.bin
#  '0.9.13'
CompiledSpaceSections(
    Version((0, 9, 12), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_12,
        BWT2_Section_0_9_12,
        BWSS_Section_0_9_12,
        BSMI_Section_0_9_12,
        WSMI_Section_0_9_12,
        BSMO_Section_0_9_12,
        WSMO_Section_0_9_12,
        BSMA_Section_0_9_12,
        SpTr_Section_0_9_12,
        BWfr_Section_0_9_12,
        WGSD_Section_0_9_12,
        WTCP_Section_0_9_12,
        BWWa_Section_0_9_12,
        BWSV_Section_0_9_12,
        BWPs_Section_0_9_12,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_12,
        WGDN_Section_0_9_12,
        BWLC_Section_0_9_12,
        WTau_Section_0_9_12
    )
)


# Tested with:
# '0.9.14', '0.9.14.1'
CompiledSpaceSections(
    Version((0, 9, 14), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWT2_Section_0_9_14,
        BWSS_Section_0_9_12,
        BSMI_Section_0_9_12,
        WSMI_Section_0_9_12,
        BSMO_Section_0_9_12,
        WSMO_Section_0_9_12,
        BSMA_Section_0_9_12,
        SpTr_Section_0_9_12,
        BWfr_Section_0_9_12,
        WGSD_Section_0_9_12,
        WTCP_Section_0_9_12,
        BWWa_Section_0_9_12,
        BWSV_Section_0_9_12,
        BWPs_Section_0_9_12,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_12,
        WGDN_Section_0_9_12,
        BWLC_Section_0_9_12,
        WTau_Section_0_9_12
    )
)


# Tested with:
# '0.9.16'
CompiledSpaceSections(
    Version((0, 9, 15, 1), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWT2_Section_0_9_20,
        BWSS_Section_0_9_16,
        BSMI_Section_0_9_16,
        BSMO_Section_0_9_20,
        BSMA_Section_0_9_12,
        SpTr_Section_0_9_12,
        BWfr_Section_0_9_12,
        WGSD_Section_0_9_12,
        WTCP_Section_0_9_12,
        BWWa_Section_0_9_12,
        BWSV_Section_0_9_12,
        BWPs_Section_0_9_12,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_0_9_12,
        WTau_Section_0_9_12
    )
)


# Tested with:
# '0.9.17.0', '0.9.17.1',
# '0.9.20.0', '0.9.20.1.4'
# '0.9.21.0', '0.9.21.0.1', '0.9.21.0.2'
# '0.9.22.0', '0.9.22.0.1'
CompiledSpaceSections(
    Version((0, 9, 17, 0), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWT2_Section_0_9_20,
        BSMI_Section_0_9_20,
        BSMO_Section_0_9_20,
        BSMA_Section_0_9_12,
        SpTr_Section_0_9_20,
        BWfr_Section_0_9_20,
        WGSD_Section_0_9_12,
        WTCP_Section_0_9_20,
        BWWa_Section_0_9_12,
        BWEP_Section_0_9_20,
        WGCO_Section_0_9_20,
        BWPs_Section_0_9_12,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_0_9_12,
        WTau_Section_0_9_12,
        WTbl_Section_0_9_20,
        WGSH_Section_0_9_20
    )
)


# Tested with:
# '1.0.0', '1.0.0.2', '1.0.0.3'
CompiledSpaceSections(
    Version((1, 0, 0), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWT2_Section_1_0_0,
        BSMI_Section_1_0_0,
        BSMO_Section_1_0_0,
        BSMA_Section_1_0_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_0_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_0_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_0,
        BWPs_Section_0_9_12,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_0_0,
        WTau_Section_0_9_12,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0
    )
)


# Tested with:
# '1.0.1.0', '1.0.1.1',
# '1.0.2.0', '1.0.2.1'
CompiledSpaceSections(
    Version((1, 0, 1, 0), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWT2_Section_1_0_1,
        BSMI_Section_1_0_0,
        BSMO_Section_1_0_0,
        BSMA_Section_1_0_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_0_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_0_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_0_9_12,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_0_1,
        WTau_Section_0_9_12,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0
    )
)


# Tested with:
# '1.1.0', '1.1.0.1'
CompiledSpaceSections(
    Version((1, 1, 0), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_1_0,
        BSMI_Section_1_0_0,
        BSMO_Section_1_0_0,
        BSMA_Section_1_0_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_0_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_0_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_0_9_12,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_0_1,
        WTau_Section_0_9_12,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0
    )
)


# Tested with:
# '1.2.0', '1.2.0.1',
# '1.3.0.0', '1.3.0.1'
CompiledSpaceSections(
    Version((1, 2, 0), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_1_0,
        BSMI_Section_1_2_0,
        BSMO_Section_1_2_0,
        BSMA_Section_1_0_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_0_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_0_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_0_9_12,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_0_1,
        WTau_Section_0_9_12,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0
    )
)


# Tested with:
# '1.4.0.0', '1.4.0.1',
# '1.4.1.0', '1.4.1.1'
CompiledSpaceSections(
    Version((1, 4, 0, 0), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_4_0,
        BSMI_Section_1_2_0,
        BSMO_Section_1_2_0,
        BSMA_Section_1_0_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_0_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_0_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_0_9_12,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_0_1,
        WTau_Section_0_9_12,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0,
        WGMM_Section_1_4_0
    )
)


# Tested with:
# '1.5.0.4'
CompiledSpaceSections(
    Version((1, 5, 0, 4), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_4_0,
        BSMI_Section_1_5_0,
        BSMO_Section_1_2_0,
        BSMA_Section_1_0_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_0_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_0_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_0_9_12,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_0_1,
        WTau_Section_0_9_12,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0,
        WGMM_Section_1_4_0
    )
)


# Tested with:
# '1.5.1.1', '1.5.1.3'
CompiledSpaceSections(
    Version((1, 5, 1, 1), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_4_0,
        BSMI_Section_1_5_0,
        BSMO_Section_1_2_0,
        BSMA_Section_1_0_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_0_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_0_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_0_9_12,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_0_1,
        WTau_Section_1_5_1,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0,
        WGMM_Section_1_4_0
    )
)


# Tested with:
# '1.6.0.0'
CompiledSpaceSections(
    Version((1, 6, 0, 0), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_4_0,
        BSMI_Section_1_5_0,
        BSMO_Section_1_2_0,
        BSMA_Section_1_6_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_0_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_0_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_1_6_0,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_6_0,
        WTau_Section_1_6_0,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0,
        WGMM_Section_1_4_0
    )
)


# Tested with:
# '1.6.1.0', '1.6.1.1', '1.6.1.2', '1.6.1.3'
CompiledSpaceSections(
    Version((1, 6, 1, 0), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_6_1,
        BSMI_Section_1_5_0,
        BSMO_Section_1_2_0,
        BSMA_Section_1_6_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_0_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_0_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_1_6_0,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_6_0,
        WTau_Section_1_6_0,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0,
        WGMM_Section_1_4_0
    )
)


# Tested with:
# '1.7.0.1', '1.7.0.2',
# '1.7.1.0', '1.7.1.1', '1.7.1.2',
# '1.8.0.0', '1.8.0.1',
# '1.10.1.4'
CompiledSpaceSections(
    Version((1, 7, 0, 1), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_6_1,
        BSMI_Section_1_5_0,
        BSMO_Section_1_2_0,
        BSMA_Section_1_6_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_0_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_0_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_1_6_0,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_7_0,
        WTau_Section_1_6_0,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0,
        WGMM_Section_1_4_0
    )
)


# Tested with:
# '1.11.0.0'
CompiledSpaceSections(
    Version((1, 11, 0, 0), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_6_1,
        BSMI_Section_1_5_0,
        BSMO_Section_1_2_0,
        BSMA_Section_1_6_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_0_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_0_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_1_6_0,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_11_0,
        WTau_Section_1_6_0,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0,
        WGMM_Section_1_4_0
    )
)


# Tested with:
# '1.12.1.0', '1.14.1.2',
# '1.16.0.0'
CompiledSpaceSections(
    Version((1, 12, 1, 0), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_6_1,
        BSMI_Section_1_12_1,
        BSMO_Section_1_2_0,
        BSMA_Section_1_6_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_0_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_0_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_1_6_0,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_11_0,
        WTau_Section_1_6_0,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0,
        WGMM_Section_1_4_0,
        GOBJ_Section_1_12_1
    )
)


# Tested with:
# '1.15.0.0'
CompiledSpaceSections(
    Version((1, 15, 0, 0), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_6_1,
        BSMI_Section_1_12_1,
        BSMO_Section_1_2_0,
        BSMA_Section_1_6_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_0_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_0_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_1_6_0,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_15_0,
        BWVL_Section_1_15_0,
        WTau_Section_1_6_0,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0,
        WGMM_Section_1_4_0,
        GOBJ_Section_1_12_1
    )
)


# Tested with:
# '1.16.1.0',
# '1.17.0.0'
CompiledSpaceSections(
    Version((1, 16, 1, 0), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_6_1,
        BSMI_Section_1_16_1,
        BSMO_Section_1_16_1,
        BSMA_Section_1_6_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_0_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_0_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_1_6_0,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_15_0,
        BWVL_Section_1_15_0,
        WTau_Section_1_6_0,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0,
        WGMM_Section_1_4_0,
        GOBJ_Section_1_12_1
    )
)


# Tested with:
# '1.17.1.0',
# '1.18.0.0',
# '1.19.0.2'
CompiledSpaceSections(
    Version((1, 17, 1, 0), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_6_1,
        BSMI_Section_1_16_1,
        BSMO_Section_1_16_1,
        BSMA_Section_1_6_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_0_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_0_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_1_6_0,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_15_0,
        BWVL_Section_1_15_0,
        WTau_Section_1_6_0,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0,
        WGMM_Section_1_4_0,
        GOBJ_Section_1_17_1
    )
)


# Tested with:
# '1.21.0.0',
# '1.21.1.0'
CompiledSpaceSections(
    Version((1, 21, 0, 0), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_6_1,
        BSMI_Section_1_16_1,
        BSMO_Section_1_16_1,
        BSMA_Section_1_6_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_0_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_0_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_1_6_0,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_15_0,
        BWVL_Section_1_15_0,
        WTau_Section_1_6_0,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0,
        WGMM_Section_1_4_0,
        GOBJ_Section_1_21_1
    )
)


# Tested with:
# '1.22.0.0',
CompiledSpaceSections(
    Version((1, 22, 0, 0), Realm.ALL),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_6_1,
        BSMI_Section_1_16_1,
        BSMO_Section_1_16_1,
        BSMA_Section_1_6_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_22_0,
        WTCP_Section_0_9_20,
        BWWa_Section_1_22_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_1_6_0,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_15_0,
        BWVL_Section_1_15_0,
        WTau_Section_1_6_0,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0,
        WGMM_Section_1_4_0,
        GOBJ_Section_1_21_1
    )
)


# Tested with:
# '1.23.0.0',
# '1.30.0.0_RU',
CompiledSpaceSections(
    Version((1, 23, 0, 0), Realm.RU),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_6_1,
        BSMI_Section_1_16_1,
        BSMO_Section_1_16_1,
        BSMA_Section_1_6_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_22_0,
        WTCP_Section_1_23_0,
        BWWa_Section_1_22_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_1_6_0,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_15_0,
        BWVL_Section_1_15_0,
        WTau_Section_1_6_0,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0,
        WGMM_Section_1_4_0,
        GOBJ_Section_1_21_1
    )
)


# Tested with:
# '1.31.0.0_RU' # actual
CompiledSpaceSections(
    Version((1, 31, 0, 0), Realm.RU),
    (
        BWST_Section_0_9_12,
        BWAL_Section_0_9_12,
        BWCS_Section_0_9_12,
        BWSG_Section_0_9_14,
        BSGD_Section_0_9_14,
        BWS2_Section_1_1_0,
        BSG2_Section_1_1_0,
        BWT2_Section_1_6_1,
        BSMI_Section_1_31_0_RU,
        BSMO_Section_1_16_1,
        BSMA_Section_1_6_0,
        SpTr_Section_1_0_0,
        WGSD_Section_1_22_0,
        WTCP_Section_1_23_0,
        BWWa_Section_1_22_0,
        BWEP_Section_1_0_0,
        WGCO_Section_1_0_1,
        BWPs_Section_1_6_0,
        CENT_Section_0_9_12,
        UDOS_Section_0_9_12,
        WGDE_Section_0_9_20,
        BWLC_Section_1_31_0_RU,
        BWVL_Section_1_15_0,
        WTau_Section_1_6_0,
        WTbl_Section_0_9_20,
        WGSH_Section_1_0_0,
        WGMM_Section_1_4_0,
        GOBJ_Section_1_31_0_RU
    )
)
