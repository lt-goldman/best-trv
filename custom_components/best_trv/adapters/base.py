"""Adapter interface: how Best TRV talks to a physical TRV."""
from __future__ import annotations

from abc import ABC, abstractmethod


class TRVAdapter(ABC):
    """One physical TRV, addressed by whichever protocol it needs.

    Implementations own all vendor-specific detail (MQTT topics, ZHA
    clusters, entity naming). The rest of the integration only ever calls
    this interface.
    """

    @property
    @abstractmethod
    def available(self) -> bool:
        """Whether this TRV can currently be commanded."""

    @property
    @abstractmethod
    def feed_temperature_bounds(self) -> tuple[float, float]:
        """(min, max) accepted by this TRV's temperature-feed input."""

    @property
    @abstractmethod
    def feed_temperature_step(self) -> float:
        """Smallest increment this TRV's temperature-feed input resolves to.

        The control loop rounds to this step before deciding whether a push
        is worth a Zigbee write. It must describe what the *device* keeps:
        too coarse a value silently hides part of the room-to-setpoint gap
        from the TRV's own controller. Don't assume the matching Home
        Assistant entity's `step` attribute says so - it may just be HA's
        default (see const.AQARA_E1_FEED_STEP).
        """

    @abstractmethod
    async def async_set_enabled(self, enabled: bool) -> bool:
        """Turn the TRV's own control loop on/off.

        For a heat-only device this maps to its system_mode (heat/off) -
        Best TRV's own `cool` mode still leaves the TRV
        "enabled", just fed a mirrored temperature.

        Returns True if the command was accepted, False if it failed (the
        adapter has already logged why) - the caller's CommandQueue uses
        this to decide when to retry, so implementations must never raise.
        """

    @abstractmethod
    async def async_set_setpoint(self, setpoint: float) -> bool:
        """Sync the TRV's own comparison point to Best TRV's setpoint.

        The mirrored-temperature math (`fake = 2*setpoint - real`) is only
        correct if the physical TRV compares the feed value against the
        *same* setpoint we computed it against. Best TRV never controls
        the valve directly, so the TRV's own setpoint has to be kept equal
        to ours by calling this whenever it changes - otherwise the TRV is
        still comparing against whatever setpoint was last set on it
        directly (e.g. via its own app), and the math silently regulates
        around the wrong target.

        Returns True/False as `async_set_enabled` does.
        """

    @abstractmethod
    async def async_push_feed_temperature(self, temperature: float) -> bool:
        """Send the (real or mirrored) temperature this TRV should see.

        Returns True/False as `async_set_enabled` does.
        """
