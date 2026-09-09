"""Indexed Console surfaces in component-local metres (CPython 2.7).

Bounds are acceleration only. Faces define every ray, distance and cone hit.
Disconnected edge components remain separate; open surfaces have surface hits
but no invented interior. No vertex clustering, hull construction or fitting
is performed. The compact payload preserves decoded doubles losslessly and
is expanded only for the vehicle whose layout is being built.
"""
import base64
import heapq
import math
import struct
import zlib


_EPS = 1.0e-8
_LEAF_SIZE = 8


def _sub(a, b):
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _add_scaled(a, b, scale):
    return a[0] + b[0] * scale, a[1] + b[1] * scale, a[2] + b[2] * scale


def encode(vertices, triangles):
    """Stable lossless payload, independent of Python object serialization."""
    parts = [struct.pack('<4sII', b'IM01', len(vertices), len(triangles))]
    parts.extend(struct.pack('<3d', *v) for v in vertices)
    parts.extend(struct.pack('<3I', *t) for t in triangles)
    return base64.b64encode(zlib.compress(b''.join(parts), 9)).decode('ascii')


def decode(payload):
    blob = zlib.decompress(base64.b64decode(payload))
    magic, nv, nt = struct.unpack_from('<4sII', blob)
    if magic != b'IM01' or len(blob) != 12 + nv * 24 + nt * 12:
        raise ValueError('invalid indexed interior mesh payload')
    vertices = tuple(struct.unpack_from('<3d', blob, 12 + i * 24)
                     for i in range(nv))
    triangles = tuple(struct.unpack_from('<3I', blob, 12 + nv * 24 + i * 12)
                      for i in range(nt))
    return vertices, triangles


def _bounds(points):
    return (tuple(min(p[a] for p in points) for a in range(3)),
            tuple(max(p[a] for p in points) for a in range(3)))


def _tree(vertices, faces, indices):
    low, high = _bounds([vertices[i] for j in indices for i in faces[j]])
    if len(indices) <= _LEAF_SIZE:
        return low, high, None, None, tuple(indices)
    axis = max(range(3), key=lambda a: high[a] - low[a])
    ordered = sorted(indices, key=lambda j: sum(vertices[i][axis]
                                                for i in faces[j]))
    mid = len(ordered) // 2
    return (low, high, _tree(vertices, faces, ordered[:mid]),
            _tree(vertices, faces, ordered[mid:]), ())


def _orientations(faces):
    """Orient a closed manifold for inside tests without changing its faces."""
    edges = {}
    for j, face in enumerate(faces):
        for k in range(3):
            a, b = face[k], face[(k + 1) % 3]
            edges.setdefault(tuple(sorted((a, b))), []).append((j, a < b))
    if any(len(owners) != 2 for owners in edges.values()):
        return None
    links = [[] for unused in faces]
    for (a, da), (b, db) in edges.values():
        relation = -1 if da == db else 1
        links[a].append((b, relation))
        links[b].append((a, relation))
    signs, stack = {0: 1}, [0]
    while stack:
        j = stack.pop()
        for neighbor, relation in links[j]:
            expected = signs[j] * relation
            if neighbor in signs:
                if signs[neighbor] != expected:
                    return None
            else:
                signs[neighbor] = expected
                stack.append(neighbor)
    return tuple(signs[j] for j in range(len(faces)))


