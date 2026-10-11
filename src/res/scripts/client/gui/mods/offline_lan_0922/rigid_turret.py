"""Mass and inertia driven detached components, owned by the hidden worker.

This is a compound-box rigid body, not a recovered retail cell implementation.
Component weights and bounds supply mass, centre of mass and inertia. Contacts
are inelastic; scenery uses a dissipative no-slip contact constraint instead
of inventing an unreviewed steel/material friction or restitution coefficient.
Position recovery never becomes velocity, and a side contact never uses a
vertical 'lift to roof' operation.
"""
import copy
import math
import time

from gui.mods.offline_lan_0922 import shot_geometry
from gui.mods.offline_lan_0922 import turret_detachment
from gui.mods.offline_lan_0922.entities import turret_obstacles as geometry

EPSILON = 1.0e-6
STEP = turret_detachment.FLIGHT_STEP_SECONDS / 4.0
SOLVER_PASSES = 8
# A soft CPU budget, not a physics timestep. Every body receives at least one
# exact substep; cheap flight can consume all elapsed time within this share.
UPDATE_BUDGET_SECONDS = turret_detachment.FLIGHT_STEP_SECONDS / 10.0
ZERO = (0.0, 0.0, 0.0)
def add(a, b):
    return a[0]+b[0], a[1]+b[1], a[2]+b[2]


def sub(a, b):
    return a[0]-b[0], a[1]-b[1], a[2]-b[2]


def scale(a, factor):
    return a[0]*factor, a[1]*factor, a[2]*factor


def dot(a, b):
    return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]


def cross(a, b):
    return a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]


def query_skin(points):
    # Math.Vector3/native scenery use binary32 world coordinates. Four ULPs
    # at the current coordinate scale survive that conversion; a fixed 1e-6
    # ray can collapse to a point or start below a sloping contact at map scale.
    return max(EPSILON, max(abs(c) for p in points for c in p) * 2.0**-21)


def length(v):
    return math.sqrt(dot(v, v))


def unit(v):
    size = length(v)
    return scale(v, 1.0 / size) if size > EPSILON else ZERO


def matrix(attitude):
    columns = [shot_geometry.transform_vehicle_vector(v, *attitude)
               for v in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]
    return tuple(columns[j][i] for i in range(3) for j in range(3))


def mul(m, v):
    x, y, z = v
    return (m[0]*x+m[1]*y+m[2]*z, m[3]*x+m[4]*y+m[5]*z,
            m[6]*x+m[7]*y+m[8]*z)


def transpose(m):
    return m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]


def inverse(m):
    a, b, c, d, e, f, g, h, i = m
    cof = (e*i-f*h, c*h-b*i, b*f-c*e, f*g-d*i, a*i-c*g,
           c*d-a*f, d*h-e*g, b*g-a*h, a*e-b*d)
    det = a*cof[0] + b*cof[3] + c*cof[6]
    if abs(det) < EPSILON:
        raise ValueError('detached component inertia is singular')
    return tuple(v / det for v in cof)


def rotate(m, omega, dt):
    speed = length(omega)
    if speed * dt <= 1e-12:
        return m
    axis = scale(omega, 1.0 / speed)
    angle = speed * dt
    cosine, sine = math.cos(angle), math.sin(angle)
    columns = []
    for j in range(3):
        v = (m[j], m[3+j], m[6+j])
        columns.append(add(add(scale(v, cosine), scale(cross(axis, v), sine)),
                           scale(axis, dot(axis, v) * (1.0-cosine))))
    return tuple(columns[j][i] for i in range(3) for j in range(3))


def angles(m):
    pitch = math.asin(max(-1.0, min(1.0, -m[5])))
    if abs(math.cos(pitch)) > 1e-8:
        return math.atan2(m[2], m[8]), pitch, math.atan2(m[3], m[4])
    return math.atan2(-m[6], m[0]), pitch, 0.0


