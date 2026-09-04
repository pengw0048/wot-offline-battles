""" SpTr (SpeedTree) """

from .v0_9_12 import SpTr_Section_0_9_12
from .v0_9_20 import SpTr_Section_0_9_20
from .v1_0_0 import SpTr_Section_1_0_0



"""
.chunk example

from WoT 0.9.10:

<root>
	<speedtree>
		<visibilityMask>4294967295</visibilityMask>
		<spt>speedtree/02_Malinovka/Spruce.spt</spt>
		<seed>1</seed>
		<transform>
			<row0>0.878626 -0.000001 -0.477496</row0>
			<row1>0.000000 0.999994 0.000000</row1>
			<row2>0.477496 0.000000 0.878626</row2>
			<row3>42.985939 -0.574993 58.981873</row3>
		</transform>
		<reflectionVisible>false</reflectionVisible>
		<castsShadow>true</castsShadow>
		<castsLocalShadow>false</castsLocalShadow>
		<editorOnly>
			<castsShadow>true</castsShadow>
		</editorOnly>
		<alwaysDynamic>true</alwaysDynamic>
	</speedtree>
</root>



from WoT 1.0:

<root>
	<speedtree>
		<editorOnly>
			<hidden>	false	</hidden>
			<frozen>	false	</frozen>
			<terrainBinding>
				<row0>	1.000000 0.000000 0.000000	</row0>
				<row1>	0.000000 1.000000 0.000000	</row1>
				<row2>	0.000000 0.000000 1.000000	</row2>
				<row3>	0.000000 -1.437946 0.000000	</row3>
			</terrainBinding>
		</editorOnly>
		<visibilityMask>	4294967295	</visibilityMask>
		<metaData>
			<created_by>	a_khadzko	</created_by>
			<created_on>	1482241132	</created_on>
			<modified_by>	a_khadzko	</modified_by>
			<modified_on>	1492872931	</modified_on>
		</metaData>
		<spt>	vegetation/Conifers/Spruce/Spruce_24m.srt	</spt>
		<seed>	0	</seed>
		<transform>
			<row0>	0.665076 0.000000 0.000000	</row0>
			<row1>	0.000000 0.665076 0.000000	</row1>
			<row2>	0.000000 0.000000 0.665076	</row2>
			<row3>	7.538100 8.189894 95.288872	</row3>
		</transform>
		<reflectionVisible>	false	</reflectionVisible>
		<castsShadow>	true	</castsShadow>
		<alwaysDynamic>	false	</alwaysDynamic>
		<serverId>	0	</serverId>
	</speedtree>
</root>
"""
