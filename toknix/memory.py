"""Toknix public compatibility wrapper for the existing memory implementation."""
import sys
from tokensaver import memory as _implementation

# Alias the actual module so public and private attributes behave consistently.
sys.modules[__name__] = _implementation
