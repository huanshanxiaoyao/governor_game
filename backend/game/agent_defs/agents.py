"""MVP NPC 蓝图定义 — 5 固定角色 + 动态村庄地主/村民代表 persona"""

MVP_AGENTS = [
    {
        "name": "沈清远",
        "role": "ADVISOR",
        "role_title": "师爷",
        "tier": "FULL",
        "attributes": {
            # 基础属性
            "intelligence": 8,
            "charisma": 6,
            "loyalty": 7,

            # 性格三维
            "personality": {
                "openness": 0.8,       # 开放性 — 善于接受新事物
                "conscientiousness": 0.9,  # 尽责性 — 做事严谨
                "agreeableness": 0.6,  # 宜人性 — 温和但不软弱
            },

            # 意识形态
            "ideology": {
                "reform_vs_tradition": 0.6,   # 偏改革
                "people_vs_authority": 0.5,    # 中立
                "pragmatic_vs_idealist": 0.8,  # 务实
            },

            # 社会声望
            "reputation": {
                "scholarly": 80,   # 学识
                "political": 50,   # 官场人脉
                "popular": 40,     # 民间声望
            },

            # 目标
            "goals": [
                "辅佐县令治理好一方",
                "积累政绩以求日后出仕",
            ],

            # 简介 & 背景
            "bio": "沈清远，年三十五，绍兴师爷世家出身。科举不第后转投幕僚之道，精通刑名钱谷。为人机敏谨慎，善于察言观色，是县令最可倚仗的左膀右臂。",
            "backstory": "自幼聪慧过人，十六岁中秀才，后屡试不第。其父为知名师爷，临终前将毕生所学倾囊相授。游历各地任幕僚十余年，见多识广，深谙官场之道。此番受聘入县衙，望能一展所长。",

            # 记忆（初始为空）
            "memory": [],

            # 对玩家好感度
            "player_affinity": 60,
        },
    },
    {
        "name": "赵廷章",
        "role": "PREFECT",
        "role_title": "知府",
        "tier": "FULL",
        "attributes": {
            "intelligence": 7,
            "charisma": 8,
            "loyalty": 4,

            "personality": {
                "openness": 0.4,
                "conscientiousness": 0.7,
                "agreeableness": 0.3,
            },

            "ideology": {
                "reform_vs_tradition": 0.3,
                "people_vs_authority": 0.2,
                "pragmatic_vs_idealist": 0.7,
            },

            "reputation": {
                "scholarly": 60,
                "political": 90,
                "popular": 30,
            },

            "goals": [
                "维护辖区稳定，确保税收上缴",
                "在朝中获得更大的政治资本",
            ],

            "bio": "赵廷章，年四十八，进士出身，官场老手。为人精明强干但城府极深，重权术轻民事。作为县令的顶头上司，既是靠山也是压力来源。",
            "backstory": "出身官宦世家，二十五岁高中进士，仕途一路顺遂。曾任京中翰林编修，后外放地方。在政绩考核上极为敏感，对下属既有提携之意，也有利用之心。近年朝中风向变动，正需要一个能干的县令为其增添政绩。",

            "memory": [],
            "player_affinity": 40,
        },
    },
    {
        "name": "周正卿",
        "role": "DEPUTY",
        "role_title": "县丞",
        "tier": "FULL",
        "attributes": {
            "intelligence": 6,
            "charisma": 5,
            "loyalty": 6,

            "personality": {
                "openness": 0.4,
                "conscientiousness": 0.9,
                "agreeableness": 0.5,
            },

            "ideology": {
                "reform_vs_tradition": 0.4,
                "people_vs_authority": 0.4,
                "pragmatic_vs_idealist": 0.7,
            },

            "reputation": {
                "scholarly": 45,
                "political": 55,
                "popular": 35,
            },

            "goals": [
                "维持县政平稳运转，不出差错",
                "积攒资历，日后谋求升迁",
            ],

            "bio": "周正卿，年四十二，八品县丞。举人出身，在县衙任职十余年，熟稳公务流程。为人谨慎务实，做事循规蹈矩，是县令不可或缺的副手。",
            "backstory": "周正卿出身小吏之家，自幼在衙门耳濡目染。中举后未能更进一步，便在县衙扎下根来。历经三任县令，深谙官场生存之道：不出头、不犯错、稳扎稳打。虽无大才，却胜在勤勉可靠。对新任县令持观望态度，既盼有所作为，又怕被牵连。",

            "memory": [],
            "player_affinity": 55,
        },
    },
    {
        "name": "李秀才",
        "role": "GENTRY",
        "role_title": "耆老",
        "tier": "LIGHT",
        "attributes": {
            "intelligence": 6,
            "charisma": 7,
            "loyalty": 8,

            "personality": {
                "openness": 0.7,
                "conscientiousness": 0.8,
                "agreeableness": 0.8,
            },

            "ideology": {
                "reform_vs_tradition": 0.5,
                "people_vs_authority": 0.8,
                "pragmatic_vs_idealist": 0.3,
            },

            "reputation": {
                "scholarly": 70,
                "political": 30,
                "popular": 80,
            },

            "goals": [
                "为百姓争取更好的生活",
                "推动兴学教化",
            ],

            "bio": "李秀才，年六十三，本县德高望重的老秀才。一生未中举，却在乡间办学育人，深受百姓爱戴。为人正直仁厚，常为乡里排忧解难。",
            "backstory": "少年得志中秀才，此后数十年屡试不第。转而在乡间开馆授徒，桃李满县。虽无官身，却因学识和品行被推举为耆老，是民间舆论的风向标。",

            "memory": [],
            "player_affinity": 50,
        },
    },
    {
        "name": "张铁根",
        "role": "VILLAGER",
        "role_title": "里长",
        "tier": "LIGHT",
        "attributes": {
            "intelligence": 4,
            "charisma": 5,
            "loyalty": 6,

            "personality": {
                "openness": 0.3,
                "conscientiousness": 0.7,
                "agreeableness": 0.5,
            },

            "ideology": {
                "reform_vs_tradition": 0.4,
                "people_vs_authority": 0.7,
                "pragmatic_vs_idealist": 0.5,
            },

            "reputation": {
                "scholarly": 10,
                "political": 20,
                "popular": 70,
            },

            "goals": [
                "保住自家和邻里的田地",
                "少交税多吃饭",
            ],

            "bio": "张铁根，年四十，本县里长。庄稼汉出身，为人朴实直率，说话不绕弯子。在村民中有一定威望，是基层民意的代表。",
            "backstory": "祖祖辈辈种地为生，因人实在、办事公道被乡里推为里长。识字不多但脑子灵活，对农事了如指掌。新县令来了，他最关心的就是今年的税会不会涨。",

            "memory": [],
            "player_affinity": 45,
        },
    },
]


