#!/usr/bin/env python3
"""Render every shipped tactical route over the matching client minimap.

The output is intended for human route review. Team 1 uses solid lines and
filled arrowheads; team 2 uses dashed lines and outlined arrowheads. Ordinary
through-routes share one validated geometry in strict reverse; both directions
remain visible so the reviewer can confirm that contract. Local terminal
routes such as Himmelsdorf's rear_guard stay team-specific.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import shutil
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


VERSION_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NAVGRAPH_DIR = (
    VERSION_ROOT / "navgraphs")
PALETTE = (
    (55, 145, 255),
    (255, 150, 35),
    (60, 220, 105),
    (220, 75, 220),
    (40, 220, 220),
    (255, 220, 55),
)
# Removing Ensk's middle route must not recolour the retained east-field lane
# from its established green review identity to the now-vacant orange slot.
PALETTE_SLOT_OVERRIDES = {
    '06_ensk': {'east_field': 2},
}
HEADER = 84
ANNOTATION_SCALE = 4


def _map_sort_key(value):
    name = value.stem if isinstance(value, Path) else str(value)
    prefix = name.split("_", 1)[0]
    try:
        return int(prefix), name
    except ValueError:
        return float("inf"), name


def _point_pair(graph, key):
    points = graph.get(key)
    if (not isinstance(points, (list, tuple)) or len(points) != 2 or
            any(not isinstance(point, (list, tuple)) or len(point) < 2
                for point in points)):
        raise ValueError("%s requires exactly two x/z points" % key)
    return points


def _font(size: int, bold: bool = False):
    candidates = (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _find_package(packages: Path, map_name: str) -> Path:
    expected = map_name.lower() + ".pkg"
    for package in packages.glob("*.pkg"):
        if package.name.lower() == expected:
            return package
    raise FileNotFoundError("map package not found: %s" % map_name)


def _load_minimap(package: Path) -> Image.Image:
    with zipfile.ZipFile(package) as archive:
        candidates = [
            name for name in archive.namelist()
            if name.replace("\\", "/").lower().endswith("/mmap.dds")
        ]
        if not candidates:
            raise FileNotFoundError("mmap.dds not found in %s" % package)
        with archive.open(candidates[0]) as stream:
            return Image.open(stream).convert("RGB")


def _lighten(colour, amount=0.38):
    return tuple(int(round(value + (255 - value) * amount)) for value in colour)


def _line(draw, points, colour, width, dashed=False):
    if len(points) < 2:
        return
    if not dashed:
        draw.line(points, fill=(12, 12, 12), width=width + 4, joint="curve")
        draw.line(points, fill=colour, width=width, joint="curve")
        return
    dash = 14.0
    gap = 9.0
    phase = 0.0
    for first, second in zip(points, points[1:]):
        dx = second[0] - first[0]
        dy = second[1] - first[1]
        length = math.hypot(dx, dy)
        if length <= 0.001:
            continue
        cursor = -phase
        while cursor < length:
            start = max(0.0, cursor)
            end = min(length, cursor + dash)
            if end > start:
                segment = (
                    (first[0] + dx * start / length,
                     first[1] + dy * start / length),
                    (first[0] + dx * end / length,
                     first[1] + dy * end / length),
                )
                draw.line(segment, fill=(12, 12, 12), width=width + 4)
                draw.line(segment, fill=colour, width=width)
            cursor += dash + gap
        phase = (phase + length) % (dash + gap)


def _point_and_tangent(points, fraction):
    lengths = [math.hypot(b[0] - a[0], b[1] - a[1])
               for a, b in zip(points, points[1:])]
    total = sum(lengths)
    target = max(0.0, min(1.0, fraction)) * total
    travelled = 0.0
    for index, length in enumerate(lengths):
        if length <= 0.001:
            continue
        if travelled + length >= target:
            ratio = (target - travelled) / length
            first, second = points[index], points[index + 1]
            point = (first[0] + (second[0] - first[0]) * ratio,
                     first[1] + (second[1] - first[1]) * ratio)
            return point, ((second[0] - first[0]) / length,
                           (second[1] - first[1]) / length)
        travelled += length
    if len(points) >= 2:
        first, second = points[-2], points[-1]
        length = max(0.001, math.hypot(second[0] - first[0],
                                      second[1] - first[1]))
        return second, ((second[0] - first[0]) / length,
                        (second[1] - first[1]) / length)
    return points[0], (0.0, -1.0)


def _arrow(draw, points, colour, fraction, outlined=False):
    if len(points) < 2:
        return
    point, tangent = _point_and_tangent(points, fraction)
    normal = (-tangent[1], tangent[0])
    tip = (point[0] + tangent[0] * 12.0,
           point[1] + tangent[1] * 12.0)
    left = (point[0] - tangent[0] * 8.0 + normal[0] * 8.0,
            point[1] - tangent[1] * 8.0 + normal[1] * 8.0)
    right = (point[0] - tangent[0] * 8.0 - normal[0] * 8.0,
             point[1] - tangent[1] * 8.0 - normal[1] * 8.0)
    polygon = (tip, left, right)
    draw.polygon(polygon, fill=(10, 10, 10))
    inset = tuple((point[0] + (value[0] - point[0]) * 0.72,
                   point[1] + (value[1] - point[1]) * 0.72)
                  for value in polygon)
    if outlined:
        draw.line(inset + (inset[0],), fill=colour, width=3)
    else:
        draw.polygon(inset, fill=colour)


def _label_origin(draw, point, label, font, canvas_width, right_offset):
    bounds = draw.textbbox((0, 0), label, font=font, stroke_width=3)
    label_width = bounds[2] - bounds[0]
    right = point[0] + right_offset
    if right + label_width <= canvas_width - 8:
        return right, point[1] - 13
    return point[0] - right_offset - label_width, point[1] - 13


def _spawn_marker(draw, point, colour, label, font, canvas_width):
    radius = 13
    draw.ellipse((point[0] - radius - 3, point[1] - radius - 3,
                  point[0] + radius + 3, point[1] + radius + 3),
                 fill=(10, 10, 10))
    draw.ellipse((point[0] - radius, point[1] - radius,
                  point[0] + radius, point[1] + radius), fill=colour)
    draw.text(_label_origin(draw, point, label, font, canvas_width, 17),
              label, font=font,
              fill=(255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0))


def _base_marker(draw, point, colour, label, font, canvas_width):
    pole_top = point[1] - 18
    pole_bottom = point[1] + 18
    draw.line(((point[0], pole_top), (point[0], pole_bottom)),
              fill=(10, 10, 10), width=7)
    draw.line(((point[0], pole_top), (point[0], pole_bottom)),
              fill=(245, 245, 245), width=3)
    flag = ((point[0] + 2, pole_top), (point[0] + 25, pole_top + 6),
            (point[0] + 2, pole_top + 13))
    draw.polygon(flag, fill=(10, 10, 10))
    inset = ((point[0] + 4, pole_top + 3),
             (point[0] + 20, pole_top + 7),
             (point[0] + 4, pole_top + 11))
    draw.polygon(inset, fill=colour)
    draw.text(_label_origin(draw, point, label, font, canvas_width, 29),
              label, font=font,
              fill=(255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0))


def _render(graph, minimap, size):
    map_name = graph["map"]
    bounds = [float(value) for value in graph["bounds"]]
    image = Image.new("RGB", (size, size + HEADER), (24, 26, 29))
    image.paste(minimap.resize((size, size), Image.Resampling.LANCZOS),
                (0, HEADER))
    draw = ImageDraw.Draw(image)
    title_font = _font(27, True)
    text_font = _font(18, False)
    small_font = _font(15, True)
    draw.text((18, 10), map_name, font=title_font, fill=(245, 245, 245))
    draw.text((18, 48), "Solid=T1  dashed=T2  circles=spawn  flags=base  *=fallback",
              font=text_font, fill=(190, 195, 205))

    left, bottom, right, top = bounds
    width = max(1.0, right - left)
    height = max(1.0, top - bottom)

    def pixel(point):
        extent = size - 1
        return ((float(point[0]) - left) / width * extent,
                HEADER + (top - float(point[1])) / height * extent)

    routes_by_team = graph.get("routes") or {}
    route_ids = []
    for team in ("1", "2"):
        for route in routes_by_team.get(team, ()):
            route_id = str(route.get("id") or "route")
            if route_id not in route_ids:
                route_ids.append(route_id)
    slot_overrides = PALETTE_SLOT_OVERRIDES.get(str(graph.get('map')), {})
    colours = {
        route_id: PALETTE[slot_overrides.get(
            route_id, index) % len(PALETTE)]
        for index, route_id in enumerate(route_ids)
    }
    fallback_keys = set((graph.get("bake") or {}).get(
        "soft_route_fallbacks") or ())
    fallback_routes = set(key.split(":", 1)[-1] for key in fallback_keys)

    for index, route_id in enumerate(route_ids):
        colour = colours[route_id]
        column = index // 2
        row = index % 2
        legend_x = 540 + column * 260
        y = 14 + row * 33
        draw.line(((legend_x, y + 7), (legend_x + 34, y + 7)),
                  fill=colour, width=7)
        suffix = " *" if route_id in fallback_routes else ""
        draw.text((legend_x + 43, y - 3),
                  "%d  %s%s" % (index + 1, route_id, suffix),
                  font=small_font, fill=(240, 240, 240))

    # Draw team 1 first and the lighter team 2 dashes on top so coincident
    # reverse paths remain visibly distinct.
    for team in ("1", "2"):
        for route in routes_by_team.get(team, ()):
            route_id = str(route.get("id") or "route")
            colour = colours[route_id]
            if team == "2":
                colour = _lighten(colour)
            points = [pixel(point) for point in route.get("waypoints", ())]
            _line(draw, points, colour, 7 if team == "1" else 4,
                  dashed=(team == "2"))
            for fraction in (0.34, 0.68):
                _arrow(draw, points, colour, fraction, outlined=(team == "2"))
            raw_points = route.get("waypoints", ())
            for raw, point in zip(raw_points, points):
                if len(raw) < 3 or not bool(raw[2]):
                    continue
                radius = 8
                diamond = (
                    (point[0], point[1] - radius),
                    (point[0] + radius, point[1]),
                    (point[0], point[1] + radius),
                    (point[0] - radius, point[1]),
                )
                draw.polygon(diamond, fill=(10, 10, 10))
                inset = tuple((point[0] + (value[0] - point[0]) * 0.68,
                               point[1] + (value[1] - point[1]) * 0.68)
                              for value in diamond)
                if team == "2":
                    draw.line(inset + (inset[0],), fill=colour, width=2)
                else:
                    draw.polygon(inset, fill=colour)

    team_colours = {"1": (65, 215, 90), "2": (235, 70, 65)}
    formations = graph.get("spawn_formations") or {}
    for team in ("1", "2"):
        colour = team_colours[team]
        for formation in formations.get(team, ()):
            if len(formation) < 3:
                continue
            point = pixel((formation[0], formation[2]))
            radius = 4
            draw.ellipse((point[0] - radius - 1, point[1] - radius - 1,
                          point[0] + radius + 1, point[1] + radius + 1),
                         fill=(10, 10, 10))
            draw.ellipse((point[0] - radius, point[1] - radius,
                          point[0] + radius, point[1] + radius), fill=colour)

    # #1513 publishes two separate contracts.  Route endpoints and extraction
    # use spawn_anchors; capture flags must use objective_bases.  In particular,
    # Tundra and Lost City prove that a spawn anchor is not a base position.
    spawn_anchors = _point_pair(graph, "spawn_anchors")
    objective_bases = _point_pair(graph, "objective_bases")
    for index, team in enumerate(("1", "2")):
        spawn_point = pixel(spawn_anchors[index])
        base_point = pixel(objective_bases[index])
        if math.hypot(base_point[0] - spawn_point[0],
                      base_point[1] - spawn_point[1]) <= 40.0:
            _spawn_marker(draw, spawn_point, team_colours[team],
                          "SPAWN+BASE %s" % team, small_font, size)
            continue
        _spawn_marker(draw, spawn_point, team_colours[team],
                      "SPAWN %s" % team, small_font, size)
        _base_marker(draw, base_point, team_colours[team],
                     "BASE %s" % team, small_font, size)
    return image


def _write_index(output: Path, rendered, game_version):
    cards = []
    for map_name, filename in rendered:
        escaped = html.escape(filename)
        cards.append(
            '<article><a href="{0}"><img src="{0}" alt="{1}"></a>'
            '<h2>{1}</h2></article>'.format(escaped, html.escape(map_name)))
    document = """<!doctype html>
