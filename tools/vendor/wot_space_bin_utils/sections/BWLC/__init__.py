""" BWLC (Lights) """

from .v0_9_12 import BWLC_Section_0_9_12
from .v1_0_0 import BWLC_Section_1_0_0
from .v1_0_1 import BWLC_Section_1_0_1
from .v1_6_0 import BWLC_Section_1_6_0
from .v1_7_0 import BWLC_Section_1_7_0
from .v1_11_0 import BWLC_Section_1_11_0
from .v1_15_0 import BWLC_Section_1_15_0
from .v1_31_0_ru import BWLC_Section_1_31_0_RU



"""

ignored types:
    directionLight
    ambientLight


.chunk example:

<root>
    <omniLight>
        <visibilityMask>4294967295</visibilityMask>
        <colour>255.000000 203.000275 151.000000</colour>
        <position>96.709732 1.857199 93.062500</position>
        <innerRadius>0.018646</innerRadius>
        <outerRadius>0.088962</outerRadius>
        <castShadows>true</castShadows>
        <priority>0</priority>
        <multiplier>5.000000</multiplier>
        <drawType>0</drawType>
    </omniLight>
    <spotLight>
        <visibilityMask>4294967295</visibilityMask>
        <colour>255.000000 225.000061 176.000000</colour>
        <position>96.632439 1.851571 93.073433</position>
        <direction>0.949620 0.223978 0.219208</direction>
        <innerRadius>0.100000</innerRadius>
        <outerRadius>3.314713</outerRadius>
        <coneAngle>0.930916</coneAngle>
        <castShadows>false</castShadows>
        <multiplier>3.000000</multiplier>
        <priority>0</priority>
        <drawType>0</drawType>
    </spotLight>
    <pulseSpotLight>
        <visibilityMask>4294967295</visibilityMask>
        <colour>254.000015 201.000015 148.000000</colour>
        <position>4.977625 4.150421 95.923676</position>
        <direction>0.962372 0.006124 -0.271658</direction>
        <innerRadius>0.517881</innerRadius>
        <outerRadius>1.209609</outerRadius>
        <castShadows>false</castShadows>
        <multiplier>100.000000</multiplier>
        <coneAngle>0.092004</coneAngle>
        <timeScale>1.000000</timeScale>
        <duration>1.000000</duration>
        <priority>0</priority>
        <drawType>0</drawType>
        <frame>0.000000 8.000000</frame>
        <frame>1.000000 8.000000</frame>
    </pulseSpotLight>
    <pulseLight>
        <visibilityMask>4294967295</visibilityMask>
        <colour>254.000076 236.000076 180.000000</colour>
        <position>92.009987 7.139999 13.569998</position>
        <innerRadius>0.280633</innerRadius>
        <outerRadius>0.299407</outerRadius>
        <castShadows>false</castShadows>
        <multiplier>50.000000</multiplier>
        <timeScale>1.000000</timeScale>
        <duration>15.100000</duration>
        <animation/>
        <priority>0</priority>
        <frame>0.000000 100.000000</frame>
        <frame>0.050000 0.000000</frame>
        <frame>0.100000 100.000000</frame>
        <frame>0.500000 0.000000</frame>
        <frame>0.600000 0.000000</frame>
        <frame>0.700000 0.000000</frame>
        <frame>2.600000 0.000000</frame>
        <frame>2.800000 0.000000</frame>
        <frame>2.800000 0.000000</frame>
        <frame>2.900000 0.000000</frame>
        <frame>3.000000 0.000000</frame>
        <frame>3.100000 0.000000</frame>
        <frame>3.400000 0.000000</frame>
        <frame>3.900000 0.000000</frame>
        <frame>4.200000 0.000000</frame>
        <frame>4.700000 0.000000</frame>
        <frame>4.800000 0.000000</frame>
        <frame>5.000000 0.000000</frame>
        <frame>14.600000 0.000000</frame>
        <frame>14.800000 0.000000</frame>
        <frame>15.100000 0.000000</frame>
        <drawType>0</drawType>
    </pulseLight>
</root>
"""
