"""Toknix public compatibility wrapper for the existing proxy implementation."""
import sys
from tokensaver import proxy as _implementation

# Alias the actual module so public and private attributes behave consistently.
sys.modules[__name__] = _implementation
