from struct import pack
from xml.etree import ElementTree as ET
import base64



class PackedTypes:
	Section  = 0
	String   = 1
	Int      = 2
	Float    = 3
	Bool     = 4
	Blob     = 5
	Enc_blob = 6



class XmlPacker:
	PACKED_HEADER = 0x62a14e45

	def pack(self, tree):
		res = pack('<I', self.PACKED_HEADER)
		res += self.collectStrings(tree)
		res += self.serializeSection(tree)
		return res

	def collectStrings(self, tree):
		strings = []
		res = b'\0'
		for el in tree.iter():
			tag = el.tag
			if tree == el:
				continue
			if tag in ('row0', 'row1', 'row2', 'row3'):
				continue
			if tag not in strings:
				strings.append(tag)
		strings = sorted(strings)
		for tag in strings:
			res += tag.encode('utf8') + b'\0'
		self.strings = strings
		res += b'\0'
		return res

	def serializeNode(self, node, simple):
		if is_matrix(node):
			res = b''
			for el in node:
				for f in el.text.strip().split():
					res += pack('<f', float(f))
			return (PackedTypes.Float, res)
		if not simple and len(node) > 0:
			return (PackedTypes.Section, self.serializeSection(node))

		if not node.text:
			return (PackedTypes.String, b'')

		text = node.text.strip()

		if is_int(text) and is_intN(int(text), 64):
			val = int(text)
			if val == 0:
				return (PackedTypes.Int, b'')
			if is_intN(val, 8):
				return (PackedTypes.Int, pack('<b', val))
			if is_intN(val, 16):
				return (PackedTypes.Int, pack('<h', val))
			if is_intN(val, 32):
				return (PackedTypes.Int, pack('<i', val))
			return (PackedTypes.Int, pack('<q', val))
		elif is_floats(text):
			res = b''
			for f in text.split():
				res += pack('<f', float(f))
			return (PackedTypes.Float, res)
		if is_bool(text):
			if text == 'true':
				return (PackedTypes.Bool, b'\1')
			return (PackedTypes.Bool, b'')
		if is_b64(text):
			return (PackedTypes.Blob, base64.b64decode(text))
		return (PackedTypes.String, text.encode('utf8'))

	def serializeSection(self, tree):
		ownData = self.serializeNode(tree, True)
		childData = []
		for el in tree:
			childData.append((self.strings.index(el.tag), self.serializeNode(el, False)))
		res = pack('<H', len(childData))
		ownDescr = buildDescr(ownData, 0)
		res += packDescr(ownDescr)
		currentOffset = ownDescr[1]
		for stringId, data in childData:
			res += pack('<H', stringId)
			descr = buildDescr(data, currentOffset)
			res += packDescr(descr)
			currentOffset = descr[1]
		res += ownData[1]
		for _, data in childData:
			res += data[1]
		return res



def packDescr(block):
	return pack('<I', block[0] << 28 | block[1])



def buildDescr(block, prevOffset):
	return (block[0], prevOffset + len(block[1]))



def is_floats(s):
	for i in s.split():
		if not i.lstrip('-').replace('.','',1).isdigit():
			return False
	return True



def is_matrix(node):
	if len(node) != 4:return False
	for i, el in enumerate(node):
		if el.tag != 'row%d' % i:
			return False
		if not is_floats(el.text):
			return False
		if len(el.text.split()) != 3:
			return False
	return True



def is_int(s):
	return s.lstrip('-').isdigit()



def is_intN(v, n):
	return abs(v) < (1<<(n-1))-1



def is_bool(s):
	return s in ('true', 'false')



def is_b64(s):
	try:
		tmp = base64.b64decode(s)
		return base64.b64encode(tmp).decode('utf8') == s
	except Exception:
		return False
