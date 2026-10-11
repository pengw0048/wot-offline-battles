"""Check real bridge diagnostic rows, reentry, failures and unchanged outputs.

Usage (CPython 2.7): check_native_simulation_diagnostics.py EXTENSION
This is a bounded host contract check, not a timing benchmark.
"""
from __future__ import print_function
import copy
import imp
import sys
import threading

sys.dont_write_bytecode = True
backend = imp.load_dynamic('offline_math_batch_native', sys.argv[1])
handle = backend.sim_open(11, 3)
snapshot = (0., 0., 0., 0., 5., .08, 0., 0., 0., -1.5, 1.5, 3.5,
            3.5, .6, 1.6, False, False, False, False)


def clear_dispatch(opcode, rows):
    if opcode in (1, 2, 6):
        return None
    return tuple((None, None, 0, row[0], row[1]) for row in rows)


def rows(capture):
    assert len(capture) == 7
    entries = dict(((r[0], r[1]), r) for r in capture[4])
    callbacks = dict(((r[0], r[1]), r) for r in capture[5])
    leaf = 0.
    for row in entries.values():
        assert len(row) == 11 and min(row[2:]) >= 0, row
        assert abs(row[4] - sum(row[5:10])) <= 1.e-7, row
        leaf += sum(row[5:8])
    for row in callbacks.values():
        assert len(row) == 8 and min(row[2:]) >= 0, row
        assert abs(row[5] - sum(row[6:8])) <= 1.e-7, row
        leaf += row[6]
    assert leaf <= capture[1] + 1.e-7, (leaf, capture[1])
    return entries, callbacks


assert backend.sim_diag_begin(999999, 1) is None
assert backend.sim_diag_end(handle) is None
assert backend.world_run(snapshot, clear_dispatch) == 0
assert backend.sim_diag_begin(handle, 1) == 1
assert backend.sim_diag_begin(handle, 2) is None
assert backend.sim_diag_end(handle + 1) is None
once = [False]


def nested_dispatch(opcode, requests):
    if not once[0]:
        once[0] = True
        assert backend.sim_lifetime(handle) == (11, 3)
        assert backend.world_run(snapshot, clear_dispatch) == 0
    return clear_dispatch(opcode, requests)


assert backend.world_run(snapshot, nested_dispatch) == 0
first = backend.sim_diag_end(handle)
entries, callbacks = rows(first)
assert first[0] == 1 and first[2] >= 4 and first[3] == 2, first[:4]
assert entries[('world_run', -1)][2] == 2
assert entries[('world_run', -1)][10] == 1
assert entries[('sim_lifetime', -1)][10] == 1
assert sum(row[7] for row in callbacks.values()) > 0
assert sum(row[3] for row in callbacks.values()) >= sum(row[2] for row in callbacks.values())
assert backend.sim_diag_end(handle) is None

# Reentrant cancellation cannot leave dangling scopes or poison a later frame.
assert backend.sim_diag_begin(handle, 2) == 1
cancelled = [False]


def cancelling_dispatch(opcode, requests):
    if not cancelled[0]:
        cancelled[0] = True
        assert backend.sim_diag_end(handle) is None
        assert backend.sim_diag_begin(handle, 3) == 1
    return clear_dispatch(opcode, requests)


assert backend.world_run(snapshot, cancelling_dispatch) == 0
entries, callbacks = rows(backend.sim_diag_end(handle))
assert not entries  # The interrupted outer scope was permanently detached.
assert callbacks  # Subsequent owner callbacks are valid observations.

# Original callback exceptions and native failure outcomes remain unchanged.
failure = KeyboardInterrupt('diagnostic callback identity')


def failing_dispatch(unused_opcode, unused_rows):
    raise failure


assert backend.sim_diag_begin(handle, 4) == 1
try:
    backend.world_run(snapshot, failing_dispatch)
except KeyboardInterrupt as caught:
    assert caught is failure
else:
    raise AssertionError('Callback exception was changed')
assert backend.world_run((float('nan'),) + snapshot[1:], clear_dispatch) is None
entries, callbacks = rows(backend.sim_diag_end(handle))
assert entries[('world_run', -1)][3] == 2
assert sum(row[4] for row in callbacks.values()) == 1

# Candidate components come from the exact solver roster, without changing it.
bodies = [dict(id=index + 1, x=x, y=0., z=0., yaw=0., mass=10000.,
               shape=(1.5, 3.5, -.8, 2.), alive=True)
          for index, x in enumerate((0., 1., 100.))]
original = copy.deepcopy(bodies)
plain = backend.contact_roster(bodies, (1, 2, 3), .1, ())
assert plain is not None and bodies == original
assert backend.sim_diag_begin(handle, 5) == 1
assert backend.contact_roster(bodies, (1, 2, 3), .1, ()) == plain
contacts = backend.sim_diag_end(handle)
entries, unused_callbacks = rows(contacts)
assert set(name for name, unused_op in entries) == set((
    'contact_roster', 'stage.contact_solve', 'stage.contact_pair_build',
    'stage.contact_solve_passes'))
counts = dict((r[0], r[1:]) for r in contacts[6])
for name, value in (('actors', 3), ('candidate_pairs', 1), ('isolated_actors', 1),
                    ('nontrivial_islands', 1), ('largest_island_actors', 2),
                    ('largest_island_pairs', 1)):
    assert counts['contact.' + name] == (1, value, value), counts
assert bodies == original

# Capture ownership is thread-local and may be serialized after owner close.
assert backend.sim_diag_begin(handle, 6) == 1
thread_result = []


def other_thread():
    thread_result.append(backend.sim_diag_end(handle))
    thread_result.append(backend.sim_lifetime(handle))


thread = threading.Thread(target=other_thread)
thread.start()
thread.join()
assert thread_result == [None, (11, 3)]
assert backend.sim_close(handle) == 1
entries, unused_callbacks = rows(backend.sim_diag_end(handle))
assert set(entries) == set((('sim_close', -1),))
assert backend.sim_diag_begin(handle, 7) is None
print('Native diagnostic bridge checks passed: partition, reentry, reset, '
      'callback exception, contact components, thread ownership and close')
