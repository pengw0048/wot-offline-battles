"""Direct observer radio sharing; independent of rendering and team totals."""
from gui.mods.offline_lan_0922 import spotting


class RadioNetwork(object):
    def __init__(self):
        self.actors = {}
        self.links = {}
        self.receipts = {}
        self.observations = {}

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
        for observer, targets in list(self.observations.items()):
            if observer not in self.actors:
                del self.observations[observer]
                continue
            for target, row in list(targets.items()):
                if row[0] <= now:
                    del targets[target]

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
        self.receipts = {}
        targets = self.observations.setdefault(observer, {})
        previous = targets.get(target)
        deadline = max(now + duration, previous[0] if previous is not None else 0.0)
        targets[target] = (deadline, now, dict(pose), True)

    def hidden(self, observer, target):
        targets = self.observations.get(observer, {})
        if target in targets:
            row = targets[target]
            targets[target] = row[:3] + (False,)
            self.receipts = {}

    def contact(self, recipient, target, now):
        key = (recipient, target, now)
        if key in self.receipts:
            return self.receipts[key]
        rows = [targets[target] for observer, targets in self.observations.items()
                if target in targets and targets[target][0] > now and
                self.connected(recipient, observer)]
        if not rows:
            result = (0.0, False, None)
        else:
            latest = max(rows, key=lambda row: row[1])
            result = (max(row[0] for row in rows) - now,
                      any(row[3] and now - row[1] <= 0.5 + 1e-9
                          for row in rows), latest[2])
        self.receipts[key] = result
        return result