def properties(components):
    parts = []
    for unused_name, component, offset, bounds in components:
        mass = geometry._finite(getattr(component, 'weight', None))
        if mass <= 0.0:
            raise ValueError('detached component weight must be positive')
        centre = tuple((bounds[0][i]+bounds[1][i])*0.5+offset[i] for i in range(3))
        size = sub(bounds[1], bounds[0])
        parts.append((mass, centre, size))
    mass = sum(p[0] for p in parts)
    centre = tuple(sum(p[0]*p[1][i] for p in parts)/mass for i in range(3))
    inertia = [0.0] * 9
    for part_mass, origin, size in parts:
        r = sub(origin, centre)
        for i in range(3):
            for j in range(3):
                inertia[3*i+j] += part_mass * (
                    (sum(size[k]**2 for k in range(3) if k != i)/12.0 + dot(r, r))
                    if i == j else 0.0) - part_mass*r[i]*r[j]
    return {'mass': mass, 'centre': centre, 'inertia': tuple(inertia),
            'inverse_inertia': inverse(inertia)}


def box_contact(a, b, horizontal=False):
    """SAT normal from B to A, penetration and a face/edge contact point."""
    if any(abs(a[0][i]-b[0][i]) > sum(abs(v[i]) for v in a[1]+b[1])+EPSILON
           for i in range(3)):
        return None
    candidates = list(a[1]) + list(b[1])
    candidates += [cross(x, y) for x in a[1] for y in b[1]]
    if horizontal:
        if box_contact(a, b) is None:
            return None
        candidates = [(v[0], 0.0, v[2]) for v in candidates]
    delta = sub(a[0], b[0])
    best = None
    for axis in candidates:
        if length(axis) <= EPSILON:
            continue
        axis = unit(axis)
        gap = sum(abs(dot(v, axis)) for v in a[1]+b[1]) - abs(dot(delta, axis))
        if gap < -EPSILON:
            return None
        if best is None or gap < best[0]:
            normal = axis if dot(delta, axis) >= 0.0 else scale(axis, -1.0)
            best = max(0.0, gap), normal
    if best is None:
        return None
    depth, normal = best
    # Clip the midpoint into each box's support face. Using an arbitrary
    # corner for a flat face would invent torque in a centred impact.
    middle = scale(add(a[0], b[0]), 0.5)
    def face(box, direction):
        point = box[0]
        for half in box[1]:
            size = length(half)
            if size <= EPSILON:
                continue
            u = scale(half, 1.0/size)
            projection = dot(u, direction)
            distance = (math.copysign(size, projection) if abs(projection) > EPSILON
                        else max(-size, min(size, dot(sub(middle, box[0]), u))))
            point = add(point, scale(u, distance))
        return point
    point = scale(add(face(a, scale(normal, -1.0)), face(b, normal)), 0.5)
    return normal, depth, point


