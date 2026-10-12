"""Minimal -S child bootstrap for the fixed offline Phase 5 product CLI.

Python's automatic site initialization executes .pth and sitecustomize code
before an application can block networking. The Phase 5 runner starts this
bootstrap with -S, blocks Python sockets first, then adds only the interpreter's
global purelib/platlib directories without processing .pth files.
"""
from __future__ import annotations

import os
import runpy
import socket
import sys
import sysconfig
from pathlib import Path


def _deny_network(*_args, **_kwargs):
    raise RuntimeError("Network access is disabled in offline Phase 5 execution")


def _block_socket_network():
    socket.create_connection = _deny_network
    socket.socket.connect = _deny_network
    socket.socket.connect_ex = _deny_network
    for name in ("send", "sendall", "sendto", "sendmsg", "sendfile",
                 "bind", "listen", "accept", "accept_into"):
        if hasattr(socket.socket, name):
            setattr(socket.socket, name, _deny_network)
    for name in ("send_fds",):
        if hasattr(socket, name):
            setattr(socket, name, _deny_network)


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("offline bootstrap requires the fixed product script path")
    if not sys.flags.no_site:
        raise SystemExit("offline bootstrap must be started with Python -S")
    _block_socket_network()

    package_paths = sysconfig.get_paths()
    for name in ("purelib", "platlib"):
        value = package_paths.get(name)
        if not value:
            continue
        path = Path(value).resolve(strict=True)
        if not path.is_dir():
            raise SystemExit("offline bootstrap package path is unavailable")
        if os.fspath(path) not in sys.path:
            sys.path.append(os.fspath(path))

    os.environ["HARDSUB2SRT_OFFLINE_BOOTSTRAPPED"] = "1"
    script = Path(sys.argv[1]).resolve(strict=True)
    if not script.is_file():
        raise SystemExit("offline bootstrap product script is unavailable")
    sys.argv = [os.fspath(script), *sys.argv[2:]]
    runpy.run_path(os.fspath(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
