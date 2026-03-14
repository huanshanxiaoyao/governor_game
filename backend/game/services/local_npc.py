"""村庄级地主/村民代表的人设分配与姓名生成。"""

import copy
import random

from ..agent_defs import (
    GENTRY_GIVEN_NAMES,
    GENTRY_PERSONAS,
    VILLAGER_GIVEN_NAMES,
    VILLAGER_PERSONAS,
)


GENTRY_PERSONA_BY_ID = {p["persona_id"]: p for p in GENTRY_PERSONAS}
VILLAGER_PERSONA_BY_ID = {p["persona_id"]: p for p in VILLAGER_PERSONAS}


def surname_from_village(village_name: str) -> str:
    """用村名首字作为本村人物姓氏。"""
    if not village_name:
        return "赵"
    return village_name[0]


def _sample_personas(personas, count):
    if count <= len(personas):
        return random.sample(personas, count)

    picked = list(personas)
    while len(picked) < count:
        picked.append(random.choice(personas))
    random.shuffle(picked)
    return picked


def _generate_unique_name(surname, given_pool, used_names):
    candidates = [surname + given for given in given_pool if surname + given not in used_names]
    if candidates:
        name = random.choice(candidates)
        used_names.add(name)
        return name

    base = surname + random.choice(given_pool)
    suffix = 2
    name = f"{base}{suffix}"
    while name in used_names:
        suffix += 1
        name = f"{base}{suffix}"
    used_names.add(name)
    return name


def ensure_county_local_cast(county, force=False):
    """确保每个村都有随机分配的地主/村民代表 persona 与姓名。"""
    villages = county.get("villages") or []
    if not villages:
        return False

    required_fields = (
        "gentry_persona_id",
        "gentry_name",
        "villager_persona_id",
        "villager_name",
        "gentry_gender",
        "villager_gender",
    )
    if not force and all(all(v.get(field) for field in required_fields) for v in villages):
        return False

    gentry_personas = _sample_personas(GENTRY_PERSONAS, len(villages))
    villager_personas = _sample_personas(VILLAGER_PERSONAS, len(villages))
    used_names = set()

    for idx, village in enumerate(villages):
        surname = surname_from_village(village.get("name", ""))
        gentry_persona = gentry_personas[idx]
        villager_persona = villager_personas[idx]

        village["gentry_persona_id"] = gentry_persona["persona_id"]
        village["villager_persona_id"] = villager_persona["persona_id"]
        village["gentry_name"] = _generate_unique_name(
            surname, GENTRY_GIVEN_NAMES, used_names,
        )
        village["villager_name"] = _generate_unique_name(
            surname, VILLAGER_GIVEN_NAMES, used_names,
        )
        village["gentry_gender"] = "male"
        village["villager_gender"] = "male"

    return True


def _render_string(value, *, name, village_name, surname):
    if not isinstance(value, str):
        return value
    return value.format(name=name, village_name=village_name, surname=surname)


def _render_list(values, *, name, village_name, surname):
    return [
        _render_string(v, name=name, village_name=village_name, surname=surname)
        for v in values
    ]


def _build_agent_definition(village, persona, *, name_field):
    village_name = village.get("name", "")
    name = village.get(name_field, "")
    surname = surname_from_village(village_name)
    attrs = copy.deepcopy(persona["attributes"])

    attrs["persona_id"] = persona["persona_id"]
    attrs["village_name"] = village_name
    attrs["gender"] = "male"
    attrs["bio"] = _render_string(
        attrs.get("bio", ""), name=name, village_name=village_name, surname=surname,
    )
    attrs["backstory"] = _render_string(
        attrs.get("backstory", ""), name=name, village_name=village_name, surname=surname,
    )
    attrs["goals"] = _render_list(
        attrs.get("goals", []), name=name, village_name=village_name, surname=surname,
    )

    return {
        "name": name,
        "role": persona["role"],
        "role_title": persona["role_title"],
        "tier": persona["tier"],
        "attributes": attrs,
    }


def build_county_local_agent_definitions(county):
    """从 county_data 中构造本县地主/村民代表 Agent 定义。"""
    ensure_county_local_cast(county)

    definitions = []
    for village in county.get("villages", []):
        gentry_persona = GENTRY_PERSONA_BY_ID[village["gentry_persona_id"]]
        villager_persona = VILLAGER_PERSONA_BY_ID[village["villager_persona_id"]]
        definitions.append(
            _build_agent_definition(village, gentry_persona, name_field="gentry_name")
        )
        definitions.append(
            _build_agent_definition(village, villager_persona, name_field="villager_name")
        )
    return definitions
