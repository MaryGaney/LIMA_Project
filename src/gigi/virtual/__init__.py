"""
Virtual robot for software-only testing of Gigi activities.

Enable with ``GIGI_VIRTUAL=1`` (``gigi virtual`` sets it for you). See
docs/VIRTUAL_ROBOT.md for what is and is not simulated.

Submodules are imported lazily so that importing ``gigi.virtual`` stays cheap.
"""

from gigi.virtual.bridge import VirtualBridge, get_bridge, set_bridge

__all__ = ["VirtualBridge", "get_bridge", "set_bridge"]
