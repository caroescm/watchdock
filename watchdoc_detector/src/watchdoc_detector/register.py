"""Registration module named by the ``nat.components`` entry point.

Importing the workflow function is what registers it with NAT.
"""
from watchdoc_detector.watchdoc_detector import watchdoc_detector_function

__all__ = ["watchdoc_detector_function"]
