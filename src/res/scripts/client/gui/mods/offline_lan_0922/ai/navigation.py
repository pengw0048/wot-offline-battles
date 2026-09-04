# -*- coding: utf-8 -*-
"""Terrain-aware hierarchical navigation for offline/LAN bots.

Strategic map annotations decide where a vehicle should fight. This module
connects those sparse anchors with a low-resolution A* path, while the battle
driver remains responsible for short-range steering around moving vehicles.
The implementation is engine-free; the caller supplies terrain and collision
probes so it can be tested outside the legacy client.
"""

import heapq
import math

from gui.mods.offline_lan_0922.ai.driver import (
	FIRST_CANDIDATE_OFFSET, WAYPOINT_ARRIVAL_RADIUS)


SQRT_TWO = math.sqrt(2.0)
BAKED_FATAL_HAZARDS = 1 | 2
# Shallow water remains passable when no dry route exists, but the baked graph
# assigns it a large traction/risk cost so ordinary shortcuts stay on land.
BAKED_SHALLOW_WATER = 4
BAKED_SHALLOW_WATER_PENALTY = 4.0
BAKED_EDGE_CLEARANCE_WEIGHT = 0.20
BAKED_FORMAT_NAME = 'offline-lan-0922-navgraph'
BAKED_FORMAT_VERSION = 2
# A bot whose global search is still queued holds briefly so a job that
# finishes within a frame or two does not produce a pointless creep. Beyond
# that grace it makes bounded, fully probed progress instead of standing
# still: the room-wide expansion budget is shared, so "pending" can last
# many seconds in a full room and an unbounded hold reads as a parked tank.
PENDING_PROGRESS_SECONDS = 0.6
BLOCKED_STEP_REPLAN_SECONDS = 1.0
BLOCKED_STEP_REPLAN_VERDICTS = 4
BLOCKED_STEP_EDGE_TTL = 12.0
BLOCKED_STEP_EDGE_PENALTY = 240.0
# A controlled ford admits LocalDriver's first avoidance branch, and no wider.
CONTROLLED_SHALLOW_YAW_WINDOW = FIRST_CANDIDATE_OFFSET + 0.03
# The commit side sees an integrated hull yaw rather than the candidate the
# planner chose, so it asks for a closing component instead of a cone. Half a
# unit of cosine is sixty degrees: it tolerates a lagging hull while still
# refusing one that is drifting sideways. The independent cell check below
# keeps either angular gate bound to the ford cells A* actually selected.
CONTROLLED_SHALLOW_COMMIT_CLOSING = 0.5

SEARCH_EXPANSIONS_PER_SECOND = 960.0
MAX_SEARCH_EXPANSIONS_PER_FRAME = 96
SEARCH_CATCH_UP_FRAMES = 4
# One catch-up frame may spend four nominal frames of credit, so the rate holds
# down to 4 Hz render callbacks and one stall cannot spend an unbounded backlog.
MAX_SEARCH_EXPANSIONS_PER_CATCH_UP_FRAME = (
	MAX_SEARCH_EXPANSIONS_PER_FRAME * SEARCH_CATCH_UP_FRAMES)
MAX_SEARCH_CREDIT = float(MAX_SEARCH_EXPANSIONS_PER_CATCH_UP_FRAME)


def _distance_2d(first, second):
	dx = float(first[0]) - float(second[0])
	dz = float(first[2]) - float(second[2])
	return math.sqrt(dx * dx + dz * dz)


