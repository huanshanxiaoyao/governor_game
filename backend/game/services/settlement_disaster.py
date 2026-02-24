"""灾害系统：环境漂移、灾害判定"""

import random

from ..models import EventLog


class DisasterMixin:
    """环境漂移与灾害判定"""

    @classmethod
    def _drift_environment(cls, county, report):
        """Spring: drift environment variables (doc 06 §2.1-2.2)."""
        env = county["environment"]

        env["agriculture_suitability"] = max(0.3, min(1.0,
            env["agriculture_suitability"] + random.uniform(-0.1, 0.1)))
        env["flood_risk"] = max(0.0, min(1.0,
            env["flood_risk"] + random.uniform(-0.1, 0.1)))
        env["border_threat"] = max(0.0, min(1.0,
            env["border_threat"] + random.uniform(-0.05, 0.05)))

        # Narrative hints
        if env["agriculture_suitability"] >= 0.8:
            report["events"].append("今春风调雨顺，老农皆言是个好年景")
        elif env["agriculture_suitability"] <= 0.4:
            report["events"].append("开春以来旱象初现，不少田地未能按时播种")

        if env["flood_risk"] >= 0.7:
            report["events"].append("入夏以来雨水偏多，堤坝需多加留意")

        if env["border_threat"] >= 0.5:
            report["events"].append("北方边报频传，朝中气氛紧张")

    @classmethod
    def _disaster_check_data(cls, county, report):
        """Summer disaster check — pure data, no EventLog creation."""
        env = county["environment"]
        medical_level = county.get("medical_level", 0)
        medical_mult = 0.85 ** medical_level
        irr_level = county.get("irrigation_level", 0)

        disaster_table = [
            (
                "flood",
                max(0.02 if env["flood_risk"] > 0 else 0,
                    env["flood_risk"] * (1 - irr_level * 0.1)),
                (0.4, 0.7),
                -10,
            ),
            ("drought", 0.15 * (1 - env["agriculture_suitability"]), (0.3, 0.6), -8),
            ("locust", 0.08, (0.2, 0.4), -5),
            ("plague", 0.05 * medical_mult, (0.05, 0.15), -15),
        ]

        for dtype, prob, sev_range, base_morale_hit in disaster_table:
            if random.random() < prob:
                severity = random.uniform(sev_range[0], sev_range[1])
                if dtype == "plague":
                    severity *= medical_mult

                county["disaster_this_year"] = {
                    "type": dtype,
                    "severity": round(severity, 3),
                    "relieved": False,
                }

                # 民心惩罚：洪灾/旱灾受水利减免，疫病受医疗减免，蝗灾无减免
                if dtype in ("flood", "drought"):
                    morale_hit = round(base_morale_hit * (1 - irr_level * 0.1))
                elif dtype == "plague":
                    morale_hit = round(base_morale_hit * 0.85 ** (1 + medical_level))
                else:
                    morale_hit = base_morale_hit
                county["morale"] = max(0, county["morale"] + morale_hit)

                # 灾害对商业的冲击
                commercial_hit = round(3 + 7 * severity)
                county["commercial"] = max(0, county["commercial"] - commercial_hit)

                # 疫病即时人口损失：每个村庄均受影响
                if dtype == "plague":
                    total_pop_loss = 0
                    for village in county["villages"]:
                        loss_rate = random.uniform(0.02, severity / 5)
                        pop_loss = int(village["population"] * loss_rate)
                        village["population"] = max(0, village["population"] - pop_loss)
                        total_pop_loss += pop_loss
                    report["events"].append(
                        f"疫病突袭！全县染疫，"
                        f"人口减少{total_pop_loss}人，民心{morale_hit}，商业-{commercial_hit}"
                        f"{'（医疗减损）' if medical_level > 0 else ''}")
                else:
                    narrative = {
                        "flood": f"夏季洪水泛滥，预计秋收损失{severity:.0%}，民心{morale_hit}，商业-{commercial_hit}",
                        "drought": f"旱灾肆虐，田地干裂，预计秋收损失{severity:.0%}，民心{morale_hit}，商业-{commercial_hit}",
                        "locust": f"蝗灾来袭，遮天蔽日，预计秋收损失{severity:.0%}，民心{morale_hit}，商业-{commercial_hit}",
                    }
                    report["events"].append(narrative[dtype])
                break

    @classmethod
    def _summer_disaster_check(cls, game, county, report):
        """Summer: roll for disasters (doc 06 §3)."""
        env = county["environment"]
        medical_level = county.get("medical_level", 0)
        medical_mult = 0.85 ** medical_level
        irr_level = county.get("irrigation_level", 0)

        # Disaster candidates: (type, probability, severity_range, base_morale_hit)
        disaster_table = [
            (
                "flood",
                max(0.02 if env["flood_risk"] > 0 else 0,
                    env["flood_risk"] * (1 - irr_level * 0.1)),
                (0.4, 0.7),
                -10,
            ),
            (
                "drought",
                0.15 * (1 - env["agriculture_suitability"]),
                (0.3, 0.6),
                -8,
            ),
            (
                "locust",
                0.08,
                (0.2, 0.4),
                -5,
            ),
            (
                "plague",
                0.05 * medical_mult,
                (0.05, 0.15),
                -15,
            ),
        ]

        for dtype, prob, sev_range, base_morale_hit in disaster_table:
            if random.random() < prob:
                severity = random.uniform(sev_range[0], sev_range[1])

                if dtype == "plague":
                    severity *= medical_mult

                county["disaster_this_year"] = {
                    "type": dtype,
                    "severity": round(severity, 3),
                    "relieved": False,
                }

                # 民心惩罚：洪灾/旱灾受水利减免，疫病受医疗减免，蝗灾无减免
                if dtype in ("flood", "drought"):
                    morale_hit = round(base_morale_hit * (1 - irr_level * 0.1))
                elif dtype == "plague":
                    morale_hit = round(base_morale_hit * 0.85 ** (1 + medical_level))
                else:
                    morale_hit = base_morale_hit
                county["morale"] = max(0, county["morale"] + morale_hit)

                # 灾害对商业的冲击
                commercial_hit = round(3 + 7 * severity)
                county["commercial"] = max(0, county["commercial"] - commercial_hit)

                # 疫病即时人口损失：每个村庄均受影响
                if dtype == "plague":
                    total_pop_loss = 0
                    for village in county["villages"]:
                        loss_rate = random.uniform(0.02, severity / 5)
                        pop_loss = int(village["population"] * loss_rate)
                        village["population"] = max(0, village["population"] - pop_loss)
                        total_pop_loss += pop_loss
                    report["events"].append(
                        f"疫病突袭！全县染疫，"
                        f"人口减少{total_pop_loss}人，民心{morale_hit}，商业-{commercial_hit}"
                        f"{'（医疗减损）' if medical_level > 0 else ''}")
                else:
                    narrative = {
                        "flood": f"夏季洪水泛滥，预计秋收损失{severity:.0%}，民心{morale_hit}，商业-{commercial_hit}",
                        "drought": f"旱灾肆虐，田地干裂，预计秋收损失{severity:.0%}，民心{morale_hit}，商业-{commercial_hit}",
                        "locust": f"蝗灾来袭，遮天蔽日，预计秋收损失{severity:.0%}，民心{morale_hit}，商业-{commercial_hit}",
                    }
                    report["events"].append(narrative[dtype])

                # EventLog
                EventLog.objects.create(
                    game=game,
                    season=game.current_season,
                    event_type=f"disaster_{dtype}",
                    category='DISASTER',
                    description=report["events"][-1],
                    data={
                        'disaster_type': dtype,
                        'severity': round(severity, 3),
                    },
                )

                break  # only one disaster per year
