""" BWTB (Root Table) """

from struct import unpack



class Row:
	header = None # section magic
	int1 = None
	position = None
	int3 = None
	length = None
	rows_num = None

	def __init__(self, data):
		(self.header, self.int1,
		self.position, self.int3,
		self.length, self.rows_num) = unpack('<4s5I', data)
		self.header = self.header.decode('ascii')
		assert self.header in [
			'BWTB',
			'BWST', # in WorldOfTanks.exe: StringTable
			'BWAL', # in WorldOfTanks.exe: AssetList
			'BWCS', # in WorldOfTanks.exe: CompiledSpaceSettings
			'BWSG', # in WorldOfTanks.exe: StaticGeometry
			'BSGD', # in WorldOfTanks.exe: StaticGeometryData
			'BWT2', # in WorldOfTanks.exe: Terrain2SceneFeature
			'BSMI', # in WorldOfTanks.exe: StaticSceneModelInstances
			'BSMO', # in WorldOfTanks.exe: Static Model
			'BSMA', # in WorldOfTanks.exe: Static Materials
			'SpTr', # in WorldOfTanks.exe: StaticSceneSpeedTree
			'BWfr', # in WorldOfTanks.exe: StaticSceneFlare
			'WGSD', # in WorldOfTanks.exe: StaticSceneDecal
			'WTCP', # in WorldOfTanks.exe: StaticSceneControlPointHandler::
			'BWWa', # in WorldOfTanks.exe: StaticSceneWater
			'BWPs', # in WorldOfTanks.exe: StaticParticlesFeature / ParticleSystemLoader
			'CENT',
			'UDOS', # user data object
			'WGDE', # in WorldOfTanks.exe: DestructiblesSceneProvider
			'BWLC', # in WorldOfTanks.exe: StaticLightsFeature
			'WTau', # in WorldOfTanks.exe: WoT_StaticSceneAudioHandler
			'BWEP', # in WorldOfTanks.exe: EnvironmentProbeSceneProvider
			'WTbl', # in WorldOfTanks.exe: WoT_BenchmarkLocationsTypes
			'WGSH', # in WorldOfTanks.exe: SHVolumeSceneProvider
			'WGCO', # in WorldOfTanks.exe: SpatialFeature
			'BWSS',
			'BWSV',
			'BWSP',
			'WGDN',
			'WSMI',
			'WSMO',
			'BWS2',
			'BSG2',
			'WGMM', # in WorldOfTanks.exe: MegalodsFeature
			'GOBJ',
			'BWVL',
			# 'BWTS', # in WorldOfTanks.exe: StaticTextureStreamingSceneProvider
		], self.header

	def __repr__(self):
		return str(
			(self.header, self.int1,
			self.position, self.int3,
			self.length, self.rows_num)
		)



class BWTB_Section:
	__root_row = None
	__child_rows = None

	def __init__(self, stream=None):
		if stream is not None:
			self.from_bin_stream(stream)

	def from_bin_stream(self, stream):
		self.load_root_row(stream)
		self.load_child_rows(stream)

	def load_root_row(self, stream):
		self.__root_row = Row(stream.read(24))
		#print(self.__root_row)
		assert self.__root_row.header == 'BWTB', self.__root_row.header

	def load_child_rows(self, stream):
		self.__child_rows = {}
		for _ in range(self.__root_row.rows_num):
			row = Row(stream.read(24))
			#print(row)
			self.__child_rows[row.header] = row

	def get_row_by_name(self, name):
		row = self.__child_rows.get(name)
		#assert row is not None
		return row
