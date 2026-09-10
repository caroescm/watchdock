"""Registration module named by the ``nat.components`` entry point.

Importing the workflow function is what registers it with NAT.
"""
from watchdock_detector.watchdock_detector import watchdock_detector_function

__all__ = ["watchdock_detector_function"]
