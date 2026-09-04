# -*- coding: utf-8 -*-
"""User-reviewed tactical corridor intent for the #1513 map set.

The points in this module are sparse strategic gates, not locomotion paths.
The navigation baker projects them onto the validated four-metre graph and
recomputes every segment before the routes reach the client.

Only maps carrying a red-line review on 2026-08-11 belong in the geometry
overlay.  Redshire, Lost City and The Pit were explicitly accepted and remain
on their previous route data.
"""

import math


ACCEPTED_UNCHANGED_MAPS = (
	'34_redshire',
	'95_lost_city',
	'100_thepit',
)


# Ensk's reviewed west-city and east-field corridors cover the useful map
# choices without a third rail-yard lane.  Keep the two capacities balanced so
# the normal fourteen-Bot team distribution remains explicit.
REMOVED_ROUTE_IDS = {
	'06_ensk': ('rail_yard',),
}


REVIEWED_ROUTE_CAPACITIES = {
	'06_ensk': {'west_city': 7, 'east_field': 7},
}


# Route points are stored in team-one-to-team-two order where practical.  The
# navigation baker reorients them and selects their representative hard gate
# against the exact #1513 team starts decoded from the current arena data.
# A normal runtime import has no authority to guess those coordinates, so it
# preserves this reviewed order and uses the reviewed endpoints only to mark a
# provisional gate; production always replaces that geometry with baked data.
REVIEWED_ROUTE_POINTS = {
	'01_karelia': {
		'west_ridge': (
			(349.0, 475.0), (-88.0, 482.0), (-305.0, 450.0),
			(-388.0, 402.0), (-458.0, 292.0), (-475.0, 195.0),
			(-462.0, -9.0), (-475.0, -199.0), (-454.0, -306.0),
			(-392.0, -372.0),
		),
		'middle_road': ((312.0, 345.0), (-345.0, -312.0)),
		'east_shelf': (
			(476.0, 282.0), (486.0, 181.0), (336.0, -209.0),
			(116.0, -403.0), (-121.0, -482.0), (-302.0, -442.0),
		),
	},
	'02_malinovka': {
		'west_lake_road': (
			(-21.0, -442.0), (-201.0, -417.0), (-355.0, -269.0),
			(-422.0, -126.0),
		),
		'central_field': (
			(242.0, -199.0), (149.0, 138.0), (52.0, 216.0),
			(-178.0, 231.0),
		),
		'east_hill_loop': (
			(302.0, -257.0), (427.0, -106.0), (469.0, 55.0),
			(472.0, 457.0), (176.0, 482.0), (9.0, 428.0),
			(-205.0, 293.0),
		),
	},
	'04_himmelsdorf': {
		# The Round 4 red stroke crosses the pinned map's initial static
		# bld413 house at x=169.5..201.5, z=163.4..195.7.  The nearest
		# non-hill graph path must return through x<=42, so retain the last
		# safe banana intent instead of manufacturing a third passage.
		'banana': (
			(190.0, -74.0), (182.0, -10.0), (154.0, -2.0),
			(74.0, 102.0), (102.0, 138.0), (138.0, 302.0),
		),
		'hill': (
			(273.0, -282.0), (386.0, -186.0), (358.0, -50.0),
			(382.0, 50.0), (382.0, 178.0), (374.0, 282.0),
			(158.0, 294.0),
		),
		'rail': (
			(-216.0, -171.0), (-241.0, 81.0), (-221.0, 191.0),
			(-152.0, 249.0), (-109.0, 314.0),
		),
	},
	'05_prohorovka': {
		'west_ridge': (
			(-282.0, 416.0), (-359.0, 292.0), (-372.0, 81.0),
			(-355.0, -326.0), (-308.0, -386.0),
		),
		'central_field': ((-13.0, 258.0), (13.0, 168.0), (69.0, -199.0)),
		'rail_line': (
			(99.0, 367.0), (272.0, 125.0), (419.0, 73.0),
			(478.0, -35.0), (496.0, -316.0), (203.0, -337.0),
			(142.0, -371.0),
		),
	},
	'06_ensk': {
		'west_city': (
			(-108.0, 170.0), (-167.0, 89.0), (-165.0, 23.0),
			(-184.0, -3.0), (-185.0, -40.0),
			(-178.0, -165.0), (-85.0, -190.0), (-41.0, -224.0),
		),
		'east_field': (
			(113.0, 228.0), (183.0, 177.0), (227.0, 41.0),
			(212.0, -131.0), (192.0, -187.0), (79.0, -228.0),
		),
	},
	'07_lakeville': {
		'west_valley': (
			(-292.0, 268.0), (-310.0, 105.0), (-294.0, -44.0),
			(-270.0, -103.0), (-296.0, -223.0), (-302.0, -270.0),
			(-230.0, -290.0),
		),
		# The lake-side road is a real stock corridor.  The #1513 graph adapter
		# verifies its one narrow diagonal instead of projecting the route onto
		# a duplicate west-valley path.
		'lake_road': (
			(-112.0, 260.0), (-96.0, 225.0), (-94.0, 97.0),
			(-108.0, 44.0), (-88.0, -12.0), (-84.0, -92.0),
			(-105.0, -212.0),
		),
		'east_town': (
			(266.0, 265.0), (335.0, 193.0), (391.0, -60.0),
			(349.0, -237.0), (313.0, -303.0), (274.0, -333.0),
			(98.0, -343.0),
		),
	},
	'08_ruinberg': {
		'west_city': (
			(-234.0, 140.0), (-305.0, 38.0), (-330.0, -20.0),
			(-297.0, -73.0), (-108.0, -181.0), (-81.0, -223.0),
		),
		'central_streets': (
			(34.0, 185.0), (64.0, 81.0), (84.0, -44.0),
			(71.0, -119.0), (31.0, -210.0), (-17.0, -267.0),
		),
		'east_fields': (
			(138.0, 295.0), (317.0, 248.0), (367.0, 73.0),
			(362.0, -130.0), (346.0, -194.0), (261.0, -243.0),
			(103.0, -255.0),
		),
	},
	'10_hills': {
		'southwest_road': (
			(20.0, -282.0), (-76.0, -192.0), (-108.0, -174.0),
			(-351.0, -100.0), (-354.0, 57.0), (-324.0, 143.0),
			(-228.0, 239.0),
		),
		'central_hills': (
			(82.0, -138.0), (44.0, -100.0), (-73.0, -28.0),
			(-78.0, 76.0), (-62.0, 175.0),
		),
		'east_coast': (
			(128.0, -119.0), (144.0, -84.0), (211.0, 4.0),
			(249.0, 107.0), (151.0, 158.0), (18.0, 253.0),
			(-60.0, 283.0),
		),
	},
	'11_murovanka': {
		'west_woods': (
			(-211.0, 328.0), (-243.0, 298.0), (-332.0, 151.0),
			(-388.0, -49.0), (-358.0, -146.0), (-322.0, -206.0),
			(-278.0, -245.0), (-151.0, -317.0),
		),
		'central_field': (
			(-5.0, 332.0), (-5.0, 28.0), (-18.0, -122.0),
			(19.0, -256.0), (-55.0, -379.0),
		),
		'east_village': (
			(72.0, 369.0), (182.0, 304.0), (232.0, 258.0),
			(300.0, 138.0), (322.0, -5.0), (290.0, -106.0),
			(162.0, -259.0), (12.0, -334.0),
		),
	},
	'13_erlenberg': {
		# The route IDs are assigned by the bridge actually used, not by the
		# annotation extractor's nearest old polyline.
		'north_bridge': (
			(319.0, -332.0), (395.0, -306.0), (406.0, -206.0),
			(441.0, 88.0), (435.0, 138.0), (306.0, 305.0),
			(162.0, 403.0), (32.0, 452.0),
		),
		'middle_crossing': (
			(-65.0, -239.0), (-105.0, -69.0), (-79.0, 28.0),
			(-8.0, 205.0),
		),
		'south_bridge': (
			(-68.0, -438.0), (-422.0, -136.0), (-434.0, -94.0),
			(-399.0, 288.0), (-225.0, 328.0),
		),
	},
	'14_siegfried_line': {
		'west_field': (
			(-1.0, -416.0), (-178.0, -310.0), (-319.0, -136.0),
			(-358.0, 11.0), (-268.0, 215.0), (-158.0, 333.0),
			(32.0, 400.0),
		),
		'fortification_line': (
			(214.0, -338.0), (258.0, -290.0), (258.0, -118.0),
			(322.0, -46.0), (314.0, 62.0), (270.0, 106.0),
			(278.0, 254.0), (238.0, 338.0),
		),
		'east_city': (
			(349.0, -398.0), (452.0, -319.0), (477.0, -246.0),
			(486.0, -146.0), (484.0, -99.0), (412.0, 128.0),
		),
	},
	'17_munchen': {
		# The western street passes under the railway bridge.  Keep gates on
		# both sides of the underpass so the baker cannot substitute the sharp
		# right-angle surface crossing visible in the second review render.
		'west_streets': (
			(-177.0, -170.0), (-214.0, -110.0), (-214.0, -22.0),
			(-205.0, 44.0), (-194.0, 78.0), (-194.0, 94.0),
			(-191.0, 103.0), (-198.0, 156.0), (-116.0, 195.0),
			(-16.0, 216.0),
		),
		'east_rail': ((169.0, -129.0), (261.0, -3.0), (270.0, 42.0), (253.0, 155.0)),
		'center_blocks': ((-22.0, -27.0), (9.0, 35.0)),
	},
	'18_cliff': {
		'west_coast': ((-358.0, -336.0), (-415.0, -46.0), (-325.0, 355.0)),
		'central_road': ((39.0, -211.0), (76.0, -106.0), (29.0, 171.0), (-48.0, 268.0)),
		'east_ridge': (
			(-41.0, -304.0), (282.0, -87.0), (326.0, -2.0),
			(266.0, 140.0), (-11.0, 268.0), (-154.0, 394.0),
		),
	},
	'19_monastery': {
		'west_field': (
			(-352.0, -363.0), (-448.0, -169.0), (-434.0, 185.0),
			(-352.0, 395.0), (-131.0, 415.0),
		),
		'monastery_lane': (
			(34.0, -250.0), (34.0, -110.0), (82.0, 62.0),
			(62.0, 162.0), (22.0, 282.0),
		),
		'east_hills': ((249.0, -372.0), (254.0, -166.0), (162.0, 61.0), (286.0, 328.0), (262.0, 394.0)),
	},
	'22_slough': {
		'west_ridge': (
			(-334.0, 305.0), (-388.0, 145.0), (-318.0, -96.0),
			(-158.0, -296.0), (49.0, -390.0),
		),
		'middle_low': ((-248.0, 382.0), (-41.0, 228.0), (113.0, 28.0), (124.0, -262.0), (242.0, -343.0)),
		'east_ridge': ((142.0, 388.0), (246.0, 328.0), (320.0, 218.0), (379.0, -76.0), (349.0, -272.0)),
	},
	'23_westfeld': {
		'north_ridge': ((-383.0, -12.0), (-274.0, 198.0), (-158.0, 331.0), (229.0, 368.0)),
		'central_village': ((-319.0, -112.0), (-25.0, 18.0), (32.0, 238.0), (146.0, 340.0), (229.0, 342.0)),
		'east_fields': ((62.0, -459.0), (462.0, -409.0), (470.0, 71.0), (426.0, 181.0)),
	},
	'28_desert': {
		'north_dunes': ((451.0, 11.0), (35.0, 321.0), (-128.0, 275.0), (-248.0, 63.0)),
		'village_road': ((198.0, -98.0), (62.0, -183.0), (-55.0, -171.0), (-292.0, 51.0)),
		'south_rocks': ((439.0, -332.0), (349.0, -464.0), (-355.0, -426.0), (-462.0, -319.0), (-407.0, -9.0)),
	},
	'29_el_hallouf': {
		'south_valley': (
			(182.0, -128.0), (-86.0, -198.0), (-130.0, -226.0),
		),
		# The extractor assigned these two strokes to the old routes in the
		# opposite order.  Geographic route IDs remain stable here.
		'north_ridge': ((32.0, 406.0), (-228.0, 462.0), (-461.0, 414.0), (-449.0, -56.0), (-416.0, -139.0)),
		'central_bowl': ((29.0, 355.0), (-181.0, 262.0), (-243.0, 128.0), (-311.0, -126.0)),
	},
	'31_airfield': {
		'north_runway': ((398.0, -74.0), (406.0, -18.0), (273.0, 248.0), (59.0, 285.0), (-270.0, 201.0), (-342.0, -34.0)),
		'central_ridges': ((296.0, 36.0), (139.0, 84.0), (-30.0, 82.0), (-305.0, -30.0)),
		'south_towns': (
			(204.0, -239.0), (150.0, -270.0), (90.0, -289.0),
			(-70.0, -260.0), (-160.0, -235.0), (-240.0, -200.0),
		),
	},
	'33_fjord': {
		'north_ridge': ((382.0, 128.0), (418.0, 338.0), (309.0, 410.0), (-88.0, 348.0), (-268.0, 231.0)),
		'middle_village': ((189.0, -59.0), (-21.0, -104.0), (-205.0, -58.0), (-252.0, -35.0)),
		'south_coast': ((286.0, -138.0), (49.0, -130.0), (-11.0, -246.0), (-105.0, -378.0), (-173.0, -322.0), (-227.0, -106.0), (-343.0, 25.0)),
	},
	'35_steppes': {
		'east_ridge': ((412.0, -62.0), (428.0, 158.0), (318.0, 330.0), (222.0, 391.0)),
		'central_hollow': ((70.0, -246.0), (34.0, -110.0), (-121.0, 31.0), (-131.0, 258.0)),
		'west_rocks': ((-282.0, -306.0), (-372.0, -236.0), (-418.0, -70.0), (-342.0, 102.0)),
	},
	'36_fishing_bay': {
		'west_fields': (
			(-86.0, 398.0), (-346.0, 374.0), (-442.0, 198.0),
			(-386.0, 46.0), (-450.0, -234.0), (-350.0, -358.0),
			(-58.0, -386.0), (-18.0, -398.0),
		),
		'central_road': (
			(-86.0, 398.0), (-58.0, 102.0), (-42.0, -90.0),
			(-26.0, -282.0), (-18.0, -398.0),
		),
		'harbor_edge': (
			(-86.0, 398.0), (70.0, 346.0), (190.0, 282.0),
			(242.0, 122.0), (294.0, -54.0), (290.0, -186.0),
			(230.0, -294.0), (174.0, -338.0), (-18.0, -398.0),
		),
	},
	'37_caucasus': {
		'west_pass': (
			(-378.0, 370.0), (-294.0, 130.0), (-418.0, -138.0),
			(-386.0, -322.0), (-150.0, -346.0), (86.0, -226.0),
			(242.0, -350.0), (346.0, -402.0),
		),
		'central_basin': (
			(-378.0, 370.0), (-298.0, 230.0), (-200.0, 240.0),
			(-110.0, 190.0), (-20.0, 120.0), (70.0, 60.0),
			(140.0, -10.0), (120.0, -80.0), (118.0, -158.0),
			(206.0, -274.0), (346.0, -402.0),
		),
		'east_road': (
			(-378.0, 370.0), (-326.0, 394.0), (-122.0, 422.0),
			(50.0, 350.0), (186.0, 166.0), (326.0, 86.0),
			(366.0, 22.0), (382.0, -142.0), (386.0, -226.0),
			(346.0, -402.0),
		),
	},
	'38_mannerheim_line': {
		'east_ridge': (
			(398.0, 294.0), (386.0, 110.0), (386.0, -114.0),
			(314.0, -206.0), (202.0, -266.0), (2.0, -286.0),
			(-170.0, -274.0), (-338.0, -306.0),
		),
		'central_gorge': (
			(398.0, 294.0), (314.0, 210.0), (254.0, 158.0),
			(114.0, 190.0), (38.0, 170.0), (-62.0, -26.0),
			(-82.0, -150.0), (-222.0, -210.0), (-306.0, -298.0),
			(-338.0, -306.0),
		),
		'west_lakeside': (
			(398.0, 294.0), (250.0, 382.0), (86.0, 398.0),
			(-30.0, 446.0), (-190.0, 358.0), (-366.0, 302.0),
			(-414.0, 134.0), (-322.0, -2.0), (-278.0, -134.0),
			(-306.0, -282.0), (-338.0, -306.0),
		),
	},
	'44_north_america': {
		'west_town': (
			(-358.0, -330.0), (-382.0, 134.0), (-418.0, 358.0),
			(-410.0, 434.0), (-162.0, 438.0), (70.0, 438.0),
			(250.0, 410.0), (302.0, 362.0),
		),
		'east_valley': (
			(-358.0, -330.0), (-150.0, -310.0), (-78.0, -350.0),
			(34.0, -362.0), (34.0, -402.0), (186.0, -370.0),
			(346.0, -246.0), (426.0, -102.0), (442.0, 206.0),
			(346.0, 322.0), (302.0, 362.0),
		),
		'lake_north_edge': (
			(-358.0, -330.0), (-282.0, -154.0), (-242.0, 66.0),
			(-158.0, 182.0), (-90.0, 218.0), (74.0, 230.0),
			(174.0, 286.0), (302.0, 362.0),
		),
	},
	'45_north_america': {
		'north_road': (
			(194.0, 358.0), (40.0, 370.0), (-50.0, 365.0),
			(-146.0, 374.0), (-258.0, 400.0), (-406.0, 398.0),
			(-466.0, 222.0),
			(-466.0, 170.0), (-422.0, 22.0), (-358.0, -182.0),
			(-342.0, -326.0),
		),
		'river_crossing': (
			(194.0, 358.0), (142.0, 242.0), (14.0, 162.0),
			(-20.0, 80.0), (-30.0, 0.0), (-70.0, -60.0),
			(-140.0, -130.0), (-170.0, -190.0), (-186.0, -234.0),
			(-202.0, -282.0), (-280.0, -300.0), (-342.0, -326.0),
		),
		'south_town': (
			(194.0, 358.0), (342.0, 238.0), (426.0, 82.0),
			(490.0, -46.0), (498.0, -214.0), (490.0, -274.0),
			(342.0, -274.0), (270.0, -338.0), (118.0, -322.0),
			(-62.0, -378.0), (-234.0, -374.0), (-342.0, -326.0),
		),
	},
	'47_canada_a': {
		'west_hills': (
			(-126.0, -306.0), (-250.0, -242.0), (-410.0, -38.0),
			(-442.0, 86.0), (-410.0, 262.0), (-354.0, 310.0),
			(-50.0, 418.0), (70.0, 410.0), (166.0, 370.0),
			(214.0, 330.0),
		),
		'central_road': (
			(-126.0, -306.0), (-158.0, -182.0), (-174.0, -38.0),
			(-154.0, 14.0), (-110.0, 58.0), (-50.0, 98.0),
			(-58.0, 214.0), (18.0, 294.0), (142.0, 330.0),
			(214.0, 330.0),
		),
		'east_shore': (
			(-126.0, -306.0), (-58.0, -286.0), (54.0, -234.0),
			(174.0, -190.0), (230.0, -142.0), (274.0, -86.0),
			(326.0, 50.0), (322.0, 142.0), (290.0, 234.0),
			(214.0, 330.0),
		),
	},
	'59_asia_great_wall': {
		# Two reviewed lanes approach the eastern gatehouse from distinct upper
		# directions, then use its real vehicle tunnel.  The #1513 BSP2 adapter
		# preserves that passage instead of treating the entire wall as one
		# collision triangle or missing the narrow opening between four-metre
		# samples and forcing both lanes onto the southern ridge.
		'wall_pass': (
			(-338.0, 386.0), (-235.0, 385.0), (-200.0, 386.0),
			(-180.0, 366.0), (-176.0, 350.0), (-160.0, 314.0),
			(-136.0, 282.0), (-108.0, 238.0), (-114.0, 118.0),
			(-67.0, -5.0), (-38.0, -39.0), (166.0, -108.0),
			(232.0, -107.0), (322.0, -86.0), (378.0, -106.0),
			(402.0, -186.0), (398.0, -378.0),
		),
		'valley': (
			(-338.0, 386.0), (-166.0, 402.0), (218.0, 448.0),
			(296.0, 422.0), (376.0, 355.0), (447.0, 252.0),
			(469.0, 151.0), (468.0, 65.0), (405.0, -92.0),
			(433.0, -269.0), (413.0, -309.0), (398.0, -378.0),
		),
		'ridge': (
			(-338.0, 386.0), (-418.0, 126.0), (-438.0, 14.0),
			(-430.0, -300.0), (-415.0, -365.0), (-370.0, -400.0),
			(-290.0, -414.0), (-238.0, -414.0), (-62.0, -370.0),
			(18.0, -370.0),
			(138.0, -414.0), (266.0, -430.0), (398.0, -378.0),
		),
	},
	'63_tundra': {
		'waterfall': (
			(14.0, -306.0), (22.0, -174.0), (-10.0, -146.0),
			(-90.0, -146.0), (-154.0, -94.0), (-178.0, 42.0),
			(-142.0, 78.0), (-90.0, 74.0), (-38.0, 162.0),
			(-2.0, 282.0),
		),
		'plateau': (
			(14.0, -306.0), (78.0, -226.0), (182.0, -206.0),
			(270.0, -142.0), (298.0, -82.0), (290.0, 10.0),
			(326.0, 38.0), (346.0, 90.0), (306.0, 138.0),
			(194.0, 194.0), (118.0, 250.0), (-2.0, 282.0),
		),
		'village': (
			(14.0, -306.0), (-130.0, -322.0), (-206.0, -290.0),
			(-334.0, -170.0), (-370.0, -70.0), (-358.0, -6.0),
			(-318.0, 130.0), (-258.0, 218.0), (-178.0, 254.0),
			(-2.0, 282.0),
		),
	},
	'73_asia_korea': {
		'temple': (
			(270.0, 266.0), (266.0, 198.0), (246.0, 154.0),
			(150.0, 86.0), (94.0, -18.0), (-114.0, -194.0),
			(-262.0, -290.0), (-278.0, -298.0),
		),
		'river': (
			(-278.0, -298.0), (-282.0, -158.0), (-322.0, -90.0),
			(-314.0, 42.0), (-334.0, 114.0), (-330.0, 190.0),
			(-300.0, 250.0), (-254.0, 282.0), (-114.0, 318.0),
			(14.0, 306.0), (138.0, 282.0), (270.0, 266.0),
		),
		'hills': (
			(270.0, 266.0), (294.0, 206.0), (286.0, -18.0),
			(310.0, -150.0), (294.0, -218.0), (186.0, -202.0),
			(86.0, -298.0), (-182.0, -294.0), (-278.0, -298.0),
		),
	},
	'83_kharkiv': {
		'factory': (
			(-198.0, -266.0), (-138.0, -174.0), (-70.0, -114.0),
			(-46.0, -54.0), (2.0, 10.0), (42.0, 22.0),
			(94.0, 74.0), (154.0, 94.0), (202.0, 126.0),
			(230.0, 158.0), (250.0, 194.0),
		),
		'square': (
			(-198.0, -266.0), (-230.0, -42.0), (-262.0, 118.0),
			(-246.0, 246.0), (-130.0, 282.0), (-102.0, 270.0),
			(-74.0, 302.0), (-54.0, 282.0), (150.0, 242.0),
			(222.0, 226.0), (250.0, 194.0),
		),
		'rail': (
			(-198.0, -266.0), (-46.0, -278.0), (74.0, -282.0),
			(178.0, -290.0), (250.0, -254.0), (266.0, -218.0),
			(282.0, -150.0), (306.0, -106.0), (282.0, -78.0),
			(282.0, 114.0), (250.0, 194.0),
		),
	},
	'84_winter': {
		'north_ridge': (
			(390.0, 238.0), (338.0, 294.0), (294.0, 378.0),
			(186.0, 446.0), (-14.0, 462.0), (-158.0, 398.0),
			(-238.0, 210.0), (-298.0, 118.0), (-314.0, -34.0),
			(-378.0, -138.0),
		),
		'ice_road': (
			(390.0, 238.0), (286.0, 238.0), (250.0, 298.0),
			(150.0, 318.0), (82.0, 254.0), (74.0, 162.0),
			(-10.0, 122.0), (-150.0, 54.0), (-250.0, -22.0),
			(-290.0, -86.0), (-378.0, -138.0),
		),
		# The painted southern crossing uses one narrow stock diagonal.  The
		# #1513 graph adapter verifies that edge against terrain, water, BSP2,
		# vehicle clearance and bidirectional grade before admitting it.
		'town': (
			(-378.0, -138.0), (-280.0, -175.0), (-160.0, -230.0),
			(-80.0, -290.0), (20.0, -305.0), (120.0, -285.0),
			(200.0, -255.0), (300.0, -210.0), (350.0, -60.0),
			(390.0, 130.0), (390.0, 238.0),
		),
	},
	'86_himmelsdorf_winter': {
		# The extractor matched by old-line proximity and swapped these names.
		# The inner city curve is banana; the east perimeter is the hill lane.
		# Round 4's replacement banana stroke meets the same pinned static
		# bld413 collision as map 04 and therefore cannot be admitted safely.
		'banana': (
			(2.0, -254.0), (170.0, -114.0), (190.0, -86.0),
			(154.0, -2.0), (110.0, 66.0), (74.0, 102.0),
			(126.0, 166.0), (126.0, 222.0), (126.0, 278.0),
			(138.0, 302.0),
			(70.0, 350.0),
		),
		'hill': (
			(2.0, -254.0), (102.0, -278.0), (326.0, -278.0),
			(382.0, -250.0),
			(398.0, -198.0), (394.0, -118.0), (358.0, -50.0),
			(386.0, 46.0), (386.0, 282.0), (162.0, 306.0),
			(70.0, 350.0),
		),
		'rail': (
			(2.0, -254.0), (-74.0, -210.0), (-130.0, -146.0),
			(-134.0, -30.0), (-198.0, 38.0), (-190.0, 166.0),
			(-160.0, 230.0), (-158.0, 306.0),
			(70.0, 350.0),
		),
	},
	'92_stalingrad': {
		'city': (
			(-106.0, 310.0), (-150.0, 250.0), (-294.0, 206.0),
			(-278.0, 158.0), (-246.0, 122.0), (-338.0, 82.0),
			(-366.0, -2.0), (-378.0, -102.0), (-362.0, -142.0),
			(-294.0, -186.0), (-234.0, -326.0),
			(-106.0, -406.0),
		),
		'railway': (
			(-106.0, -406.0), (50.0, -394.0), (106.0, -354.0),
			(198.0, -330.0), (250.0, -250.0), (250.0, -166.0),
			(250.0, -100.0), (250.0, 0.0), (250.0, 107.0),
			(250.0, 170.0), (254.0, 210.0), (190.0, 242.0),
			(158.0, 262.0), (82.0, 294.0), (-10.0, 322.0),
			(-106.0, 310.0),
		),
		'embankment': (
			(-106.0, -406.0), (-55.0, -385.0), (0.0, -355.0),
			(30.0, -335.0), (50.0, -305.0), (58.0, -275.0),
			(52.0, -230.0), (50.0, -180.0), (48.0, -130.0),
			(50.0, -94.0), (38.0, -42.0), (58.0, -2.0),
			(70.0, 158.0), (54.0, 214.0), (-6.0, 298.0),
			(-106.0, 310.0),
		),
	},
	'101_dday': {
		'beach': (
			(146.0, -398.0), (114.0, -326.0), (46.0, -258.0),
			(-2.0, -194.0), (-86.0, -14.0), (-90.0, 22.0),
			(-42.0, 114.0), (114.0, 278.0), (150.0, 402.0),
		),
		'village': (
			(146.0, -398.0), (138.0, -370.0), (174.0, -174.0),
			(170.0, 58.0), (206.0, 238.0), (198.0, 326.0),
			(150.0, 402.0),
		),
		'cliff': (
			(146.0, -398.0), (186.0, -290.0), (294.0, -174.0),
			(442.0, -86.0), (474.0, -22.0), (466.0, 62.0),
			(390.0, 326.0), (350.0, 350.0), (150.0, 402.0),
		),
	},
	'103_ruinberg_winter': {
		'city': (
			(-70.0, 306.0), (-10.0, 246.0), (42.0, 138.0),
			(78.0, -54.0), (58.0, -150.0),
			(10.0, -238.0), (-82.0, -290.0),
		),
		'field': (
			(-70.0, 306.0), (214.0, 274.0), (326.0, 210.0),
			(366.0, 114.0), (366.0, -222.0), (342.0, -270.0),
			(238.0, -322.0), (122.0, -326.0), (-82.0, -290.0),
		),
		'rail': (
			(-70.0, 306.0), (-110.0, 242.0), (-170.0, 202.0),
			(-290.0, 74.0), (-318.0, 18.0), (-282.0, -82.0),
			(-210.0, -142.0), (-82.0, -218.0),
			(-82.0, -290.0),
		),
	},
	'112_eiffel_tower_ctf': {
		'tower_west': (
			(-346.0, -22.0), (-274.0, 58.0), (-242.0, 174.0),
			(-118.0, 306.0), (-6.0, 354.0), (210.0, 242.0),
			(266.0, 90.0), (310.0, 50.0), (342.0, -18.0),
		),
		'center': (
			(-346.0, -22.0), (-242.0, 22.0),
			(-46.0, -14.0), (82.0, -14.0), (190.0, 38.0),
			(294.0, 42.0), (342.0, -18.0),
		),
		'tower_east': (
			(-346.0, -22.0), (-246.0, -158.0), (-138.0, -266.0),
			(-58.0, -378.0), (-38.0, -378.0), (-22.0, -350.0),
			(46.0, -334.0), (126.0, -270.0), (230.0, -118.0),
			(342.0, -18.0),
		),
	},
	'114_czech': {
		'town': (
			(-10.0, -346.0), (-206.0, -322.0), (-286.0, -242.0),
			(-312.0, -90.0), (-280.0, 100.0), (-260.0, 200.0),
			(-210.0, 265.0), (-90.0, 305.0), (-2.0, 338.0),
		),
		'valley': (
			(-10.0, -346.0), (34.0, -254.0), (46.0, -190.0),
			(62.0, -140.0), (62.0, -100.0), (62.0, -25.0),
			(62.0, 55.0), (62.0, 100.0), (62.0, 160.0),
			(30.0, 220.0), (0.0, 300.0), (-2.0, 338.0),
		),
		'ridge': (
			(-2.0, 338.0), (146.0, 354.0), (278.0, 242.0),
			(362.0, 158.0), (322.0, -2.0),
			(390.0, -166.0), (338.0, -286.0), (294.0, -334.0),
			(142.0, -318.0), (58.0, -326.0), (-10.0, -346.0),
		),
	},
}


