"""Direct observer radio sharing; independent of rendering and team totals."""
from gui.mods.offline_lan_0922 import spotting


class RadioNetwork(object):
    def __init__(self):
        self.actors = {}
        self.links = {}
        self.receipts = {}
        self.observations = {}
        self._targets = {}
        self._observer_ranks = {}
        self._groups = {}

    def configure(self, actors, now):
        self.actors = dict(actors)
        # The legacy Relaying skill boosts allies' radio range by 0.1% per
        # level. Use only unboosted direct links for eligibility: no cascade.
        for actor, row in actors.items():
            bonus = max([float(donor[3]) for owner, donor in actors.items()
                if owner != actor and len(donor) > 3 and donor[0] == row[0] and
                spotting.radio_link(row[1], row[2], donor[1], donor[2])] or [0.0])
            self.actors[actor] = row[:2] + (row[2] * (1.0 + bonus),) + row[3:]
        self.links = {}
        self.receipts = {}
        self._groups = {}
        for observer, targets in list(self.observations.items()):
            if observer not in self.actors:
                del self.observations[observer]
                continue
            for target, row in list(targets.items()):
                if row[0] <= now:
                    del targets[target]
        self._observer_ranks = dict((observer, index) for index, observer in
                                    enumerate(self.observations))
        self._targets = {}
        for observer, targets in self.observations.items():
            for target, row in targets.items():
                self._targets.setdefault(target, {})[observer] = row

    def connected(self, first, second):
        if first == second:
            return True
        key = (first, second)
        if key not in self.links:
            left, right = self.actors.get(first), self.actors.get(second)
            self.links[key] = bool(left and right and left[0] == right[0] and
                spotting.radio_link(left[1], left[2], right[1], right[2]))
        return self.links[key]

    def observe(self, observer, target, now, duration, pose):
        if observer not in self.observations:
            self.observations[observer] = {}
            # Equal-time poses retain the original observer-dict traversal
            # winner, including when an insertion resizes a Python 2 dict.
            self._observer_ranks = dict((owner, index) for index, owner in
                                        enumerate(self.observations))
            self.receipts = {}
        targets = self.observations.setdefault(observer, {})
        previous = targets.get(target)
        deadline = max(now + duration, previous[0] if previous is not None else 0.0)
        row = (deadline, now, dict(pose), True)
        targets[target] = row
        self._targets.setdefault(target, {})[observer] = row
        for group, receipt in list(self.receipts.get(target, {}).items()):
            if observer not in group or deadline <= receipt[0]:
                continue
            # A delayed sample may replace a winning sample with an older
            # one. Rebuild only that target/group when it is next requested.
            if (previous is not None and previous[0] > receipt[0] and
                    now < previous[1] and
                    (receipt[2] == observer or
                     (previous[3] and previous[1] == receipt[4]))):
                del self.receipts[target][group]
                continue
            receipt[1] = max(receipt[1], deadline)
            latest = receipt[3]
            if (latest is None or now > latest[1] or
                    (now == latest[1] and self._observer_ranks[observer] <=
                     self._observer_ranks[receipt[2]])):
                receipt[2], receipt[3] = observer, row
            if receipt[4] is None or now > receipt[4]:
                receipt[4] = now

    def hidden(self, observer, target):
        targets = self.observations.get(observer, {})
        if target in targets and targets[target][3]:
            row = targets[target]
            targets[target] = row[:3] + (False,)
            self._targets[target][observer] = targets[target]
            for group, receipt in list(self.receipts.get(target, {}).items()):
                if observer in group and row[1] == receipt[4]:
                    del self.receipts[target][group]

    def _recipient_group(self, recipient):
        group = self._groups.get(recipient)
        if group is None:
            # Equal direct-radio neighborhoods may share the same aggregate.
            # This is deliberately not a connected-component/relay closure.
            group = frozenset([recipient] + [observer for observer in self.actors
                if observer != recipient and self.connected(recipient, observer)])
            self._groups[recipient] = group
        return group

    def contact(self, recipient, target, now):
        group = self._recipient_group(recipient)
        receipts = self.receipts.setdefault(target, {})
        receipt = receipts.get(group)
        if receipt is None or receipt[0] != now:
            # Sample time, maximum lease, pose owner, latest pose, freshest
            # direct sample. Monotone observations update these in O(1).
            receipt = [now, 0.0, None, None, None]
            for observer, row in self._targets.get(target, {}).items():
                if observer not in group or row[0] <= now:
                    continue
                receipt[1] = max(receipt[1], row[0])
                latest = receipt[3]
                if (latest is None or row[1] > latest[1] or
                        (row[1] == latest[1] and self._observer_ranks[observer] <
                         self._observer_ranks[receipt[2]])):
                    receipt[2], receipt[3] = observer, row
                if row[3] and (receipt[4] is None or row[1] > receipt[4]):
                    receipt[4] = row[1]
            receipts[group] = receipt
        if receipt[3] is None:
            return (0.0, False, None)
        return (receipt[1] - now,
                receipt[4] is not None and now - receipt[4] <= 0.5 + 1e-9,
                receipt[3][2])