def prepare(vertices, triangles, primitive_id='mesh'):
    """One accelerated mesh per actual edge-connected piece, never a hull."""
    if not vertices or not triangles:
        raise ValueError('empty interior mesh')
    vertices = tuple(tuple(float(c) for c in v) for v in vertices)
    if any(len(v) != 3 or any(not -60.0 <= c <= 60.0 for c in v)
           for v in vertices):
        raise ValueError('invalid interior mesh coordinate')
    # Exactly identical seam vertices can share topology without moving them.
    # Nearby vertices are deliberately NOT welded: a narrow gap is geometry.
    unique, remap, canonical = [], [], {}
    for v in vertices:
        if v not in canonical:
            canonical[v] = len(unique)
            unique.append(v)
        remap.append(canonical[v])
    faces = []
    for f in triangles:
        if len(f) != 3 or any(i < 0 or i >= len(vertices) for i in f):
            raise ValueError('invalid interior triangle index')
        face = tuple(remap[i] for i in f)
        a, b, c = (unique[i] for i in face)
        normal = _cross(_sub(b, a), _sub(c, a))
        if _dot(normal, normal) == 0.0:
            continue
        faces.append(face)
    if not faces:
        raise ValueError('interior mesh has no nondegenerate triangle')
    edges = {}
    for j, f in enumerate(faces):
        for k in range(3):
            edge = tuple(sorted((f[k], f[(k + 1) % 3])))
            edges.setdefault(edge, []).append(j)
    adjacency = [set() for unused in faces]
    for linked in edges.values():
        for j in linked:
            adjacency[j].update(linked)
    remaining = set(range(len(faces)))
    result = []
    while remaining:
        seed = min(remaining)
        stack, component = [seed], set()
        while stack:
            j = stack.pop()
            if j in component:
                continue
            component.add(j)
            stack.extend(adjacency[j] - component)
        remaining.difference_update(component)
        source_faces = [faces[j] for j in sorted(component)]
        vertex_ids = sorted(set(i for f in source_faces for i in f))
        mapping = dict((v, i) for i, v in enumerate(vertex_ids))
        piece_vertices = tuple(unique[i] for i in vertex_ids)
        piece_faces = tuple(tuple(mapping[i] for i in f) for f in source_faces)
        orientations = _orientations(piece_faces)
        closed = orientations is not None
        tree = _tree(piece_vertices, piece_faces, list(range(len(piece_faces))))
        low, high = tree[:2]
        signed_volume = sum(_dot(piece_vertices[f[0]], _cross(
            piece_vertices[f[1]], piece_vertices[f[2]])) * (orientations[j] if closed else 1)
            for j, f in enumerate(piece_faces)) / 6.0
        result.append({
            'shape': 'mesh', 'primitive_id': '%s:%d' % (primitive_id, len(result)),
            'vertices': piece_vertices, 'triangles': piece_faces, 'bvh': tree,
            'minimum': low, 'maximum': high,
            'center': tuple((low[a] + high[a]) * 0.5 for a in range(3)),
            'half_extents': tuple((high[a] - low[a]) * 0.5 for a in range(3)),
            'closed': closed, 'orientations': orientations,
            'volume_m3': abs(signed_volume) if closed else 0.0,
            'volume_status': 'closed_surface' if closed else 'open_surface_only',
        })
    return tuple(result)


def _line_box(start, delta, low, high, first, last):
    for a in range(3):
        if abs(delta[a]) < 1.0e-15:
            if start[a] < low[a] - _EPS or start[a] > high[a] + _EPS:
                return None
            continue
        x, y = (low[a] - start[a]) / delta[a], (high[a] - start[a]) / delta[a]
        if x > y:
            x, y = y, x
        first, last = max(first, x), min(last, y)
        if first > last + _EPS:
            return None
    return first, last


def _triangle_line(start, delta, a, b, c):
    e1, e2 = _sub(b, a), _sub(c, a)
    p = _cross(delta, e2)
    det = _dot(e1, p)
    if abs(det) <= 1.0e-14:
        return None
    t = _sub(start, a)
    u = _dot(t, p) / det
    if u < -_EPS or u > 1.0 + _EPS:
        return None
    q = _cross(t, e1)
    v = _dot(delta, q) / det
    if v < -_EPS or u + v > 1.0 + _EPS:
        return None
    return _dot(e2, q) / det, (1 if det > 0.0 else -1)