class Body(object):
    def __init__(self, components, frame):
        self.components = components
        self.props = properties(components)
        self.rotation = matrix(frame['attitude'])
        self.com = add(tuple(frame['position']), mul(self.rotation, self.props['centre']))
        self.velocity = tuple(frame.get('velocity', ZERO))
        self.angular = tuple(frame.get('angular_velocity', ZERO))
        self.grounded = bool(frame.get('grounded', False))
        self.sleeping = bool(frame.get('sleeping', False))
        self.impact_serial = int(frame.get('impact_serial', 0))
        self.impact = copy.deepcopy(frame.get('impact'))
        self.acks = copy.deepcopy(frame.get('acks', []))
        self.quiet_time = 0.0
        self.support_points = []
        self._box_cache = None
        self._point_cache = None
        self._inertia_cache = None
        self._local_points = tuple(corner
            for unused_name, unused_component, offset, bounds in components
            for corner in geometry._corners(bounds, offset))
        self.radius = max(length(sub(p, self.props['centre'])) for p in self._local_points)

    @property
    def position(self):
        return sub(self.com, mul(self.rotation, self.props['centre']))

    def frame(self):
        return {'position': self.position, 'attitude': angles(self.rotation),
                'centre': self.props['centre'],
                'velocity': self.velocity, 'angular_velocity': self.angular,
                'grounded': self.grounded, 'sleeping': self.sleeping,
                'impact_serial': self.impact_serial, 'impact': self.impact,
                'acks': copy.deepcopy(self.acks)}

    def boxes(self):
        key = (self.com, self.rotation)
        if self._box_cache is not None and self._box_cache[0] == key:
            return self._box_cache[1]
        result = []
        for unused_name, unused_component, offset, bounds in self.components:
            centre = tuple((bounds[0][i]+bounds[1][i])*0.5+offset[i] for i in range(3))
            axes = []
            for i in range(3):
                axis = [0.0]*3
                axis[i] = (bounds[1][i]-bounds[0][i])*0.5
                axes.append(mul(self.rotation, axis))
            result.append((add(self.position, mul(self.rotation, centre)), tuple(axes)))
        self._box_cache = (key, tuple(result))
        return self._box_cache[1]

    def points(self):
        key = self.com, self.rotation
        if self._point_cache is not None and self._point_cache[0] == key:
            return self._point_cache[1]
        position = self.position
        points = tuple(add(position, mul(self.rotation, corner))
                       for corner in self._local_points)
        self._point_cache = key, points
        return points

    def inverse_inertia(self, value):
        if self._inertia_cache is None or self._inertia_cache[0] != self.rotation:
            rotation, inertia = self.rotation, self.props['inverse_inertia']
            transposed = transpose(rotation)
            columns = [mul(rotation, mul(inertia, mul(transposed, axis)))
                       for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]
            world = tuple(columns[j][i] for i in range(3) for j in range(3))
            self._inertia_cache = rotation, world
        return mul(self._inertia_cache[1], value)

    def point_velocity(self, point):
        return add(self.velocity, cross(self.angular, sub(point, self.com)))

    def denominator(self, point, normal):
        arm = cross(sub(point, self.com), normal)
        return 1.0/self.props['mass'] + dot(arm, self.inverse_inertia(arm))

    def momentum(self, linear, angular):
        self.velocity = add(self.velocity, scale(linear, 1.0/self.props['mass']))
        self.angular = add(self.angular, self.inverse_inertia(angular))
        if length(linear) + length(angular) > EPSILON:
            self.sleeping = False
            self.quiet_time = 0.0

    def impulse(self, impulse, point):
        self.momentum(impulse, cross(sub(point, self.com), impulse))

    def kinetic_energy(self):
        local = mul(transpose(self.rotation), self.angular)
        return (0.5*self.props['mass']*dot(self.velocity, self.velocity) +
                0.5*dot(local, mul(self.props['inertia'], local)))

    def integrate(self, dt):
        self.com = add(self.com, add(scale(self.velocity, dt), (0, -0.5*9.81*dt*dt, 0)))
        self.velocity = add(self.velocity, (0, -9.81*dt, 0))
        self.rotation = rotate(self.rotation, self.angular, dt)


def frame_at(row, elapsed):
    """Read a new body checkpoint or migrate the initial accepted throw."""
    frame = row['flight'].get('body')
    if frame is not None:
        return copy.deepcopy(frame)
    flight = row['flight']
    position, attitude = turret_detachment.pose_at(flight, row['attitude'], row['spin'], elapsed)
    if elapsed >= flight['duration']:
        velocity, spin = ZERO, ZERO
    else:
        segment = flight['segments'][0]
        for candidate in flight['segments']:
            if elapsed >= candidate['start']:
                segment = candidate
        velocity = turret_detachment.flight_velocity(segment['velocity'], elapsed-segment['start'])
        spin = (row['spin'][1], row['spin'][0], row['spin'][2])
    return dict(position=position, attitude=attitude, velocity=velocity,
                angular_velocity=spin, grounded=False, sleeping=False)


