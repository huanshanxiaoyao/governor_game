"""MVP NPC 蓝图定义 — 4 固定角色 + 6 村庄地主 + 6 村民代表"""

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

            "bio": "张铁根，年四十，李家村里长。庄稼汉出身，为人朴实直率，说话不绕弯子。在村民中有一定威望，是基层民意的代表。",
            "backstory": "祖祖辈辈种地为生，因人实在、办事公道被推为里长。识字不多但脑子灵活，对农事了如指掌。新县令来了，他最关心的就是今年的税会不会涨。",

            "memory": [],
            "player_affinity": 45,
        },
    },
    # ---- 6 个村庄地主 (FULL tier, 用于谈判事件) ----
    {
        "name": "李德厚",
        "role": "GENTRY",
        "role_title": "地主",
        "tier": "FULL",
        "attributes": {
            "village_name": "李家村",
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
                "维持李家在村中的族长地位",
                "稳健经营田产，不求暴利但求安稳",
            ],

            "bio": "李德厚，年五十五，李家村族长兼大地主。为人持重老练，在村中威望颇高。虽不如王家富裕，但胜在根基深厚、人脉广泛。",
            "backstory": "李家世居此地五代，是村中第一大姓。李德厚年轻时曾赴府城经商，后返乡继承祖业。为人处世讲究中庸之道，既不与官府为敌，也不刻意巴结。手下佃户百余户，经营有方。",

            "memory": [],
            "player_affinity": 40,
        },
    },
    {
        "name": "张守业",
        "role": "GENTRY",
        "role_title": "地主",
        "tier": "FULL",
        "attributes": {
            "village_name": "张家村",
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

            "bio": "张守业，年四十八，张家村首富。为人谨小慎微，一文钱掰两半花。虽不大方但也不刻薄，佃户日子过得去。",
            "backstory": "祖上靠节俭攒下家业，张守业将此发扬光大。从不冒险投资，宁可少赚也不愿亏本。家中粮仓常年满储，是附近有名的'铁公鸡'。面对新县令的各项政策，他第一反应永远是'要花多少钱'。",

            "memory": [],
            "player_affinity": 35,
        },
    },
    {
        "name": "王伯丰",
        "role": "GENTRY",
        "role_title": "地主",
        "tier": "FULL",
        "attributes": {
            "village_name": "王家村",
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

            "bio": "王伯丰，年五十二，本县最大地主，家有良田三千亩。为人精明刻薄，善于钻营。与知府衙门多有往来，在地方上颇有势力。",
            "backstory": "王家世居本县三代，靠经营田产和放贷起家。其父曾捐纳一个监生功名，王伯丰本人虽无功名，却凭借财力在乡里呼风唤雨。手下佃户数百，控制着县内最肥沃的良田。新县令上任，他既想试探虚实，也想寻求合作。",

            "memory": [],
            "player_affinity": 30,
        },
    },
    {
        "name": "陈文礼",
        "role": "GENTRY",
        "role_title": "地主",
        "tier": "FULL",
        "attributes": {
            "village_name": "陈家村",
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

            "bio": "陈文礼，年四十二，陈家村乡绅，举人出身。虽有功名却无意仕途，返乡经营田产兼办私塾。思想开明，在乡绅中难得的改革派。",
            "backstory": "少年中举后赴京会试，目睹朝政腐败后心灰意冷，回乡做了'田舍翁'。将部分田产收入用于办学，在村中口碑甚佳。对新县令的改革举措持开放态度，但也不愿自身利益受损太多。",

            "memory": [],
            "player_affinity": 50,
        },
    },
    {
        "name": "赵元亨",
        "role": "GENTRY",
        "role_title": "地主",
        "tier": "FULL",
        "attributes": {
            "village_name": "赵家村",
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
                "借助知府关系巩固地方势力",
                "在县中各项工程中分得利益",
            ],

            "bio": "赵元亨，年四十六，赵家村大户，与知府赵廷章同宗远亲。善于经营政治关系，在官场和地方间左右逢源。",
            "backstory": "赵家与知府赵廷章虽为远亲，但赵元亨极善利用这层关系。常以知府名号压人，在地方上作威作福。手中田产多为近年兼并所得，与佃户关系紧张。面对新县令，他首先考虑的是此人是否好控制。",

            "memory": [],
            "player_affinity": 25,
        },
    },
    {
        "name": "刘三旺",
        "role": "GENTRY",
        "role_title": "地主",
        "tier": "FULL",
        "attributes": {
            "village_name": "刘家村",
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
                "让刘家村不被大村欺负",
                "多攒些家底给儿孙",
            ],

            "bio": "刘三旺，年三十八，刘家村唯一的地主，家底在全县地主中最薄。为人务实爽快，与佃户关系不错。",
            "backstory": "刘家村地少人稀，刘三旺的'地主'身份在其他大户眼中不值一提。但他脑子灵活，除种地外还搞些小买卖。对县令的政策，他最关心的是能否给小村子带来实际好处。谈判时好说话，但也精明，不会白白吃亏。",

            "memory": [],
            "player_affinity": 45,
        },
    },
    # ---- 6 个村庄村民代表 (FULL tier, 每村一人) ----
    {
        "name": "李四",
        "role": "VILLAGER",
        "role_title": "村民代表",
        "tier": "FULL",
        "attributes": {
            "village_name": "李家村",
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

            "bio": "李四，年四十五，李家村老农。种了一辈子地，朴实憨厚，最关心的就是粮食够不够吃、税负重不重。",
            "backstory": "李家世代佃农，李四从小跟父亲学种地。年轻时经历过一次大旱，饿死了不少乡邻，从此对粮食问题格外敏感。为人老实，在村中人缘好，常被推举代表村民说话。",

            "memory": [],
            "player_affinity": 50,
        },
    },
    {
        "name": "张大嫂",
        "role": "VILLAGER",
        "role_title": "村民代表",
        "tier": "FULL",
        "attributes": {
            "village_name": "张家村",
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

            "bio": "张大嫂，年三十五，张家村精明妇人。善于算计，对物价涨跌了如指掌，是村中公认的'活账本'。",
            "backstory": "娘家在镇上开过杂货铺，耳濡目染学了一身精打细算的本事。嫁到张家村后，常帮乡邻算账理财。村中但凡涉及钱粮之事，大家都愿找她商量。性格泼辣直爽，不怕得罪人。",

            "memory": [],
            "player_affinity": 48,
        },
    },
    {
        "name": "王铁柱",
        "role": "VILLAGER",
        "role_title": "村民代表",
        "tier": "FULL",
        "attributes": {
            "village_name": "王家村",
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

            "bio": "王铁柱，年三十二，王家村壮汉。力大如牛，性格刚烈，最看不惯地主欺压穷人，是村中佃农的'出头鸟'。",
            "backstory": "王铁柱家三代佃农，在王伯丰家做长工。亲眼看着父亲被地主逼债含恨而终，心中积怨已久。虽然不识几个字，但天生有股子正气，敢替穷人说话。村中年轻后生都服他。",

            "memory": [],
            "player_affinity": 45,
        },
    },
    {
        "name": "陈小六",
        "role": "VILLAGER",
        "role_title": "村民代表",
        "tier": "FULL",
        "attributes": {
            "village_name": "陈家村",
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

            "bio": "陈小六，年二十四，陈家村读过书的后生。在陈文礼私塾念过几年书，是村中少有的识字人，关心教育和新事物。",
            "backstory": "家境贫寒，幸得陈文礼赏识，免费在私塾读了五年书。虽未考取功名，但在村中已是'秀才'般的存在。心中有一股改变乡村面貌的理想，希望更多孩子能读书明理。对新县令的教育政策格外关注。",

            "memory": [],
            "player_affinity": 55,
        },
    },
    {
        "name": "赵老三",
        "role": "VILLAGER",
        "role_title": "村民代表",
        "tier": "FULL",
        "attributes": {
            "village_name": "赵家村",
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

            "bio": "赵老三，年五十，赵家村谨慎佃农。胆小怕事但心存不满，对地主赵元亨的巧取豪夺敢怒不敢言。",
            "backstory": "原本家有十余亩地，被赵元亨用放贷的手段逐步蚕食，如今只剩三亩薄田。虽然心中愤恨，但深知赵元亨与知府有亲，不敢公然对抗。在村中代表的是那些沉默的大多数——忍气吞声但内心不满的佃农。",

            "memory": [],
            "player_affinity": 50,
        },
    },
    {
        "name": "刘二妮",
        "role": "VILLAGER",
        "role_title": "村民代表",
        "tier": "FULL",
        "attributes": {
            "village_name": "刘家村",
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
                "让刘家村治安好起来，夜里能安心睡觉",
                "带着两个孩子好好活下去",
            ],

            "bio": "刘二妮，年三十，刘家村能干寡妇。丈夫被山匪所害，独自抚养两个孩子，最关心治安问题。",
            "backstory": "三年前丈夫外出赶集遇上山匪，被害身亡。从此独自撑起家业，种地带娃样样不落。因这段经历，对治安问题格外敏感，多次向里长请求加强巡防。为人能干坚韧，在村中很受同情和尊敬。",

            "memory": [],
            "player_affinity": 52,
        },
    },
]
