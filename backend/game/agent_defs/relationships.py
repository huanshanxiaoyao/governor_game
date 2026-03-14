"""固定核心 NPC 关系模板。"""

# (agent_a_name, agent_b_name, affinity, data)
MVP_RELATIONSHIPS = [
    ("沈清远", "赵廷章", 20, {"type": "professional", "desc": "师爷与知府有公务往来，关系一般"}),
    ("李秀才", "张铁根", 40, {"type": "trust", "desc": "耆老关心民生，里长信任耆老"}),
    ("沈清远", "李秀才", 30, {"type": "respect", "desc": "师爷敬重耆老学识"}),
    ("周正卿", "沈清远", 40, {"type": "professional", "desc": "县丞与师爷同在衙门共事，互相尊重"}),
    ("周正卿", "赵廷章", 30, {"type": "subordinate", "desc": "县丞对知府恭敬有加，谨守下属本分"}),
]