def _line_hits(mesh, start, delta, first, last, stats=None):
    vertices, faces = mesh['vertices'], mesh['triangles']
    stack, hits = [mesh['bvh']], []
    while stack:
        node = stack.pop()
        if _line_box(start, delta, node[0], node[1], first, last) is None:
            continue
        if node[2] is not None:
            stack.extend((node[2], node[3]))
            continue
        for j in node[4]:
            if stats is not None:
                stats['triangles'] = stats.get('triangles', 0) + 1
            a, b, c = (vertices[i] for i in faces[j])
            hit = _triangle_line(start, delta, a, b, c)
            if hit is not None and first - _EPS <= hit[0] <= last + _EPS:
                hits.append((hit[0], hit[1] * (mesh['orientations'][j]
                    if mesh['orientations'] is not None else 1)))
    hits.sort()
    groups = []
    for t, sign in hits:
        if groups and abs(t - groups[-1][0]) <= 1.0e-9:
            groups[-1][1].add(sign)
        else:
            groups.append([t, set((sign,))])
    return groups


def intervals(start, end, mesh, stats=None):
    """All disjoint occupied intervals, clipped to the finite input segment."""
    delta = _sub(end, start)
    if _line_box(start, delta, mesh['minimum'], mesh['maximum'], 0.0, 1.0) is None:
        return ()
    if _dot(delta, delta) < 1.0e-24:
        return ((0.0, 1.0),) if contains(start, mesh) else ()
    first, last = (-float('inf'), float('inf')) if mesh['closed'] else (0.0, 1.0)
    hits = _line_hits(mesh, start, delta, first, last, stats)
    if not mesh['closed']:
        return tuple((max(0.0, min(1.0, t)), max(0.0, min(1.0, t)))
                     for t, unused in hits)
    result, entry = [], None
    for t, signs in hits:
        # Both signs at the same edge/vertex mean a tangent, not an entry.
        if len(signs) != 1:
            if 0.0 <= t <= 1.0:
                result.append((t, t))
            continue
        if entry is None:
            entry = t
        else:
            lo, hi = max(0.0, entry), min(1.0, t)
            if lo <= hi:
                result.append((lo, hi))
            entry = None
    return tuple(sorted(result))


def contains(point, mesh):
    if not mesh['closed'] or any(point[a] < mesh['minimum'][a] - _EPS or
                                point[a] > mesh['maximum'][a] + _EPS
                                for a in range(3)):
        return False
    # Non-axis-aligned to avoid repeated coplanarity with box-shaped meshes.
    delta = (1.0, 0.3713906763541037, 0.52999894000318)
    hits = _line_hits(mesh, point, delta, -float('inf'), float('inf'))
    count = 0
    for t, signs in hits:
        if abs(t) <= _EPS:
            return True
        if t > 0.0 and len(signs) == 1:
            count += 1
    return bool(count % 2)


def _box_distance_sq(point, low, high):
    return sum(max(low[a] - point[a], 0.0, point[a] - high[a]) ** 2
               for a in range(3))


def triangle_closest(point, a, b, c):
    """Closest point on the closed triangle, including its edges/vertices."""
    ab, ac, ap = _sub(b, a), _sub(c, a), _sub(point, a)
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return a
    bp = _sub(point, b)
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    if d3 >= 0.0 and d4 <= d3:
        return b
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        return _add_scaled(a, ab, d1 / (d1 - d3))
    cp = _sub(point, c)
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    if d6 >= 0.0 and d5 <= d6:
        return c
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        return _add_scaled(a, ac, d2 / (d2 - d6))
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and d4 - d3 >= 0.0 and d5 - d6 >= 0.0:
        return _add_scaled(b, _sub(c, b), (d4 - d3) / ((d4 - d3) + (d5 - d6)))
    denominator = 1.0 / (va + vb + vc)
    return _add_scaled(_add_scaled(a, ab, vb * denominator), ac, vc * denominator)


def distance(point, mesh, stats=None):
    if contains(point, mesh):
        return 0.0, tuple(point)
    vertices, faces = mesh['vertices'], mesh['triangles']
    best, closest, serial = float('inf'), None, 0
    queue = [(0.0, serial, mesh['bvh'])]
    while queue:
        lower, unused, node = heapq.heappop(queue)
        if lower > best:
            break
        if node[2] is not None:
            for child in (node[2], node[3]):
                bound = _box_distance_sq(point, child[0], child[1])
                if bound <= best:
                    serial += 1
                    heapq.heappush(queue, (bound, serial, child))
            continue
        for j in node[4]:
            if stats is not None:
                stats['triangles'] = stats.get('triangles', 0) + 1
            a, b, c = (vertices[i] for i in faces[j])
            candidate = triangle_closest(point, a, b, c)
            delta = _sub(point, candidate)
            squared = _dot(delta, delta)
            if squared < best:
                best, closest = squared, candidate
    return math.sqrt(best), closest