<html lang="en"><meta charset="utf-8"><title>WoT %s tactical route review</title>
<style>
body{margin:24px;background:#17191c;color:#eee;font:15px Arial,sans-serif}
h1{margin:0 0 8px}.note{color:#b9bec8;margin:0 0 24px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:20px}
article{background:#24272c;padding:10px;border-radius:8px}img{width:100%%;height:auto;display:block}
h2{font-size:17px;margin:9px 3px 2px}
</style><body><h1>World of Tanks %s tactical route review</h1>
<p class="note">Current reviewed routes only. Ordinary through-routes use the same geometry in reverse: team 1 is solid and team 2 is dashed. Circles are spawn anchors, flags are objective bases, and * marks a soft fallback. Click a map for the full-resolution PNG.</p>
<main class="grid">%s</main></body></html>""" % (
        html.escape(game_version), html.escape(game_version), "\n".join(cards))
    (output / "index.html").write_text(document, encoding="utf-8")
    (output / "README.txt").write_text(
        "World of Tanks %s tactical route review\n\n" % game_version +
        "This folder renders the current reviewed route candidate.\n"
        "There are 41 maps: 39 have three routes per team; 06_ensk has two reviewed routes; 04_himmelsdorf has an additional rear_guard route.\n\n"
        "Team 1: solid lines and filled arrows.\n"
        "Team 2: dashed lines and outlined arrows.\n"
        "Ordinary through-routes: one validated geometry, travelled in strict reverse.\n"
        "Circles and small dots: route spawn anchors and 15-slot formations.\n"
        "Flags: actual standard-battle objective bases.\n"
        "Diamonds: tactical hold waypoints.\n"
        "An asterisk in the legend marks a route with a soft fallback on at least one team side.\n"
        "Green: team 1. Red: team 2.\n\n"
        "Annotate any incorrect lane with opaque red ink, preserve the image dimensions and map filename, and return only changed PNGs.\n"
        "Local terminal routes such as Himmelsdorf rear_guard are reviewed manually from the marked image.\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-root", type=Path, required=True,
                        help="World of Tanks client directory")
    parser.add_argument("--navgraph-dir", type=Path,
                        default=DEFAULT_NAVGRAPH_DIR)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    if args.clean and args.output_dir.exists():
        shutil.rmtree(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    packages = args.client_root / "res" / "packages"
    rendered = []
    game_versions = set()
    requested_size = int(args.size)
    if requested_size < 640 or requested_size % ANNOTATION_SCALE:
        parser.error("--size must be at least 640 and divisible by 4")
    graphs = sorted((path for path in args.navgraph_dir.glob("*.json")
                     if path.name != "manifest.json"),
                    key=_map_sort_key)
    for graph_path in graphs:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        game_versions.add(str(graph.get("game_version") or "unknown"))
        map_name = str(graph.get("map") or graph_path.stem)
        package = _find_package(packages, map_name)
        minimap = _load_minimap(package)
        image = _render(graph, minimap, requested_size)
        filename = map_name + ".png"
        image.save(args.output_dir / filename, optimize=True)
        rendered.append((map_name, filename))
        print("rendered %s" % map_name)
    game_version = ", ".join(sorted(game_versions)) if game_versions else "unknown"
    _write_index(args.output_dir, rendered, game_version)
    print("wrote %d maps to %s" % (len(rendered), args.output_dir))


if __name__ == "__main__":
    main()