# A few urban/lakeside corridors need their representative hard gate kept
# away from an endpoint or obstacle pocket.  Every other route uses the point
# farthest from the direct line between the two exact route starts.
REVIEWED_GATE_INDEXES = {
	'06_ensk': {'west_city': 4},
	'07_lakeville': {'east_town': 1},
	'17_munchen': {'west_streets': 1},
	'19_monastery': {'monastery_lane': 2},
	'29_el_hallouf': {'south_valley': 0},
	'31_airfield': {'south_towns': 3},
	'36_fishing_bay': {'central_road': 2},
	'37_caucasus': {'central_basin': 5},
	'45_north_america': {'north_road': 5, 'river_crossing': 5},
	'47_canada_a': {'central_road': 5},
	'59_asia_great_wall': {
		'wall_pass': 15, 'valley': 6, 'ridge': 6,
	},
	'73_asia_korea': {'river': 6, 'hills': 3},
	'84_winter': {'town': 4},
	'86_himmelsdorf_winter': {
		'banana': 2, 'hill': 4, 'rail': 6,
	},
	'92_stalingrad': {'railway': 7, 'embankment': 8},
	'103_ruinberg_winter': {'city': 3, 'field': 3, 'rail': 4},
	'112_eiffel_tower_ctf': {
		'tower_west': 4, 'center': 3, 'tower_east': 5,
	},
	'114_czech': {'town': 3, 'valley': 8, 'ridge': 5},
}


