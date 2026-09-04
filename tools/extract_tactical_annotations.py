#!/usr/bin/env python3
"""Extract hand-drawn tactical corridors from reviewed route images.

The reviewer draws opaque red lines over images produced by
``render_tactical_routes.py``.  This tool compares each reviewed image with its
unannotated original, follows the red ink from one route spawn anchor to the
other, and writes both machine-readable candidate polylines and a visual
verification overlay.  It deliberately does not edit tactical map data: the
extracted lines are evidence for a human-reviewed route change, not trusted
navigation input.
Only routes connecting the two spawn anchors are extracted automatically;
local terminal or rear-guard routes are listed for manual review.
"""

from __future__ import annotations

import argparse
import heapq
import itertools
import json
import math
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


HEADER = 84
SCALE = 4
MAX_INK_DISTANCE = 8
PATH_COLOURS = ((0, 255, 255), (255, 80, 255), (255, 240, 40),
                (130, 120, 255))


def _font(size: int):
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _map_stem(path: Path) -> str:
    return path.stem.split("(", 1)[0]


def _red_annotation_mask(reviewed: Image.Image, original: Image.Image):
    reviewed_pixels = reviewed.load()
    original_pixels = original.load()
    width, height = reviewed.size
    mask = set()
    for y in range(HEADER, height):
        for x in range(width):
            red, green, blue = reviewed_pixels[x, y]
            if (reviewed_pixels[x, y] != original_pixels[x, y] and
                    red >= 145 and green < 195 and
                    red > green * 1.32 and red > blue * 1.45):
                mask.add((x // SCALE, (y - HEADER) // SCALE))
    return mask


def _distance_from_ink(ink, width, height):
    maximum = MAX_INK_DISTANCE + 1
    distance = [maximum] * (width * height)
    queue = deque()
    for x, y in ink:
        if 0 <= x < width and 0 <= y < height:
            index = y * width + x
            if distance[index] != 0:
                distance[index] = 0
                queue.append((x, y))
    while queue:
        x, y = queue.popleft()
        value = distance[y * width + x] + 1
        if value > MAX_INK_DISTANCE:
            continue
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1),
                       (-1, -1), (-1, 1), (1, -1), (1, 1)):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            index = ny * width + nx
            if value < distance[index]:
                distance[index] = value
                queue.append((nx, ny))
    return distance


def _anchor_pixels(graph, size):
    bounds = [float(value) for value in graph["bounds"]]
    anchors = graph.get("spawn_anchors")
    if (not isinstance(anchors, (list, tuple)) or len(anchors) != 2 or
            any(not isinstance(point, (list, tuple)) or len(point) < 2
                for point in anchors)):
        raise ValueError("spawn_anchors requires exactly two x/z points")
    left, bottom, right, top = bounds
    result = []
    extent = size - 1
    for anchor in anchors:
        result.append((
            (float(anchor[0]) - left) / (right - left) * extent,
            (top - float(anchor[1])) / (top - bottom) * extent,
        ))
    return tuple(result)


def _astar(width, height, ink_distance, start, goal, diversity):
    def index(point):
        return point[1] * width + point[0]

    start = (max(0, min(width - 1, int(round(start[0])))),
             max(0, min(height - 1, int(round(start[1])))))
    goal = (max(0, min(width - 1, int(round(goal[0])))),
            max(0, min(height - 1, int(round(goal[1])))))
    queue = [(0.0, 0.0, start)]
    best = {start: 0.0}
    parent = {}
    while queue:
        unused_score, cost, point = heapq.heappop(queue)
        if cost != best.get(point):
            continue
        if point == goal:
            path = [point]
            while point in parent:
                point = parent[point]
                path.append(point)
            path.reverse()
            return path
        x, y = point
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1),
                       (-1, -1), (-1, 1), (1, -1), (1, 1)):
            neighbour = (x + dx, y + dy)
            nx, ny = neighbour
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            ink = ink_distance[index(neighbour)]
            endpoint_distance = min(
                math.hypot(nx - start[0], ny - start[1]),
                math.hypot(nx - goal[0], ny - goal[1]),
            )
            if ink > MAX_INK_DISTANCE and endpoint_distance > 12.0:
                continue
            step = math.sqrt(2.0) if dx and dy else 1.0
            ink_cost = min(ink, MAX_INK_DISTANCE) ** 2 * 6.0
            new_cost = cost + step + ink_cost + diversity[index(neighbour)]
            if new_cost >= best.get(neighbour, float("inf")):
                continue
            best[neighbour] = new_cost
            parent[neighbour] = point
            heuristic = math.hypot(nx - goal[0], ny - goal[1])
            heapq.heappush(queue, (new_cost + heuristic, new_cost, neighbour))
    return None


