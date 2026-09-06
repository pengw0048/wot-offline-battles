# -*- coding: utf-8 -*-
"""Mod-owned UI text; the launcher supplies its resolved display language."""

import os

LANGUAGE_ENV = 'WOT_OFFLINE_UI_LANGUAGE'
CHINESE_FONT = 'offline_lan_cjk.font'

try:
    text_type = unicode
except NameError:
    text_type = str


def as_text(value):
    """Keep JSON Unicode and decode UTF-8 before Python 2 interpolation."""
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return text_type(value)


def language():
    # Direct batch-file launches retain their existing English UI.
    return 'zh' if os.environ.get(LANGUAGE_ENV) == 'zh' else 'en'


def tr(source):
    if language() == 'zh':
        return _ZH.get(source, as_text(source))
    return as_text(source)


_ZH = {
    'LAN WAITING ROOM': u'局域网等待房间',
    'TEAM 1': u'队伍 1',
    'TEAM 2': u'队伍 2',
    'RANDOM': u'随机',
    'LEAVE': u'离开',
    'START BATTLE': u'开始战斗',
    'Random': u'随机',
    'Same tier': u'同级',
    'Tier -1 / 0': u'低一级 / 同级',
    'Tier 0 / +1': u'同级 / 高一级',
    'Tier -1 / +2': u'低一级 / 高两级',
    'Unknown': u'未知',
    'BOT TIER: %s%s': u'电脑等级：%s%s',
    'RANDOM%s': u'随机%s',
    ' (ON)': u'（已选）',
    '  (YOU)': u'（已选）',
    'TEAM %d SIZE: %d%s': u'队伍 %d 人数：%d%s',
    'TEAM %d%s': u'队伍 %d%s',
    'MAP: %s': u'地图：%s',
    'waiting for the server map list': u'等待服务器地图列表',
    'The room host starts the battle.': u'由房主开始战斗。',
    'Random team selection is already on.': u'已选择随机队伍。',
    'Already on Team %d.': u'已在队伍 %d。',
    'Requesting a random team...': u'正在申请随机队伍...',
    'Requesting Team %d...': u'正在申请加入队伍 %d...',
    'The server did not accept random selection.': u'服务器未接受随机队伍选择。',
    'The server did not accept Team %d.': u'服务器未接受加入队伍 %d。',
    'Team %d already has %d player(s).': u'队伍 %d 已有 %d 名玩家。',
    'Team %d size is already %d.': u'队伍 %d 人数已为 %d。',
    'Setting Team %d size to %d...': u'正在设置队伍 %d 人数为 %d...',
    'The server did not accept that team size.': u'服务器未接受该队伍人数。',
    'Setting Bot tier preset...': u'正在设置电脑等级...',
    'The server did not accept that Bot tier preset.': u'服务器未接受该电脑等级设置。',
    'The server has not published its map list yet.': u'服务器尚未发送地图列表。',
    'Choose a map first.': u'请先选择地图。',
    'Starting %s...': u'正在开始 %s...',
    'The server did not accept that map.': u'服务器未接受该地图。',
    'Select a valid vehicle in the garage, then click Battle! again.': u'请在车库选择有效车辆，然后再次点击战斗按钮。',
    'You left the LAN room. Click Battle! to join again.': u'已离开局域网房间。点击战斗按钮可重新加入。',
    'waiting for roster': u'等待玩家列表',
    'Requesting Bot tier preset...': u'正在申请电脑等级设置...',
    'Could not save the LAN waiting room choices. Check that the user data directory is writable.': u'无法保存房间设置，请检查用户数据目录是否可写。',
    'The LAN round is starting. Wait for the battle to load.': u'本局正在开始，请等待战斗加载。',
    'PLAYERS (%d): %s': u'玩家（%d）：%s',
    'The LAN server did not accept that team selection.': u'服务器未接受该队伍选择。',
    'The LAN server did not accept that team size.': u'服务器未接受该队伍人数。',
    'Requesting Team %d size %d...': u'正在申请队伍 %d 人数为 %d...',
    'The LAN server did not accept that Bot tier preset.': u'服务器未接受该电脑等级设置。',
    'Joined LAN room. Waiting for host %s to choose the map.': u'已加入房间，等待房主 %s 选择地图。',
    'Could not save the LAN server address. Check that the user data directory is writable.': u'无法保存服务器地址，请检查用户数据目录是否可写。',
    'LAN server connected.': u'已连接局域网服务器。',
    'Battle could not start and the lobby was not restored (%s).': u'无法开始战斗，且未能返回车库（%s）。',
    'LAN room connection lost (%s). Click Battle! to rejoin.': u'房间连接已断开（%s），点击战斗按钮可重新加入。',
    'The LAN room could not be rejoined.': u'无法重新加入房间。',
    'Joining LAN room at %s...': u'正在加入房间 %s...',
    'Still connecting to LAN room at %s. Opening server settings.': u'仍在连接房间 %s，正在打开服务器设置。',
    '+%d more': u'另有 %d 人',
    'SELECT A MAP, THEN CLICK CREATE TO START': u'选择地图，然后点击创建开始战斗',
    'OTHER PLAYERS JOIN WITH THE BATTLE BUTTON': u'其他玩家可点击战斗按钮加入',
    'LAN server %s:%s is unavailable (%s). Retrying and opening server settings.': u'服务器 %s:%s 不可用（%s），正在重试并打开服务器设置。',
    'WAITING FOR %s TO START THE BATTLE': u'等待 %s 开始战斗',
    'You are now the LAN room host. Choose a map to start.': u'你现在是房主，请选择地图开始战斗。',
    'Connecting to %s. The selected map will start automatically.': u'正在连接 %s，所选地图将自动开始。',
    'The LAN server did not accept the start request.': u'服务器未接受开始战斗请求。',
    'Battle could not start (%s). Returning to the map picker.': u'无法开始战斗（%s），正在返回地图选择。',
    'The LAN room could not be joined.': u'无法加入房间。',
    'NO ACTION NEEDED; THE BATTLE OPENS AUTOMATICALLY': u'无需操作，战斗将自动开始',
    'EDIT THE FIRST LINE TO CHANGE THE SERVER': u'编辑第一行可更改服务器',
    'THEN CLICK CREATE TO CONNECT': u'然后点击创建进行连接',
    'The LAN server does not offer the selected map.': u'服务器不支持所选地图。',
    'Only the LAN room host can choose the map and start.': u'只有房主可以选择地图并开始战斗。',
    'Battle could not start (%s). LAN session stopped.': u'无法开始战斗（%s），局域网会话已停止。',
    'The LAN server refused the battle start (%s).': u'服务器拒绝开始战斗（%s）。',
    'Only the LAN room host can change team sizes.': u'只有房主可以更改队伍人数。',
    'Team %s is full. Choose the other team or Random.': u'队伍 %s 已满，请选择另一队或随机。',
    'The LAN server refused the team selection (%s).': u'服务器拒绝队伍选择（%s）。',
    'Only the LAN room host can change the Bot tier preset.': u'只有房主可以更改电脑等级。',
    'Team %s already has too many players for that size.': u'队伍 %s 现有玩家数超过该人数设置。',
    'The LAN server refused the team size (%s).': u'服务器拒绝队伍人数设置（%s）。',
    'The LAN server refused the Bot tier preset (%s).': u'服务器拒绝电脑等级设置（%s）。',
    'The selected LAN team is full. Choose the other team or Random in the waiting room.': u'所选队伍已满，请在等待房间选择另一队或随机。',
    'The launcher team selection is invalid.': u'启动器中的队伍选择无效。',
    'LAN battle connection lost (%s). Returning to the garage.': u'战斗连接已断开（%s），正在返回车库。',
}