def render_pose(frame, elapsed):
    # Extrapolation is bounded to one published motion slice; missed updates
    # cannot launch a remote body through terrain. Physics never uses this.
    dt = 0.0 if frame['sleeping'] else min(turret_detachment.FLIGHT_STEP_SECONDS, max(0.0, elapsed))
    rotation = rotate(matrix(frame['attitude']), frame['angular_velocity'], dt)
    centre = tuple(frame.get('centre', ZERO))
    com = add(tuple(frame['position']), mul(matrix(frame['attitude']), centre))
    position = sub(add(com, scale(tuple(frame['velocity']), dt)), mul(rotation, centre))
    if not frame['grounded']:
        position = add(position, (0, -0.5*9.81*dt*dt, 0))
    return position, angles(rotation)


def interpolate_rotation(first, second, alpha):
    """Shortest SO(3) arc, including the Euler pitch/roll singularities."""
    def quaternion(m):
        trace = m[0]+m[4]+m[8]
        if trace > 0.0:
            s = 2.0*math.sqrt(trace+1.0)
            return ((m[7]-m[5])/s, (m[2]-m[6])/s, (m[3]-m[1])/s, s*.25)
        i = max(range(3), key=lambda j: m[4*j])
        j, k = (i+1)%3, (i+2)%3
        s = 2.0*math.sqrt(max(0.0, 1.0+m[4*i]-m[4*j]-m[4*k]))
        q = [0.0]*4
        q[i], q[j], q[k] = s*.25, (m[3*i+j]+m[3*j+i])/s, (m[3*i+k]+m[3*k+i])/s
        q[3] = (m[3*k+j]-m[3*j+k])/s
        return tuple(q)
    a, b = quaternion(first), quaternion(second)
    cosine = sum(x*y for x, y in zip(a, b))
    if cosine < 0.0:
        b = tuple(-v for v in b)
        cosine = -cosine
    angle = math.acos(min(1.0, cosine))
    if angle <= EPSILON:
        q = tuple(x+(y-x)*alpha for x, y in zip(a, b))
    else:
        q = tuple((x*math.sin((1.0-alpha)*angle)+y*math.sin(alpha*angle))/math.sin(angle)
                  for x, y in zip(a, b))
    size = math.sqrt(sum(v*v for v in q))
    x, y, z, w = (v/size for v in q)
    return (1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
            2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
            2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y))


