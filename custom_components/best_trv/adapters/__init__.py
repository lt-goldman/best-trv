"""TRV adapters for Best TRV.

An adapter is the only thing that knows how to talk to a specific physical
TRV. The controller and climate entity never touch vendor-specific
attributes or MQTT topics directly - they only call the `TRVAdapter`
interface in `base.py`. This is what lets additional adapters (a generic
Z2M/ZHA climate entity, direct valve-position control, plain target-
temperature manipulation) slot in later without touching climate.py.

Only `AqaraE1Z2MAdapter` (Strategy 1: let the TRV's own controller do the
work, steer it via a mirrored feedback temperature) is implemented for the
MVP.
"""
