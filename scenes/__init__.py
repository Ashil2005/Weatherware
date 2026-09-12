"""Weather scenes. Each scene owns its visuals, audio cues, and the
brightness intent it hands to the effects layer."""

from scenes.base import Scene
from scenes.rainy import RainyScene
from scenes.snowy import SnowyScene
from scenes.sunny import SunnyScene
from scenes.sunshower import SunshowerScene
from scenes.windy import WindyScene

SCENES = {
    "sunny": SunnyScene,
    "rainy": RainyScene,
    "windy": WindyScene,
    "snowy": SnowyScene,
    "sunshower": SunshowerScene,
}


def make(name):
    """Instantiate a scene by its config key."""
    if name not in SCENES:
        raise ValueError(f"unknown scene {name!r}; expected one of {sorted(SCENES)}")
    return SCENES[name]()


__all__ = ["Scene", "SunnyScene", "RainyScene", "WindyScene", "SnowyScene",
           "SunshowerScene", "SCENES", "make"]
