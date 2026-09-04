from collections import OrderedDict
from struct import unpack
from io import BytesIO, SEEK_SET, SEEK_CUR, SEEK_END



def from_AnimationChannel(data):
	'''
	AnimationChannel
	'''
	identifierLen = unpack('<I', data.read(4))[0]
	#return {'identifier': data.read(identifierLen).decode('ascii')}
	return data.read(identifierLen).decode('ascii')



def from_InterpolatedAnimationChannel(data, actype):
	'''
	InterpolatedAnimationChannel
	'''
	ac = from_AnimationChannel(data)

	if actype == 1:
		pass
	elif actype == 3:
		pass
	elif actype == 4:
		data.seek(4, SEEK_CUR) # scaleCompressionError f
		data.seek(4, SEEK_CUR) # positionCompressionError f
		data.seek(4, SEEK_CUR) # rotationCompressionError f

	cnt = unpack('<I', data.read(4))[0]
	data.seek(cnt*4*4, SEEK_CUR) # scaleKeys 4f

	cnt = unpack('<I', data.read(4))[0]
	data.seek(cnt*4*4, SEEK_CUR) # positionKeys 4f

	cnt = unpack('<I', data.read(4))[0]
	data.seek(cnt*4*5, SEEK_CUR) # rotationKeys 5f

	cnt = unpack('<I', data.read(4))[0]
	data.seek(cnt*4, SEEK_CUR) # scaleIndex I

	cnt = unpack('<I', data.read(4))[0]
	data.seek(cnt*4, SEEK_CUR) # positionIndex I

	cnt = unpack('<I', data.read(4))[0]
	data.seek(cnt*4, SEEK_CUR) # rotationIndex I

	return ac



def from_MorphAnimationChannel(data, actype):
	'''
	MorphAnimationChannel
	'''
	ac = from_AnimationChannel(data)

	cnt = unpack('<I', data.read(4))[0]
	data.seek(cnt*4, SEEK_CUR) # influences f

	return ac



def from_StreamedAnimationChannel(data, actype):
	'''
	StreamedAnimationChannel
	'''
	ac = from_AnimationChannel(data)
	data.seek(12, SEEK_CUR) # scaleFallback 3f
	data.seek(12, SEEK_CUR) # positionFallback 3f
	data.seek(16, SEEK_CUR) # rotationFallback 4f
	return ac



type_to_func = {
	1: from_InterpolatedAnimationChannel,
	2: from_MorphAnimationChannel,
	3: from_InterpolatedAnimationChannel,
	4: from_InterpolatedAnimationChannel,
	5: from_StreamedAnimationChannel,
}



def animation_load(data):
	data = BytesIO(data)

	entryInfo = {'animation_channels':[]}

	entryInfo['totalTime'] = unpack('<I', data.read(4))[0]

	identifierLen = unpack('<I', data.read(4))[0]
	entryInfo['identifier'] = data.read(identifierLen).decode('ascii')

	internalIdentifierLen = unpack('<I', data.read(4))[0]
	entryInfo['internalIdentifier'] = data.read(internalIdentifierLen).decode('ascii')

	ncb = unpack('<i', data.read(4))[0]
	for i in range(ncb):
		type = unpack('<i', data.read(4))[0]
		assert type in type_to_func, type
		entryInfo['animation_channels'].append(type_to_func[type](data, type))

	return entryInfo



def anca_load(data, secName):
	packedGroups = OrderedDict()

	data = BytesIO(data)

	ENTRY_DATA_MASK = ~(1<<31)
	data.seek(-4, SEEK_END)
	endEntriesInfo = data.tell()
	startEntriesInfoOffset = unpack('<I', data.read(4))[0]
	data.seek(-4-startEntriesInfoOffset, SEEK_END)
	position = 0

	while data.tell() < endEntriesInfo:
		entryDataLen, entryPreloadLen, entryVersion, entryModified, nameLen = unpack('<3IQI', data.read(24))
		entryDataLen &= ENTRY_DATA_MASK
		assert entryVersion == 6
		dataName = data.read(nameLen).decode('ascii')
		packedGroups[dataName] = {
			'length': entryDataLen,
			'position': position
		}
		position += (entryDataLen + 3) & (~3)
		data.seek(((nameLen+3) & ~3)-nameLen, SEEK_CUR)

	entryInfo = packedGroups[secName]

	offset = entryInfo['position']
	data.seek(offset, SEEK_SET)

	return animation_load(data.read(entryInfo['length']))
