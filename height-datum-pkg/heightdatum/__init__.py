"""heightdatum —— 1985 国家高程基准与 EGM2008 高程互转工具包。

核心关系（正常高/正高系统）：
    h(椭球高) = H_1985 + ζ_1981(高程异常，1985 似大地水准面)
    h(椭球高) = H_EGM2008 + N_EGM2008(大地水准面高)
=>  H_EGM2008 = H_1985 + (ζ_1985 − N_EGM2008) = H_1985 + δ

本包提供两条可追溯路线：
  1. 文献常数/区域格网路线：δ 取文献系统差（默认 +0.32 m，带不确定度），
     或用户提供 CQG2000/省级精化似大地水准面格网后按 δ=ζ_grid−N_grid 逐点计算。
  2. GNSS 锚定路线（推荐用于业务定版）：实测站址椭球高 h，H_EGM2008 = h − N，
     完全绕开 1985 基准，同时可反算本地 δ 作为对文献值的验证。
"""

from .geoid_grid import GeoidGrid
from .convert import (
    egm2008_from_1985,
    h1985_from_egm2008,
    egm2008_from_gnss,
    h1985_from_gnss,
    uncertainty_budget,
)
from .offsets import DATUM_OFFSETS, DEFAULT_OFFSET_M, DEFAULT_OFFSET_SIGMA_M

__version__ = "1.0.0"
__all__ = [
    "GeoidGrid",
    "egm2008_from_1985",
    "h1985_from_egm2008",
    "egm2008_from_gnss",
    "h1985_from_gnss",
    "uncertainty_budget",
    "DATUM_OFFSETS",
    "DEFAULT_OFFSET_M",
    "DEFAULT_OFFSET_SIGMA_M",
]
