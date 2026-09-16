# SPDX-FileCopyrightText: Copyright (C) 2026 Mauker and the Bespok3d contributors
# SPDX-License-Identifier: Apache-2.0
"""The live settings, the file they survive a restart in, and reading them off a posted form.

Everything a stranger on the network can write to is validated here against what actually exists.
An unknown palette or a rotation that is not a quarter turn leaves the field alone rather than
reaching the renderer and failing one frame later, where the cause is invisible.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import threading
from pathlib import Path
from typing import TypeVar

from .camera import VALID_GAINS, CameraSettings
from .pipeline import VALID_ROTATIONS, RenderSettings, ThermalRenderer
from .temperature import MAX_EMISSIVITY, MIN_EMISSIVITY, VALID_UNITS

# Both settings dataclasses go through `restored`, and it has to hand back the same kind it
# was given rather than a common base, or every caller loses its type.
_Settings = TypeVar("_Settings", RenderSettings, CameraSettings)


# Each entry is a setting that was split, and the settings that replaced it, oldest first. Applied
# in order, so a file old enough to need both steps gets both: `overlay` became `colorbar` and
# `markers` in 0.9.0, and `markers` became the three individual marker switches in 0.10.0.
SETTING_SPLITS = (
    ("overlay", ("colorbar", "markers")),
    ("markers", ("reticle", "hotspot", "coldspot")),
)


def migrated(saved: dict) -> dict:
    """Read a settings file written by an older version.

    Someone who deliberately turned part of the readout off should not have it come back because a
    field was renamed underneath them, so an old switch sets the switches that replaced it.

    Only when none of the replacements is present. A file written by a newer version carries them,
    and may still carry the old key beside them, where it is a leftover that must not win.
    """

    updated = dict(saved)
    for old, replacements in SETTING_SPLITS:
        if old in updated and not any(new in updated for new in replacements):
            updated.update(dict.fromkeys(replacements, updated[old]))
    return updated


def restored(current: _Settings, saved: dict) -> _Settings:
    """Rebuild a settings dataclass from a saved file, ignoring keys it does not have.

    One function for both sets, because the file is flat: a key belongs to whichever dataclass
    declares it, and a key from an older or newer version belongs to neither and is dropped rather
    than raising on the way to a camera that then never starts.
    """

    known = {field.name for field in dataclasses.fields(current)}
    return dataclasses.replace(
        current, **{key: value for key, value in saved.items() if key in known}
    )


class SettingsStore:
    """The live settings, and the file they survive a restart in.

    Held behind a lock because HTTP handler threads write and the capture thread reads. Handed out
    as a whole snapshot rather than field by field, so a frame is never rendered from half of one
    change and half of another. `revision` is what lets the capture loop notice a change without
    polling every field.
    """

    def __init__(
        self,
        palette_name: str,
        settings: RenderSettings,
        state_file: Path | None,
        camera: CameraSettings = CameraSettings(),
    ) -> None:
        self._palette_name = palette_name
        self._settings = settings
        self._camera = camera
        self._state_file = state_file
        self._revision = 0
        self._camera_revision = 0
        self._lock = threading.Lock()
        self._load()

    def snapshot(self) -> tuple[int, str, RenderSettings]:
        with self._lock:
            return (self._revision, self._palette_name, self._settings)

    def camera_snapshot(self) -> tuple[int, CameraSettings]:
        """Counted separately, so changing a palette does not re-send a gain command over USB."""

        with self._lock:
            return (self._camera_revision, self._camera)

    def update(self, palette_name: str, settings: RenderSettings) -> None:
        with self._lock:
            self._palette_name = palette_name
            self._settings = settings
            self._revision += 1
        self._save()

    def update_camera(self, camera: CameraSettings) -> None:
        with self._lock:
            self._camera = camera
            self._camera_revision += 1
        self._save()

    def as_dict(self) -> dict:
        _, palette_name, settings = self.snapshot()
        _, camera = self.camera_snapshot()
        return {
            "palette": palette_name,
            **dataclasses.asdict(settings),
            **dataclasses.asdict(camera),
        }

    def _load(self) -> None:
        """Restore what was saved. A missing or unreadable file just means the defaults stand."""

        if self._state_file is None or not self._state_file.is_file():
            return
        try:
            saved = json.loads(self._state_file.read_text())
        except (OSError, ValueError) as error:
            print(f"thermal-master: ignoring unreadable settings: {error}", file=sys.stderr)
            return
        self._palette_name = saved.pop("palette", self._palette_name)
        self._settings = restored(self._settings, migrated(saved))
        self._camera = restored(self._camera, saved)

    def _save(self) -> None:
        if self._state_file is None:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps(self.as_dict(), indent=2))
        except OSError as error:
            print(f"thermal-master: could not save settings: {error}", file=sys.stderr, flush=True)


def settings_from_form(
    form: dict, palettes: dict, current: RenderSettings
) -> tuple[str | None, RenderSettings]:
    """Read a posted form into a palette name and settings, ignoring anything unrecognised.

    Everything is validated against what actually exists: an unknown palette or a rotation that is
    not a quarter turn leaves that field as it was, rather than reaching the renderer and failing
    one frame later where the cause is invisible.
    """

    palette_name = form.get("palette", [None])[0]
    posted_rotation = form.get("rotation", [""])[0]
    rotation = int(posted_rotation) if posted_rotation.isdigit() else -1
    posted_units = form.get("units", [""])[0]
    return (
        palette_name if palette_name in palettes else None,
        dataclasses.replace(
            current,
            rotation=rotation if rotation in VALID_ROTATIONS else current.rotation,
            flip_horizontal="flip_horizontal" in form,
            flip_vertical="flip_vertical" in form,
            colorbar="colorbar" in form,
            reticle="reticle" in form,
            hotspot="hotspot" in form,
            coldspot="coldspot" in form,
            units=posted_units if posted_units in VALID_UNITS else current.units,
            emissivity=posted_emissivity(form, current.emissivity),
        ),
    )


def settings_from_json(payload: dict, palettes: dict, current: RenderSettings) -> tuple:
    """Apply only the settings a JSON body actually names.

    A posted HTML form cannot express "change this one thing": an unticked checkbox is simply
    absent, so a form that omits a field is saying that field is off. That is right for the
    control page, which always sends every field, and wrong for anything that wants to change one
    setting, which would silently switch off every box it did not think to mention.

    So changing one setting goes through JSON instead, where absent means absent. Same validators
    either way, because the rule about what a setting may be does not depend on how it arrived.
    """

    changes = {}
    if "rotation" in payload and payload["rotation"] in VALID_ROTATIONS:
        changes["rotation"] = payload["rotation"]
    if "units" in payload and payload["units"] in VALID_UNITS:
        changes["units"] = payload["units"]
    if "emissivity" in payload:
        changes["emissivity"] = clamped_emissivity(payload["emissivity"], current.emissivity)
    flags = ("flip_horizontal", "flip_vertical", "colorbar", "reticle", "hotspot", "coldspot")
    for flag in flags:
        if flag in payload:
            changes[flag] = bool(payload[flag])
    palette_name = payload.get("palette")
    return (
        palette_name if palette_name in palettes else None,
        dataclasses.replace(current, **changes),
    )


def camera_settings_from_json(payload: dict, current: CameraSettings) -> CameraSettings:
    if payload.get("gain") in VALID_GAINS:
        return dataclasses.replace(current, gain=payload["gain"])
    return current


def clamped_emissivity(value: object, current: float) -> float:
    """Pulled into range rather than refused, wherever it came from."""

    try:
        posted = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return current
    return min(max(posted, MIN_EMISSIVITY), MAX_EMISSIVITY)


def posted_emissivity(form: dict, current: float) -> float:
    """Read an emissivity, clamped rather than refused.

    The page offers a list, but the settings file is a supported thing to hand-edit, so anything
    parseable is accepted and pulled into range. Zero would divide by zero in the correction, which
    is why the floor is not zero.
    """

    return clamped_emissivity(form.get("emissivity", [""])[0], current)


def camera_settings_from_form(form: dict, current: CameraSettings) -> CameraSettings:
    """The half of the form that becomes a USB command rather than a rendering choice."""

    gain = form.get("gain", [""])[0]
    return dataclasses.replace(current, gain=gain if gain in VALID_GAINS else current.gain)


class RendererSource:
    """Hands out a renderer that matches the current settings, rebuilding it when they change.

    Rebuilding discards the smoothed bounds and the previous frame, so a settings change costs about
    a second of re-settling. That is the right trade: the alternative is mutating a renderer while
    the capture thread is inside it.
    """

    def __init__(self, settings_store: SettingsStore, palettes: dict) -> None:
        self._settings_store = settings_store
        self._palettes = palettes
        self._revision: int | None = None
        self._renderer: ThermalRenderer | None = None

    def current(self) -> ThermalRenderer:
        revision, palette_name, settings = self._settings_store.snapshot()
        if revision != self._revision or self._renderer is None:
            self._revision = revision
            self._renderer = ThermalRenderer(self._palettes[palette_name], settings)
        return self._renderer
