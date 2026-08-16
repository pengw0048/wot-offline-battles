"""Present this client's engine calls in the shape the copied law expects.

The 0.9.22 law reads a collision as a sequence: point first, normal
second. 2.3.1.2 returns an object with `closestPoint` and `normal`. The
copied modules take `bigworld` and `math_module` as arguments and swap
them into `sys.modules` for the call, so the whole difference lives
here instead of inside the law.
"""
from __future__ import absolute_import


class _Collision(tuple):
    """A 0.9.22-shaped collision that still answers by name."""

    __slots__ = ()

    def __new__(cls, result):
        return tuple.__new__(cls, (result.closestPoint,
                                   getattr(result, 'normal', None),
                                   getattr(result, 'matKind', 0)))

    @property
    def closestPoint(self):
        return self[0]

    @property
    def normal(self):
        return self[1]

    @property
    def matKind(self):
        return self[2]


class _BigWorld(object):
    """Every call goes to the client, except the one whose shape differs.

    The real module is held, not imported on demand: the copied law
    swaps this object into sys.modules under the name BigWorld for the
    duration of the call.
    """

    __slots__ = ('_module',)

    def __init__(self, module):
        self._module = module

    def __getattr__(self, name):
        return getattr(self._module, name)

    def wg_collideSegment(self, space_id, start, end, *arguments):
        result = self._module.wg_collideSegment(space_id, start, end,
                                                *arguments)
        return None if result is None else _Collision(result)


def wrap(module):
    """A shim over the client's BigWorld, for one copied-law call."""
    return _BigWorld(module)
