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


def _get_local_prefecture(county) -> str:
    """从 county_data 的 admin_location 取府名，未设置时返回空串。"""
    if not county:
        return ""
    return (county.get("admin_location") or {}).get("prefecture", "")


def _build_agent_definition(village, persona, *, name_field, county=None):
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

    # ── 社会身份：年龄 + 籍贯/宗族 ──
    age_base = persona.get("age_base", 40)
    attrs["age"] = age_base + random.randint(-2, 3)
    local_prefecture = _get_local_prefecture(county)
    attrs["social_identity"] = {
        "surname": surname,
        "native_place": local_prefecture,
        "clan_id": f"{local_prefecture}{surname}氏" if local_prefecture else "",
    }

    return {
        "name": name,
        "role": persona["role"],
        "role_title": persona["role_title"],
        "tier": persona["tier"],
        "attributes": attrs,
    }


_YAMEN_SURNAMES         = ["王", "李", "张", "刘", "陈", "杨", "赵", "钱", "周", "吴",
                           "胡", "林", "郑", "罗", "韩", "曹", "沈", "董", "傅", "叶"]
_YAMEN_SURNAME_WEIGHTS  = [10,  10,  10,   8,   8,   6,   5,   3,   5,   5,
                            4,   4,   4,   4,   3,   3,   3,   3,   3,   3]
_CLERK_GIVEN_NAMES = ["文远", "德财", "守礼", "明典", "书贤", "安邦", "承恩", "正直", "有道", "仁义"]
_OFFICER_GIVEN_NAMES = ["铁柱", "石头", "虎子", "大壮", "三儿", "福根", "发旺", "长顺", "得力", "猛生"]

_LIUFANG_DEFS = [
    {"role_title": "吏房书办", "dept": "吏", "focus": "官吏考核、任免文书"},
    {"role_title": "户房书办", "dept": "户", "focus": "户籍田赋、钱粮征收"},
    {"role_title": "礼房书办", "dept": "礼", "focus": "科举祭祀、教化事务"},
    {"role_title": "兵房书办", "dept": "兵", "focus": "兵丁驿站、治安巡防"},
    {"role_title": "刑房书办", "dept": "刑", "focus": "刑狱诉讼、缉捕文书"},
    {"role_title": "工房书办", "dept": "工", "focus": "营建水利、工匠管理"},
]

_SANBAN_DEFS = [
    {"role": "CONSTABLE",        "role_title": "捕快", "focus": "缉捕盗贼、执行拘押", "bio_role": "捕快班头"},
    {"role": "BAILIFF_CEREMONY", "role_title": "皂班", "focus": "仪仗排场、衙署门面", "bio_role": "皂班班头"},
    {"role": "BAILIFF_LABOR",    "role_title": "壮班", "focus": "押送犯人、搬运差役", "bio_role": "壮班班头"},
]


def build_yamen_staff_definitions(county=None):
    """生成六房书办（×6）与衙役三班班头（×3）Agent 定义。"""
    used_names = set()
    defs = []
    local_prefecture = _get_local_prefecture(county)

    # 六房书办
    for lf in _LIUFANG_DEFS:
        surname = random.choices(_YAMEN_SURNAMES, weights=_YAMEN_SURNAME_WEIGHTS, k=1)[0]
        name = _generate_unique_name(surname, _CLERK_GIVEN_NAMES, used_names)
        age = random.randint(30, 50)
        defs.append({
            "name": name,
            "role": "LIUFANG",
            "role_title": lf["role_title"],
            "tier": "LIGHT",
            "attributes": {
                "intelligence": random.randint(5, 7),
                "charisma": random.randint(4, 5),
                "loyalty": random.randint(5, 6),
                "personality": {
                    "sociability": round(random.uniform(0.3, 0.6), 1),
                    "rationality": round(random.uniform(0.6, 0.9), 1),
                    "assertiveness": round(random.uniform(0.2, 0.4), 1),
                },
                "ideology": {
                    "state_vs_people": 0.5,
                    "central_vs_local": 0.7,
                    "pragmatic_vs_ideal": 0.7,
                },
                "reputation": {
                    "integrity": random.randint(40, 65),
                    "competence": random.randint(50, 70),
                    "popularity": random.randint(30, 50),
                    "authority": random.randint(15, 30),
                },
                "goals": [
                    f"做好{lf['dept']}房事务，不出差错",
                    "积攒资历，在衙门站稳脚跟",
                ],
                "bio": f"{name}，{lf['role_title']}，负责{lf['focus']}。在县衙任职多年，熟稔文书程序。",
                "backstory": f"{name}出身书香小户，科举无望后入衙为吏。在{lf['dept']}房多年，积累了一套处理公务的经验，为人谨慎少言。",
                "age": age,
                "social_identity": {
                    "surname": surname,
                    "native_place": local_prefecture,
                    "clan_id": f"{local_prefecture}{surname}氏" if local_prefecture else "",
                },
                "memory": [],
                "player_affinity": 50,
            },
        })

    # 衙役三班班头
    for sb in _SANBAN_DEFS:
        surname = random.choices(_YAMEN_SURNAMES, weights=_YAMEN_SURNAME_WEIGHTS, k=1)[0]
        name = _generate_unique_name(surname, _OFFICER_GIVEN_NAMES, used_names)
        age = random.randint(28, 48)
        defs.append({
            "name": name,
            "role": sb["role"],
            "role_title": sb["role_title"],
            "tier": "LIGHT",
            "attributes": {
                "intelligence": random.randint(4, 6),
                "charisma": random.randint(5, 6),
                "loyalty": random.randint(6, 7),
                "personality": {
                    "sociability": round(random.uniform(0.5, 0.7), 1),
                    "rationality": round(random.uniform(0.4, 0.6), 1),
                    "assertiveness": round(random.uniform(0.6, 0.8), 1),
                },
                "ideology": {
                    "state_vs_people": 0.5,
                    "central_vs_local": 0.6,
                    "pragmatic_vs_ideal": 0.8,
                },
                "reputation": {
                    "integrity": random.randint(35, 55),
                    "competence": random.randint(50, 65),
                    "popularity": random.randint(35, 55),
                    "authority": random.randint(40, 60),
                },
                "goals": [
                    f"做好{sb['focus']}的职责",
                    "维持班内秩序，保住班头位置",
                ],
                "bio": f"{name}，{sb['bio_role']}，负责{sb['focus']}。为人豪爽，在衙役中颇有威信。",
                "backstory": (
                    f"{name}入衙多年，从普通差役做到班头。"
                    f"熟悉衙门规矩，处事干练，对上官忠顺，对下属有一定约束力。"
                ),
                "age": age,
                "social_identity": {
                    "surname": surname,
                    "native_place": local_prefecture,
                    "clan_id": f"{local_prefecture}{surname}氏" if local_prefecture else "",
                },
                "memory": [],
                "player_affinity": 48,
            },
        })

    return defs


def build_county_local_agent_definitions(county):
    """从 county_data 中构造本县地主/村民代表 Agent 定义。"""
    ensure_county_local_cast(county)

    definitions = []
    for village in county.get("villages", []):
        gentry_persona = GENTRY_PERSONA_BY_ID[village["gentry_persona_id"]]
        villager_persona = VILLAGER_PERSONA_BY_ID[village["villager_persona_id"]]
        definitions.append(
            _build_agent_definition(village, gentry_persona, name_field="gentry_name", county=county)
        )
        definitions.append(
            _build_agent_definition(village, villager_persona, name_field="villager_name", county=county)
        )
    return definitions
