""" BWWa (Water) """

from .v0_9_12 import BWWa_Section_0_9_12
from .v1_0_0 import BWWa_Section_1_0_0
from .v1_22_0 import BWWa_Section_1_22_0



"""
.chunk example:

from WoT 0.9.10:

<root>
	<vlo>
		<uid>	d881daba.4f7f166e.b35d7f8a.145db23d	</uid>
		<type>	water	</type>
	</vlo>
</root>


.vlo example:

<root>
	<water>
		<position>	-69.300003 1.800000 17.300001	</position>
		<orientation>	0.000000	</orientation>
		<size>	120.000000 0.000000 120.000000	</size>
		<fresnelConstant>	0.300000	</fresnelConstant>
		<fresnelExponent>	5.000000	</fresnelExponent>
		<reflectionTint>	1.000000 1.000000 1.000000 1.000000	</reflectionTint>
		<reflectionStrength>	0.040000	</reflectionStrength>
		<refractionTint>	1.000000 1.000000 1.000000 1.000000	</refractionTint>
		<refractionStrength>	0.040000	</refractionStrength>
		<tessellation>	10.000000	</tessellation>
		<consistency>	0.950000	</consistency>
		<textureTessellation>	10.000000	</textureTessellation>
		<scrollSpeed1>	-1.000000 0.500000	</scrollSpeed1>
		<scrollSpeed2>	1.000000 0.000000	</scrollSpeed2>
		<waveScale>	1.000000 0.750000	</waveScale>
		<windVelocity>	0.020000	</windVelocity>
		<sunPower>	32.000000	</sunPower>
		<waveTexture>	system/maps/waves2.dds	</waveTexture>
		<cellsize>	100.000000	</cellsize>
		<smoothness>	0.000000	</smoothness>
		<useEdgeAlpha>	true	</useEdgeAlpha>
		<useSimulation>	true	</useSimulation>
	</water>
</root>



from WoT 1.0:

<root>
	<water>
		<orientation>	0.000000	</orientation>
		<waveTexture>	system/maps/water/river_normal_animation/normal	</waveTexture>
		<waveHeightTexture>	system/maps/water/river_height_animation/height	</waveHeightTexture>
		<waveTexture2>	system/maps/water/stream_normal_animation/normal	</waveTexture2>
		<waveHeightTexture2>	system/maps/water/stream_height_animation/height	</waveHeightTexture2>
		<rampTexture>	system/maps/water/ramp_clear_water.dds	</rampTexture>
		<flowmapTexture0>	spaces/hangar_v3/water/flow_map.dds	</flowmapTexture0>
		<flowmapTexture1>	spaces/hangar_v3/water/wave_amplitude_foam.dds	</flowmapTexture1>
		<foamTexture>	system/maps/water/Foam01.dds	</foamTexture>
		<position>	-107.198006 5.718000 -28.654001	</position>
		<size>	243.104019 0.000000 500.000031	</size>
		<fogDepth>	20.000000	</fogDepth>
		<fogColorMultiplier>	1.000000	</fogColorMultiplier>
		<fogColor>	0.000000 0.200000 0.330000 1.000000	</fogColor>
		<causticsIntensity>	1.000000	</causticsIntensity>
		<forwardReflectionIntensity>	1.000000	</forwardReflectionIntensity>
		<minOpacity>	0.000000	</minOpacity>
		<waveTextureNumber>	32	</waveTextureNumber>
		<animationSpeed>	8.000000	</animationSpeed>
		<scaleParameters>	50.000000 2.000000 0.300000 1.000000	</scaleParameters>
		<textureRotation>	-70.000000	</textureRotation>
		<scroll1>	0.000000 0.000000	</scroll1>
		<waveTextureNumber2>	32	</waveTextureNumber2>
		<animationSpeed2>	4.000000	</animationSpeed2>
		<scaleParameters2>	50.000000 2.000000 0.220000 0.800000	</scaleParameters2>
		<textureRotation2>	-95.000000	</textureRotation2>
		<scroll2>	0.000000 0.000000	</scroll2>
		<flowMapParameters>	0.000000 0.000000 0.500000 0.270000	</flowMapParameters>
		<flowMapParameters2>	0.000000 0.000000 0.250000 0.600000	</flowMapParameters2>
		<rampDepth>	20.000000	</rampDepth>
		<wetOverIntensity>	1.000000	</wetOverIntensity>
		<wetPower>	3.000000	</wetPower>
		<wetUnderwaterPower>	0.500000	</wetUnderwaterPower>
		<wetHeight>	0.250000	</wetHeight>
		<foamContrast>	0.200000	</foamContrast>
		<foamEnabled>	true	</foamEnabled>
		<foamFlowMapParameters>	0.500000 1.500000	</foamFlowMapParameters>
		<foamScroll>	-0.020000 -0.100000	</foamScroll>
		<foamScale>	0.510000	</foamScale>
		<softDepth>	0.300000	</softDepth>
		<useFlowmap>	true	</useFlowmap>
		<useAmplitudesMap>	true	</useAmplitudesMap>
		<flowMapInvertX>	false	</flowMapInvertX>
		<flowMapInvertZ>	true	</flowMapInvertZ>
		<wave2Enabled>	true	</wave2Enabled>
		<outWaterSides0>	false	</outWaterSides0>
		<outWaterSides1>	false	</outWaterSides1>
		<outWaterSides2>	false	</outWaterSides2>
		<outWaterSides3>	false	</outWaterSides3>
		<outWaterCorners0>	false	</outWaterCorners0>
		<outWaterCorners1>	false	</outWaterCorners1>
		<outWaterCorners2>	false	</outWaterCorners2>
		<outWaterCorners3>	false	</outWaterCorners3>
		<spaceMinX>	-299.500000	</spaceMinX>
		<spaceMinZ>	-299.500000	</spaceMinZ>
		<spaceMaxX>	199.500000	</spaceMaxX>
		<spaceMaxZ>	199.500000	</spaceMaxZ>
		<useWaterProbes>	false	</useWaterProbes>
		<forwardScroll>	0.050000 -0.010000	</forwardScroll>
		<cameraOffset>	0.000000	</cameraOffset>
		<bboxMin>	-228.750015 5.718000 -278.654022	</bboxMin>
		<bboxMax>	14.354004 5.718000 221.346008	</bboxMax>
		<id>	4F504BDA.476DEBEC.EE800CA4.4A851451	</id>
		<editorOnly>
			<hidden>	false	</hidden>
			<frozen>	false	</frozen>
		</editorOnly>
		<visibilityMask>	4294967295	</visibilityMask>
	</water>
</root>
"""