class TerrainGrid(object):
	"""Lazy terrain graph. Cells and edges are probed only when A* needs them."""

	_NEIGHBOURS = (
		(-1, -1, SQRT_TWO), (0, -1, 1.0), (1, -1, SQRT_TWO),
		(-1, 0, 1.0),                         (1, 0, 1.0),
		(-1, 1, SQRT_TWO),  (0, 1, 1.0),  (1, 1, SQRT_TWO),
	)

	def __init__(self, ground_probe, obstacle_probe=None, bounds=None,
			cell_size=18.0, max_grade_up=0.48, max_grade_down=0.38,
			baked_graph=None):
		self.ground_probe = ground_probe
		self.obstacle_probe = obstacle_probe
		self.bounds = bounds
		self.cell_size = max(1.0, float(cell_size))
		self.prebaked = False
		self._baked_origin = (0.0, 0.0)
		self._baked_width = 0
		self._baked_height = 0
		self._baked_heights = ()
		self._baked_links = ()
		self._baked_hazards = ()
		self._baked_max_grade = 0.30
		if baked_graph is not None:
			self._install_baked_graph(baked_graph)
		self.max_grade_up = float(max_grade_up)
		self.max_grade_down = float(max_grade_down)
		# Weighted A* deliberately favours forward progress over a perfectly
		# shortest coarse-grid route. Every returned edge is still terrain-probed;
		# only the amount of side exploration changes.
		self.heuristic_weight = 1.70
		self._ground_cache = {}
		self._edge_cache = {}
		self._segment_cache = {}
		self._failed_edges = {}

	def _install_baked_graph(self, graph):
		if (graph.get('format') != BAKED_FORMAT_NAME or
				int(graph.get('version', -1)) != BAKED_FORMAT_VERSION):
			raise ValueError('unsupported baked navigation graph')
		width = int(graph.get('width', 0))
		height = int(graph.get('height', 0))
		heights = graph.get('heights_mm') or ()
		links = graph.get('links') or ()
		hazards = graph.get('hazards')
		origin = graph.get('origin') or ()
		if (width <= 0 or height <= 0 or len(origin) != 2 or
				len(heights) != width * height or len(links) != width * height or
				(hazards is not None and len(hazards) != width * height)):
			raise ValueError('invalid baked navigation graph')
		self.cell_size = max(1.0, float(graph.get('cell_size', 0.0)))
		self._baked_origin = (float(origin[0]), float(origin[1]))
		self._baked_width = width
		self._baked_height = height
		self._baked_heights = heights
		self._baked_links = links
		self._baked_hazards = hazards if hazards is not None else (0,) * (width * height)
		bake = graph.get('bake') if isinstance(graph.get('bake'), dict) else {}
		self._baked_max_grade = max(0.05, float(bake.get('max_grade', 0.30)))
		self.bounds = tuple(graph.get('bounds') or self.bounds or ()) or None
		self.prebaked = True

	def cell_for(self, point):
		origin_x, origin_z = self._baked_origin if self.prebaked else (0.0, 0.0)
		return (int(math.floor((float(point[0]) - origin_x) /
		                       self.cell_size + 0.5)),
		        int(math.floor((float(point[2]) - origin_z) /
		                       self.cell_size + 0.5)))

	def point_for(self, cell, height):
		origin_x, origin_z = self._baked_origin if self.prebaked else (0.0, 0.0)
		return (origin_x + cell[0] * self.cell_size, float(height),
		        origin_z + cell[1] * self.cell_size)

	def _baked_index(self, cell):
		if not self.prebaked:
			return None
		x, z = cell
		if x < 0 or x >= self._baked_width or z < 0 or z >= self._baked_height:
			return None
		index = z * self._baked_width + x
		if self._baked_heights[index] is None:
			return None
		return index

	def _baked_flat_index(self, cell):
		if not self.prebaked:
			return None
		x, z = cell
		if x < 0 or x >= self._baked_width or z < 0 or z >= self._baked_height:
			return None
		return z * self._baked_width + x

	def _baked_cell_height(self, cell):
		index = self._baked_index(cell)
		if index is None:
			return None
		return float(self._baked_heights[index]) / 1000.0

	def near_baked_navigation(self, point, max_radius=1):
		"""Return whether a realised pose remains in the baked safe corridor."""
		if not self.prebaked:
			return True
		return self._nearest_baked_cell(
			self.cell_for(point), max(0, int(max_radius))) is not None

	def baked_hazard_near(self, point, max_radius=0):
		'''Return whether a pose is in shipped water/cliff risk, not an obstacle.'''
		if not self.prebaked or not self._baked_hazards:
			return False
		cell = self.cell_for(point)
		radius = max(0, int(max_radius))
		for z in range(cell[1] - radius, cell[1] + radius + 1):
			for x in range(cell[0] - radius, cell[0] + radius + 1):
				index = self._baked_flat_index((x, z))
				if (index is not None and
						int(self._baked_hazards[index]) & BAKED_FATAL_HAZARDS):
					return True
		return False

	def _nearest_baked_cell(self, cell, max_radius):
		if self._baked_index(cell) is not None:
			return cell
		best = None
		best_distance = None
		for radius in range(1, max(0, int(max_radius)) + 1):
			for z in range(cell[1] - radius, cell[1] + radius + 1):
				for x in range(cell[0] - radius, cell[0] + radius + 1):
					if max(abs(x - cell[0]), abs(z - cell[1])) != radius:
						continue
					candidate = (x, z)
					if self._baked_index(candidate) is None:
						continue
					distance = ((x - cell[0]) ** 2 + (z - cell[1]) ** 2)
					if best_distance is None or distance < best_distance:
						best = candidate
						best_distance = distance
			if best is not None:
				return best
		return None

	def _inside(self, x, z):
		if self.bounds is None:
			return True
		try:
			return (float(self.bounds[0]) <= x <= float(self.bounds[2]) and
			        float(self.bounds[1]) <= z <= float(self.bounds[3]))
		except Exception:
			# Invalid bounds must not silently disable the map-edge guard.
			return False

	def clear_negative_cache(self):
		"""Retry cells that may have missed while distant chunks streamed in."""
		for cache in (self._ground_cache, self._edge_cache):
			for key, value in list(cache.items()):
				if value is None:
					cache.pop(key, None)
		for key, value in list(self._segment_cache.items()):
			if not value:
				self._segment_cache.pop(key, None)

	def _layer(self, hint_y):
		return int(math.floor(float(hint_y) / 8.0 + 0.5))

	def _point_key(self, point):
		return (int(math.floor(float(point[0]) * 0.5 + 0.5)),
		        int(math.floor(float(point[2]) * 0.5 + 0.5)),
		        self._layer(point[1]))

	def _edge_cells_for_segment(self, start, end):
		edges = self._edge_keys_for_segment(start, end)
		return edges[0] if edges else None

	def _edge_keys_for_segment(self, start, end):
		start_cell = self.cell_for(start)
		end_cell = self.cell_for(end)
		if start_cell == end_cell:
			return ()
		x, z = start_cell
		target_x, target_z = end_cell
		dx = abs(target_x - x)
		dz = abs(target_z - z)
		step_x = 1 if x < target_x else -1
		step_z = 1 if z < target_z else -1
		error = dx - dz
		cells = [start_cell]
		while x != target_x or z != target_z:
			double_error = error * 2
			if double_error > -dz:
				error -= dz
				x += step_x
			if double_error < dx:
				error += dx
				z += step_z
			cells.append((x, z))
		return tuple(tuple(sorted((cells[index], cells[index + 1])))
		             for index in range(len(cells) - 1))

	def prune_failed_edges(self, now):
		for key, value in list(self._failed_edges.items()):
			if float(now) >= value[0]:
				self._failed_edges.pop(key, None)
		if len(self._failed_edges) > 128:
			ordered = sorted(self._failed_edges.items(), key=lambda item: item[1][0])
			for key, unused in ordered[:len(self._failed_edges) - 128]:
				self._failed_edges.pop(key, None)

	def trim_caches(self):
		for cache, limit in ((self._ground_cache, 4096),
		                     (self._edge_cache, 4096),
		                     (self._segment_cache, 4096)):
			while len(cache) > limit:
				try:
					cache.popitem()
				except Exception:
					break

	def _failed_edge_penalty(self, first_cell, second_cell, now):
		key = tuple(sorted((first_cell, second_cell)))
		value = self._failed_edges.get(key)
		if value is None:
			return 0.0
		if float(now) >= value[0]:
			self._failed_edges.pop(key, None)
			return 0.0
		return value[1]

	def segment_penalty(self, start, end, now):
		if not self._failed_edges:
			return 0.0
		penalty = 0.0
		for key in self._edge_keys_for_segment(start, end):
			penalty = max(penalty,
			              self._failed_edge_penalty(key[0], key[1], now))
		return penalty

	def _baked_segment_cells(self, start, end):
		"""Return graph cells crossed by a straight segment, start included."""
		if not self.prebaked:
			return ()
		start_cell = self._nearest_baked_cell(self.cell_for(start), 2)
		end_cell = self.cell_for(end)
		if start_cell is None or self._baked_index(end_cell) is None:
			return ()
		x, z = start_cell
		target_x, target_z = end_cell
		cells = [(x, z)]
		dx = abs(target_x - x)
		dz = abs(target_z - z)
		step_x = 1 if x < target_x else -1
		step_z = 1 if z < target_z else -1
		error = dx - dz
		while x != target_x or z != target_z:
			double_error = error * 2
			if double_error > -dz:
				error -= dz
				x += step_x
			if double_error < dx:
				error += dx
				z += step_z
			cells.append((x, z))
		return tuple(cells)

	def segment_has_baked_hazard(self, start, end, hazard_mask):
		"""Check cells entered by a shortcut without trapping a tank at its start."""
		if not self.prebaked:
			return False
		cells = self._baked_segment_cells(start, end)
		if not cells:
			return True
		# Exclude the start cell so a tank already on a shallow ford may leave it.
		for cell in cells[1:]:
			index = self._baked_index(cell)
			if (index is None or
					int(self._baked_hazards[index]) & int(hazard_mask)):
				return True
		return False

	def baked_hazard_cells(self, start, end, hazard_mask):
		"""Return entered hazard cells, or ``None`` for an invalid corridor."""
		if (not self.prebaked or
				self._baked_index(self.cell_for(start)) is None):
			return None
		cells = self._baked_segment_cells(start, end)
		if not cells:
			return None
		result = []
		# Match segment_has_baked_hazard: the occupied start cell is not a new
		# entry, so a tank already in a ford remains able to leave it.
		for cell in cells[1:]:
			index = self._baked_index(cell)
			if index is None:
				return None
			if int(self._baked_hazards[index]) & int(hazard_mask):
				result.append(cell)
		return tuple(result)

	def point_has_baked_hazard(self, point, hazard_mask):
		"""Return whether one realised pose occupies a baked hazard cell."""
		if not self.prebaked:
			return False
		index = self._baked_flat_index(self.cell_for(point))
		return bool(index is not None and
		            int(self._baked_hazards[index]) & int(hazard_mask))

	def dry_segment_clear(self, start, end, now):
		"""Prove one preferred direct segment without entering shallow water.

		Shallow cells remain linked for the weighted A* fallback.  This predicate
		is only for raw goals, smoothing and reactive shortcuts which have not
		been selected by that planner.
		"""
		return (self.segment_penalty(start, end, now) <= 0.0 and
		        not self.segment_has_baked_hazard(
		            start, end, BAKED_SHALLOW_WATER) and
		        self.segment_clear(start, end))

	def path_has_penalty(self, path, now):
		if not self._failed_edges:
			return False
		for index in range(len(path) - 1):
			if self.segment_penalty(path[index], path[index + 1], now) > 0.0:
				return True
		return False

	def _ground(self, x, z, hint_y):
		if not self._inside(x, z):
			return None
		if self.prebaked:
			return self._baked_cell_height(self.cell_for((x, hint_y, z)))
		key = (int(math.floor(x * 10.0 + 0.5)),
		       int(math.floor(z * 10.0 + 0.5)), self._layer(hint_y))
		if key in self._ground_cache:
			return self._ground_cache[key]
		try:
			height = self.ground_probe(float(x), float(z), float(hint_y))
			if height is not None:
				height = float(height)
		except Exception:
			height = None
		self._ground_cache[key] = height
		return height

	def segment_clear(self, start, end):
		"""Check continuous support and drivable grade, not just both endpoints."""
		# In a prebaked graph, rounding can map a raw point just beyond the
		# authored rectangle back onto the last valid edge cell. The cell is safe;
		# the out-of-bounds world pose is not. A hull already outside may still use
		# an inward segment, so constrain the destination rather than the start.
		if not self._inside(float(end[0]), float(end[2])):
			return False
		distance = _distance_2d(start, end)
		if distance < 0.25:
			return True
		if self.prebaked:
			cells = self._baked_segment_cells(start, end)
			if not cells:
				return False
			if len(cells) == 1:
				return True
			for old_cell, cell in zip(cells, cells[1:]):
				if self._baked_edge_height(old_cell, cell) is None:
					return False
			return True
		start_key = self._point_key(start)
		end_key = self._point_key(end)
		key = (start_key, end_key)
		cached = self._segment_cache.get(key)
		if cached is not None:
			return bool(cached)
		steps = max(1, int(math.ceil(distance / (self.cell_size * 0.42))))
		start_y = self._ground(float(start[0]), float(start[2]), float(start[1]))
		if start_y is None:
			start_y = float(start[1])
		previous = (float(start[0]), start_y, float(start[2]))
		grounded_start = previous
		clear = True
		for index in range(1, steps + 1):
			fraction = float(index) / float(steps)
			x = float(start[0]) + (float(end[0]) - float(start[0])) * fraction
			z = float(start[2]) + (float(end[2]) - float(start[2])) * fraction
			horizontal = math.sqrt((x - previous[0]) ** 2 + (z - previous[2]) ** 2)
			y = self._ground(x, z, previous[1])
			if y is None:
				clear = False
				break
			delta = y - previous[1]
			# Ordinary route segments must be controllable in reverse too. A
			# physically possible slide is not a valid tank-navigation shortcut.
			reversible_grade = min(self.max_grade_up, self.max_grade_down)
			if abs(delta) > horizontal * reversible_grade:
				clear = False
				break
			current = (x, y, z)
			previous = current
		if clear and self.obstacle_probe is not None:
			try:
				if self.obstacle_probe(grounded_start, previous, 2.15):
					clear = False
			except Exception:
				# Collision-query failure is unknown terrain, not proof that a
				# several-ton vehicle has a clear corridor.
				clear = False
		self._segment_cache[key] = bool(clear)
		self._segment_cache[(end_key, start_key)] = bool(clear)
		return clear

	@staticmethod
	def shortcut_preserves_climb_approach(path, start_index, end_index,
			minimum_grade=0.10, minimum_turn=0.30):
		"""Keep the setup point before a steep path changes direction.

		A collision-free chord is not always a controllable tank manoeuvre. If a
		climb begins immediately after a bend, skipping the bend point makes the
		hull meet the slope diagonally. Flat corners and straight climbs remain
		eligible for smoothing.
		"""
		if end_index - start_index < 2:
			return True
		for index in range(start_index + 1, end_index):
			before = path[index - 1]
			pivot = path[index]
			after = path[index + 1]
			out_dx = float(after[0]) - float(pivot[0])
			out_dz = float(after[2]) - float(pivot[2])
			out_run = math.sqrt(out_dx * out_dx + out_dz * out_dz)
			if out_run <= 0.1:
				continue
			grade = (float(after[1]) - float(pivot[1])) / out_run
			if grade <= float(minimum_grade):
				continue
			in_dx = float(pivot[0]) - float(before[0])
			in_dz = float(pivot[2]) - float(before[2])
			if abs(in_dx) + abs(in_dz) <= 0.1:
				continue
			incoming = math.atan2(in_dx, in_dz)
			outgoing = math.atan2(out_dx, out_dz)
			turn = outgoing - incoming
			while turn > math.pi:
				turn -= math.pi * 2.0
			while turn < -math.pi:
				turn += math.pi * 2.0
			if abs(turn) > float(minimum_turn):
				return False
		return True

	@staticmethod
	def live_shortcut_preserves_climb_approach(current, path, start_index,
			end_index):
		"""Apply the climb-approach guard from the hull's live position."""
		if end_index < start_index:
			return True
		live_path = ((float(current[0]), float(current[1]),
		              float(current[2])),) + tuple(
			path[start_index:end_index + 1])
		return TerrainGrid.shortcut_preserves_climb_approach(
			live_path, 0, len(live_path) - 1)

	def _edge(self, cell, height, next_cell):
		if self.prebaked:
			return self._baked_edge_height(cell, next_cell)
		key = (cell, next_cell, self._layer(height))
		if key in self._edge_cache:
			return self._edge_cache[key]
		start = self.point_for(cell, height)
		point = self.point_for(next_cell, height)
		x = point[0]
		z = point[2]
		end_y = self._ground(x, z, height)
		if end_y is None:
			result = None
		else:
			end = (x, end_y, z)
			result = end_y if self.segment_clear(start, end) else None
		self._edge_cache[key] = result
		return result

	def _baked_edge_height(self, cell, next_cell):
		index = self._baked_index(cell)
		next_index = self._baked_index(next_cell)
		if index is None or next_index is None:
			return None
		dx = next_cell[0] - cell[0]
		dz = next_cell[1] - cell[1]
		direction_index = None
		for candidate, neighbour in enumerate(self._NEIGHBOURS):
			if neighbour[0] == dx and neighbour[1] == dz:
				direction_index = candidate
				break
		if (direction_index is None or
				not (int(self._baked_links[index]) & (1 << direction_index))):
			return None
		return float(self._baked_heights[next_index]) / 1000.0

	def _baked_link_count(self, cell):
		"""Return the number of independently baked exits from one safe cell."""
		index = self._baked_index(cell)
		if index is None:
			return 0
		mask = int(self._baked_links[index]) & 0xff
		count = 0
		while mask:
			count += mask & 1
			mask >>= 1
		return count

	def _baked_clearance_penalty(self, cell):
		"""Prefer the middle of a proved corridor without inventing new links.

		Every baked link already includes the shipped vehicle-width, obstacle,
		water and grade checks.  A cell with exits on both sides therefore has
		more independently proved manoeuvring room than a cell along the edge.
		The small weight only breaks otherwise similar routes; an unavoidable
		one-cell passage remains usable because no link or hazard rule changes.
		"""
		if not self.prebaked:
			return 0.0
		missing = max(0, len(self._NEIGHBOURS) -
		              self._baked_link_count(cell))
		return (float(missing) * self.cell_size *
		        BAKED_EDGE_CLEARANCE_WEIGHT)

	def _baked_clearance_exposure(self, path):
		"""Return mean missing-link exposure along a realised path chord."""
		if not self.prebaked or not path:
			return 0.0
		cells = []
		if len(path) == 1:
			cell = self._nearest_baked_cell(self.cell_for(path[0]), 2)
			if cell is None:
				return None
			cells.append(cell)
		else:
			for start, end in zip(path, path[1:]):
				segment = self._baked_segment_cells(start, end)
				if not segment:
					return None
				if cells and cells[-1] == segment[0]:
					segment = segment[1:]
				cells.extend(segment)
		if not cells:
			return None
		missing = sum(len(self._NEIGHBOURS) -
		              self._baked_link_count(cell) for cell in cells)
		return float(missing) / float(len(cells))

	def shortcut_preserves_baked_clearance(self, path, start_index, end_index,
			maximum_exposure_increase=0.25):
		"""Do not smooth a centred proved path back onto a corridor edge."""
		if not self.prebaked or end_index - start_index < 2:
			return True
		original = self._baked_clearance_exposure(
			path[start_index:end_index + 1])
		shortcut = self._baked_clearance_exposure(
			(path[start_index], path[end_index]))
		if original is None or shortcut is None:
			return False
		return shortcut <= original + float(maximum_exposure_increase)

	def _penalty(self, cell, avoid_points, prefer_clearance=False):
		penalty = (self._baked_clearance_penalty(cell)
		           if prefer_clearance else 0.0)
		if self.prebaked:
			index = self._baked_index(cell)
			if (index is not None and
					int(self._baked_hazards[index]) & BAKED_SHALLOW_WATER):
				# Preserve a small traction preference for dry ground without
				# turning an easy ford into a strategic detour.
				penalty += self.cell_size * BAKED_SHALLOW_WATER_PENALTY
		if not avoid_points:
			return penalty
		point = self.point_for(cell, 0.0)
		x = point[0]
		z = point[2]
		for point in avoid_points:
			dx = x - float(point[0])
			dz = z - float(point[2])
			distance = math.sqrt(dx * dx + dz * dz)
			if distance < self.cell_size * 1.5:
				penalty += (self.cell_size * 1.5 - distance) * 3.0
		return penalty

	def safe_local_target(self, current, goal, now, avoid_points=None,
			side_preference=1.0):
		"""Choose one short, fully probed detour when the global search fails.

		This is deliberately not a direct-to-goal fallback. Every candidate must
		have supported ground, a safe grade, no deep water, no static collision and
		no remembered failed edge. Returning ``None`` means the only safe action is
		to stop and retry the global planner later.
		"""
		dx = float(goal[0]) - float(current[0])
		dz = float(goal[2]) - float(current[2])
		if abs(dx) + abs(dz) < 0.1:
			return None
		desired_yaw = math.atan2(dx, dz)
		side = 1.0 if float(side_preference) >= 0.0 else -1.0
		offsets = (0.0, side * 0.45, -side * 0.45,
		           side * 0.85, -side * 0.85,
		           side * 1.30, -side * 1.30,
		           side * 1.75, -side * 1.75)
		distances = (self.cell_size * 0.78, self.cell_size * 0.52)
		best = None
		for distance in distances:
			for offset in offsets:
				yaw = desired_yaw + offset
				x = float(current[0]) + math.sin(yaw) * distance
				z = float(current[2]) + math.cos(yaw) * distance
				y = self._ground(x, z, float(current[1]))
				if y is None:
					continue
				candidate = (x, y, z)
				if not self.dry_segment_clear(current, candidate, now):
					continue
				cell = self.cell_for(candidate)
				score = (_distance_2d(candidate, goal) + abs(offset) * 3.5 +
					         self._penalty(cell, avoid_points, False) * 2.0)
				value = (score, abs(offset), candidate)
				if best is None or value[:2] < best[:2]:
					best = value
		return best[2] if best is not None else None

	def plan(self, start, goal, avoid_points=None, max_expansions=1600, now=0.0,
			prefer_clearance=True, edge_penalties=None):
		"""Return a supported path synchronously (mainly for tests/tools)."""
		search = self.begin_plan(
			start, goal, avoid_points, max_expansions, now, prefer_clearance,
			edge_penalties)
		while not search.done:
			search.step(256)
		return search.result

	def begin_plan(self, start, goal, avoid_points=None, max_expansions=1600,
			now=0.0, prefer_clearance=False, edge_penalties=None):
		return _TerrainSearch(self._plan_steps(
			start, goal, avoid_points, max_expansions, now,
			bool(prefer_clearance), edge_penalties))

	def _plan_steps(self, start, goal, avoid_points, max_expansions, now,
			prefer_clearance, edge_penalties):
		start_cell = self.cell_for(start)
		goal_cell = self.cell_for(goal)
		if self.prebaked:
			# The server advances a tactical waypoint at 13 metres. Keep snapping
			# within three four-metre cells so A* can never stop outside that radius.
			start_cell = self._nearest_baked_cell(start_cell, 3)
			goal_cell = self._nearest_baked_cell(goal_cell, 3)
			if start_cell is None or goal_cell is None:
				yield ()
				return
			start_y = self._baked_cell_height(start_cell)
		else:
			start_y = self._ground(float(start[0]), float(start[2]), float(start[1]))
			if start_y is None:
				start_y = float(start[1])
		frontier = []
		sequence = 0
		heapq.heappush(frontier, (0.0, sequence, start_cell, 0.0))
		came_from = {}
		cost_so_far = {start_cell: 0.0}
		heights = {start_cell: start_y}
		reached = None
		closest = start_cell
		closest_distance = math.sqrt(
			(start_cell[0] - goal_cell[0]) ** 2 +
			(start_cell[1] - goal_cell[1]) ** 2)
		expansions = 0
		while frontier and expansions < int(max_expansions):
			_unused_priority, _unused_sequence, current, queued_cost = heapq.heappop(frontier)
			# A better route may have reached this cell after an older heap entry
			# was queued. Expanding stale entries repeatedly exhausted the bounded
			# search on risk-weighted maps even though every destination was linked.
			if queued_cost != cost_so_far.get(current):
				continue
			expansions += 1
			goal_distance = math.sqrt(
				(current[0] - goal_cell[0]) ** 2 +
				(current[1] - goal_cell[1]) ** 2)
			if goal_distance < closest_distance:
				closest = current
				closest_distance = goal_distance
			if current == goal_cell:
				reached = current
				break
			current_y = heights[current]
			for offset_x, offset_z, length_scale in self._NEIGHBOURS:
				next_cell = (current[0] + offset_x, current[1] + offset_z)
				next_y = self._edge(current, current_y, next_cell)
				if next_y is None:
					continue
				if offset_x and offset_z and not self.prebaked:
					# Do not squeeze diagonally across a blocked corner.
					if (self._edge(current, current_y,
					               (current[0] + offset_x, current[1])) is None or
					        self._edge(current, current_y,
					               (current[0], current[1] + offset_z)) is None):
						continue
				run = self.cell_size * length_scale
				delta_y = next_y - current_y
				slope = abs(delta_y) / max(run, 0.1)
				grade_limit = (self._baked_max_grade if self.prebaked else
				               min(self.max_grade_up, self.max_grade_down))
				slope_ratio = slope / max(0.05, grade_limit)
				# Risk rises non-linearly near the controllable-grade limit.  A
				# downhill edge costs a little more because braking and lateral
				# slide leave less recovery room than climbing the same surface.
				slope_cost = run * slope_ratio * slope_ratio * 6.0
				if delta_y < 0.0:
					slope_cost *= 1.25
				local_penalty = 0.0
				if edge_penalties:
					local_penalty = float(edge_penalties.get(
						tuple(sorted((current, next_cell))), 0.0))
				new_cost = (cost_so_far[current] + run + slope_cost +
				            self._penalty(
				                next_cell, avoid_points, prefer_clearance) +
				            self._failed_edge_penalty(current, next_cell, now) +
				            local_penalty)
				if next_cell not in cost_so_far or new_cost < cost_so_far[next_cell]:
					cost_so_far[next_cell] = new_cost
					came_from[next_cell] = current
					heights[next_cell] = next_y
					dx = next_cell[0] - goal_cell[0]
					dz = next_cell[1] - goal_cell[1]
					# A modest weighted heuristic keeps the old 32-bit client from
					# exploring a broad irrelevant front around long ridges.
					heuristic = (math.sqrt(dx * dx + dz * dz) * self.cell_size *
					             self.heuristic_weight)
					sequence += 1
					heapq.heappush(frontier,
					               (new_cost + heuristic, sequence, next_cell, new_cost))
			yield None
		if reached is None:
			# Sparse strategic anchors are hand placed on a minimap. A point a few
			# metres inside a building footprint, cliff lip or water edge must not
			# invalidate an otherwise complete route. Use the nearest cell A* could
			# actually reach, but only within three coarse cells: a grossly wrong
			# anchor still fails instead of silently changing battle lanes.
			if closest_distance <= 3.0:
				reached = closest
			elif frontier and closest != start_cell:
				# The bounded search still has work, so return the safest progress it
				# has already proved instead of reporting a false hard failure. The next
				# request continues from that supported partial path.
				reached = closest
			else:
				yield ()
				return
		cells = [reached]
		while cells[-1] != start_cell:
			cells.append(came_from[cells[-1]])
		cells.reverse()
		if self.prebaked:
			path = [self.point_for(start_cell, start_y)]
		else:
			path = [(float(start[0]), start_y, float(start[2]))]
		for cell in cells[1:]:
			path.append(self.point_for(cell, heights[cell]))
		goal_y = self._ground(float(goal[0]), float(goal[2]), path[-1][1])
		goal_point = (float(goal[0]), goal_y if goal_y is not None else path[-1][1],
		              float(goal[2]))
		if self.segment_clear(path[-1], goal_point):
			path.append(goal_point)
		yield self._smooth(tuple(path), now, prefer_clearance)

	def _smooth(self, path, now=0.0, prefer_clearance=False):
		if len(path) < 3:
			return path
		result = [path[0]]
		index = 0
		while index < len(path) - 1:
			furthest = min(len(path) - 1, index + 6)
			while furthest > index + 1:
				if ((not prefer_clearance or
					 self.shortcut_preserves_baked_clearance(
						 path, index, furthest)) and
						self.shortcut_preserves_climb_approach(
						path, index, furthest) and
						self.dry_segment_clear(
							path[index], path[furthest], now)):
					break
				furthest -= 1
			result.append(path[furthest])
			index = furthest
		return tuple(result)