def _add_diversity_penalty(diversity, width, height, path):
    if not path:
        return
    margin = max(8, len(path) // 12)
    interior = path[margin:-margin] if len(path) > margin * 2 else path
    radius = 7
    for x, y in interior:
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < width and 0 <= ny < height):
                    continue
                distance = math.hypot(dx, dy)
                if distance <= radius:
                    diversity[ny * width + nx] += (radius - distance + 1) * 22.0


def _point_segment_distance(point, first, second):
    dx, dy = second[0] - first[0], second[1] - first[1]
    denominator = dx * dx + dy * dy
    if denominator <= 1e-9:
        return math.hypot(point[0] - first[0], point[1] - first[1])
    ratio = ((point[0] - first[0]) * dx +
             (point[1] - first[1]) * dy) / denominator
    ratio = max(0.0, min(1.0, ratio))
    return math.hypot(point[0] - first[0] - ratio * dx,
                      point[1] - first[1] - ratio * dy)


def _rdp(points, epsilon):
    if len(points) <= 2:
        return points
    first, last = points[0], points[-1]
    distances = [_point_segment_distance(point, first, last)
                 for point in points[1:-1]]
    if not distances:
        return points
    maximum = max(distances)
    if maximum <= epsilon:
        return [first, last]
    split = distances.index(maximum) + 1
    return _rdp(points[:split + 1], epsilon)[:-1] + _rdp(points[split:], epsilon)


def _smooth_and_simplify(path, anchor_pixels):
    pixels = [(x * SCALE + SCALE * 0.5,
               y * SCALE + SCALE * 0.5) for x, y in path]
    smoothed = []
    radius = 3
    for index in range(len(pixels)):
        window = pixels[max(0, index - radius):index + radius + 1]
        smoothed.append((sum(point[0] for point in window) / len(window),
                         sum(point[1] for point in window) / len(window)))
    epsilon = 9.0
    simplified = _rdp(smoothed, epsilon)
    while len(simplified) > 14:
        epsilon += 2.0
        simplified = _rdp(smoothed, epsilon)
    simplified[0] = anchor_pixels[0]
    simplified[-1] = anchor_pixels[1]
    return simplified


