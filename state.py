"""Ограниченное процессное состояние, изолированное по native home и session."""
from collections import OrderedDict
import hashlib
import json
import re
import threading
import time

TERMINAL = {'completed', 'error', 'timeout', 'interrupted', 'max_iterations', 'partial', 'cancelled', 'failed'}


def identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value) else ''


def meaningful(value):
    return isinstance(value, str) and len(value.strip()) >= 8 and value.strip().casefold() not in {'спасибо!', 'всё ясно', 'понятно.'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, default=str).encode('utf-8')).hexdigest()


def bounded_put(mapping, key, value, cap=128):
    mapping[key] = value
    mapping.move_to_end(key)
    while len(mapping) > cap:
        mapping.popitem(last=False)


class Store:
    def __init__(self, ttl_getter=lambda: 1800):
        self.sessions = OrderedDict()
        self.lock = threading.RLock()
        self.clock = time.monotonic
        self.ttl_getter = ttl_getter

    def session(self, sid, ttl=None):
        from hermes_constants import get_hermes_home
        from .protocol import number
        try:
            ttl = number(self.ttl_getter() if ttl is None else ttl, .02, 3600)
        except Exception:
            ttl = 1800
        sid = identifier(sid)
        if not sid:
            return None, None
        home = get_hermes_home().resolve()
        key = (str(home), sid)
        now = self.clock()
        with self.lock:
            for old in [k for k, s in self.sessions.items() if now - s['time'] > ttl]:
                self.sessions.pop(old, None)
            for session in self.sessions.values():
                for name in ('children', 'dispatches'):
                    for old in [k for k, record in session[name].items() if now - record['time'] > ttl]:
                        session[name].pop(old, None)
            state = self.sessions.get(key)
            if state is None:
                state = {'time': now, 'goal': '', 'parent': '', 'children': OrderedDict(),
                         'dispatches': OrderedDict(), 'cache': OrderedDict(), 'retries': OrderedDict(), 'nudges': OrderedDict()}
            state['time'] = now
            bounded_put(self.sessions, key, state)
            return key, state

    def current(self, key, state):
        """Identity/owner/TTL check, without creating or refreshing a state."""
        from hermes_constants import get_hermes_home
        from .protocol import number
        try:
            ttl = number(self.ttl_getter(), .02, 3600)
        except Exception:
            ttl = 1800
        with self.lock:
            return (key is not None and key[0] == str(get_hermes_home().resolve())
                    and self.sessions.get(key) is state and self.clock() - state['time'] <= ttl)

    def clear(self):
        with self.lock:
            self.sessions.clear()
