#include "world_stage.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <stdexcept>
#include <vector>
namespace {
const double EPS = .001, CHANGE = .15, UP = 1.28, DOWN = 1.75;
struct V {
  double x, y, z;
  V(double a = 0, double b = 0, double c = 0) : x(a), y(b), z(c) {}
  double &operator[](int i) { return i == 0 ? x : i == 1 ? y : z; }
  double operator[](int i) const { return i == 0 ? x : i == 1 ? y : z; }
};
V operator+(V a, V b) { return V(a.x + b.x, a.y + b.y, a.z + b.z); }
V operator-(V a, V b) { return V(a.x - b.x, a.y - b.y, a.z - b.z); }
V operator*(V a, double b) { return V(a.x * b, a.y * b, a.z * b); }
double length(V a) { return std::sqrt(a.x * a.x + a.y * a.y + a.z * a.z); }
double dot(V a, V b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
V vec(const double *p) { return V(p[0], p[1], p[2]); }
void put(double *p, V a) {
  p[0] = a.x;
  p[1] = a.y;
  p[2] = a.z;
}
struct Aborted {};
struct Plane {
  double x, y, z, gx, gz;
};
struct Lane {
  double x1, z1, x2, z2, target, px, pz, ps, pc, pd, look;
  bool perimeter;
};
struct Ray {
  V start, end;
  WorldResponse hit;
  double distance;
};
struct Engine {
  const WorldSnapshot &s;
  WorldDispatch callback;
  void *ctx;
  V pos, py, axes[3];
  double cy, sy, left, right, back, front, hw, ahead, tx, tz, ty;
  Plane plane;
  bool kinetic;
  Engine(const WorldSnapshot &p, WorldDispatch cb, void *c)
      : s(p), callback(cb), ctx(c), pos(vec(p.position)), kinetic(false) {}
  void call(unsigned op, WorldRequest *q, unsigned n, WorldResponse *r) {
    std::memset(r, 0, sizeof(*r) * n);
    if (!callback(ctx, op, q, n, r))
      throw Aborted();
  }
  WorldRequest request(V a, V b, unsigned flags = 0) {
    WorldRequest q = {};
    put(q.start, a);
    put(q.end, b);
    q.flags = flags;
    return q;
  }
  bool drivable(const WorldResponse &h, double g = UP) {
    V n = vec(h.normal);
    double len = length(n);
    return len > 0 && n.y / len >= 1 / std::sqrt(1 + g * g);
  }
  bool drivableNormal(V n, double g) {
    WorldResponse h = {};
    put(h.normal, n);
    return drivable(h, g);
  }
  double support(double y) {
    return y + std::abs(py.x) * hw + std::max(-back * py.z, front * py.z);
  }
  WorldRequest groundReq(V p, double x, double z, double look,
                         const Plane *pl) {
    double down = std::max(5., look * DOWN + 1), y = p.y + 12;
    if (pl)
      y = std::min(y, pl->y + (x - pl->x) * pl->gx + (z - pl->z) * pl->gz);
    return request(V(x, y, z), V(x, p.y - down, z), y <= p.y - down ? 4 : 0);
  }
  bool ground(V p, double x, double z, double look, const Plane *pl,
              double &out) {
    WorldRequest q = groundReq(p, x, z, look, pl);
    if (q.flags & 4)
      return false;
    WorldResponse r;
    call(WORLD_GROUND, &q, 1, &r);
    if (!r.status)
      return false;
    out = r.point[1];
    return true;
  }
  Ray horizontal(V a, V b, bool departing = true) {
    WorldRequest q = request(a, b, departing ? 1 : 0);
    WorldResponse r;
    call(WORLD_HORIZONTAL, &q, 1, &r);
    Ray v;
    v.start = vec(r.start);
    v.end = vec(r.end);
    v.hit = r;
    v.distance = r.status ? length(vec(r.point) - v.start) : 0;
    return v;
  }
  void trace(unsigned reason, const Ray &r, bool ga, double aheadGround,
             const std::vector<double> &heights = std::vector<double>()) {
    WorldRequest q = request(r.start, r.end, ga ? 2 : 0);
    q.handle = r.hit.handle;
    q.reason = reason;
    q.auxiliary[0] = aheadGround;
    q.auxiliary[1] = heights.size();
    for (unsigned i = 0; i < heights.size(); ++i)
      q.auxiliary[i + 2] = heights[i];
    WorldResponse out;
    call(WORLD_TRACE, &q, 1, &out);
  }
  bool profileOK(const std::vector<double> &h, double seg, bool flat = false,
                 bool reverse = false) {
    if (h.size() < 2 || (!flat && std::abs(h.back() - h.front()) <= CHANGE))
      return false;
    seg = std::max(.001, seg);
    for (unsigned i = 1; i < h.size(); ++i) {
      double d =
          reverse ? h[h.size() - 1 - i] - h[h.size() - i] : h[i] - h[i - 1];
      if (std::abs(d) > seg * (d < 0 ? DOWN : UP))
        return false;
    }
    return true;
  }
  bool laneProfileOK(const Lane &l, const std::vector<double> &h, double seg,
                     bool flat = false) {
    return profileOK(h, seg, flat) ||
           (l.perimeter && profileOK(h, seg, flat, true));
  }
  bool matches(const WorldResponse &r, const std::vector<double> &h, double seg,
               const Lane &l) {
    if (h.size() < 2 || seg <= 0)
      return false;
    double d = l.pd * ((r.point[0] - l.px) * l.ps + (r.point[2] - l.pz) * l.pc);
    if (d < 0 || d > seg * (h.size() - 1))
      return false;
    unsigned i = std::min<unsigned>(h.size() - 2, unsigned(d / seg));
    double f = (d - i * seg) / seg;
    return std::abs(r.point[1] - (h[i] + (h[i + 1] - h[i]) * f)) <= CHANGE;
  }
  bool exact(V p, const WorldResponse &r, double look, const Plane &pl) {
    double top;
    return ground(p, r.point[0], r.point[2], look, &pl, top) &&
           std::abs(top - r.point[1]) <= EPS;
  }
  bool exitClear(V p, const Ray &r, double look, const Plane &pl) {
    if (!drivable(r.hit, DOWN))
      return false;
    V d = r.end - r.start;
    double len = length(d);
    if (len <= EPS || dot(d, vec(r.hit.normal)) / len <= EPS ||
        !exact(p, r.hit, look, pl))
      return false;
    V rem = r.end - vec(r.hit.point);
    len = length(rem);
    if (len <= EPS)
      return false;
    return !horizontal(vec(r.hit.point) + rem * (EPS / len), r.end, false)
                .hit.status;
  }
  bool flatClear(V p, const Ray &r, const std::vector<double> &h, double seg,
                 double look, const Plane &pl) {
    if (h.empty() || std::abs(h.back() - h.front()) > CHANGE ||
        !drivable(r.hit) || r.hit.point[1] > support(p.y) + EPS ||
        !profileOK(h, seg, true) || !exact(p, r.hit, look, pl))
      return false;
    V rem = r.end - vec(r.hit.point);
    double len = length(rem);
    return len <= EPS ||
           !horizontal(vec(r.hit.point) + rem * (EPS / len), r.end, false)
                .hit.status;
  }
  void profile(V p, const Lane &l, const Plane &pl, std::vector<double> &h,
               double &seg) {
    seg = l.look / 6.;
    WorldRequest q[7];
    WorldResponse r[7];
    for (unsigned i = 0; i < 7; ++i) {
      double d = seg * i;
      q[i] = groundReq(p, l.px + l.ps * d * l.pd, l.pz + l.pc * d * l.pd,
                       l.look, &pl);
      q[i].flags |= 1;
    }
    call(WORLD_GROUND, q, 7, r);
    h.clear();
    for (unsigned i = 0; i < 7; ++i) {
      if (!r[i].status) {
        h.clear();
        return;
      }
      h.push_back(r[i].point[1]);
    }
  }
  void posed(const Lane &l, V lp, const std::array<double, 2> &ls,
             const std::array<double, 2> &le, double height, bool ga,
             double gaY, V &a, V &b) {
    a = V(l.x1, lp.y + ls[0] * py.x + height * py.y + ls[1] * py.z, l.z1);
    b = V(l.x2, lp.y + le[0] * py.x + height * py.y + le[1] * py.z, l.z2);
    if (s.passive) {
      for (int i = 0; i < 2; ++i) {
        const std::array<double, 2> &v = i ? le : ls;
        double dx = v[0] * (axes[0].x - cy) + height * axes[1].x +
                    v[1] * (axes[2].x - sy);
        double dz = v[0] * (axes[0].z + sy) + height * axes[1].z +
                    v[1] * (axes[2].z - cy);
        if (i) {
          b.x += dx;
          b.z += dz;
        } else {
          a.x += dx;
          a.z += dz;
        }
      }
      b.y += l.perimeter ? 0 : ty;
    }
    if (ga)
      b.y = std::min(b.y, gaY + height);
  }
  std::vector<Ray> uppers(const Lane &l, V lp, const std::array<double, 2> &ls,
                          const std::array<double, 2> &le, bool ga,
                          double gaY) {
    WorldRequest q[2];
    WorldResponse r[2];
    for (int i = 0; i < 2; ++i) {
      V a, b;
      posed(l, lp, ls, le, i ? 1.6 : 1.1, ga, gaY, a, b);
      q[i] = request(a, b, 1);
    }
    call(WORLD_HORIZONTAL, q, 2, r);
    std::vector<Ray> hits;
    for (int i = 0; i < 2; ++i) {
      Ray v;
      v.start = vec(r[i].start);
      v.end = vec(r[i].end);
      v.hit = r[i];
      v.distance = r[i].status ? length(vec(r[i].point) - v.start) : 0;
      hits.push_back(v);
    }
    return hits;
  }
  bool raised(const Lane &l, V lp, const std::array<double, 2> &ls,
              const std::array<double, 2> &le, double target, double limit,
              const std::vector<double> &h, double seg, const Plane &pl,
              bool ga, double gaY, bool requireExit) {
    for (int i = 0; i < 2; ++i) {
      V a, b;
      posed(l, lp, ls, le, i ? 1.6 : 1.1, ga, gaY, a, b);
      Ray r = horizontal(a, b);
      if (!r.hit.status || r.distance >= target)
        continue;
      if (drivable(r.hit, limit) &&
          (!requireExit || exitClear(lp, r, l.look, pl)))
        continue;
      if (!requireExit && matches(r.hit, h, seg, l) &&
          exact(lp, r.hit, l.look, pl))
        continue;
      trace(1, r, ga, gaY);
      return true;
    }
    return false;
  }
  bool seam(const Lane &l, V p, const std::array<double, 2> &ls,
            const std::array<double, 2> &le, const WorldResponse &h) {
    V n = vec(h.normal), pt = vec(h.point);
    double norm = length(n);
    if (norm <= 1e-12 || n.y / norm < -.2 || drivable(h))
      return false;
    double len = std::hypot(n.x, n.z);
    if (len <= 1e-12)
      return false;
    double nx = n.x / len, nz = n.z / len, top = support(p.y);
    if (top < pt.y - EPS)
      return false;
    double tops[3], offsets[3] = {.12, -.12, -.60};
    for (int i = 0; i < 3; ++i) {
      double x = pt.x + nx * offsets[i], z = pt.z + nz * offsets[i];
      WorldRequest q = request(V(x, p.y + 1.6, z), V(x, p.y - 3, z));
      WorldResponse r;
      call(WORLD_GROUND, &q, 1, &r);
      if (!r.status || r.normal[1] <= 0 || (offsets[i] < 0 && !drivable(r, .5)))
        return false;
      tops[i] = r.point[1];
    }
    double rise = std::max(tops[1], tops[2]) - tops[0];
    if (!(rise > .03 && rise <= .75) || std::abs(tops[1] - tops[2]) > .12 ||
        std::min(tops[1], tops[2]) < pt.y - EPS ||
        std::max(tops[1], tops[2]) > top + .075)
      return false;
    for (int i = 0; i < 3; ++i) {
      V a, b;
      posed(l, p, ls, le, i == 0 ? .6 : (i == 1 ? 1.1 : 1.6), false, 0, a, b);
      a.y += rise;
      b.y += rise;
      if (horizontal(a, b, false).hit.status)
        return false;
    }
    return true;
  }
  bool groundAhead(const Lane &l, const std::array<double, 2> &ls,
                   const std::array<double, 2> &le, const Plane &pl,
                   double &out) {
    double x = pos.x + cy * le[0] + sy * le[1],
           z = pos.z - sy * le[0] + cy * le[1];
    double st = 0, ft = 0;
    bool hs = ground(pos, l.x1, l.z1, l.target, &pl, st),
         hf = ground(pos, x, z, l.target, &pl, ft);
    double supportY = pos.y + ls[0] * py.x + ls[1] * py.z;
    if (hs && st < supportY - EPS)
      return false;
    bool desc = (le[0] - ls[0]) * py.x + (le[1] - ls[1]) * py.z < -1e-9;
    if (hs && hf && (desc || ft < st)) {
      double mid;
      if (!ground(pos, (l.x1 + x) * .5, (l.z1 + z) * .5, l.target, &pl, mid) ||
          std::abs(mid - (st + ft) * .5) > EPS)
        return false;
    }
    double in = std::sqrt(std::pow(x - l.x1, 2) + std::pow(z - l.z1, 2)),
           full =
               std::sqrt(std::pow(l.x2 - l.x1, 2) + std::pow(l.z2 - l.z1, 2));
    if (hs && hf && in > 1e-6) {
      out = st + (ft - st) * full / in;
      return true;
    }
    if (hs || hf) {
      out = hs ? (hf ? std::min(st, ft) : st) : ft;
      return true;
    }
    return false;
  }
  void setup() {
    left = s.bounds[0];
    right = s.bounds[1];
    back = s.bounds[2];
    front = s.bounds[3];
    hw = std::max(std::abs(left), std::abs(right));
    ahead = s.exact_footprint ? 0 : std::abs(s.speed) * std::max(0., s.dt);
    cy = std::cos(s.yaw);
    sy = std::sin(s.yaw);
    double cp = std::cos(s.pitch), sp = std::sin(s.pitch),
           cr = std::cos(s.roll), sr = std::sin(s.roll);
    py = (s.pitch == 0 && s.roll == 0) ? V(0, 1, 0) : V(cp * sr, cp * cr, -sp);
    V local[3] = {V(cr, sr, 0), V(-sr, cr, 0), V(0, 0, 1)};
    for (int i = 0; i < 3; ++i) {
      double y = cp * local[i].y - sp * local[i].z,
             z = sp * local[i].y + cp * local[i].z;
      axes[i] = V(cy * local[i].x + sy * z, y, -sy * local[i].x + cy * z);
    }
    double gx = std::abs(axes[1].y) > .1 ? -axes[1].x / axes[1].y : 0,
           gz = std::abs(axes[1].y) > .1 ? -axes[1].z / axes[1].y : 0;
    double travelYaw =
        s.passive ? s.motion_yaw : s.yaw + (s.speed < 0 ? std::acos(-1.) : 0);
    tx = std::sin(travelYaw) * ahead;
    tz = std::cos(travelYaw) * ahead;
    bool gp = s.passive && !s.airborne && drivableNormal(axes[1], DOWN);
    ty = gp ? gx * tx + gz * tz : 0;
    if (gp && std::abs(ty) > EPS) {
      Plane pl = {pos.x, pos.y + .6, pos.z, gx, gz};
      WorldRequest q[3];
      WorldResponse r[3];
      for (int i = 0; i < 3; ++i)
        q[i] = groundReq(pos, pos.x + tx * (i * .5), pos.z + tz * (i * .5),
                         ahead, &pl);
      call(WORLD_GROUND, q, 3, r);
      if (!r[0].status || !r[1].status || !r[2].status ||
          std::abs(r[0].point[1] - pos.y) > .6 ||
          std::abs(r[2].point[1] - r[0].point[1] - ty) > .6 ||
          std::abs(r[1].point[1] - (r[0].point[1] + r[2].point[1]) * .5) > EPS)
        ty = 0;
      else
        ty = r[2].point[1] - r[0].point[1];
    }
    if (!s.supplied_departing && ahead > 0) {
      WorldRequest q = {};
      q.auxiliary[0] = tx;
      q.auxiliary[1] = tz;
      q.auxiliary[2] = ty;
      put(q.auxiliary + 3, py);
      for (int i = 0; i < 3; ++i)
        put(q.auxiliary + 6 + i * 3, axes[i]);
      WorldResponse r;
      call(WORLD_DEPARTING, &q, 1, &r);
    }
    plane = {pos.x, pos.y + 1.6 * py.y, pos.z,
             s.passive ? gx : cy * py.x + sy * py.z,
             s.passive ? gz : -sy * py.x + cy * py.z};
  }
  std::vector<Lane> lanes() {
    std::vector<Lane> v;
    if (!s.passive) {
      double bm = s.speed > 0 ? -.5 : .5,
             fm = s.speed > 0 ? front + ahead : -(back + ahead),
             d = s.speed >= 0 ? 1 : -1,
             look = (s.speed > 0 ? front : back) + ahead,
             target = std::abs(bm) + look;
      double offsets[3] = {left, 0, right};
      for (int i = 0; i < 3; ++i) {
        double x = pos.x + cy * offsets[i], z = pos.z - sy * offsets[i];
        v.push_back({x + sy * bm, z + cy * bm, x + sy * fm, z + cy * fm, target,
                     x, z, sy, cy, d, look, false});
      }
    } else {
      double ms = std::sin(s.motion_yaw), mc = std::cos(s.motion_yaw), px = mc,
             pz = -ms, ru = ms * cy - mc * sy, fu = ms * sy + mc * cy,
             rv = px * cy - pz * sy, fv = px * sy + pz * cy;
      double coords[4][2] = {
          {left, -back}, {right, -back}, {right, front}, {left, front}};
      std::vector<std::array<double, 3>> projected;
      for (int i = 0; i < 4; ++i) {
        double u = ru * coords[i][0] + fu * coords[i][1],
               w = rv * coords[i][0] + fv * coords[i][1];
        projected.push_back({{w, u, u + ahead}});
      }
      std::vector<double> limits;
      if (ru > 1e-9)
        limits.push_back(right / ru);
      else if (ru < -1e-9)
        limits.push_back(left / ru);
      if (fu > 1e-9)
        limits.push_back(front / fu);
      else if (fu < -1e-9)
        limits.push_back(-back / fu);
      double cf =
          limits.empty() ? 0 : *std::min_element(limits.begin(), limits.end());
      projected.push_back({{0, -.5, cf + ahead}});
      std::sort(projected.begin(), projected.end());
      std::vector<std::array<double, 3>> merged;
      for (auto p : projected) {
        if (!merged.empty() && std::abs(p[0] - merged.back()[0]) <= 1e-7) {
          merged.back()[1] = std::min(merged.back()[1], p[1]);
          merged.back()[2] = std::max(merged.back()[2], p[2]);
        } else
          merged.push_back(p);
      }
      for (auto m : merged) {
        double x1 = pos.x + px * m[0] + ms * m[1],
               z1 = pos.z + pz * m[0] + mc * m[1],
               x2 = pos.x + px * m[0] + ms * m[2],
               z2 = pos.z + pz * m[0] + mc * m[2], len = m[2] - m[1];
        v.push_back({x1, z1, x2, z2, len, x1, z1, ms, mc, 1, len, false});
      }
      if (ahead > 0) {
        double corners[4][2];
        for (int i = 0; i < 4; ++i) {
          corners[i][0] = pos.x + cy * coords[i][0] + sy * coords[i][1] + tx;
          corners[i][1] = pos.z - sy * coords[i][0] + cy * coords[i][1] + tz;
        }
        for (int i = 0; i < 4; ++i) {
          int j = (i + 1) % 4;
          double x1 = corners[i][0], z1 = corners[i][1], x2 = corners[j][0],
                 z2 = corners[j][1], len = std::hypot(x2 - x1, z2 - z1);
          if (len > 1e-9)
            v.push_back({x1, z1, x2, z2, len, x1, z1, (x2 - x1) / len,
                         (z2 - z1) / len, 1, len, true});
        }
      }
    }
    return v;
  }
  void filter(const std::vector<Lane> &v) {
    double bounds[4] = {v[0].x1, v[0].x1, v[0].z1, v[0].z1};
    for (auto l : v) {
      bounds[0] = std::min(bounds[0], std::min(l.x1, l.x2));
      bounds[1] = std::max(bounds[1], std::max(l.x1, l.x2));
      bounds[2] = std::min(bounds[2], std::min(l.z1, l.z2));
      bounds[3] = std::max(bounds[3], std::max(l.z1, l.z2));
    }
    for (int i = 0; i < 2; ++i) {
      int a = i ? 2 : 0;
      double r = axes[0][a], u = axes[1][a], f = axes[2][a],
             travel = i ? tz : tx, origin = pos[a];
      double mn = origin + r * (r < 0 ? right : left) + u * (u < 0 ? 1.6 : .6) +
                  f * (f < 0 ? front : -back) + (travel < 0 ? travel : 0);
      double mx = origin + r * (r < 0 ? left : right) + u * (u < 0 ? .6 : 1.6) +
                  f * (f < 0 ? -back : front) + (travel > 0 ? travel : 0);
      bounds[i * 2] = std::min(bounds[i * 2], mn);
      bounds[i * 2 + 1] = std::max(bounds[i * 2 + 1], mx);
    }
    WorldRequest q = request(V(bounds[0], pos.y + .6, bounds[2]),
                             V(bounds[1], pos.y + 1.6, bounds[3]));
    WorldResponse r;
    call(WORLD_FILTER, &q, 1, &r);
  }
  bool resolve(std::vector<Ray> &hits, double target, unsigned reason, bool ga,
               double gaY, const std::vector<double> &heights) {
    std::stable_sort(hits.begin(), hits.end(), [](const Ray &a, const Ray &b) {
      return a.distance < b.distance;
    });
    for (auto r : hits) {
      if (!r.hit.status || r.distance >= target)
        continue;
      WorldRequest q = request(r.start, r.end);
      q.handle = r.hit.handle;
      WorldResponse out;
      call(WORLD_RESOLVE, &q, 1, &out);
      if (out.status == 2)
        kinetic = true;
      else if (out.status != 1) {
        trace(reason, r, ga, gaY, heights);
        return false;
      }
    }
    return true;
  }
  int run() {
    setup();
    auto all = lanes();
    filter(all);
    for (Lane l : all) {
      double dx = l.x1 - pos.x, dz = l.z1 - pos.z, ex = l.x2 - pos.x,
             ez = l.z2 - pos.z;
      V lp = pos;
      Plane pl = plane;
      if (l.perimeter) {
        dx -= tx;
        dz -= tz;
        ex -= tx;
        ez -= tz;
        pl.x += tx;
        pl.y += ty;
        pl.z += tz;
        lp.y += ty;
      }
      std::array<double, 2> ls = {{dx * cy - dz * sy, dx * sy + dz * cy}},
                            re = {{ex * cy - ez * sy, ex * sy + ez * cy}}, le;
      double fraction = 1;
      for (int i = 0; i < 2; ++i) {
        double delta = re[i] - ls[i], lo = i ? -back : left,
               hi = i ? front : right;
        if (delta > 0)
          fraction = std::min(fraction, (hi - ls[i]) / delta);
        else if (delta < 0)
          fraction = std::min(fraction, (lo - ls[i]) / delta);
      }
      fraction = std::max(0., std::min(1., fraction));
      for (int i = 0; i < 2; ++i)
        le[i] = ls[i] + (re[i] - ls[i]) * fraction;
      bool clamped =
          (py.x != 0 || py.y != 1 || py.z != 0) &&
          (std::abs(le[0] - re[0]) > 1e-9 || std::abs(le[1] - re[1]) > 1e-9);
      double gaY = 0;
      bool ga = py.z && !s.passive && groundAhead(l, ls, le, pl, gaY);
      V a, b;
      posed(l, lp, ls, le, .6, ga, gaY, a, b);
      Ray lower = horizontal(a, b);
      double target = length(lower.end - lower.start);
      if (s.passive) {
        l.px = lower.start.x;
        l.pz = lower.start.z;
        l.look = std::hypot(lower.end.x - lower.start.x,
                            lower.end.z - lower.start.z);
        if (l.look > 1e-9) {
          l.ps = (lower.end.x - lower.start.x) / l.look;
          l.pc = (lower.end.z - lower.start.z) / l.look;
          l.pd = 1;
        }
      }
      if (lower.hit.status && lower.distance < target) {
        std::vector<double> h;
        double seg = 0, limit = DOWN;
        Plane pp = pl;
        bool surface = false;
        if (drivable(lower.hit, limit)) {
          pp = {lower.hit.point[0], lower.hit.point[1] + 1.6,
                lower.hit.point[2], -lower.hit.normal[0] / lower.hit.normal[1],
                -lower.hit.normal[2] / lower.hit.normal[1]};
        }
        if (drivable(lower.hit, limit) || clamped) {
          profile(lp, l, pp, h, seg);
          limit = l.perimeter
                      ? DOWN
                      : (!h.empty() && h.back() < h.front() ? DOWN : UP);
          if (l.perimeter && !h.empty() && laneProfileOK(l, h, seg, true) &&
              drivable(lower.hit, limit) && matches(lower.hit, h, seg, l) &&
              exact(lp, lower.hit, l.look, pp)) {
            if (raised(l, lp, ls, le, target, limit, h, seg, pp, ga, gaY,
                       false))
              return 1;
            continue;
          }
          if (!h.empty() && std::abs(h.back() - h.front()) > CHANGE &&
              !laneProfileOK(l, h, seg)) {
            bool monotonic = true;
            for (unsigned i = 1; i < h.size(); ++i)
              if (h[i] > h[i - 1])
                monotonic = false;
            if (monotonic && exitClear(lp, lower, l.look, pp)) {
              if (raised(l, lp, ls, le, target, limit, h, seg, pp, ga, gaY,
                         true))
                return 1;
              continue;
            }
            trace(2, lower, ga, gaY, h);
            return 1;
          }
          surface = drivable(lower.hit, limit);
        }
        if (!surface && clamped && matches(lower.hit, h, seg, l))
          surface = exact(lp, lower.hit, l.look, pp);
        bool flat = !s.airborne && flatClear(lp, lower, h, seg, l.look, pp);
        if ((!h.empty() && laneProfileOK(l, h, seg) && surface) || flat) {
          if (raised(l, lp, ls, le, target, limit, h, seg, pp, ga, gaY, false))
            return 1;
          continue;
        }
        if (!s.airborne && seam(l, lp, ls, le, lower.hit))
          continue;
        auto hits = uppers(l, lp, ls, le, ga, gaY);
        hits.insert(hits.begin(), lower);
        if (!resolve(hits, target, 3, ga, gaY, h))
          return 1;
      } else {
        auto hits = uppers(l, lp, ls, le, ga, gaY);
        if (!resolve(hits, target, 4, ga, gaY, std::vector<double>()))
          return 1;
      }
    }
    return kinetic ? 2 : 0;
  }
};
} // namespace
extern "C" int wot_world_run(const WorldSnapshot *s, WorldDispatch cb,
                             void *context) {
  try {
    return Engine(*s, cb, context).run();
  } catch (const Aborted &) {
    return -1;
  } catch (...) {
    return -2;
  }
}
