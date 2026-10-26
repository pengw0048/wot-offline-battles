"""Input routing with the public engine's unprefixed KeyMap contract."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'bigworld/res/scripts/client/WotStandalone.py'


class Event:
    def __init__(self, key, down=True, repeated=False):
        self.key = key
        self.down = down
        self.repeated = repeated

    def isKeyDown(self):
        return self.down

    def isRepeatedEvent(self):
        return self.repeated


class InputRoutingTests(unittest.TestCase):
    def setUp(self):
        # key_code.cpp stringifies KEY_ADD's argument, without the KEY_ enum
        # prefix. Unknown names return KEY_NOT_FOUND, rather than a new key.
        self.keys = dict(zip(('W', 'S', 'A', 'D', 'SPACE', 'LEFTMOUSE', 'UP'),
                             (17, 31, 30, 32, 57, 256, 200)))
        self.camera = Mock()
        self.engine = types.ModuleType('BigWorld')
        self.engine.stringToKey = lambda name: self.keys.get(name, 0)
        self.engine.camera = lambda: self.camera
        self.engine.standaloneFire = Mock(return_value=False)
        spec = importlib.util.spec_from_file_location('bootstrap_under_test', SOURCE)
        self.bootstrap = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'BigWorld': self.engine}):
            spec.loader.exec_module(self.bootstrap)

    def test_vehicle_keys_never_drive_the_free_camera(self):
        for name in ('W', 'S', 'A', 'D', 'SPACE'):
            for down, repeated in ((True, False), (True, True), (False, False)):
                with self.subTest(key=name, down=down, repeated=repeated):
                    self.assertIs(self.bootstrap.handleKeyEvent(
                        Event(self.keys[name], down, repeated)), True)
        self.camera.handleKeyEvent.assert_not_called()
        self.engine.standaloneFire.assert_not_called()

    def test_left_click_fires_only_on_initial_press(self):
        for down, repeated in ((True, False), (True, True), (False, False)):
            self.assertIs(self.bootstrap.handleKeyEvent(
                Event(self.keys['LEFTMOUSE'], down, repeated)), True)
        self.engine.standaloneFire.assert_called_once_with()
        self.camera.handleKeyEvent.assert_not_called()

    def test_other_keys_keep_camera_handler_result(self):
        self.camera.handleKeyEvent.return_value = False
        event = Event(self.keys['UP'])
        self.assertIs(self.bootstrap.handleKeyEvent(event), False)
        self.camera.handleKeyEvent.assert_called_once_with(event)
        self.engine.standaloneFire.assert_not_called()

    def test_mouse_and_axis_keep_camera_handler_result(self):
        for name in ('handleMouseEvent', 'handleAxisEvent'):
            handler = getattr(self.camera, name)
            handler.return_value = True
            event = object()
            self.assertIs(getattr(self.bootstrap, name)(event), True)
            handler.assert_called_once_with(event)


if __name__ == '__main__':
    unittest.main()
