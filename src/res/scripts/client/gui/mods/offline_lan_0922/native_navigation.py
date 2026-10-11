"""Main-thread ownership of pure C++ navigation jobs and engine queries."""
from __future__ import print_function

from collections import deque
import sys
import time

from gui.mods.offline_lan_0922 import native_math
from gui.mods.offline_lan_0922.worker_diagnostics import count as combat_count


class NativeSearch(object):
    """A search receipt; polling never performs an A* expansion in Python."""

    def __init__(self, job_id, hull_revision, now):
        self.job_id = job_id
        self.hull_revision = hull_revision
        self.submitted_at = float(now)
        self.done = False
        self.result = None
        self.last_frame = None
        self.status = 'pending'

    def step(self, unused_budget):
        return self.done


def _edge_rows(values):
    return tuple((edge[0][0], edge[0][1], edge[1][0], edge[1][1],
                  float(penalty)) for edge, penalty in (values or {}).items())


class NativeNavigation(object):
    """Own one map context without passing Python objects to worker threads."""

    def __init__(self, grid, backend):
        self.grid = grid
        self.backend = backend
        self.context = backend.nav_open((
            grid._baked_origin[0], grid._baked_origin[1], grid.cell_size,
            grid._baked_width, grid._baked_height,
            tuple(grid._baked_heights), tuple(grid._baked_links),
            tuple(grid._baked_hazards), grid.bounds,
            grid._baked_max_grade, grid.heuristic_weight))
        if self.context is None:
            raise RuntimeError('native navigation map could not be opened')
        self.next_job_id = 0
        self.jobs = {}
        self.queries = {}
        self.query_order = deque()
        self.closed = False
        self.stats = ()
        self.total_submitted = 0
        self.total_completed = 0
        self.total_cancelled = 0
        self.total_queries = 0
        self.total_expansions = 0
        self.total_worker_seconds = 0.0
        self.total_main_seconds = 0.0
        self.max_completion_age = 0.0
        sys.stdout.write('[Offline LAN 0.9.22] native asynchronous navigation active\n')

    @classmethod
    def create(cls, grid):
        if not grid.prebaked:
            return None
        backend = native_math._load()
        if backend is None or not callable(getattr(backend, 'nav_open', None)):
            return None
        try:
            return cls(grid, backend)
        except Exception as error:
            native_math._report_failure(error)
            return None

    def submit(self, start, goal, now, max_expansions, prefer_clearance,
               edge_penalties=None, hard_edge_penalties=None,
               avoid_points=None):
        if self.closed:
            raise RuntimeError('native navigation context is closed')
        self.next_job_id += 1
        job = NativeSearch(self.next_job_id, self.grid.static_hull_revision, now)
        # Expiring failures belong to this search's simulation-time snapshot.
        # Permanent wrecks and current native-review cells have the same owner.
        world = dict(self.grid._static_hull_edges)
        for edge, value in tuple(self.grid._failed_edges.items()):
            if float(now) < value[0]:
                world[edge] = max(world.get(edge, 0.0), float(value[1]))
        hard = tuple((edge[0][0], edge[0][1], edge[1][0], edge[1][1])
                     for edge in (hard_edge_penalties or ()))
        accepted = self.backend.nav_submit(
            self.context, job.job_id, tuple(start), tuple(goal),
            int(max_expansions), bool(prefer_clearance),
            tuple(tuple(point) for point in (avoid_points or ())),
            _edge_rows(edge_penalties), hard, _edge_rows(world),
            tuple(self.grid._native_review_cells), job.hull_revision)
        if not accepted:
            raise RuntimeError('native navigation job was not accepted')
        self.jobs[job.job_id] = job
        self.total_submitted += 1
        combat_count('nav_async_submitted')
        return job

    def cancel(self, jobs):
        identifiers = []
        for job in jobs:
            if not isinstance(job, NativeSearch):
                continue
            if self.jobs.pop(job.job_id, None) is not None:
                identifiers.append(job.job_id)
                self.queries.pop(job.job_id, None)
                job.done = True
                job.result = ()
                job.status = 'cancelled'
        if identifiers and not self.closed:
            self.backend.nav_cancel(self.context, tuple(identifiers))
            self.total_cancelled += len(identifiers)
            combat_count('nav_async_cancelled', len(identifiers))

    def _poll(self, now):
        completed, queries, self.stats = self.backend.nav_poll(self.context)
        for row in completed:
            job_id, status, path, hull_revision, expanded, worker_seconds = row
            job = self.jobs.pop(job_id, None)
            self.queries.pop(job_id, None)
            if job is None:
                continue
            job.done = True
            job.status = status
            job.result = tuple(tuple(point) for point in path) if status == 'done' else ()
            job.hull_revision = hull_revision
            job.last_frame = float(now)
            self.total_completed += 1
            self.total_expansions += int(expanded)
            self.total_worker_seconds += float(worker_seconds)
            self.max_completion_age = max(
                self.max_completion_age, max(0.0, float(now) - job.submitted_at))
            combat_count('nav_async_completed')
            combat_count('nav_astar_expansions', int(expanded))
        for query_id, job_id, start, end in queries:
            if job_id not in self.jobs:
                continue
            queue = self.queries.get(job_id)
            if queue is None:
                queue = self.queries[job_id] = deque()
                self.query_order.append(job_id)
            queue.append((query_id, start, end))

    def advance(self, now, query_budget):
        """Drain results and service a fair bounded engine-query frontier."""
        if self.closed:
            return
        started = time.time()
        try:
            self._poll(now)
            answers = []
            deferred = []
            budget = max(0, int(query_budget))
            while budget and self.query_order:
                job_id = self.query_order.popleft()
                queue = self.queries.get(job_id)
                if not queue or job_id not in self.jobs:
                    self.queries.pop(job_id, None)
                    continue
                query_id, start, end = queue.popleft()
                # This call owns BigWorld probes and its exact live cache.
                # C++ workers only consume the copied Boolean answer.
                try:
                    clear = self.grid._native_segment_clear(start, end)
                except Exception:
                    # A failed engine query rejects this edge and still
                    # settles its receipt, so the worker cannot wait forever.
                    clear = False
                    combat_count('nav_async_query_failed')
                pending = self.grid._native_review_pending
                if (not clear and self.grid.native_query_oracle is not None and
                        pending is not None and
                        (tuple(start), tuple(end)) in pending):
                    # A missing column is not a proved wall. Keep the receipt
                    # owned by this job and retry next callback, never again
                    # in this callback's round-robin service loop.
                    deferred.append((job_id, query_id, start, end))
                    combat_count('nav_async_query_unknown')
                elif job_id in self.jobs:
                    answers.append((query_id, bool(clear)))
                budget -= 1
                if queue:
                    self.query_order.append(job_id)
                else:
                    self.queries.pop(job_id, None)
            for job_id, query_id, start, end in deferred:
                if job_id not in self.jobs:
                    continue
                queue = self.queries.get(job_id)
                if queue is None:
                    queue = self.queries[job_id] = deque()
                    self.query_order.append(job_id)
                queue.append((query_id, start, end))
            if answers:
                self.backend.nav_answer(self.context, tuple(answers))
                self.total_queries += len(answers)
                combat_count('nav_async_queries', len(answers))
            # This is a single nonblocking drain, never a wait for the worker.
            self._poll(now)
        finally:
            self.total_main_seconds += max(0.0, time.time() - started)

    def close(self):
        if self.closed:
            return
        self.closed = True
        for job in self.jobs.values():
            job.done = True
            job.result = ()
            job.status = 'cancelled'
        self.jobs.clear()
        self.queries.clear()
        self.query_order.clear()
        self.backend.nav_close(self.context)

    def snapshot(self):
        return {'submitted': self.total_submitted,
                'completed': self.total_completed,
                'cancelled': self.total_cancelled,
                'pending': len(self.jobs),
                'queries': self.total_queries,
                'expansions': self.total_expansions,
                'worker_seconds': self.total_worker_seconds,
                'main_seconds': self.total_main_seconds,
                'max_completion_age': self.max_completion_age,
                'worker_stats': self.stats}