def _distance_to_route_start_line(point, start, end):
	dx = float(end[0]) - float(start[0])
	dz = float(end[1]) - float(start[1])
	length_squared = dx * dx + dz * dz
	if length_squared <= 0.000001:
		return 0.0
	amount = (((float(point[0]) - float(start[0])) * dx +
	           (float(point[1]) - float(start[1])) * dz) /
	          length_squared)
	amount = max(0.0, min(1.0, amount))
	near_x = float(start[0]) + dx * amount
	near_z = float(start[1]) + dz * amount
	offset_x = float(point[0]) - near_x
	offset_z = float(point[1]) - near_z
	return offset_x * offset_x + offset_z * offset_z


def _validated_route_starts(route_starts, map_name):
	if route_starts is None:
		return None
	if not isinstance(route_starts, (list, tuple)) or len(route_starts) != 2:
		raise ValueError(
			'reviewed tactical map needs exactly two route starts: %s' %
			map_name)
	result = []
	for point in route_starts:
		if not isinstance(point, (list, tuple)) or len(point) < 2:
			raise ValueError(
				'reviewed tactical route start is invalid: %s' % map_name)
		try:
			value = (float(point[0]), float(point[1]))
		except (TypeError, ValueError):
			raise ValueError(
				'reviewed tactical route start is invalid: %s' % map_name)
		if any(math.isnan(axis) or math.isinf(axis) for axis in value):
			raise ValueError(
				'reviewed tactical route start is not finite: %s' % map_name)
		result.append(value)
	return tuple(result)


