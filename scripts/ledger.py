#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tiny idempotency ledger for the Vast safety tail. Atomic write + flock. No daemon.

This is the LEDGER per the W1 contract: $TAIL/state.json mutated through flock+atomic
helpers so STOP/evac steps are idempotent and never double-fire. park_timer.sh uses it
for an idempotent stop (key 'stopped':true).

Usage:
    ledger.py set <key> <val> [<key> <val> ...]   # idempotent merge; 'true'/'false' -> bool
    ledger.py get <key>                           # prints value (empty line if absent)

Box-side only (POSIX flock). P = /workspace/_tail/state.json, overridable via $TAIL.
"""
import json
import os
import sys
import tempfile

try:
    import fcntl  # POSIX only; the ledger runs box-side on Linux
except ImportError:  # pragma: no cover - never on the box
    fcntl = None

TAIL = os.environ.get("TAIL", "/workspace/_tail")
P = os.path.join(TAIL, "state.json")


def _load():
    try:
        with open(P, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _atomic(d):
    os.makedirs(os.path.dirname(P), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(P), prefix=".state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, P)  # atomic on POSIX
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _coerce(v):
    if v == "true":
        return True
    if v == "false":
        return False
    return v


def main(argv):
    os.makedirs(os.path.dirname(P), exist_ok=True)
    a = argv[1:]
    if not a:
        sys.stderr.write(__doc__ or "")
        return 2

    lock_path = P + ".lock"
    lock = open(lock_path, "w")
    try:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)

        if a[0] == "get":
            if len(a) < 2:
                sys.stderr.write("get needs a key\n")
                return 2
            val = _load().get(a[1], "")
            print(val if val is not None else "")
            return 0

        if a[0] == "set":
            kv = a[1:]
            if len(kv) % 2 != 0:
                sys.stderr.write("set needs key/value pairs\n")
                return 2
            d = _load()
            for i in range(0, len(kv), 2):
                d[kv[i]] = _coerce(kv[i + 1])
            _atomic(d)
            return 0

        sys.stderr.write("unknown command: %s\n" % a[0])
        return 2
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(lock, fcntl.LOCK_UN)
        except Exception:
            pass
        lock.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
