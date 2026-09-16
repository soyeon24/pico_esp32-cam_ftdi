"""Thermal false-colour lookup tables. Nothing here needs mediapipe.

Split out of thermal_pose.py so the palettes can be used from a path that has
no pose model — the plain camera view, a still image, a test — without dragging
in a heavyweight dependency that would make those paths fail to start.
"""
from __future__ import annotations

import cv2
import numpy as np


def build_lut(stops):
    """256-entry BGR lookup table. stops interpolates (position, R, G, B)."""
    pos = np.array([s[0] for s in stops], np.float32)
    rgb = np.array([s[1] for s in stops], np.float32)
    x = np.linspace(0.0, 1.0, 256, dtype=np.float32)
    lut = np.stack([np.interp(x, pos, rgb[:, ch]) for ch in (2, 1, 0)], axis=1)
    return lut.astype(np.uint8).reshape(256, 1, 3)


# The cold end is black rather than deep blue, so an empty cell reads as
# "nothing here" instead of "a cold thing here".
RAINBOW_HC = build_lut([
    (0.00, (0, 0, 0)),
    (0.14, (10, 12, 90)),
    (0.28, (0, 60, 190)),
    (0.40, (0, 130, 150)),
    (0.50, (25, 165, 60)),
    (0.60, (130, 155, 20)),
    (0.70, (205, 45, 25)),
    (0.82, (255, 125, 0)),
    (0.92, (255, 220, 40)),
    (1.00, (255, 255, 205)),
])

PALETTES = [
    ("RAINBOW-HC", RAINBOW_HC),
    ("IRONBOW", cv2.COLORMAP_INFERNO),
    ("RAINBOW", cv2.COLORMAP_JET),
    ("TURBO", cv2.COLORMAP_TURBO),
    ("WHITE-HOT", None),
    ("ARCTIC", cv2.COLORMAP_OCEAN),
]


def sensor_grid(img: np.ndarray, step: int) -> np.ndarray:
    """Darken the seams between sensor cells so the grid reads as a sensor.

    Only the seam rows and columns are touched. Multiplying the whole image by
    a float costs tens of milliseconds a frame and buys nothing.
    """
    step = max(step, 3)
    img[::step, :] = (img[::step, :] * 0.30).astype(np.uint8)
    img[:, ::step] = (img[:, ::step] * 0.30).astype(np.uint8)
    return img
