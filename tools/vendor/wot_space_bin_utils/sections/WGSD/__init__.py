""" WGSD (Decal) """

from .v0_9_12 import WGSD_Section_0_9_12
from .v1_0_0 import WGSD_Section_1_0_0
from .v1_22_0 import WGSD_Section_1_22_0



"""
.chunk example:

from WoT 0.9.10:

<root>
	<staticDecal>
		<visibilityMask>4294967295</visibilityMask>
		<transform>
			<row0>0.007189 0.000000 0.259413</row0>
			<row1>0.000000 2.126485 0.000000</row1>
			<row2>-0.072897 0.000000 0.002020</row2>
			<row3>89.262558 2.457689 82.189613</row3>
		</transform>
		<priority>3</priority>
		<type>1</type>
		<influence>18</influence>
		<diffTex>diffTex.dds</diffTex>
		<bumpTex>bumpTex.dds</bumpTex>
		<hmTex>hmTex.dds</hmTex>
		<addTex>addTex.dds</addTex>
		<offsets>0.000000 0.000000 0.000000 0.000000</offsets>
		<uvWrapping>1.000000 1.000000</uvWrapping>
		<accurate>0</accurate>
	</staticDecal>
</root>



from WoT 1.0:

<root>
	<staticDecal>
		<editorOnly>
			<hidden>	false	</hidden>
			<frozen>	false	</frozen>
			<terrainBinding>
				<row0>	0.993969 0.109662 0.000768	</row0>
				<row1>	-0.109557 0.993283 -0.037220	</row1>
				<row2>	-0.004844 0.036912 0.999307	</row2>
				<row3>	0.830979 -2.080859 0.232431	</row3>
			</terrainBinding>
		</editorOnly>
		<visibilityMask>	4294967295	</visibilityMask>
		<transform>
			<row0>	4.881178 0.527406 -0.946395	</row0>
			<row1>	0.934489 0.160560 4.909246	</row1>
			<row2>	0.109646 -0.993900 0.011635	</row2>
			<row3>	15.475554 6.257648 6.602108	</row3>
		</transform>
		<diffTex>	maps/decals_pbs/Tree_Roots_AM.dds	</diffTex>
		<bumpTex>	maps/decals_pbs/Tree_Roots_nm.dds	</bumpTex>
		<hmTex>	maps/decals_pbs/Tree_Roots_gmm.dds	</hmTex>
		<addTex />
		<priority>	0	</priority>
		<type>	1	</type>
		<influence>	2	</influence>
		<offsets>	0.000000 0.000000 0.000000 0.000000	</offsets>
		<uvWrapping>	1.000000 1.000000	</uvWrapping>
		<accurate>	3	</accurate>
		<tilesFade>	1.000000	</tilesFade>
		<invertDistanceFadeFlag>	false	</invertDistanceFadeFlag>
		<parallax_offset>	0.010000	</parallax_offset>
		<parallax_mode>	1.000000	</parallax_mode>
		<parallax_amplitude>	0.010000	</parallax_amplitude>
	</staticDecal>
</root>
"""