GENTRY_PERSONAS = [
    {
        "persona_id": "clan_elder_landlord",
        "role": "GENTRY",
        "role_title": "地主",
        "tier": "FULL",
        "attributes": {
            "intelligence": 6,
            "charisma": 6,
            "loyalty": 5,

            "personality": {
                "openness": 0.5,
                "conscientiousness": 0.7,
                "agreeableness": 0.5,
            },

            "ideology": {
                "reform_vs_tradition": 0.4,
                "people_vs_authority": 0.4,
                "pragmatic_vs_idealist": 0.6,
            },

            "reputation": {
                "scholarly": 50,
                "political": 40,
                "popular": 45,
            },

            "goals": [
                "维持本族在村中的主导地位",
                "稳健经营田产，不求暴利但求安稳",
            ],

            "bio": "{name}，年五十五，{village_name}的族中大户。为人持重老练，在村中威望颇高。虽不算县中最富，却胜在根基深厚、人脉广泛。",
            "backstory": "{surname}家世居本地数代，{name}年轻时曾赴外经商，后返乡继承祖业。为人处世讲究中庸之道，既不轻易与官府为敌，也不愿轻易示弱。手下佃户不少，经营颇有章法。",

            "memory": [],
            "player_affinity": 40,
            "gender": "male",
        },
    },
    {
        "persona_id": "frugal_granary_keeper",
        "role": "GENTRY",
        "role_title": "地主",
        "tier": "FULL",
        "attributes": {
            "intelligence": 5,
            "charisma": 4,
            "loyalty": 5,

            "personality": {
                "openness": 0.3,
                "conscientiousness": 0.8,
                "agreeableness": 0.5,
            },

            "ideology": {
                "reform_vs_tradition": 0.3,
                "people_vs_authority": 0.4,
                "pragmatic_vs_idealist": 0.7,
            },

            "reputation": {
                "scholarly": 30,
                "political": 35,
                "popular": 35,
            },

            "goals": [
                "守住祖上传下来的田产",
                "存粮备荒，以防万一",
            ],

            "bio": "{name}，年四十八，是{village_name}中最会持家的大户之一。为人谨小慎微，一文钱掰两半花。虽不大方，却也不至于刻薄到断人生路。",
            "backstory": "{name}家祖上靠节俭攒下家业，他将此发扬光大。从不冒险扩张，宁可少赚也不愿亏本。家中粮仓常年满储，是附近有名的守成派。面对新县令的各项政策，他第一反应永远是“要花多少钱”。",

            "memory": [],
            "player_affinity": 35,
            "gender": "male",
        },
    },
    {
        "persona_id": "wealthy_power_broker",
        "role": "GENTRY",
        "role_title": "地主",
        "tier": "FULL",
        "attributes": {
            "intelligence": 7,
            "charisma": 5,
            "loyalty": 3,

            "personality": {
                "openness": 0.3,
                "conscientiousness": 0.6,
                "agreeableness": 0.2,
            },

            "ideology": {
                "reform_vs_tradition": 0.2,
                "people_vs_authority": 0.3,
                "pragmatic_vs_idealist": 0.9,
            },

            "reputation": {
                "scholarly": 40,
                "political": 60,
                "popular": 20,
            },

            "goals": [
                "保护自家田产不受侵害",
                "扩大在本县的经济影响力",
            ],

            "bio": "{name}，年五十二，是县中最强势的大地主之一。为人精明刻薄，善于钻营，在地方上颇有势力。",
            "backstory": "{surname}家靠经营田产和放贷起家，至{name}这一代已积下厚实家底。本人虽无显赫功名，却凭借财力在乡里呼风唤雨。手下佃户众多，兼并之心向来不小。新县令上任，他既想试探虚实，也想伺机寻求合作。",

            "memory": [],
            "player_affinity": 30,
            "gender": "male",
        },
    },
    {
        "persona_id": "reformist_scholar_gentry",
        "role": "GENTRY",
        "role_title": "地主",
        "tier": "FULL",
        "attributes": {
            "intelligence": 7,
            "charisma": 7,
            "loyalty": 6,

            "personality": {
                "openness": 0.8,
                "conscientiousness": 0.7,
                "agreeableness": 0.7,
            },

            "ideology": {
                "reform_vs_tradition": 0.7,
                "people_vs_authority": 0.6,
                "pragmatic_vs_idealist": 0.4,
            },

            "reputation": {
                "scholarly": 70,
                "political": 40,
                "popular": 60,
            },

            "goals": [
                "推动村中兴学办教",
                "以文化人，改善乡风",
            ],

            "bio": "{name}，年四十二，{village_name}乡绅，举业出身。虽曾走科举之路却无意久困仕途，返乡经营田产兼办私塾。思想开明，在乡绅中难得偏向改革。",
            "backstory": "{name}少年时曾在府城应试，见惯官场得失后心灰意冷，回乡做了田舍翁。将部分田产收入用于办学，在村中口碑甚佳。对新县令的改革举措持开放态度，但也不愿自身利益受损太多。",

            "memory": [],
            "player_affinity": 50,
            "gender": "male",
        },
    },
    {
        "persona_id": "well_connected_opportunist",
        "role": "GENTRY",
        "role_title": "地主",
        "tier": "FULL",
        "attributes": {
            "intelligence": 7,
            "charisma": 6,
            "loyalty": 3,

            "personality": {
                "openness": 0.4,
                "conscientiousness": 0.6,
                "agreeableness": 0.3,
            },

            "ideology": {
                "reform_vs_tradition": 0.3,
                "people_vs_authority": 0.2,
                "pragmatic_vs_idealist": 0.8,
            },

            "reputation": {
                "scholarly": 35,
                "political": 75,
                "popular": 25,
            },

            "goals": [
                "借助上层关系巩固地方势力",
                "在县中各项工程中分得利益",
            ],

            "bio": "{name}，年四十六，是{village_name}最擅长经营关系的大户。善于往来应酬，在官场风声与地方利益之间左右逢源。",
            "backstory": "{name}最擅长借势做人，平日里总爱结交吏胥、乡绅和上层门路。手中田产多有兼并而来，与佃户关系紧张。面对新县令，他首先考虑的从来不是是非，而是此人是否好打交道、是否值得押注。",

            "memory": [],
            "player_affinity": 25,
            "gender": "male",
        },
    },
    {
        "persona_id": "smallholder_pragmatist",
        "role": "GENTRY",
        "role_title": "地主",
        "tier": "FULL",
        "attributes": {
            "intelligence": 5,
            "charisma": 5,
            "loyalty": 6,

            "personality": {
                "openness": 0.5,
                "conscientiousness": 0.6,
                "agreeableness": 0.6,
            },

            "ideology": {
                "reform_vs_tradition": 0.5,
                "people_vs_authority": 0.6,
                "pragmatic_vs_idealist": 0.7,
            },

            "reputation": {
                "scholarly": 20,
                "political": 25,
                "popular": 55,
            },

            "goals": [
                "让本村不被大村欺负",
                "多攒些家底给儿孙",
            ],

            "bio": "{name}，年三十八，是{village_name}少有的地主户，家底在全县诸多大户里并不算厚。为人务实爽快，与佃户关系尚可。",
            "backstory": "{village_name}地少人稀，{name}这个地主在别的大户眼里算不上显赫。但他脑子灵活，除种地外还会做些小买卖。对县令的政策，他最关心的是能否给小村子带来实际好处。谈判时好说话，但也精明，不会白白吃亏。",

            "memory": [],
            "player_affinity": 45,
            "gender": "male",
        },
    },
]


