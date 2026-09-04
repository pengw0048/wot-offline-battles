from __future__ import print_function

"""#1513 path adapter for the copied 0.8.2 layout override store."""

import os


USER_DIR = os.path.join('.', 'mods', 'configs', 'offline_lan_0922')


def _ensure_user_dir():
    if not os.path.isdir(USER_DIR):
        os.makedirs(USER_DIR)


def user_data_path(name):
    _ensure_user_dir()
    return os.path.abspath(os.path.join(USER_DIR, name))


def atomic_write_user_file(name, payload, validate=None):
    target = user_data_path(name)
    temporary = target + '.tmp'
    stream = None
    try:
        stream = open(temporary, 'wb')
        if isinstance(payload, bytes):
            stream.write(payload)
        else:
            stream.write(payload.encode('utf-8'))
        stream.close()
        stream = None
        if validate is not None:
            validate(temporary)
        if os.path.exists(target):
            os.unlink(target)
        os.rename(temporary, target)
        return target
    except (IOError, OSError, ValueError):
        return None
    finally:
        if stream is not None:
            try:
                stream.close()
            except (IOError, OSError):
                pass
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except (IOError, OSError):
                pass