class PresentationBuffer(object):
    """Confirmed body samples with a monotonic, adaptive render cursor.

    No simulation runs here. A 40-ms extrapolation cap used to expire before
    slow-worker packets even arrived, causing high-FPS freeze/jump playback.
    Buffer complete accepted poses instead, using the vehicle presentation's
    existing delay floor/decay and convergence law. Collision stays canonical.
    """
    def __init__(self):
        self.samples = []
        self.cursor = None
        self.last_render = None
        self.arrival = None
        self.anchor = 0.0
        self.delay = 0.0

    def push(self, row, now, age):
        from gui.mods.offline_lan_0922 import snapshot_sync
        frame = row['flight'].get('body')
        if frame is None:
            return
        source = float(row.get('motion_time_ms', row['created_time_ms']))/1000.0
        if self.samples and source < self.samples[-1][0]:
            return
        waking = bool(self.samples and self.samples[-1][1]['sleeping'])
        interval = (max(source-self.samples[-1][0], now-self.arrival)
                    if self.samples else 0.0)
        floor = snapshot_sync.MIN_TIMED_DELAY_US/1000000.0
        initial = snapshot_sync.INITIAL_TIMED_DELAY_US/1000000.0
        if waking:
            # A sleeping body publishes no unchanged poses. Ten seconds of
            # rest is not ten seconds of network jitter: retain its confirmed
            # still pose as the left endpoint of one normal wake transition.
            # Advancing time while that pose is unchanged cannot cause a snap.
            hold = max(self.samples[-1][0], source-max(floor, self.delay))
            self.samples = [(hold, self.samples[-1][1])]
            self.cursor = max(self.cursor, hold)
            self.last_render = float(now)
            interval = turret_detachment.FLIGHT_STEP_SECONDS
        self.delay = max(floor, self.delay-interval*snapshot_sync.TIMED_DELAY_DECAY_RATIO,
                         max(0.0, age)+interval, initial if not self.samples else 0.0)
        if self.samples and source == self.samples[-1][0]:
            self.samples.pop()
        self.samples.append((source, copy.deepcopy(frame)))
        self.samples = self.samples[-64:]
        self.arrival, self.anchor = float(now), source+max(0.0, age)
        if self.cursor is None:
            self.cursor = source

    def pose(self, now):
        newest = self.samples[-1][0]
        first_render = self.last_render is None
        dt = max(0.0, float(now)-(self.last_render if self.last_render is not None else now))
        self.last_render = float(now)
        target = self.anchor+max(0.0, now-self.arrival)-self.delay
        if first_render:
            # Prerequisite loading can outlast several packets. Adopt the
            # current buffered pose on first appearance, not the launch pose.
            self.cursor = max(self.samples[0][0], min(newest, target))
        error = max(0.0, target-self.cursor)
        maximum = self.cursor+dt*(1.0+min(1.0, error*error))
        self.cursor = max(self.samples[0][0], min(newest, maximum, max(self.cursor, target)))
        while len(self.samples) > 2 and self.samples[1][0] <= self.cursor:
            self.samples.pop(0)
        left, right = self.samples[0], self.samples[-1]
        for candidate in self.samples[1:]:
            if candidate[0] >= self.cursor:
                right = candidate
                break
            left = candidate
        span = right[0]-left[0]
        alpha = max(0.0, min(1.0, (self.cursor-left[0])/span)) if span > 0.0 else 1.0
        a, b = left[1], right[1]
        ra, rb = matrix(a['attitude']), matrix(b['attitude'])
        rotation = interpolate_rotation(ra, rb, alpha)
        ca = add(tuple(a['position']), mul(ra, tuple(a['centre'])))
        cb = add(tuple(b['position']), mul(rb, tuple(b['centre'])))
        com = add(ca, scale(sub(cb, ca), alpha))
        centre = tuple(b['centre'])
        # Impacts/sleep become visible at their sample time, never on receipt
        # while the body is still visibly airborne in the history buffer.
        event = b if alpha >= 1.0 else a
        return sub(com, mul(rotation, centre)), angles(rotation), event


def revision(row, body, now_ms):
    result = copy.deepcopy(row)
    frame = body.frame()
    position = frame['position']
    impact = frame['impact']
    result['flight'] = {
        'origin': position, 'velocity': frame['velocity'],
        'segments': [{'origin': position, 'velocity': frame['velocity'], 'start': 0.0, 'duration': 0.0}],
        'duration': 0.0, 'rest': position,
        'contact': impact['point'] if impact else (position if body.grounded else None),
        'impact_velocity': impact['velocity'] if impact else ZERO,
        'energy': impact['energy'] if impact else 0.0,
        'landed': bool(impact or body.grounded), 'rest_attitude': frame['attitude'], 'body': frame,
    }
    result.update(attitude=frame['attitude'], spin=ZERO, support_key=None,
                  motion_seq=row.get('motion_seq', 0)+1,
                  motion_time_ms=int(now_ms), created_time_ms=int(now_ms))
    return result


