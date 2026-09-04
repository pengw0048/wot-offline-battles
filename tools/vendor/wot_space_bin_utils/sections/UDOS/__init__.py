""" UDOS (User Data Object Section?) """

from .._base_xml_section import *



__all__ = ('UDOS_Section_0_9_12',)



class UDOS_Section_0_9_12(Base_XML_Section):
	header = 'UDOS'
	int1 = 1
