"""New-recording shot-contract tests; array.array models a native vector.

These test sequence/object representation and shared live donation, not actual
BigWorld rendering. All recorded inputs are created afresh in each test.
"""
from __future__ import print_function
import array
import copy
import json
import math
import os
import sys
import types
import unittest

from gui.mods.offline_lan_0922 import descriptor_donation as donation
from gui.mods.offline_lan_0922 import effective_params
from gui.mods.offline_lan_0922 import replay_presentation as candidate

class Obj(object):
    def __init__(self, **fields):
        self.__dict__.update(fields)

class IndexVector(object):
    """Native-sequence shape: not a list/tuple, only fixed indexed numbers."""
    def __init__(self, *numbers):
        self.values = tuple(numbers)
    def __getitem__(self, index):
        return self.values[index]
    def __len__(self):
        return len(self.values)


def make_descriptor(vector='array', shell_type=False, he=False):
    def v(numbers):
        if vector == 'array': return array.array('f', numbers)
        if vector == 'indexed': return IndexVector(*numbers)
        if vector == 'tuple': return tuple(numbers)
        return list(numbers)
    shots = []
    for cd, pp, kind, speed in ((21578, (250., 225.), 'ARMOR_PIERCING', 912.8000136017799),
                                (21834, (300., 275.), 'ARMOR_PIERCING_CR', 1170.400017440319)):
        shell = Obj(compactDescr=cd, damage=v((20., 5000.)), caliber=57.)
        if he:
            kind = 'HIGH_EXPLOSIVE'
        if shell_type:
            shell.type = Obj(name=kind, explosionRadius=(3.5 if he else 0.))
        else:
            shell.kind = kind
            shell.explosionRadius = 3.5 if he else 0.
        shots.append(Obj(shell=shell, piercingPower=v(pp), speed=speed,
                         gravity=6.278400455665598, maxDistance=720.))
    return Obj(gun=Obj(clip=(50, .1), shots=shots))


def freshly_recorded(d, deadeye=True):
    """Same production shot donor and accepted-schema parser as live play."""
    rows = []
    for shot in d.gun.shots:
        donated = donation.project_shot(shot, deadeye=deadeye)
        canonical = effective_params._canonical_source_shot(donated)
        assert canonical is not None, donated
        rows.append(dict(compact_descr=shot.shell.compactDescr, source_shot=canonical))
    return json.loads(json.dumps(dict(gun=dict(clip_size=d.gun.clip[0], shots=rows)), allow_nan=False))