def scenery_step(body, dt, collide):
    """Sweep each component corner against actual scenery points/normals."""
    before = body.points()
    body.integrate(dt)
    after = body.points()
    skin = query_skin(before + after)
    contacts = []
    for start, end in zip(before, after):
        # The tiny skin only keeps an exact touching surface in the ray. It
        # is numerical tolerance, never a suspension height or upward lift.
        # Extend along motion as well as up: a horizontal wall contact needs
        # a nonzero ray after the same binary32 conversion as a floor contact.
        direction = unit(sub(end, start))
        hit = collide(add(sub(start, scale(direction, skin)), (0, skin, 0)),
                      sub(add(end, scale(direction, skin)), (0, skin, 0)))
        if hit is None:
            continue
        point, normal = tuple(hit[0]), unit(tuple(hit[1]))
        if length(normal) <= EPSILON:
            continue
        penetration = max(0.0, skin-dot(sub(end, point), normal))
        if dot(sub(end, start), normal) > skin:
            continue
        contacts.append((point, normal, penetration))
    was_grounded = body.grounded
    body.grounded = any(n[1] > 0.0 for p, n, d in contacts)
    if not contacts:
        body.support_points = []
        return
    initial_velocity = body.velocity
    # Resolve position independently, along the real scenery normal.
    correction = ZERO
    for unused_pass in range(SOLVER_PASSES):
        changed = False
        for point, normal, depth in contacts:
            remaining = depth-dot(correction, normal)
            if remaining > EPSILON:
                correction = add(correction, scale(normal, remaining))
                changed = True
        if not changed:
            break
    body.com = add(body.com, correction)
    for unused in range(SOLVER_PASSES):
        for point, normal, unused_depth in contacts:
            closing = dot(body.point_velocity(point), normal)
            if closing < 0.0:
                body.impulse(scale(normal, -closing/body.denominator(point, normal)), point)
            # Maximum-dissipation, no-slip impact. Each scalar constraint
            # decreases kinetic energy; no bounce/drag constant is guessed.
            tangent = sub(body.point_velocity(point), scale(normal, dot(body.point_velocity(point), normal)))
            if length(tangent) > EPSILON:
                direction = unit(tangent)
                body.impulse(scale(direction, -length(tangent)/body.denominator(point, direction)), point)
    if not was_grounded and body.grounded:
        point, normal, unused_depth = max(contacts, key=lambda c: c[1][1])
        body.impact_serial += 1
        body.impact = {'point': point, 'normal': normal, 'velocity': initial_velocity,
                       'energy': turret_detachment.impact_energy(initial_velocity)}
    body.support_points = list(set(p for p, n, d in contacts if n[1] > 0.0))


