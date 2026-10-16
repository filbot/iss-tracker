"""Minimal systemd notify protocol client."""

from __future__ import annotations

import os
import socket


def sd_notify(message: str) -> None:
    """Send a notification to systemd (if running under systemd).

    Uses raw socket to $NOTIFY_SOCKET, avoiding python-systemd dependency.
    Several assignments can be sent at once, separated by newlines.
    """
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    try:
        if addr[0] == "@":
            addr = "\0" + addr[1:]
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            sock.sendto(message.encode(), addr)
        finally:
            sock.close()
    except Exception:
        pass
