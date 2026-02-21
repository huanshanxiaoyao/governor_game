"""游戏核心业务逻辑"""

from .constants import (
    MAX_YIELD_PER_MU,
    ANNUAL_CONSUMPTION,
    BASE_GROWTH_RATE,
    GROWTH_RATE_CLAMP,
    MEDICAL_COST_PER_THOUSAND,
    MEDICAL_NAMES,
    calculate_medical_cost,
    CORVEE_PER_CAPITA,
    GENTRY_POP_RATIO_COEFF,
    COUNTY_TYPES,
    generate_governor_profile,
)
from .county import CountyService
from .investment import InvestmentService
from .settlement import SettlementService
from .ai_governor import AIGovernorService
from .neighbor import NeighborService

__all__ = [
    "MAX_YIELD_PER_MU",
    "ANNUAL_CONSUMPTION",
    "BASE_GROWTH_RATE",
    "GROWTH_RATE_CLAMP",
    "MEDICAL_COST_PER_THOUSAND",
    "MEDICAL_NAMES",
    "calculate_medical_cost",
    "CORVEE_PER_CAPITA",
    "GENTRY_POP_RATIO_COEFF",
    "COUNTY_TYPES",
    "generate_governor_profile",
    "CountyService",
    "InvestmentService",
    "SettlementService",
    "AIGovernorService",
    "NeighborService",
]