def supported_com(body):
    """The COM projection must lie in a genuine support polygon to sleep."""
    points = sorted(set((p[0], p[2]) for p in body.support_points))
    if len(points) < 3:
        return False
    def turn(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    lower, upper = [], []
    for target, source in ((lower, points), (upper, reversed(points))):
        for p in source:
            while len(target) >= 2 and turn(target[-2], target[-1], p) <= EPSILON:
                target.pop()
            target.append(p)
    hull = lower[:-1] + upper[:-1]
    com = (body.com[0], body.com[2])
    return len(hull) >= 3 and all(turn(a, b, com) >= -EPSILON
                                 for a, b in zip(hull, hull[1:]+hull[:1]))


def vehicle_contact(body, boxes, mass, velocity, horizontal=False):
    """Return reciprocal vehicle delta and body momentum at a real contact."""
    boxes = tuple(b for b in boxes if length(sub(body.com, b[0])) <=
                  body.radius+sum(length(v) for v in b[1]))
    if not boxes:
        return None
    # A turret resting below the tracks is struck from the side. Retain
    # that entry face if a late snapshot already has a deeper overlap; the
    # shallow roof axis must not turn geometric recovery into an elevator.
    side = (body.grounded and min(p[1] for p in body.points()) <
            min(b[0][1]-sum(abs(v[1]) for v in b[1]) for b in boxes))
    contacts = []
    side_contacts = []
    for a in body.boxes():
        for b in boxes:
            # A thin barrel can meet the hull while the chassis and debris
            # share the same ground height. The old whole-body bottom test
            # then selected the barrel's shallow vertical axis and discarded
            # the human's horizontal contact. Classify each component pair:
            # a grounded component centred below this vehicle box's roof is
            # a side contact; a component above it retains vertical support.
            component_side = side or (body.grounded and
                a[0][1] < b[0][1] + sum(abs(axis[1]) for axis in b[1]) - EPSILON)
            contact = box_contact(a, b, horizontal=component_side)
            if contact is not None:
                contacts.append(contact)
                if component_side and contact[1] > EPSILON:
                    side_contacts.append(contact)
    # A tangent contact on the track roof must not mask an intersecting
    # barrel/hull side. Resolve that side before considering roof support.
    if side_contacts:
        contacts = side_contacts
    if horizontal and not side:
        contacts = [c for c in contacts if abs(c[0][1]) < max(abs(c[0][0]), abs(c[0][2]))]
    if not contacts:
        return None
    # Separate by the least occupied face, rather than the highest roof.
    normal, depth, point = min(contacts, key=lambda c: c[1])
    if horizontal:
        normal = unit((normal[0], 0.0, normal[2]))
        if length(normal) <= EPSILON:
            return None
    inv_vehicle = 1.0/mass
    relative = dot(sub(body.point_velocity(point), velocity), normal)
    impulse = ZERO
    if relative < 0.0:
        impulse = scale(normal, -relative/(body.denominator(point, normal)+inv_vehicle))
    opposite = scale(impulse, -inv_vehicle)
    linear_correction = scale(normal, depth/(1.0/body.props['mass']+inv_vehicle))
    return {'point': point, 'normal': normal, 'momentum': impulse,
            'angular_momentum': cross(sub(point, body.com), impulse),
            'delta': opposite,
            'body_correction': scale(linear_correction, 1.0/body.props['mass']),
            'vehicle_correction': scale(linear_correction, -inv_vehicle)}


def translate(body, displacement, collide):
    """Contact recovery may move debris only through free scenery."""
    fraction = 1.0
    points = body.points()
    skin = query_skin(points + tuple(add(p, displacement) for p in points))
    direction = unit(displacement)
    for point in points:
        # This path used to omit the native binary32 skin used by flight.
        # A resting corner a few ULPs below a slope then missed the ground
        # entirely during a horizontal shove, leaving subsequent rays buried.
        hit = collide(add(sub(point, scale(direction, skin)), (0, skin, 0)),
                      sub(add(add(point, displacement), scale(direction, skin)), (0, skin, 0)))
        if hit is None:
            continue
        normal = unit(tuple(hit[1]))
        closing = -dot(displacement, normal)
        if closing <= EPSILON:
            continue
        clearance = dot(sub(point, tuple(hit[0])), normal)-skin
        fraction = min(fraction, max(0.0, clearance/closing))
    actual = scale(displacement, fraction)
    body.com = add(body.com, actual)
    return actual


def nearby_vehicles(body, dt, vehicles):
    """Conservative swept-sphere broad phase with cached vehicle bounds."""
    end = add(body.com, add(scale(body.velocity, dt), (0, -0.5*9.81*dt*dt, 0)))
    lows = tuple(min(body.com[i], end[i])-body.radius for i in range(3))
    highs = tuple(max(body.com[i], end[i])+body.radius for i in range(3))
    # Include the ballistic apex even when both endpoints lie below it.
    apex = body.velocity[1]/9.81
    if 0.0 < apex < dt:
        highs = (highs[0], body.com[1]+0.5*body.velocity[1]*apex+body.radius, highs[2])
    result = []
    for vehicle in vehicles:
        boxes = vehicle['boxes']
        cached = vehicle.get('_rigid_bounds')
        if cached is None or cached[0] is not boxes:
            bounds = [(tuple(c[i]-sum(abs(v[i]) for v in axes) for i in range(3)),
                       tuple(c[i]+sum(abs(v[i]) for v in axes) for i in range(3)))
                      for c, axes in boxes]
            cached = (boxes, tuple(min(b[0][i] for b in bounds) for i in range(3)),
                      tuple(max(b[1][i] for b in bounds) for i in range(3)))
            vehicle['_rigid_bounds'] = cached
        if all(lows[i] <= cached[2][i] and highs[i] >= cached[1][i] for i in range(3)):
            result.append(vehicle)
    return result


def advance(body, dt, vehicles, collide, apply_vehicle=None, budget=None, clock=None):
    """Advance elapsed time with substeps and mass-aware vehicle contacts."""
    remaining = max(0.0, dt)
    clock = clock or time.time
    deadline = clock()+budget if budget is not None else None
    all_vehicles = vehicles
    vehicles = nearby_vehicles(body, remaining, all_vehicles)
    if body.sleeping:
        # A sleeping body still checks its actual supporting scenery and
        # nearby moving vehicles. Destroyed support cannot leave it airborne.
        support = body.support_points
        skin = query_skin(body.points())
        stable = len(support) >= 3 and all(collide(add(p, (0, skin, 0)),
                                                  sub(p, (0, skin, 0))) is not None
                                                for p in support)
        touched = any(vehicle_contact(body, v['boxes'], v['mass'], v['velocity']) is not None
                      for v in vehicles)
        if stable and not touched:
            return remaining
        body.sleeping = False
    while remaining > 1e-9:
        if remaining < dt and deadline is not None and clock() >= deadline:
            break
        step = min(STEP, remaining)
        vehicles = nearby_vehicles(body, step, all_vehicles)
        had_support = body.grounded
        scenery_step(body, step, collide)
        for unused_pass in range(SOLVER_PASSES):
            touched = False
            for vehicle in vehicles:
                hit = vehicle_contact(body, vehicle['boxes'], vehicle['mass'], vehicle['velocity'])
                if hit is None:
                    continue
                if hit['normal'][1] > max(abs(hit['normal'][0]), abs(hit['normal'][2])):
                    if not had_support and not body.grounded:
                        body.impact_serial += 1
                        body.impact = {'point': hit['point'], 'normal': hit['normal'],
                                       'velocity': body.velocity,
                                       'energy': turret_detachment.impact_energy(body.velocity)}
                    body.grounded = True
                # Living human horizontal impulses are consumed exactly once
                # from the visible integrator's cumulative ledger. Vertical
                # support remains worker-owned, as for terrain suspension.
                human_horizontal = (vehicle.get('human') and vehicle.get('alive', True) and
                                    abs(hit['normal'][1]) < max(abs(hit['normal'][0]), abs(hit['normal'][2])))
                if not human_horizontal:
                    body.momentum(hit['momentum'], hit['angular_momentum'])
                    touched = touched or length(hit['momentum']) > EPSILON
                    if (callable(apply_vehicle) and not vehicle.get('human') and
                            (length(hit['delta']) > EPSILON or
                             length(hit['vehicle_correction']) > EPSILON)):
                        old_boxes, old_velocity = vehicle['boxes'], vehicle['velocity']
                        apply_vehicle(vehicle, hit, step)
                        touched = (touched or vehicle['boxes'] != old_boxes or
                                   vehicle['velocity'] != old_velocity)
                correction = hit['body_correction']
                if length(correction) > EPSILON:
                    actual = translate(body, correction, collide)
                    touched = touched or length(actual) > EPSILON
            if not touched:
                break
            # A correction or an earlier contact can change the trajectory.
            # Recheck cached bounds before the next constraint sweep.
            vehicles = nearby_vehicles(body, 0.0, all_vehicles)
        remaining -= step
    # Gravity over one solver slice bounds the contact integrator's residual
    # energy. Sleep only below that numerical floor and over a support polygon;
    # neither an unsupported corner nor an arbitrary height can freeze a body.
    if (body.grounded and supported_com(body) and
            body.kinetic_energy() < 0.5*body.props['mass']*(9.81*STEP)**2):
        body.velocity, body.angular = ZERO, ZERO
        body.sleeping = True
    # The loop's sub-nanosecond termination tolerance is already consumed.
    # Do not publish 5999 ms for a body that reached exactly 6000 ms.
    return dt if remaining <= 1e-9 else dt-remaining