class _TerrainSearch(object):
	"""Small resumable A* task so collision probes are spread across frames."""

	def __init__(self, generator):
		self.generator = generator
		self.done = False
		self.result = None
		self.last_frame = None

	def step(self, budget):
		if self.done:
			return True
		for _unused in range(max(1, int(budget))):
			try:
				value = next(self.generator)
			except StopIteration:
				self.done = True
				self.result = ()
				break
			if value is not None:
				self.done = True
				self.result = value
				break
		return self.done


class TerrainNavigator(object):
	"""Shared strategic path cache plus per-bot path following and recovery."""

	def __init__(self, ground_probe, obstacle_probe=None, bounds=None,
			cell_size=18.0, baked_graph=None):
		self.grid = TerrainGrid(ground_probe, obstacle_probe, bounds, cell_size,
		                        baked_graph=baked_graph)
		self.paths = {}
		self.path_times = {}
		self.searches = {}
		self.search_times = {}
		self.bot_states = {}
		self.bot_failed_edges = {}
		self.search_frame_time = None
		self.housekeeping_time = None
		self.search_next_key = None
		self.search_credit = 0.0
		self.search_frame_serial = 0
		self.search_processed_frame = -1
		self.search_frame_budget = MAX_SEARCH_EXPANSIONS_PER_FRAME
		self.search_frame_open = False
		self.search_auto_time = None
		# A bounded search returns its best fully-probed partial path. This keeps a
		# 29-bot room from waiting tens of seconds for 1600 expansions per job; the
		# continuation search starts after the bot reaches that safe endpoint.
		self.search_max_expansions = 128
		if self.grid.prebaked:
			self.search_max_expansions = 4096
		self.search_completed = 0
		self.search_failed = 0
		self.search_now = 0.0
		self.fallback_totals = {
			'pending': 0, 'safe_direct': 0,
			'safe_local': 0, 'reactive': 0}
		self.fallback_recovered = 0
		self.fallback_modes = {}

	def _set_fallback_mode(self, bot_id, mode):
		if mode is None or mode == 'safe_direct':
			# A real routed target ends the pending episode. A safe-local or
			# reactive step taken *while* a search is still queued must not,
			# or the hold grace would restart after every short step and the
			# bot would creep instead of driving.
			state = self.bot_states.get(int(bot_id))
			if state is not None:
				state.pop('pending_since', None)
		old_mode = self.fallback_modes.get(int(bot_id))
		if old_mode == mode:
			return
		if old_mode is not None and mode is None:
			self.fallback_recovered += 1
		if mode is None:
			self.fallback_modes.pop(int(bot_id), None)
		else:
			self.fallback_modes[int(bot_id)] = mode
			self.fallback_totals[mode] = self.fallback_totals.get(mode, 0) + 1

	def fallback_diagnostics(self, active_bot_ids=None, now=None):
		if active_bot_ids is not None:
			active_ids = set(int(value) for value in active_bot_ids)
			for bot_id in list(self.fallback_modes):
				if bot_id not in active_ids:
					self.fallback_modes.pop(bot_id, None)
		active = {
			'pending': 0, 'safe_direct': 0,
			'safe_local': 0, 'reactive': 0}
		for mode in self.fallback_modes.values():
			active[mode] = active.get(mode, 0) + 1
		return {
			'graph': {
				'source': 'baked' if self.grid.prebaked else 'runtime',
				'cell_mm': int(round(self.grid.cell_size * 1000.0)),
				'nodes': (sum(1 for value in self.grid._baked_heights
				              if value is not None) if self.grid.prebaked else 0),
			},
			'total': dict(self.fallback_totals),
			'active': active,
			'recovered': int(self.fallback_recovered),
			'search': {
				'pending': len(self.searches),
				'completed': int(self.search_completed),
				'failed': int(self.search_failed),
				'oldest_ms': int(max(0.0, self.search_now -
					min(self.search_times.values())) * 1000.0)
					if self.search_times else 0,
				'tick_age_ms': int(max(0.0, float(now) - self.search_now) * 1000.0)
					if now is not None else 0,
			},
			'blocked_step_replans': sum(
				int(state.get('blocked_step_replans', 0))
				for state in self.bot_states.values()),
		}

	def _fallback_target(self, bot_id, current, goal, now, avoid_points, state,
			allow_safe_local=True):
		"""Keep moving without treating an unproved long segment as drivable.

		A fully probed short waypoint is preferred after a conclusive A* failure.
		If none exists (or the search is merely pending), return the strategic goal
		as steering intent for LocalDriver. The caller still probes every candidate
		vehicle-width corridor and can only throttle into one that is locally safe.
		"""
		# A fallback replaces the route decision, not the ford the planner already
		# selected. Drop that ford only once it stops being a reachable safe edge.
		ford = state.get('controlled_shallow_target')
		if ford is not None and (
				_distance_2d(current, ford) <= WAYPOINT_ARRIVAL_RADIUS or
				self._bot_edges_penalized(bot_id, current, ford, now) or
				self.grid.segment_has_baked_hazard(
					current, ford, BAKED_FATAL_HAZARDS) or
				not self.grid.segment_clear(current, ford)):
			state.pop('controlled_shallow_target', None)
		if allow_safe_local:
			fallback = self.grid.safe_local_target(
				current, goal, now, avoid_points,
				1.0 if (int(bot_id) % 2) else -1.0)
			if fallback is not None:
				state['last_target'] = tuple(fallback)
				state['navigation_status'] = 'safe'
				state['target_is_terminal'] = bool(
					_distance_2d(fallback, goal) <= WAYPOINT_ARRIVAL_RADIUS)
				self._set_fallback_mode(bot_id, 'safe_local')
				return tuple(fallback)
		state['last_target'] = tuple(goal)
		state['navigation_status'] = 'blocked'
		state['target_is_terminal'] = False
		self._set_fallback_mode(bot_id, 'reactive')
		return tuple(goal)

	def _pending_target(self, bot_id, current, goal, now, state,
			avoid_points=None):
		"""Continue a proved local edge, else make bounded probed progress.

		Returning the hull's own position is a complete stop: the driver reads it
		as arrival and the order adapter suppresses steering entirely, so nothing
		times the wait out and nothing recovers from it. A queued global search is
		not evidence that standing still is safe, only that the strategic route is
		not known yet, so after a short grace this returns the same fully probed
		short waypoint a conclusive search failure would use. Every candidate
		still needs supported ground, a safe grade, no deep water, no static
		collision and no remembered failed edge; ``None`` from that search still
		means the only safe action is to hold.
		"""
		last_target = state.get('last_target')
		if last_target is not None:
			last_target = tuple(last_target)
			shallow = self.grid.segment_has_baked_hazard(
				current, last_target, BAKED_SHALLOW_WATER)
			controlled = state.get('controlled_shallow_target')
			if (self.grid.segment_penalty(current, last_target, now) <= 0.0 and
					self.grid.segment_clear(current, last_target) and
					(not shallow or controlled == last_target) and
					_distance_2d(current, last_target) >
					WAYPOINT_ARRIVAL_RADIUS):
				state['navigation_status'] = 'pending'
				state['target_is_terminal'] = False
				self._set_fallback_mode(bot_id, 'pending')
				return last_target
		started = state.get('pending_since')
		if started is None:
			started = float(now)
			state['pending_since'] = started
		if (goal is not None and
				float(now) - float(started) >= PENDING_PROGRESS_SECONDS):
			fallback = self.grid.safe_local_target(
				current, goal, now, avoid_points,
				1.0 if (int(bot_id) % 2) else -1.0)
			if fallback is not None:
				state['last_target'] = tuple(fallback)
				state['navigation_status'] = 'pending'
				state['target_is_terminal'] = False
				self._set_fallback_mode(bot_id, 'safe_local')
				return tuple(fallback)
		state['last_target'] = tuple(current)
		state['navigation_status'] = 'pending'
		state['target_is_terminal'] = False
		self._set_fallback_mode(bot_id, 'pending')
		return tuple(current)

	@staticmethod
	def _path_owner(path_key):
		try:
			kind = path_key[0]
			if kind in ('local', 'route_join', 'join', 'recovery', 'continue'):
				return int(path_key[1])
		except Exception:
			pass
		return None

	def _active_bot_edge_penalties(self, bot_id, now):
		if bot_id is None:
			return None
		failed = self.bot_failed_edges.get(int(bot_id))
		if not failed:
			return None
		result = {}
		for key, value in list(failed.items()):
			if float(now) >= float(value[0]):
				failed.pop(key, None)
			else:
				result[key] = float(value[1])
		if not failed:
			self.bot_failed_edges.pop(int(bot_id), None)
		return result or None

	def _bot_edges_penalized(self, bot_id, start, end, now):
		"""True when this bot's own escalation covers any edge of the segment."""
		penalties = self._active_bot_edge_penalties(bot_id, now)
		return bool(penalties and any(
			edge in penalties
			for edge in self.grid._edge_keys_for_segment(start, end)))

	def bot_segment_penalized(self, bot_id, start, end, now):
		"""Expose one Bot's live hard-contact edge veto to target admission."""
		return self._bot_edges_penalized(bot_id, start, end, now)

	def report_blocked_step(self, bot_id, current, target, now):
		"""Escalate a repeated contact or planner veto into a bot-local replan."""
		bot_id = int(bot_id)
		state = self.bot_states.get(bot_id)
		if state is None or target is None:
			return False
		try:
			target = tuple(target)
		except Exception:
			return False
		if _distance_2d(current, target) <= WAYPOINT_ARRIVAL_RADIUS:
			return False
		key = self.grid._edge_cells_for_segment(current, target)
		if key is None:
			return False
		now = float(now)
		if now < float(state.get('blocked_step_escalated_until', 0.0)):
			return False
		tracker = state.get('blocked_step_tracker')
		same_edge = bool(
			isinstance(tracker, dict) and tracker.get('key') == key and
			now - float(tracker.get('last_at', now)) <= 0.5 and
			_distance_2d(current, tracker.get('origin', current)) <=
			max(1.5, self.grid.cell_size * 0.5))
		if same_edge:
			tracker['count'] = int(tracker.get('count', 0)) + 1
			tracker['last_at'] = now
		else:
			tracker = {
				'key': key, 'count': 1, 'first_at': now,
				'last_at': now, 'origin': tuple(current)}
			state['blocked_step_tracker'] = tracker
		if (int(tracker['count']) < BLOCKED_STEP_REPLAN_VERDICTS or
				now - float(tracker['first_at']) < BLOCKED_STEP_REPLAN_SECONDS):
			return False
		failed = self.bot_failed_edges.setdefault(bot_id, {})
		failed[key] = (
			now + BLOCKED_STEP_EDGE_TTL, BLOCKED_STEP_EDGE_PENALTY)
		state['replan_generation'] = int(
			state.get('replan_generation', 0)) + 1
		state['blocked_step_replans'] = int(
			state.get('blocked_step_replans', 0)) + 1
		state['blocked_step_escalated_until'] = now + 2.0
		state['blocked_step_tracker'] = None
		state['path_key'] = None
		state['index'] = 0
		state['recovery_start'] = tuple(current)
		state['replan_active'] = True
		state.pop('controlled_shallow_target', None)
		self._cancel_bot_searches(bot_id)
		return True

	def _cache_key(self, path_key, goal):
		return (tuple(path_key), self.grid.cell_for(goal))

	@staticmethod
	def _prefers_baked_clearance(path_key):
		"""Centre shared route legs, never a bot's spawn or recovery join."""
		try:
			kind = path_key[0]
		except Exception:
			return False
		if kind == 'route':
			return True
		return (kind == 'continue' and len(path_key) > 3 and
		        path_key[3] == 'route')

	def _trim_cache(self, now):
		if len(self.paths) <= 96:
			return
		ordered = sorted(self.path_times.items(), key=lambda item: item[1])
		for key, _timestamp in ordered[:len(ordered) - 80]:
			self.paths.pop(key, None)
			self.path_times.pop(key, None)

	def _finish_search(self, key, search, now):
		path = search.result or ()
		self.searches.pop(key, None)
		self.search_times.pop(key, None)
		self.paths[key] = path
		self.path_times[key] = float(now)
		if path:
			self.search_completed += 1
		else:
			self.search_failed += 1

	def _cancel_bot_searches(self, bot_id, keep_key=None, kind=None):
		"""Discard superseded private jobs without touching shared route plans."""
		bot_id = int(bot_id)
		for key in list(self.searches):
			try:
				path_key = key[0]
				owned = (isinstance(path_key, tuple) and len(path_key) > 1 and
				         path_key[0] in (
				             'local', 'route_join', 'join', 'recovery', 'continue') and
				         int(path_key[1]) == bot_id)
			except Exception:
				owned = False
			if (owned and key != keep_key and
					(kind is None or path_key[0] == kind)):
				self.searches.pop(key, None)
				self.search_times.pop(key, None)

	def _accrue_search_credit(self, elapsed):
		"""Earn elapsed credit and size this frame's expansion ceiling from it."""
		self.search_credit = min(
			MAX_SEARCH_CREDIT,
			self.search_credit +
			max(0.0, float(elapsed)) * SEARCH_EXPANSIONS_PER_SECOND)
		self.search_frame_budget = min(
			MAX_SEARCH_EXPANSIONS_PER_CATCH_UP_FRAME,
			max(MAX_SEARCH_EXPANSIONS_PER_FRAME, int(self.search_credit)))

	def begin_frame(self, elapsed):
		"""Accrue deterministic A* work once for one render callback.

		Search progress is paid with simulation elapsed rather than CPU wall time,
		so a given elapsed interval buys the same expansions at any frame rate.
		Credit is retained across frames up to a bounded catch-up reserve, and the
		rotating queue below preserves fairness between jobs.
		"""
		self.search_frame_serial += 1
		self.search_frame_open = True
		self._accrue_search_credit(elapsed)

	def end_frame(self):
		"""Close an explicit render-frame work budget."""
		self.search_frame_open = False

	def _begin_automatic_frame(self, now):
		"""Keep direct TerrainNavigator users deterministic without a runtime."""
		now = float(now)
		if (self.search_auto_time is not None and
				abs(now - self.search_auto_time) < 0.000001):
			return
		# A direct caller has no preceding render callback from which to earn its
		# first credit. Give it one nominal 10 Hz slice; production always opens an
		# explicit frame with the real elapsed value.
		elapsed = (0.10 if self.search_auto_time is None else
		           max(0.0, now - self.search_auto_time))
		self.search_auto_time = now
		self.search_frame_serial += 1
		self._accrue_search_credit(elapsed)

	def _advance_searches(self, now):
		"""Give every pending A* task a deterministic fair frame share.

		The old on-demand scheduler handed the whole frame budget to whichever bots
		were updated first. With a 29-bot room, later join searches could therefore
		remain pending forever. This rotating queue gives every task one expansion
		before any task receives a second, and remembers the next task across frames.
		No branch reads CPU time, so identical elapsed/input produces identical
		paths on fast and slow machines.
		"""
		self.search_now = float(now)
		if not self.search_frame_open:
			self._begin_automatic_frame(now)
		if self.search_processed_frame == self.search_frame_serial:
			return
		self.search_processed_frame = self.search_frame_serial
		self.search_frame_time = float(now)
		keys = sorted(self.searches, key=lambda value: repr(value))
		if not keys:
			self.search_next_key = None
			return
		if self.search_next_key in keys:
			start = keys.index(self.search_next_key)
			queue = keys[start:] + keys[:start]
		else:
			queue = keys
		budget = min(
			max(0, int(self.search_credit)),
			max(0, int(self.search_frame_budget)))
		processed = 0
		while budget > 0 and queue:
			key = queue.pop(0)
			search = self.searches.get(key)
			if search is None:
				continue
			search.step(1)
			search.last_frame = float(now)
			budget -= 1
			processed += 1
			if search.done:
				self._finish_search(key, search, now)
			else:
				queue.append(key)
		self.search_credit = max(0.0, self.search_credit - processed)
		self.search_frame_budget = max(
			0, int(self.search_frame_budget) - processed)
		self.search_next_key = queue[0] if queue else None
		self._trim_cache(now)

	def tick(self, now):
		"""Advance shared path jobs once per render budget, even when bots hold."""
		self._advance_searches(now)
		if (self.housekeeping_time is None or
				float(now) - float(self.housekeeping_time) >= 1.0):
			self.housekeeping_time = float(now)
			self.grid.prune_failed_edges(now)
			for bot_id in list(self.bot_failed_edges):
				self._active_bot_edge_penalties(bot_id, now)
			self.grid.trim_caches()

	def _path(self, path_key, start, goal, now, avoid_points):
		key = self._cache_key(path_key, goal)
		owner = self._path_owner(path_key)
		if owner is not None:
			# Join and continuation keys include the Bot's current cell. A
			# pending safe-local fallback can therefore move into a new cell
			# and request a replacement before the old job finishes. Keep only
			# that Bot's current request of the same kind so stale jobs cannot
			# divide the navigator-wide expansion budget. A cached route_join and
			# its pending child join are separate stages of one live request, so a
			# different private kind must remain independent. Do this before a
			# cached-path return: revisiting a cached cell still supersedes a
			# pending same-kind job elsewhere.
			self._cancel_bot_searches(
				owner, keep_key=key, kind=path_key[0])
		if key in self.paths:
			path = self.paths[key]
			# A probe can fail while distant chunks are still streaming. Successful
			# paths are permanent for the battle; failed ones get another chance.
			if path and not self.grid.path_has_penalty(path, now):
				self.path_times[key] = float(now)
				return key, path
			if path:
				del self.paths[key]
				self.path_times.pop(key, None)
			else:
				if float(now) - self.path_times.get(key, 0.0) < 8.0:
					return key, path
				del self.paths[key]
				self.path_times.pop(key, None)
				self.grid.clear_negative_cache()
		search = self.searches.get(key)
		if search is None:
			edge_penalties = self._active_bot_edge_penalties(owner, now)
			penalized_direct = bool(
				edge_penalties and any(
					edge in edge_penalties
					for edge in self.grid._edge_keys_for_segment(start, goal)))
			# Most annotated segments are already open roads. Avoid invoking A*
			# when one continuous support/collision check proves the direct link.
			if (not penalized_direct and
					self.grid.dry_segment_clear(start, goal, now)):
				path = (tuple(start), tuple(goal))
				self.paths[key] = path
				self.path_times[key] = float(now)
				return key, path
			# Moving tanks do not belong in a cached static terrain path. Including all
			# 28 peers made every expansion scan transient positions, permanently baked
			# traffic into shared paths, and multiplied probe cost. LocalDriver handles
			# moving OBBs every frame; A* only owns static terrain and remembered edges.
			search = self.grid.begin_plan(
				start, goal, avoid_points=None,
				max_expansions=self.search_max_expansions, now=now,
				prefer_clearance=self._prefers_baked_clearance(path_key),
				edge_penalties=edge_penalties)
			self.searches[key] = search
			self.search_times[key] = float(now)
		self._advance_searches(now)
		if key in self.paths:
			return key, self.paths[key]
		if not search.done:
			return key, None
		# _advance_searches normally caches completed jobs. This branch only covers
		# a test double or an externally completed task.
		self._finish_search(key, search, now)
		return key, self.paths[key]

	def _planned_next_segment_clear(self, current, path, index, now):
		"""Keep an adjacent A* ford without inventing a shallow shortcut."""
		if index + 1 >= len(path):
			return False
		target = path[index + 1]
		if (not self.grid.live_shortcut_preserves_climb_approach(
				current, path, index, index + 1) or
				self.grid.segment_penalty(current, target, now) > 0.0 or
				not self.grid.segment_clear(current, target)):
			return False
		if not self.grid.segment_has_baked_hazard(
				current, target, BAKED_SHALLOW_WATER):
			return True
		# The offset from the realised hull pose may enter shallow water only
		# when the adjacent edge selected by A* is itself the planned ford.
		# Otherwise reaching one dry corner could skip the next dry corner and
		# turn their diagonal into an unplanned water shortcut.
		return self.grid.segment_has_baked_hazard(
			path[index], target, BAKED_SHALLOW_WATER)

	def _planned_current_segment_clear(self, current, path, index, now):
		"""Keep following the adjacent A* ford selected on the prior frame."""
		if index <= 0 or index >= len(path):
			return False
		target = path[index]
		if (not self.grid.live_shortcut_preserves_climb_approach(
				current, path, index - 1, index) or
				self.grid.segment_penalty(current, target, now) > 0.0 or
				not self.grid.segment_clear(current, target)):
			return False
		if not self.grid.segment_has_baked_hazard(
				current, target, BAKED_SHALLOW_WATER):
			return True
		return self.grid.segment_has_baked_hazard(
			path[index - 1], target, BAKED_SHALLOW_WATER)

	def _lookahead_index(self, current, path, index, path_key, now,
			lookahead_distance):
		"""Select a proved corridor point far enough ahead for current speed."""
		lookahead = int(index)
		if lookahead_distance is None:
			limit = min(len(path), index + 3)
			horizon = None
		else:
			limit = min(len(path), index + 7)
			horizon = max(
				self.grid.cell_size * 2.0, float(lookahead_distance))
		prefer_clearance = self._prefers_baked_clearance(path_key)
		for candidate in range(index + 1, limit):
			if (horizon is not None and candidate > index + 1 and
					_distance_2d(current, path[candidate]) > horizon):
				break
			if ((not prefer_clearance or
					 self.grid.shortcut_preserves_baked_clearance(
						 path, index, candidate)) and
					self.grid.live_shortcut_preserves_climb_approach(
						current, path, index, candidate) and
					self.grid.dry_segment_clear(
						current, path[candidate], now)):
				lookahead = candidate
			else:
				break
		return lookahead

	def next_target(self, bot_id, current, goal, path_key, now,
			anchor=None, avoid_points=None, lookahead_distance=None):
		"""Return a terrain-safe local target, holding if no safe path is ready."""
		bot_id = int(bot_id)
		# Search progress is a navigator-wide frame task, not a cache-miss side
		# effect. Once every active bot had a cached/partial path, _path() returned
		# before advancing unrelated join and continuation jobs, leaving the whole
		# room parked with an ever-growing pending queue.
		self.tick(now)
		state = self.bot_states.get(bot_id)
		if state is None:
			state = {'last_position': tuple(current), 'progress_time': float(now),
			         'path_key': None, 'index': 0, 'recovery': 0,
			         'recovery_until': 0.0, 'recovery_key': None,
			         'recovery_start': None, 'request_key': None,
			         'request_path_key': None, 'planned_goal': None,
			         'planned_at': 0.0, 'navigation_status': 'pending',
			         'target_is_terminal': False,
			         'replan_generation': 0, 'replan_active': False,
			         'blocked_step_replans': 0,
			         'blocked_step_tracker': None,
			         'blocked_step_escalated_until': 0.0}
			self.bot_states[bot_id] = state
		path_identity = tuple(path_key)
		planned_goal = state.get('planned_goal')
		if (state.get('request_path_key') == path_identity and
				planned_goal is not None and
				_distance_2d(planned_goal, goal) < self.grid.cell_size * 2.0 and
				float(now) - float(state.get('planned_at', 0.0)) < 2.0):
			# A moving contact may cross a coarse cell every observation. Keep the
			# current terrain plan briefly instead of cancelling it before A* can
			# finish; aiming still uses the target's current live pose.
			goal = tuple(planned_goal)
		else:
			state['request_path_key'] = path_identity
			state['planned_goal'] = tuple(goal)
			state['planned_at'] = float(now)
		request_key = self._cache_key(path_key, goal)
		if state.get('request_key') != request_key:
			# A new route segment or combat target is not evidence that the previous
			# request stalled. Reset recovery before evaluating progress.
			self._cancel_bot_searches(bot_id)
			state['request_key'] = request_key
			state['path_key'] = None
			state['index'] = 0
			state['last_position'] = tuple(current)
			state['progress_time'] = float(now)
			state['recovery'] = 0
			state['recovery_until'] = 0.0
			state['recovery_key'] = None
			state['recovery_start'] = None
			state['replan_active'] = False
		if _distance_2d(current, state['last_position']) >= 2.0:
			state['last_position'] = tuple(current)
			state['progress_time'] = float(now)
			state['recovery'] = 0
			state['recovery_until'] = 0.0
			state['replan_active'] = False
		plan_start = tuple(anchor or current)
		if anchor is not None:
			# Strategic route annotations are two-dimensional and LAN protocol v5
			# historically transported them with y=0.  Use the live vehicle layer as
			# the terrain-probe hint; otherwise elevated spawns make every shared
			# route search start below the map and fail before its first edge.
			plan_start = (float(plan_start[0]), float(current[1]),
			              float(plan_start[2]))
		# A lack of displacement is not proof that the static terrain edge is bad.
		# It is commonly a traffic jam, a tank-to-tank push, or LocalDriver turning
		# in place.  The former recovery path marked that edge globally, invalidated
		# every bot's shared route, and caused an expanding replan/failure storm.
		# LocalDriver already owns short-range stuck recovery; the terrain graph is
		# now changed only by actual terrain/collision probes.
		effective_key = tuple(path_key)
		if state.get('replan_active'):
			effective_key = (
				('recovery', bot_id,
				 int(state.get('replan_generation', 0))) +
				tuple(path_key))
			plan_start = tuple(state.get('recovery_start') or current)
		key, path = self._path(effective_key, plan_start, goal, now,
		                       None)
		if path is None:
			if self.grid.dry_segment_clear(current, goal, now):
				state.pop('controlled_shallow_target', None)
				state['last_target'] = tuple(goal)
				state['navigation_status'] = 'safe'
				state['target_is_terminal'] = True
				self._set_fallback_mode(bot_id, 'safe_direct')
				return tuple(goal)
			return self._pending_target(
				bot_id, current, goal, now, state, avoid_points)
		if not path:
			if self.grid.dry_segment_clear(current, goal, now):
				state.pop('controlled_shallow_target', None)
				state['last_target'] = tuple(goal)
				state['navigation_status'] = 'safe'
				state['target_is_terminal'] = True
				self._set_fallback_mode(bot_id, 'safe_direct')
				return tuple(goal)
			state['path_key'] = key
			return self._fallback_target(
				bot_id, current, goal, now, avoid_points, state, True)
		active_key = state.get('path_key')
		if active_key is not None and active_key != key:
			active_path = self.paths.get(active_key)
			if (active_path and
					not self.grid.path_has_penalty(active_path, now)):
				# A join/recovery/continuation path starts at this hull's real
				# position. Follow it to completion instead of replacing it with
				# the shared strategic path again on the next frame.
				key = active_key
				path = active_path
				self.path_times[key] = float(now)
		if state.get('path_key') != key:
			state['path_key'] = key
			state['index'] = 0
			best_index = 0
			best_distance = 1e18
			for index, point in enumerate(path):
				distance = _distance_2d(current, point)
				if distance < best_distance:
					best_distance = distance
					best_index = index
			state['index'] = best_index
		index = min(int(state.get('index', 0)), len(path) - 1)
		current_segment_shallow = self.grid.segment_has_baked_hazard(
			current, path[index], BAKED_SHALLOW_WATER)
		if (self.grid.segment_penalty(current, path[index], now) > 0.0 or
				(current_segment_shallow and
				 not self._planned_current_segment_clear(
					 current, path, index, now)) or
				not self.grid.segment_clear(current, path[index])):
			join_key = ('join', bot_id, self.grid.cell_for(current)) + tuple(path_key)
			key, joined_path = self._path(join_key, current, goal, now, avoid_points)
			if joined_path is None:
				return self._pending_target(
				bot_id, current, goal, now, state, avoid_points)
			if not joined_path:
				# The cached strategic path is unusable from this hull's actual
				# position and the join search has conclusively failed. Reuse the
				# same fully probed short fallback as a failed global search; if no
				# safe candidate exists, remain stopped and retry.
				state['path_key'] = key
				return self._fallback_target(
					bot_id, current, goal, now, avoid_points, state, True)
			path = joined_path
			state['path_key'] = key
			state['index'] = 0
			index = 0
		reach_radius = min(10.0, max(1.5, self.grid.cell_size * 0.55))
		while (index + 1 < len(path) and
		       _distance_2d(current, path[index]) < reach_radius and
		       self._planned_next_segment_clear(
			       current, path, index, now)):
			index += 1
		# Look ahead only while every skipped piece is continuously supported.
		lookahead = self._lookahead_index(
			current, path, index, effective_key, now, lookahead_distance)
		if (lookahead == len(path) - 1 and
				_distance_2d(current, path[lookahead]) < reach_radius and
				_distance_2d(path[lookahead], goal) > reach_radius):
			# A bounded A* may return a safe partial path. Reaching that endpoint
			# means "continue planning from here", not "the strategic goal is
			# complete" and not "wait four seconds until stall recovery".
			continue_key = (('continue', bot_id, self.grid.cell_for(current)) +
			                tuple(path_key))
			next_key, continued = self._path(
				continue_key, current, goal, now, avoid_points)
			if continued:
				path = continued
				state['path_key'] = next_key
				next_index = 0
				if (len(path) > 1 and
						self._planned_next_segment_clear(
							current, path, 0, now)):
					next_index = 1
				next_index = self._lookahead_index(
					current, path, next_index, continue_key, now,
					lookahead_distance)
				state['index'] = next_index
				selected = tuple(path[next_index])
				state['last_target'] = selected
				if self.grid.segment_has_baked_hazard(
						current, selected, BAKED_SHALLOW_WATER):
					state['controlled_shallow_target'] = selected
				else:
					state.pop('controlled_shallow_target', None)
				state['navigation_status'] = 'safe'
				state['target_is_terminal'] = bool(
					_distance_2d(selected, goal) <= WAYPOINT_ARRIVAL_RADIUS)
				self._set_fallback_mode(bot_id, None)
				return selected
			if continued is None:
				return self._pending_target(
				bot_id, current, goal, now, state, avoid_points)
			return self._fallback_target(
				bot_id, current, goal, now, avoid_points, state, True)
		selected = tuple(path[lookahead])
		if (_distance_2d(current, selected) <= WAYPOINT_ARRIVAL_RADIUS and
				_distance_2d(current, goal) > 15.0):
			# A cached path whose first usable edge has become blocked must not park
			# the hull on its own position until the four-second stall timer fires.
			return self._fallback_target(
				bot_id, current, goal, now, avoid_points, state, True)
		state['index'] = lookahead
		state['last_target'] = selected
		if self.grid.segment_has_baked_hazard(
				current, selected, BAKED_SHALLOW_WATER):
			state['controlled_shallow_target'] = selected
		else:
			state.pop('controlled_shallow_target', None)
		state['navigation_status'] = 'safe'
		state['target_is_terminal'] = bool(
			_distance_2d(selected, goal) <= WAYPOINT_ARRIVAL_RADIUS)
		self._set_fallback_mode(bot_id, None)
		return selected

	def target_is_terminal(self, bot_id):
		state = self.bot_states.get(int(bot_id))
		return bool(state is not None and state.get('target_is_terminal'))

	def _controlled_shallow_corridor_matches(self, current, target,
			travel_yaw):
		"""Keep a shallow exception inside the cells of the armed A* step."""
		try:
			distance = max(1.0, float(self.grid.cell_size))
			end = (
				float(current[0]) + math.sin(float(travel_yaw)) * distance,
				float(current[1]),
				float(current[2]) + math.cos(float(travel_yaw)) * distance,
			)
			planned_shallow = self.grid.baked_hazard_cells(
				current, target, BAKED_SHALLOW_WATER)
			committed_shallow = self.grid.baked_hazard_cells(
				current, end, BAKED_SHALLOW_WATER)
			if (not planned_shallow or committed_shallow is None or
					self.grid.segment_has_baked_hazard(
						current, target, BAKED_FATAL_HAZARDS) or
					self.grid.segment_has_baked_hazard(
						current, end, BAKED_FATAL_HAZARDS)):
				return False
			allowed = set(planned_shallow)
			return all(cell in allowed for cell in committed_shallow)
		except (AttributeError, IndexError, TypeError, ValueError,
				OverflowError):
			return False

	def controlled_shallow_step(self, bot_id, current, sample_yaw,
			maximum_yaw_error=CONTROLLED_SHALLOW_YAW_WINDOW):
		"""Admit only headings toward the A*-selected ford into a shallow cell."""
		state = self.bot_states.get(int(bot_id))
		if state is None:
			return False
		target = state.get('controlled_shallow_target')
		if target is None:
			return False
		dx = float(target[0]) - float(current[0])
		dz = float(target[2]) - float(current[2])
		if abs(dx) + abs(dz) < 0.1:
			return False
		difference = float(sample_yaw) - math.atan2(dx, dz)
		while difference > math.pi:
			difference -= math.pi * 2.0
		while difference < -math.pi:
			difference += math.pi * 2.0
		return (
			abs(difference) <= max(0.0, float(maximum_yaw_error)) and
			self._controlled_shallow_corridor_matches(
				current, target, sample_yaw))

	def controlled_shallow_committed(self, bot_id, current, travel_yaw):
		"""Admit a realised step while the hull still closes on the ford.

		``controlled_shallow_step`` exists for the planner's candidate fan,
		where the sampled yaw *is* the intended heading, so a tight cone around
		the ford bearing is the right question. The committed hull yaw is an
		integrated pose that lags the chosen candidate by however much traverse
		one step could deliver, so re-deriving admission from it vetoed the very
		rotation the planner had just asked for: the step was refused, the
		heading was banned, and the next tactical update selected the same ford
		again. Once A* has armed a ford, the commit side accepts a lagging hull
		only while it still closes on the target and its realised corridor enters
		no shallow cell outside that selected A* step. Fatal and invalid graph
		corridors are never admitted.
		"""
		state = self.bot_states.get(int(bot_id))
		if state is None:
			return False
		target = state.get('controlled_shallow_target')
		if target is None:
			return False
		dx = float(target[0]) - float(current[0])
		dz = float(target[2]) - float(current[2])
		length = math.sqrt(dx * dx + dz * dz)
		if length < 0.1:
			return False
		closing = (math.sin(float(travel_yaw)) * dx +
		           math.cos(float(travel_yaw)) * dz) / length
		return (
			closing > CONTROLLED_SHALLOW_COMMIT_CLOSING and
			self._controlled_shallow_corridor_matches(
				current, target, travel_yaw))

	@staticmethod
	def navigation_paused(current, requested_goal, selected_target,
			minimum_request_distance=15.0,
			hold_radius=WAYPOINT_ARRIVAL_RADIUS):
		"""True when pathfinding intentionally returned the current position."""
		return (_distance_2d(current, requested_goal) > float(minimum_request_distance) and
		        _distance_2d(current, selected_target) <= float(hold_radius))