def _polyline_distance(candidate, existing):
    if len(existing) < 2:
        return float("inf")
    total = 0.0
    samples = candidate[::max(1, len(candidate) // 32)]
    for point in samples:
        total += min(_point_segment_distance(point, first, second)
                     for first, second in zip(existing, existing[1:]))
    return total / max(1, len(samples))


def _existing_routes(graph, size):
    bounds = [float(value) for value in graph["bounds"]]
    left, bottom, right, top = bounds
    extent = size - 1
    result = []
    for route in (graph.get("routes") or {}).get("1", ()):
        points = []
        for point in route.get("waypoints", ()):
            points.append(((float(point[0]) - left) / (right - left) * extent,
                           (top - float(point[1])) / (top - bottom) * extent))
        result.append((str(route.get("id") or "route"), points))
    return result


def _split_through_routes(existing, anchors, tolerance=32.0):
    through = []
    manual = []
    for route_id, points in existing:
        if (len(points) >= 2 and
                math.hypot(points[0][0] - anchors[0][0],
                           points[0][1] - anchors[0][1]) <= tolerance and
                math.hypot(points[-1][0] - anchors[1][0],
                           points[-1][1] - anchors[1][1]) <= tolerance):
            through.append((route_id, points))
        else:
            manual.append(route_id)
    return through, manual


def _assign_ids(candidates, existing):
    if not existing:
        return [("route_%d" % (index + 1), candidate)
                for index, candidate in enumerate(candidates)]
    ids = [item[0] for item in existing]
    best = None
    for permutation in itertools.permutations(range(len(candidates)),
                                               min(len(ids), len(candidates))):
        score = sum(_polyline_distance(candidates[candidate_index],
                                       existing[route_index][1])
                    for route_index, candidate_index in enumerate(permutation))
        if best is None or score < best[0]:
            best = (score, permutation)
    assigned = []
    used = set()
    if best is not None:
        for route_index, candidate_index in enumerate(best[1]):
            assigned.append((ids[route_index], candidates[candidate_index]))
            used.add(candidate_index)
    for index, candidate in enumerate(candidates):
        if index not in used:
            assigned.append(("route_%d" % (index + 1), candidate))
    return assigned


def _world_points(points, graph, size):
    left, bottom, right, top = [float(value) for value in graph["bounds"]]
    extent = max(1.0, float(size - 1))
    return [[round(left + x / extent * (right - left), 1),
             round(top - y / extent * (top - bottom), 1)]
            for x, y in points]


def _draw_overlay(background, assigned, output):
    image = background.copy()
    draw = ImageDraw.Draw(image)
    font = _font(18)
    for index, (route_id, points) in enumerate(assigned):
        colour = PATH_COLOURS[index % len(PATH_COLOURS)]
        image_points = [(x, y + HEADER) for x, y in points]
        draw.line(image_points, fill=(0, 0, 0), width=8, joint="curve")
        draw.line(image_points, fill=colour, width=4, joint="curve")
        midpoint = image_points[len(image_points) // 2]
        draw.text((midpoint[0] + 8, midpoint[1] - 22), route_id,
                  font=font, fill=colour, stroke_width=3,
                  stroke_fill=(0, 0, 0))
    image.save(output)


def extract_map(reviewed_path, original_path, graph_path, output_dir):
    reviewed = Image.open(reviewed_path).convert("RGB")
    original = Image.open(original_path).convert("RGB")
    if (reviewed.size != original.size or
            reviewed.height - reviewed.width != HEADER or
            reviewed.width % SCALE):
        raise ValueError("route review image geometry mismatch")
    graph = json.loads(graph_path.read_text())
    anchors = _anchor_pixels(graph, reviewed.width)
    existing = _existing_routes(graph, reviewed.width)
    extractable, manual_routes = _split_through_routes(existing, anchors)
    ink = _red_annotation_mask(reviewed, original)
    result = {
        "map": _map_stem(reviewed_path),
        "reviewed": reviewed_path.name,
        "red_pixels_downsampled": len(ink),
        "manual_review_routes": manual_routes,
        "routes": [],
    }
    if len(ink) < 120:
        return result
    width = reviewed.width // SCALE
    height = (reviewed.height - HEADER) // SCALE
    distance = _distance_from_ink(ink, width, height)
    starts = tuple((point[0] / SCALE, point[1] / SCALE) for point in anchors)
    diversity = [0.0] * (width * height)
    raw_paths = []
    for unused_index in range(len(extractable)):
        path = _astar(width, height, distance, starts[0], starts[1], diversity)
        if not path:
            break
        raw_paths.append(path)
        _add_diversity_penalty(diversity, width, height, path)
    candidates = [_smooth_and_simplify(path, anchors) for path in raw_paths]
    assigned = _assign_ids(candidates, extractable)
    for route_id, points in assigned:
        result["routes"].append({
            "id": route_id,
            "pixels": [[round(x, 1), round(y + HEADER, 1)] for x, y in points],
            "world": _world_points(points, graph, reviewed.width),
        })
    if assigned:
        # Keep the reviewer's red ink unobscured in its source image.  Draw the
        # extracted candidates over the clean render as a separate comparison.
        _draw_overlay(original, assigned,
                      output_dir / (result["map"] + "-extracted.png"))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--original", required=True, type=Path)
    parser.add_argument("--navgraphs", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--map")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for reviewed_path in sorted(args.review.glob("*.png")):
        map_name = _map_stem(reviewed_path)
        if args.map and map_name != args.map:
            continue
        original_path = args.original / (map_name + ".png")
        graph_path = args.navgraphs / (map_name + ".json")
        if not original_path.is_file() or not graph_path.is_file():
            raise FileNotFoundError("missing source for %s" % map_name)
        result = extract_map(reviewed_path, original_path, graph_path,
                             args.output)
        results.append(result)
        print("%s: %d candidate route(s)" %
              (map_name, len(result["routes"])))
    destination = args.output / "annotations.json"
    destination.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print("Output: %s" % destination)


if __name__ == "__main__":
    main()