def _orient_and_mark(points, route_starts=None, gate_index=None):
	values = tuple((float(point[0]), float(point[1])) for point in points)
	if len(values) < 2:
		raise ValueError('reviewed tactical route needs at least two gates')
	if route_starts is None:
		own_start, enemy_start = values[0], values[-1]
	else:
		own_start, enemy_start = route_starts

	def _distance_squared(point, base):
		return ((point[0] - float(base[0])) ** 2 +
		        (point[1] - float(base[1])) ** 2)

	forward = (_distance_squared(values[0], own_start) +
	           _distance_squared(values[-1], enemy_start))
	reverse = (_distance_squared(values[-1], own_start) +
	           _distance_squared(values[0], enemy_start))
	if reverse < forward:
		values = tuple(reversed(values))
		if gate_index is not None:
			gate_index = len(values) - 1 - int(gate_index)
	if gate_index is None:
		gate_index = max(
			range(len(values)),
			key=lambda index: _distance_to_route_start_line(
				values[index], own_start, enemy_start),
		)
	gate_index = int(gate_index)
	if gate_index < 0 or gate_index >= len(values):
		raise ValueError('reviewed tactical gate index is outside the route')
	return tuple((point[0], point[1], int(index == gate_index))
	             for index, point in enumerate(values))


