# -*- coding: utf-8 -*-
"""Display-only names for the exact #1513 Bot editor.

Keys remain protocol identifiers, never translated configuration data. Chinese
map names use the mainland legacy names; Himmelsdorf's spelling is the project
owner's explicitly requested 锡莫尔斯多夫. Winter and removed maps retain their
pre-1.0 identity rather than borrowing a modern replacement's title.
Built-in route names are editor translations, not official tactical routes.
"""
from __future__ import annotations

MAP_NAMES = {
    '01_karelia': ('卡累利阿', 'Karelia'),
    '02_malinovka': ('马利诺夫卡', 'Malinovka'),
    '04_himmelsdorf': ('锡莫尔斯多夫', 'Himmelsdorf'),
    '05_prohorovka': ('普罗霍洛夫卡', 'Prokhorovka'),
    '06_ensk': ('安斯克', 'Ensk'),
    '07_lakeville': ('拉斯威利', 'Lakeville'),
    '08_ruinberg': ('鲁别克', 'Ruinberg'),
    '10_hills': ('湖边的角逐', 'Mines'),
    '11_murovanka': ('穆勒万卡', 'Murovanka'),
    '13_erlenberg': ('埃勒斯堡', 'Erlenberg'),
    '14_siegfried_line': ('齐格菲防线', 'Siegfried Line'),
    '17_munchen': ('慕尼黑', 'Widepark'),
    '18_cliff': ('海岸争霸', 'Cliff'),
    '19_monastery': ('小镇争夺战', 'Abbey'),
    '22_slough': ('黑暗沼泽', 'Swamp'),
    '23_westfeld': ('韦斯特菲尔德', 'Westfield'),
    '28_desert': ('荒漠小镇', 'Sand River'),
    '29_el_hallouf': ('埃里-哈罗夫', 'El Halluf'),
    '31_airfield': ('阿拉曼机场', 'Airfield'),
    '33_fjord': ('北欧峡湾', 'Fjords'),
    '34_redshire': ('斯特拉特福', 'Redshire'),
    '35_steppes': ('荒蛮之地', 'Steppes'),
    '36_fishing_bay': ('费舍尔湾', "Fisherman's Bay"),
    '37_caucasus': ('胜利之门', 'Mountain Pass'),
    '38_mannerheim_line': ('极地冰原', 'Arctic Region'),
    '44_north_america': ('里夫奥克斯', 'Live Oaks'),
    '45_north_america': ('州际公路', 'Highway'),
    '47_canada_a': ('寂静海岸', 'Serene Coast'),
    '59_asia_great_wall': ('钢铁长城', "Empire's Border"),
    '63_tundra': ('喀秋莎', 'Tundra'),
    '73_asia_korea': ('神圣之谷', 'Sacred Valley'),
    '83_kharkiv': ('哈尔科夫', 'Kharkov'),
    '84_winter': ('飓风小镇', 'Windstorm'),
    '86_himmelsdorf_winter': ('锡莫尔斯多夫（冬季）', 'Winter Himmelsdorf'),
    '92_stalingrad': ('斯大林格勒', 'Stalingrad'),
    '95_lost_city': ('失落之城', 'Ghost Town'),
    '100_thepit': ('密特朗', 'Mittengard'),
    '101_dday': ('诺曼底', 'Overlord'),
    '103_ruinberg_winter': ('鲁别克（冬季）', 'Winterberg'),
    '112_eiffel_tower_ctf': ('巴黎', 'Paris'),
    '114_czech': ('布拉格', 'Pilsen'),
}

# Every distinct built-in route in the 41 pinned navigation resources.
_ROUTE_ZH = {
    'banana': '香蕉弯道', 'beach': '海滩', 'boulevard': '林荫大道',
    'center': '中央区域', 'center_blocks': '中央街区',
    'central_basin': '中央盆地', 'central_bowl': '中央洼地',
    'central_field': '中央旷野', 'central_gorge': '中央峡谷',
    'central_hills': '中央丘陵', 'central_hollow': '中央低洼地',
    'central_ridges': '中央山脊', 'central_road': '中央道路',
    'central_streets': '中央街道', 'central_village': '中央村庄',
    'city': '城区', 'cliff': '悬崖', 'east_city': '东侧城区',
    'east_coast': '东侧海岸', 'east_field': '东侧旷野',
    'east_fields': '东侧田野', 'east_hill_loop': '东侧山丘环线',
    'east_hills': '东侧丘陵', 'east_rail': '东侧铁路',
    'east_ridge': '东侧山脊', 'east_road': '东侧道路',
    'east_shelf': '东侧台地', 'east_shore': '东侧岸线',
    'east_town': '东侧城镇', 'east_valley': '东侧山谷',
    'east_village': '东侧村庄', 'embankment': '堤岸', 'factory': '工厂',
    'field': '旷野', 'fortification_line': '防御工事线',
    'harbor_edge': '港口外围', 'hill': '山丘', 'hills': '丘陵',
    'ice_road': '冰面道路', 'lake_north_edge': '湖泊北岸',
    'lake_road': '湖边道路', 'middle_crossing': '中央渡口',
    'middle_low': '中央低地', 'middle_road': '中路',
    'middle_village': '中部村庄', 'monastery_lane': '修道院通道',
    'north_bridge': '北侧桥梁', 'north_dunes': '北侧沙丘',
    'north_ridge': '北侧山脊', 'north_road': '北侧道路',
    'north_runway': '北侧跑道', 'outskirts': '城郊', 'park': '公园',
    'pit': '深坑', 'plateau': '高原', 'rail': '铁路线',
    'rail_line': '铁路沿线', 'railway': '铁路', 'rear_guard': '后方守备',
    'ridge': '山脊', 'rim_east': '东侧边缘', 'rim_west': '西侧边缘',
    'river': '河流', 'river_crossing': '渡河点', 'river_town': '河畔城镇',
    'south_bridge': '南侧桥梁', 'south_coast': '南侧海岸',
    'south_rocks': '南侧岩石区', 'south_town': '南侧城镇',
    'south_towns': '南侧村镇群', 'south_valley': '南侧山谷',
    'southwest_road': '西南道路', 'square': '广场', 'temple': '寺庙',
    'tower_east': '铁塔东侧', 'tower_west': '铁塔西侧',
    'town': '城镇', 'valley': '山谷', 'village': '村庄',
    'village_road': '村庄道路', 'wall_pass': '城墙隘口', 'waterfall': '瀑布',
    'west_city': '西侧城区', 'west_coast': '西侧海岸',
    'west_field': '西侧旷野', 'west_fields': '西侧田野',
    'west_hills': '西侧丘陵', 'west_lake_road': '西侧湖滨道路',
    'west_lakeside': '西侧湖岸', 'west_pass': '西侧山口',
    'west_ridge': '西侧山脊', 'west_rocks': '西侧岩石区',
    'west_streets': '西侧街道', 'west_town': '西侧城镇',
    'west_valley': '西侧山谷', 'west_woods': '西侧树林',
}
ROUTE_NAMES = {key: (value, key.replace('_', ' ').capitalize())
               for key, value in _ROUTE_ZH.items()}
