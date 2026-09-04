# -*- coding: utf-8 -*-
"""Bounded, engine-independent scheduler for artillery world-arc probes.

The #1513 BigWorld BSP query must stay on the game thread.  This queue spreads
the sampled chords of each candidate trajectory across rendered frames while
preserving a strict native-ray budget.  A candidate is published only after
every required chord is clear (or its terminal chord reaches the target).
"""

import math


def _coords(value):
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except Exception:
        return (float(value.x), float(value.y), float(value.z))


def _distance(first, second):
    first = _coords(first)
    second = _coords(second)
    delta_x = first[0] - second[0]
    delta_y = first[1] - second[1]
    delta_z = first[2] - second[2]
    return math.sqrt(
        delta_x * delta_x + delta_y * delta_y + delta_z * delta_z)


class ArcProbeQueue(object):
    """Resolve caller-ordered trajectory candidates under a ray quota."""

    def __init__(self, max_jobs=8, success_ttl=2.5, failure_ttl=0.75,
                 max_job_age=4.0, target_slop=7.0, max_waiting=64):
        self.max_jobs = max(1, int(max_jobs))
        self.success_ttl = max(0.05, float(success_ttl))
        self.failure_ttl = max(0.05, float(failure_ttl))
        self.max_job_age = max(0.25, float(max_job_age))
        self.target_slop = max(0.0, float(target_slop))
        self.max_waiting = max(self.max_jobs, int(max_waiting))
        self.jobs = {}
        self.order = []
        self.waiting = {}
        self.waiting_order = []
        self.results = {}

    def reset(self):
        """Drop all pending work and positive/negative cached results."""
        self.jobs = {}
        self.order = []
        self.waiting = {}
        self.waiting_order = []
        self.results = {}

    def _cache_result(self, key, now, solution):
        ttl = self.success_ttl if solution is not None else self.failure_ttl
        self.results[key] = (float(now) + ttl, solution)

    def _discard_job(self, key):
        self.jobs.pop(key, None)
        try:
            self.order.remove(key)
        except ValueError:
            pass

    def _complete(self, key, now, solution):
        self._cache_result(key, now, solution)
        self._discard_job(key)

    def _discard_waiting(self, key):
        self.waiting.pop(key, None)
        try:
            self.waiting_order.remove(key)
        except ValueError:
            pass

    def _promote_waiting(self, now):
        while len(self.jobs) < self.max_jobs and self.waiting_order:
            key = self.waiting_order.pop(0)
            job = self.waiting.pop(key, None)
            if job is None:
                continue
            if float(now) - float(job['created']) > self.max_job_age:
                self._cache_result(key, now, None)
                continue
            self.jobs[key] = job
            self.order.append(key)

    def _purge(self, now):
        now = float(now)
        for key, value in list(self.results.items()):
            if float(value[0]) <= now:
                self.results.pop(key, None)
        for key in list(self.order):
            job = self.jobs.get(key)
            if job is None:
                self._discard_job(key)
            elif now - float(job['created']) > self.max_job_age:
                # Expired/incomplete work is a short negative result.  It must
                # never become an unchecked clear lane.
                self._complete(key, now, None)
        for key in list(self.waiting_order):
            job = self.waiting.get(key)
            if job is None:
                self._discard_waiting(key)
            elif now - float(job['created']) > self.max_job_age:
                self._discard_waiting(key)
                self._cache_result(key, now, None)

    def result(self, key, now):
        """Return ``(ready, solution)``; ready ``None`` is cached failure."""
        self._purge(now)
        value = self.results.get(key)
        if value is None:
            return False, None
        return True, value[1]

    def is_pending(self, key, now):
        self._purge(now)
        return key in self.jobs or key in self.waiting

    def request(self, key, candidates, target_position, now):
        """Queue one caller-ordered candidate set without evicting work.

        Callers should include every input that changes trajectory validity
        (vehicle/target identity, shell and quantised poses) in ``key``.  A new
        key cannot reuse a cached result from an earlier pose.
        """
        ready, solution = self.result(key, now)
        if ready:
            return ready, solution
        if key in self.jobs or key in self.waiting:
            return False, None
        usable = []
        for candidate in candidates or ():
            path = candidate.get('path') if isinstance(candidate, dict) else None
            try:
                has_chord = path is not None and len(path) >= 2
            except Exception:
                has_chord = False
            if has_chord:
                usable.append(candidate)
        if not usable:
            self._cache_result(key, now, None)
            return True, None
        job = {
            'created': float(now),
            'target': _coords(target_position),
            'candidates': usable,
            'candidate': 0,
            'chord': 0,
        }
        if len(self.jobs) >= self.max_jobs:
            # Keep a bounded FIFO so fixed bot iteration order cannot starve
            # later artillery pieces while the active native-ray set is full.
            if len(self.waiting_order) < self.max_waiting:
                self.waiting[key] = job
                self.waiting_order.append(key)
            return False, None
        self.jobs[key] = job
        self.order.append(key)
        return False, None

    def _blocked_candidate(self, key, job, now):
        candidate_index = int(job['candidate']) + 1
        if candidate_index >= len(job['candidates']):
            self._complete(key, now, None)
            return
        job['candidate'] = candidate_index
        job['chord'] = 0
        self.order.append(key)

    def advance(self, now, ray_budget, probe):
        """Probe at most ``ray_budget`` actual chords in fair rotation.

        ``probe(first, second)`` returns ``None`` for a clear chord or a world
        hit position.  A terminal hit within ``target_slop`` is a valid arrival.
        Probe exceptions and malformed hits fail closed for that candidate.
        The return value is the exact number of probe calls attempted.
        """
        self._purge(now)
        self._promote_waiting(now)
        budget = max(0, int(ray_budget))
        used = 0
        while used < budget and self.order:
            key = self.order.pop(0)
            job = self.jobs.get(key)
            if job is None:
                continue
            candidate_index = int(job['candidate'])
            if candidate_index >= len(job['candidates']):
                self._complete(key, now, None)
                continue
            solution = job['candidates'][candidate_index]
            path = solution['path']
            chord = int(job['chord'])
            if chord >= len(path) - 1:
                self._complete(key, now, solution)
                continue

            try:
                hit = probe(path[chord], path[chord + 1])
            except Exception:
                hit = False
            used += 1

            if hit is None:
                chord += 1
                job['chord'] = chord
                if chord >= len(path) - 1:
                    # Publish atomically only after the final actual chord has
                    # been checked during this or an earlier frame.
                    self._complete(key, now, solution)
                else:
                    self.order.append(key)
                continue

            try:
                reaches_target = (
                    _distance(hit, job['target']) <= self.target_slop)
            except Exception:
                reaches_target = False
            if reaches_target and chord == len(path) - 2:
                self._complete(key, now, solution)
            else:
                self._blocked_candidate(key, job, now)
        return used

    def diagnostics(self):
        return {
            'pending': len(self.order) + len(self.waiting_order),
            'waiting': len(self.waiting_order),
            'results': len(self.results),
        }