def apply_reviewed_map(map_name, original, route_starts=None):
	"""Return one reviewed map, using exact route starts when supplied."""
	if map_name not in REVIEWED_ROUTE_POINTS:
		raise ValueError('reviewed tactical map is unavailable: %s' % map_name)
	route_points = REVIEWED_ROUTE_POINTS[map_name]
	updated = dict(original)
	route_starts = _validated_route_starts(route_starts, map_name)
	known_ids = set(route.get('id') for route in
	                original.get('routes', {}).get(1, ()))
	removed_ids = set(REMOVED_ROUTE_IDS.get(map_name, ()))
	capacity_overrides = REVIEWED_ROUTE_CAPACITIES.get(map_name, {})
	missing = set(route_points) - known_ids
	if missing:
		raise ValueError('reviewed tactical route is unavailable: %s %s' %
		             (map_name, ','.join(sorted(missing))))
	unknown_removed = removed_ids - known_ids
	if unknown_removed:
		raise ValueError('removed tactical route is unavailable: %s %s' %
		             (map_name, ','.join(sorted(unknown_removed))))
	if removed_ids & set(route_points):
		raise ValueError('reviewed tactical route is also removed: %s' %
		             map_name)
	unknown_capacities = set(capacity_overrides) - (
		known_ids - removed_ids)
	if unknown_capacities:
		raise ValueError('reviewed tactical capacity is unavailable: %s %s' %
		             (map_name,
		              ','.join(sorted(unknown_capacities))))

	team_one = {}
	for route_id, points in route_points.items():
		gate_index = REVIEWED_GATE_INDEXES.get(
			map_name, {}).get(route_id)
		team_one[route_id] = _orient_and_mark(
			points, route_starts, gate_index)
	new_routes = {}
	for team in (1, 2):
		converted = []
		for original_route in original.get('routes', {}).get(team, ()):
			if original_route.get('id') in removed_ids:
				continue
			route = dict(original_route)
			route['role_weights'] = dict(
				original_route.get('role_weights', {}) or {})
			route_id = route.get('id')
			if route_id in capacity_overrides:
				route['capacity'] = int(capacity_overrides[route_id])
			if route_id in team_one:
				waypoints = team_one[route_id]
				if team == 2:
					waypoints = tuple(reversed(waypoints))
				route['waypoints'] = tuple(waypoints)
			converted.append(route)
		new_routes[team] = tuple(converted)
	updated['routes'] = new_routes
	return updated


def apply_reviewed_routes(tactical_maps, route_starts_by_map=None):
	"""Replace reviewed route geometry while preserving tactical metadata."""
	route_starts_by_map = route_starts_by_map or {}
	for map_name in REVIEWED_ROUTE_POINTS:
		if map_name not in tactical_maps:
			raise ValueError('reviewed tactical map is unavailable: %s' % map_name)
		tactical_maps[map_name] = apply_reviewed_map(
			map_name, tactical_maps[map_name],
			route_starts_by_map.get(map_name))
