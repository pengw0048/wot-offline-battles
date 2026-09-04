""" BWT2 (Terrain 2) """

from .v0_9_12 import BWT2_Section_0_9_12
from .v0_9_14 import BWT2_Section_0_9_14
from .v0_9_20 import BWT2_Section_0_9_20
from .v1_0_0 import BWT2_Section_1_0_0
from .v1_0_1 import BWT2_Section_1_0_1
from .v1_1_0 import BWT2_Section_1_1_0
from .v1_4_0 import BWT2_Section_1_4_0
from .v1_6_1 import BWT2_Section_1_6_1



"""
.chunk example:

<root>
	<terrain>
		<resource>ffff0000o.cdata/terrain2</resource>
		<visibilityMask>4294967295</visibilityMask>
	</terrain>
</root>


space.settings example:

<root>
	...
	<bounds>
		<minX>	-1	</minX>
		<maxX>	0	</maxX>
		<minY>	-1	</minY>
		<maxY>	0	</maxY>
	</bounds>
	<terrain>
		<version>	200	</version>
		<lodMapSize>	16	</lodMapSize>
		<aoMapSize>	16	</aoMapSize>
		<heightMapSize>	16	</heightMapSize>
		<normalMapSize>	16	</normalMapSize>
		<normalMapCaching>	true	</normalMapCaching>
		<holeMapSize>	64	</holeMapSize>
		<shadowMapSize>	16	</shadowMapSize>
		<blendMapSize>	16	</blendMapSize>
		<blendMapCaching>	true	</blendMapCaching>
		<lodInfo>
			<startBias>	0.800000	</startBias>
			<endBias>	0.990000	</endBias>
			<lodTextureStart>	600.000000	</lodTextureStart>
			<lodTextureDistance>	100.000000	</lodTextureDistance>
			<blendPreloadDistance>	600.000000	</blendPreloadDistance>
			<macroLODStart>	0.000000	</macroLODStart>
			<bumpFadingStart>	600.000000	</bumpFadingStart>
			<bumpFadingDistance>	100.000000	</bumpFadingDistance>
			<defaultHeightMapLod>	0	</defaultHeightMapLod>
			<detailHeightMapDistance>	500.000000	</detailHeightMapDistance>
			<lodDistances>
				<distance0>	25.000000	</distance0>
				<distance1>	202.000000	</distance1>
				<distance2>	379.000000	</distance2>
			</lodDistances>
			<server>
				<heightMapLod>	0	</heightMapLod>
			</server>
		</lodInfo>
		<detailNormal>
			<normalMap/>
			<wrapU>	4.000000	</wrapU>
			<wrapV>	4.000000	</wrapV>
		</detailNormal>
		<tintTexture/>
		<noiseTexture/>
		<borderline>
			<width>	0.001000	</width>
			<blendMultiplier>	0.400000	</blendMultiplier>
			<fadingStart>	0.000000	</fadingStart>
			<fadingEnd>	100.000000	</fadingEnd>
			<attenuationDistance>	300.000000	</attenuationDistance>
		</borderline>
		<soundOcclusion>
			<directOcclusion>	0.000000	</directOcclusion>
			<reverbOcclusion>	0.000000	</reverbOcclusion>
		</soundOcclusion>
		<editor>
			<enableAutoRebuildNormalMap>	true	</enableAutoRebuildNormalMap>
		</editor>
	</terrain>
	...
</root>
"""