ROUTE_NAMES.update({
    'banana': ('香蕉弯道', 'Banana bend'), 'rear_guard': ('后方守备', 'Rear guard'),
    'rail': ('铁路线', 'Rail line'), 'rim_east': ('东侧边缘', 'Eastern rim'),
    'rim_west': ('西侧边缘', 'Western rim'),
    'tower_east': ('铁塔东侧', 'East of the tower'),
    'tower_west': ('铁塔西侧', 'West of the tower'),
})

ENUM_NAMES = {
    'class_tag': {
        'all': ('全部车型', 'All vehicle classes'),
        'lightTank': ('轻型坦克', 'Light tank'),
        'mediumTank': ('中型坦克', 'Medium tank'),
        'heavyTank': ('重型坦克', 'Heavy tank'),
        'AT-SPG': ('坦克歼击车', 'Tank destroyer'),
        'SPG': ('自行火炮', 'Self-propelled gun'),
    },
    'skill': {'': ('继承上级设置', 'Inherit'),
              'rookie': ('新手', 'Rookie'), 'regular': ('普通', 'Regular'),
              'veteran': ('老兵', 'Veteran'), 'elite': ('精英', 'Elite')},
    'policy': {'preferred': ('优先路线', 'Preferred route'),
               'fixed': ('固定路线', 'Fixed route')},
    'crew_level': {'': ('继承上级设置', 'Inherit'),
                   '75': ('75%', '75%'), '90': ('90%', '90%'), '100': ('100%', '100%')},
    'team': {'0': ('全部队伍', 'All teams'), '1': ('队伍 1', 'Team 1'), '2': ('队伍 2', 'Team 2')},
    'slot': {'': ('全部槽位', 'All slots')},
}
PARAM_NAMES = {
    'skill': ('难度', 'Difficulty'), 'crew_level': ('乘员等级', 'Crew level'),
    'reaction_seconds': ('反应时间（秒）', 'Reaction delay (s)'),
    'patience_seconds': ('首次开火缩圈等待上限（秒）', 'Opening aim patience (s)'),
    'converged_factor': ('接受的缩圈倍数', 'Accepted dispersion factor'),
    'aim_bias_factor': ('瞄准点偏差系数', 'Aim-point bias factor'),
    'lead_error': ('移动目标提前量误差', 'Lead error fraction'),
}
VALIDATION_NAMES = {
    'baked_route_connected': ('烘焙导航图上路线连通', 'Baked route connected'),
    'generic_parking_found': ('存在通用停车空间', 'Generic parking space found'),
    'no_generic_parking': ('没有通用停车空间', 'No generic parking space'),
    'connected': ('路线连通', 'Route connected'),
    'waypoint_unusable': ('路径点不可用', 'Waypoint unusable'),
    'waypoints_disconnected': ('路径点之间不连通', 'Waypoints disconnected'),
    'parking_available': ('存在可用停车空间', 'Parking space available'),
    'no_parking_space': ('没有可用停车空间', 'No parking space'),
}


def _label(table, key, language):
    return table.get(key, (str(key), str(key)))[language != 'zh']


def map_label(key, language):
    return _label(MAP_NAMES, key, language)


def route_label(key, language):
    return _label(ROUTE_NAMES, key, language)


def enum_label(kind, key, language):
    return _label(ENUM_NAMES.get(kind, {}), str(key), language)


def parameter_label(key, language):
    return _label(PARAM_NAMES, key, language)


def parameter_value(key, value, language):
    if key in ('skill', 'crew_level'):
        return enum_label(key, value, language)
    return str(value)


def parameter_summary(values, language):
    return '; '.join('%s: %s' % (parameter_label(key, language),
                               parameter_value(key, value, language))
                     for key, value in values.items())


def validation_label(value, language):
    return _label(VALIDATION_NAMES, value, language)
