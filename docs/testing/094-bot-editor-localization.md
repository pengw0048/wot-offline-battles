# Launcher Bot editor display localization (2026-09-24)

Baseline: c584fa591ccff2429c3850d92bafb1d4d6afbd1a (delivered PR #38).

## Corrected display

The previous MAP_LABELS table covered only 12 entries, including two IDs that
were not in the 41-map catalogue. It also hard-coded bilingual labels and the
abbreviation 锡城. The new launcher-only catalogue covers all 41 actual IDs,
96 distinct built-in route names, vehicle classes, gunnery difficulty, crew,
route policy, table values and effective-value previews. Use full names,
including the owner's requested 锡莫尔斯多夫 and 锡莫尔斯多夫（冬季）.

The main launcher's existing Auto / English / 中文 selector is the only language
preference. Open editors update in place. No window rebuilding, active-profile
writing, route-coordinate changes, enum translation, target reselection or
reset of zoom/pan/selection/undo/form drafts occurs when language changes.
A LocalizedCombobox keeps a separate display variable and canonical model value;
JSON still uses rookie/regular/veteran/elite, vehicle class tags and fixed/preferred.
User-authored profile, route and position names are not translated or renamed.

The metadata remains #1513, including the old Arctic Region name, not the later
Mannerheim Line revision. Chinese map names use mainland legacy names. The
Himmelsdorf spelling follows this user's explicit instruction rather than
silently replacing it with the current publisher's 锡默尔斯多夫. Built-in route
translations describe the existing authoring IDs; they are not official tactics.

Naming references consulted:
- Historical English publisher map list, including Arctic Region and winter maps:
  https://worldoftanks.eu/en/news/general-news/stronghold-changes-maps/
- Publisher's 1.0 notes explicitly documenting the later Arctic Region rename:
  https://worldoftanks.eu/uk/news/general-news/1-0-release-announcement/
- Mainland publisher map names:
  https://wotgame.cn/zh-cn/news/news/stronghold-map-changes/
- XVM Chinese localization/map catalogue, including removed maps:
  https://xvm.garphy.com/?page_id=7911

## Build boundary

This is a launcher-only fix. The previous Windows artifact is checksum-pinned.
Its exact client ZIP and all bundled server/worker Python sources are retained,
so their original runtime build identity remains valid. Only the launcher is
rebuilt, with source identity appended to README and recorded in build evidence.
No account reset, schema migration, main merge, tag or formal release.

## Tests

17 targeted catalogue/UI/real-main-selector cases cover all IDs, no numbered
map captions, Chinese-English switching, Auto resolution through the main
selector, already open and newly opened editors, closed editor cleanup,
unsaved forms/active file invariance, stable enum serialization, canonical
programmatic variables, copied built-in names, unchanged custom names and
translated previews. Existing nine editor UI cases and 331 launcher cases run
unchanged. The actual packaged --verify-bot-editor command also checks both
languages and reads the resulting route/SPG plan through the existing runtime.

Neither a localization check nor successful startup is native game-battle
acceptance. This patch intentionally does not modify Bot navigation or physics.