def _clip_axial(polygon, axis, limit, keep_above):
    result = []
    if not polygon:
        return result
    previous = polygon[-1]
    prev = _dot(previous, axis) - limit
    for current in polygon:
        now = _dot(current, axis) - limit
        before = prev >= 0.0 if keep_above else prev <= 0.0
        after = now >= 0.0 if keep_above else now <= 0.0
        if before != after:
            result.append(_add_scaled(previous, _sub(current, previous), prev / (prev - now)))
        if after:
            result.append(current)
        previous, prev = current, now
    return result


def triangle_intersects_cone(a, b, c, apex, axis, depth, tangent):
    """Minimize the cone quadratic over a triangle clipped to its axial slab.

    The minimum on the clipped convex polygon lies on a vertex/edge, or at
    an interior stationary point with positive-definite restricted Hessian.
    This tests the triangle itself, including cases where every vertex misses.
    """
    a, b, c = _sub(a, apex), _sub(b, apex), _sub(c, apex)
    polygon = _clip_axial([a, b, c], axis, 0.0, True)
    polygon = _clip_axial(polygon, axis, depth, False)
    if not polygon:
        return False
    k = 1.0 + tangent * tangent

    def quadratic(p, q):
        return _dot(p, q) - k * _dot(p, axis) * _dot(q, axis)

    tolerance = 1.0e-10
    for i, p in enumerate(polygon):
        f = quadratic(p, p)
        if f <= tolerance:
            return True
        edge = _sub(polygon[(i + 1) % len(polygon)], p)
        aa, bb = quadratic(edge, edge), quadratic(p, edge)
        if aa > 0.0:
            t = max(0.0, min(1.0, -bb / aa))
            if f + 2.0 * t * bb + t * t * aa <= tolerance:
                return True
    e1, e2 = _sub(b, a), _sub(c, a)
    aa, bb, cc = quadratic(e1, e1), quadratic(e1, e2), quadratic(e2, e2)
    det = aa * cc - bb * bb
    if aa > 0.0 and det > 1.0e-18:
        l1, l2 = quadratic(a, e1), quadratic(a, e2)
        u, v = (bb * l2 - cc * l1) / det, (bb * l1 - aa * l2) / det
        if u >= -_EPS and v >= -_EPS and u + v <= 1.0 + _EPS:
            point = _add_scaled(_add_scaled(a, e1, u), e2, v)
            axial = _dot(point, axis)
            if -_EPS <= axial <= depth + _EPS and quadratic(point, point) <= tolerance:
                return True
    return False


def intersects_cone(mesh, apex, axis, depth, tangent, stats=None):
    if contains(apex, mesh):
        return True
    base = _add_scaled(apex, axis, depth)
    radii = tuple(depth * tangent * math.sqrt(max(0.0, 1.0 - axis[a] ** 2))
                  for a in range(3))
    low = tuple(min(apex[a], base[a] - radii[a]) for a in range(3))
    high = tuple(max(apex[a], base[a] + radii[a]) for a in range(3))
    stack = [mesh['bvh']]
    vertices, faces = mesh['vertices'], mesh['triangles']
    while stack:
        node = stack.pop()
        if any(node[0][a] > high[a] + _EPS or node[1][a] < low[a] - _EPS
               for a in range(3)):
            continue
        if node[2] is not None:
            stack.extend((node[2], node[3]))
            continue
        for j in node[4]:
            if stats is not None:
                stats['triangles'] = stats.get('triangles', 0) + 1
            a, b, c = (vertices[i] for i in faces[j])
            if triangle_intersects_cone(a, b, c, apex, axis, depth, tangent):
                return True
    return False


def export_primitive(mesh):
    """Public diagnostic geometry; acceleration nodes are an implementation detail."""
    return dict((key, value) for key, value in mesh.items() if key != 'bvh')
