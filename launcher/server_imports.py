"""Standard-library modules that the bundled servers import at runtime.

PyInstaller analyses this launcher, not the server payload that ``runpy`` loads
from the bundle. Every module a server imports must therefore be visible in
this file. ``test_launcher_core.py`` fails when a server adds an import that is
missing here.
"""

import argparse
import array
import base64
import collections
import copy
import ctypes
import dataclasses
import hashlib
import heapq
import json
import math
import os
import pickle
import random
import re
import socket
import socketserver
import subprocess
import sys
import threading
import time
import traceback
import types
import typing
import unicodedata
import uuid
import weakref
import zlib

SERVER_STDLIB_MODULES = (
    argparse.__name__,
    array.__name__,
    base64.__name__,
    collections.__name__,
    copy.__name__,
    ctypes.__name__,
    dataclasses.__name__,
    hashlib.__name__,
    heapq.__name__,
    json.__name__,
    math.__name__,
    os.__name__,
    pickle.__name__,
    random.__name__,
    re.__name__,
    socket.__name__,
    socketserver.__name__,
    subprocess.__name__,
    sys.__name__,
    threading.__name__,
    time.__name__,
    traceback.__name__,
    types.__name__,
    typing.__name__,
    unicodedata.__name__,
    uuid.__name__,
    weakref.__name__,
    zlib.__name__,
)