VILLAGER_PERSONAS = [
    {
        "persona_id": "seasoned_old_farmer",
        "role": "VILLAGER",
        "role_title": "村民代表",
        "tier": "FULL",
        "attributes": {
            "intelligence": 4,
            "charisma": 5,
            "loyalty": 6,

            "personality": {
                "openness": 0.3,
                "conscientiousness": 0.7,
                "agreeableness": 0.6,
            },

            "ideology": {
                "reform_vs_tradition": 0.4,
                "people_vs_authority": 0.7,
                "pragmatic_vs_idealist": 0.5,
            },

            "reputation": {
                "scholarly": 10,
                "political": 10,
                "popular": 65,
            },

            "goals": [
                "少交税多留粮，让一家老小吃饱饭",
                "不让地主再随意涨租",
            ],

            "bio": "{name}，年四十五，是{village_name}的老农。种了一辈子地，朴实憨厚，最关心的就是粮食够不够吃、税负重不重。",
            "backstory": "{surname}家世代佃农，{name}从小跟父亲学种地。年轻时经历过一次大旱，饿死了不少乡邻，从此对粮食问题格外敏感。为人老实，在村中人缘好，常被推举代表村民说话。",

            "memory": [],
            "player_affinity": 50,
            "gender": "male",
        },
    },
    {
        "persona_id": "marketwise_householder",
        "role": "VILLAGER",
        "role_title": "村民代表",
        "tier": "FULL",
        "attributes": {
            "intelligence": 6,
            "charisma": 5,
            "loyalty": 5,

            "personality": {
                "openness": 0.5,
                "conscientiousness": 0.8,
                "agreeableness": 0.4,
            },

            "ideology": {
                "reform_vs_tradition": 0.5,
                "people_vs_authority": 0.6,
                "pragmatic_vs_idealist": 0.8,
            },

            "reputation": {
                "scholarly": 15,
                "political": 15,
                "popular": 60,
            },

            "goals": [
                "盯紧物价，不让奸商哄抬粮价",
                "为村里争取更多实惠",
            ],

            "bio": "{name}，年三十七，是{village_name}里精明能算的庄户。善于盘账，对粮价涨跌了如指掌，是村中公认的“活账本”。",
            "backstory": "{name}年轻时在镇上铺面做过伙计，耳濡目染学了一身精打细算的本事。回村后常帮乡邻算账理财。村中但凡涉及钱粮之事，大家都愿找他商量。性格直爽，不怕得罪人。",

            "memory": [],
            "player_affinity": 48,
            "gender": "male",
        },
    },
    {
        "persona_id": "fiery_tenant_leader",
        "role": "VILLAGER",
        "role_title": "村民代表",
        "tier": "FULL",
        "attributes": {
            "intelligence": 4,
            "charisma": 6,
            "loyalty": 5,

            "personality": {
                "openness": 0.4,
                "conscientiousness": 0.5,
                "agreeableness": 0.3,
            },

            "ideology": {
                "reform_vs_tradition": 0.5,
                "people_vs_authority": 0.9,
                "pragmatic_vs_idealist": 0.4,
            },

            "reputation": {
                "scholarly": 5,
                "political": 10,
                "popular": 70,
            },

            "goals": [
                "反对地主盘剥，为佃农争公道",
                "有朝一日能有自己的田地",
            ],

            "bio": "{name}，年三十二，是{village_name}的壮汉。力大如牛，性格刚烈，最看不惯地主欺压穷人，是村中佃农的出头鸟。",
            "backstory": "{name}家三代佃农，父亲曾被地主逼债至家道中落，他因此心中积怨极深。虽然不识几个字，却天生有股正气，敢替穷人说话。村中年轻后生多愿听他招呼。",

            "memory": [],
            "player_affinity": 45,
            "gender": "male",
        },
    },
    {
        "persona_id": "educated_youth",
        "role": "VILLAGER",
        "role_title": "村民代表",
        "tier": "FULL",
        "attributes": {
            "intelligence": 6,
            "charisma": 5,
            "loyalty": 6,

            "personality": {
                "openness": 0.8,
                "conscientiousness": 0.7,
                "agreeableness": 0.7,
            },

            "ideology": {
                "reform_vs_tradition": 0.7,
                "people_vs_authority": 0.7,
                "pragmatic_vs_idealist": 0.3,
            },

            "reputation": {
                "scholarly": 35,
                "political": 15,
                "popular": 55,
            },

            "goals": [
                "让村里孩子都能读上书",
                "用学到的知识改善乡亲们的生活",
            ],

            "bio": "{name}，年二十四，是{village_name}读过书的后生。念过几年私塾，是村中少有的识字人，关心教育和新事物。",
            "backstory": "{name}家境贫寒，却靠乡里资助读了几年书。虽未考取功名，但在村中已算见过世面的人。心里一直想着让孩子们多识几个字，也盼着新县令真能办成几件利民的事。",

            "memory": [],
            "player_affinity": 55,
            "gender": "male",
        },
    },
    {
        "persona_id": "cautious_smallholder",
        "role": "VILLAGER",
        "role_title": "村民代表",
        "tier": "FULL",
        "attributes": {
            "intelligence": 5,
            "charisma": 4,
            "loyalty": 5,

            "personality": {
                "openness": 0.2,
                "conscientiousness": 0.8,
                "agreeableness": 0.6,
            },

            "ideology": {
                "reform_vs_tradition": 0.3,
                "people_vs_authority": 0.6,
                "pragmatic_vs_idealist": 0.6,
            },

            "reputation": {
                "scholarly": 10,
                "political": 10,
                "popular": 50,
            },

            "goals": [
                "保住自家那几亩薄田不被兼并",
                "平平安安过日子，别惹祸上身",
            ],

            "bio": "{name}，年五十，是{village_name}里最谨慎的一类小农。胆小怕事却心存不满，对地主的巧取豪夺敢怒不敢言。",
            "backstory": "{name}原本家有十余亩地，后来因借贷与岁歉接连折损，如今只剩几亩薄田。虽然心中愤恨，但深知自己无力与大户公然对抗。在村中代表的正是那些沉默的大多数，嘴上不说，心里却一直记账。",

            "memory": [],
            "player_affinity": 50,
            "gender": "male",
        },
    },
    {
        "persona_id": "security_burdened_father",
        "role": "VILLAGER",
        "role_title": "村民代表",
        "tier": "FULL",
        "attributes": {
            "intelligence": 5,
            "charisma": 6,
            "loyalty": 7,

            "personality": {
                "openness": 0.5,
                "conscientiousness": 0.8,
                "agreeableness": 0.5,
            },

            "ideology": {
                "reform_vs_tradition": 0.4,
                "people_vs_authority": 0.8,
                "pragmatic_vs_idealist": 0.6,
            },

            "reputation": {
                "scholarly": 10,
                "political": 10,
                "popular": 65,
            },

            "goals": [
                "让本村治安好起来，夜里能安心睡觉",
                "保住一家老小的生计",
            ],

            "bio": "{name}，年三十四，是{village_name}里格外看重治安的壮年庄户。家里负担重，最怕盗匪和兵扰再毁掉眼前这点活路。",
            "backstory": "{name}早年外出贩运时曾遭过山匪，虽侥幸捡回一命，却因此赔掉半副家当。从此一边种地一边养家，对治安问题格外敏感，多次向里长请求加强巡防。为人能干坚韧，在村中颇受敬重。",

            "memory": [],
            "player_affinity": 52,
            "gender": "male",
        },
    },
]


GENTRY_GIVEN_NAMES = [
    "伯年", "景和", "德成", "文昌", "廷瑞", "守中", "世隆", "允厚",
    "承业", "宗贤", "维礼", "仲安", "国祯", "克让", "绍先", "载丰",
]

VILLAGER_GIVEN_NAMES = [
    "有田", "阿福", "老实", "守成", "大山", "旺生", "永贵", "长顺",
    "铁生", "茂才", "福旺", "满仓", "二牛", "来顺", "四海", "保田",
]