class ReplayContractTests(unittest.TestCase):
    def assertMismatch(self, d, recorded, name):
        with self.assertRaises(ValueError) as cm:
            candidate.verify_descriptor(d, recorded)
        self.assertIn(name, str(cm.exception))
        return str(cm.exception)

    def test_native_vector_is_not_a_plain_list(self):
        self.assertNotEqual(array.array('f', [250.,225.]), [250.,225.])
        self.assertFalse(isinstance(array.array('f'), (list,tuple)))

    def test_fresh_array_roundtrip_passes(self):
        d=make_descriptor(); self.assertTrue(candidate.verify_descriptor(d,freshly_recorded(d)))

    def test_fresh_indexed_native_roundtrip_passes(self):
        d=make_descriptor('indexed');self.assertTrue(candidate.verify_descriptor(d,freshly_recorded(d)))

    def test_fresh_tuple_roundtrip_passes(self):
        d=make_descriptor('tuple');self.assertTrue(candidate.verify_descriptor(d,freshly_recorded(d)))

    def test_fresh_list_roundtrip_passes(self):
        d=make_descriptor('list');self.assertTrue(candidate.verify_descriptor(d,freshly_recorded(d)))

    def test_penetration_vector_and_damage_vector_both_pass(self):
        d=make_descriptor(); a=freshly_recorded(d)
        self.assertEqual([250.,225.],a['gun']['shots'][0]['source_shot']['piercingPower'])
        self.assertEqual([20.,5000.],a['gun']['shots'][0]['source_shot']['shell']['damage'])
        self.assertTrue(candidate.verify_descriptor(d,a))

    def test_raw_shell_type_fallback_matches_live_donor(self):
        d=make_descriptor(shell_type=True);self.assertTrue(candidate.verify_descriptor(d,freshly_recorded(d)))

    def test_he_type_radius_projection_matches_live_donor(self):
        d=make_descriptor(shell_type=True,he=True);a=freshly_recorded(d)
        self.assertEqual(3.5,a['gun']['shots'][0]['source_shot']['shell']['explosionRadius'])
        self.assertTrue(candidate.verify_descriptor(d,a))

    def test_real_near_penetration_change_still_rejected(self):
        d=make_descriptor();a=freshly_recorded(d);d.gun.shots[0].piercingPower[0]=251.
        self.assertMismatch(d,a,'piercingPower')

    def test_real_far_penetration_change_still_rejected(self):
        d=make_descriptor();a=freshly_recorded(d);d.gun.shots[0].piercingPower[1]=224.
        self.assertMismatch(d,a,'piercingPower')

    def test_apcr_change_still_rejected_by_shell_id(self):
        d=make_descriptor();a=freshly_recorded(d);d.gun.shots[1].piercingPower[1]=225.
        text=self.assertMismatch(d,a,'piercingPower');self.assertIn('shell=21834',text)

    def test_damage_and_device_damage_changes_rejected(self):
        for index in (0,1):
            d=make_descriptor();a=freshly_recorded(d);d.gun.shots[0].shell.damage[index]+=1.
            self.assertMismatch(d,a,'shell.damage')

    def test_speed_gravity_range_changes_rejected(self):
        for field in ('speed','gravity','maxDistance'):
            d=make_descriptor();a=freshly_recorded(d);setattr(d.gun.shots[0],field,getattr(d.gun.shots[0],field)+1.)
            self.assertMismatch(d,a,field)

    def test_caliber_kind_and_radius_changes_rejected(self):
        for field,value in (('caliber',58.),('kind','HOLLOW_CHARGE'),('explosionRadius',3.)):
            d=make_descriptor();a=freshly_recorded(d);setattr(d.gun.shots[0].shell,field,value)
            self.assertMismatch(d,a,'shell.'+field)

    def test_clip_capacity_change_rejected(self):
        d=make_descriptor();a=freshly_recorded(d);d.gun.clip=(8,1.)
        self.assertMismatch(d,a,'clip')

    def test_missing_mounted_shell_rejected(self):
        d=make_descriptor();a=freshly_recorded(d);d.gun.shots.pop()
        self.assertMismatch(d,a,'ammunition')

    def test_lookup_is_by_compact_id_not_array_order(self):
        d=make_descriptor();a=freshly_recorded(d);d.gun.shots.reverse()
        self.assertTrue(candidate.verify_descriptor(d,a))

    def test_values_are_never_written_back(self):
        d=make_descriptor();a=freshly_recorded(d);old=copy.deepcopy(a)
        pp=d.gun.shots[0].piercingPower;dm=d.gun.shots[0].shell.damage
        candidate.verify_descriptor(d,a)
        self.assertIs(pp,d.gun.shots[0].piercingPower);self.assertIs(dm,d.gun.shots[0].shell.damage)
        self.assertEqual(old,a)

    def test_deadeye_perk_is_not_a_resource_mismatch(self):
        d=make_descriptor()
        for perk in (False,True):self.assertTrue(candidate.verify_descriptor(d,freshly_recorded(d,perk)))

    def test_vector_length_mismatch_rejected(self):
        d=make_descriptor();a=freshly_recorded(d);d.gun.shots[0].piercingPower=array.array('f',[250.])
        self.assertMismatch(d,a,'piercingPower')

    def test_unreadable_vector_rejected_not_ignored(self):
        d=make_descriptor();a=freshly_recorded(d);d.gun.shots[0].piercingPower=object()
        self.assertMismatch(d,a,'piercingPower')

    def test_nonfinite_native_component_rejected(self):
        for value in (float('inf'),float('-inf'),float('nan')):
            d=make_descriptor();a=freshly_recorded(d);d.gun.shots[0].piercingPower[0]=value
            self.assertMismatch(d,a,'piercingPower')

    def test_numeric_tolerance_has_not_been_relaxed(self):
        self.assertTrue(candidate._equal_values(250.,250.00001))
        self.assertFalse(candidate._equal_values(250.,250.1))
        self.assertFalse(candidate._equal_values(250.,float('inf')))
        self.assertFalse(candidate._equal_values(float('inf'),float('inf')))
        self.assertFalse(candidate._equal_values(float('nan'),float('nan')))

    def test_failure_details_report_both_canonical_values(self):
        d=make_descriptor();a=freshly_recorded(d);d.gun.shots[0].piercingPower[0]=123.
        text=self.assertMismatch(d,a,'piercingPower')
        for fragment in ('current=[123.0, 225.0]', 'recorded=[250.0, 225.0]', 'native_type=array', 'projection=descriptor_donation'):
            self.assertIn(fragment,text)

    def test_repeated_new_sessions_no_accumulating_state(self):
        for unused in range(50):
            d=make_descriptor();a=freshly_recorded(d)
            self.assertTrue(candidate.verify_descriptor(d,a))

if __name__=='__main__':
    unittest.main(verbosity=2)
