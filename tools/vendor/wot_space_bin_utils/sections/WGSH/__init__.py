""" WGSH (SHVolume) """

from .v0_9_20 import WGSH_Section_0_9_20
from .v1_0_0 import WGSH_Section_1_0_0



"""
.chunk example

from WoT 1.0:

<root>
	<shGridVolume>
		<cellSize>	4.000000	</cellSize>
		<elevation>	3.500000	</elevation>
		<globalHeight>	10.000000	</globalHeight>
		<distFromWall>	0.800000	</distFromWall>
		<searchRadius>	1.000000	</searchRadius>
		<useBestFit>	true	</useBestFit>
		<findClosestWall>	true	</findClosestWall>
		<alwaysShowProbes>	false	</alwaysShowProbes>
		<useQuadTree>	false	</useQuadTree>
		<showQuadTree>	true	</showQuadTree>
		<slopeThreshold>	1.500000	</slopeThreshold>
		<maxDepth>	7	</maxDepth>
		<enable>	true	</enable>
		<position>	99.999321 0.999995 0.000000	</position>
		<scale>	249.999893 19.999939 249.999969	</scale>
		<globalLerp>	10.000000	</globalLerp>
	</shGridVolume>
</root>
"""
