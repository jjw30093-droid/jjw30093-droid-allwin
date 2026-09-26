"""B费系列中文名/球队名权威映射——全部来自生产库 dim_player_i18n /
dim_team_i18n 的真实记录（2026-09-24 逐个 Player_ID/Team_ID 查库核对），
不是手工音译。之前两版曾把 Rayan Cherki 写成"切尔基"、把 Savinho
（FotMob player_name 字段里显示的是其昵称 Sávio）写成"萨维奥"——
都不是项目 i18n 表里的权威译名，这一版全部改用 dim_player_i18n.name_zh_short。
"""

CHINESE_SHORT = {
    "Bruno Fernandes": "B费",
    "Morgan Gibbs-White": "吉布斯-怀特",
    "Granit Xhaka": "扎卡",
    "Savinho": "萨维尼奥",
    "Sávio": "萨维尼奥",  # FotMob player_name 字段用的昵称，同一人
    "Cole Palmer": "帕尔默",
    "Martin Ødegaard": "厄德高",
    "Rayan Cherki": "谢尔基",
    "Dominik Szoboszlai": "索博斯洛伊",
    "Bukayo Saka": "萨卡",
    "Declan Rice": "赖斯",
    "Anton Stach": "斯塔赫",
    "Morgan Rogers": "罗杰斯",
    "Alex Iwobi": "伊沃比",
    "Pascal Groß": "格罗斯",
    "Cody Gakpo": "加克波",
    "Enzo Le Fée": "恩佐",
    "Dan Ndoye": "恩多耶",
    "Abdul Fatawu": "法塔乌",
}

CHINESE_FULL = {
    "Bruno Fernandes": "布鲁诺·费尔南德斯",
    "Morgan Gibbs-White": "摩根·吉布斯-怀特",
    "Granit Xhaka": "格拉尼特·扎卡",
    "Savinho": "萨维尼奥",
    "Sávio": "萨维尼奥",
    "Cole Palmer": "科尔·帕尔默",
    "Martin Ødegaard": "马丁·厄德高",
    "Rayan Cherki": "拉扬·谢尔基",
    "Dominik Szoboszlai": "多米尼克·索博斯洛伊",
    "Bukayo Saka": "布卡约·萨卡",
    "Declan Rice": "德克兰·赖斯",
    "Anton Stach": "安东·斯塔赫",
    "Morgan Rogers": "摩根·罗杰斯",
    "Alex Iwobi": "亚历克斯·伊沃比",
    "Pascal Groß": "帕斯卡尔·格罗斯",
    "Cody Gakpo": "科迪·加克波",
    "Enzo Le Fée": "恩佐·勒费",
    "Dan Ndoye": "丹·恩多耶",
    "Abdul Fatawu": "阿卜杜勒·法塔乌",
}

TEAM_ZH = {
    8455: "切尔西", 8456: "曼城", 8472: "桑德兰", 8586: "热刺", 8650: "利物浦",
    8667: "赫尔城", 8668: "埃弗顿", 9825: "阿森纳", 9879: "富勒姆",
    9902: "伊普斯维奇", 10203: "诺丁汉森林", 10204: "布莱顿", 10260: "曼联",
}
