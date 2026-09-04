""" WTCP (WoT static scene Control Point) """

from .v0_9_12 import WTCP_Section_0_9_12
from .v0_9_20 import WTCP_Section_0_9_20
from .v1_23_0 import WTCP_Section_1_23_0



"""
.chunk example:

from WoT 0.9.10:

<root>
	<ControlPoint>
		<visibilityMask>4294959113</visibilityMask>
		<transform>
			<row0>1.000000 0.000000 0.000000</row0>
			<row1>0.000000 1.000000 0.000000</row1>
			<row2>0.000000 0.000000 1.000000</row2>
			<row3>97.524170 56.690334 2.612030</row3>
		</transform>
		<radius>50.000000</radius>
		<team>1</team>
		<baseID>1</baseID>
		<pointsPerSecond>1.000000</pointsPerSecond>
		<maxPointsPerSecond>3.000000</maxPointsPerSecond>
		<overTerrainHeight>0.200000</overTerrainHeight>
		<ownerStopsCapturing>false</ownerStopsCapturing>
		<radiusColor>1.000000 1.000000 1.000000 1.000000</radiusColor>
		<flagPath>content/MilitaryEnvironment/mle000_BaseFlagstaff/normal/lod0/flag.model</flagPath>
		<flagstaffPath>content/MilitaryEnvironment/mle000_BaseFlagstaff/normal/lod0/mle000_BaseFlagstaff01.model</flagstaffPath>
		<radiusPath>content/Interface/CheckPoint/CheckPoint.visual</radiusPath>
		<flagScale>3.000000</flagScale>
		<beforeWind>false</beforeWind>
		<applyOverlay>false</applyOverlay>
		<eventName>/ambient/flag/flag_flapping</eventName>
		<maxDistance>15.000000</maxDistance>
	</ControlPoint>
</root>



from WoT 1.0:

<root>
	<ControlPoint>
		<editorOnly>
			<hidden>	false	</hidden>
			<frozen>	false	</frozen>
			<terrainBinding>
				<row0>	1.000000 0.000000 0.000000	</row0>
				<row1>	0.000000 1.000000 0.000000	</row1>
				<row2>	0.000000 0.000000 1.000000	</row2>
				<row3>	0.000000 56.611996 0.000000	</row3>
			</terrainBinding>
		</editorOnly>
		<visibilityMask>	4294959113	</visibilityMask>
		<transform>
			<row0>	1.000000 0.000000 0.000000	</row0>
			<row1>	0.000000 1.000000 0.000000	</row1>
			<row2>	0.000000 0.000000 1.000000	</row2>
			<row3>	99.683578 56.690315 2.612029	</row3>
		</transform>
		<radius>	50.000000	</radius>
		<team>	1	</team>
		<baseID>	1	</baseID>
		<pointsPerSecond>	1.000000	</pointsPerSecond>
		<maxPointsPerSecond>	3.000000	</maxPointsPerSecond>
		<overTerrainHeight>	0.200000	</overTerrainHeight>
		<ownerStopsCapturing>	false	</ownerStopsCapturing>
		<radiusColor>	1.000000 1.000000 1.000000 1.000000	</radiusColor>
		<flagPath>	content/MilitaryEnvironment/mle000_BaseFlagstaff/normal/lod0/flag.model	</flagPath>
		<flagstaffPath>	content/MilitaryEnvironment/mle000_BaseFlagstaff/normal/lod0/mle000_BaseFlagstaff01.model	</flagstaffPath>
		<radiusPath>	content/Interface/CheckPoint/CheckPoint.visual	</radiusPath>
		<flagScale>	3.000000	</flagScale>
		<beforeWind>	false	</beforeWind>
		<applyOverlay>	false	</applyOverlay>
		<wweventName>	ambient_static_objects_flag_flapping	</wweventName>
	</ControlPoint>
</root>
"""
