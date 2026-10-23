"""Pure-Python 2D nesting engine used by the SheetNester add-in.

Nothing in this package imports the Fusion API, so it can be unit tested
outside Fusion. All lengths are in whatever unit the caller uses (the add-in
passes Fusion's internal unit, centimetres).
"""

from .nester import NestOptions, NestPart, NestResult, Placement, SheetLayout, nest

__all__ = ['NestOptions', 'NestPart', 'NestResult', 'Placement', 'SheetLayout', 'nest']
