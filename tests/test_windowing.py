"""Windowing: the Auto window worked out from the pixels, and the preset list."""

from __future__ import annotations

import os

import numpy as np
import pytest

from dicom_viewer import series
from dicom_viewer.states.dicom_state import PRESET_GROUPS, PRESETS
from tests import synth


def test_auto_window_leaves_out_the_background_and_applies_the_rescale():
    # Stored values with intercept -1024: padding 0 (-1024 HU) around tissue
    # from 0 to 300 HU.
    pixels = np.zeros((100, 100), dtype=np.uint16)
    pixels[20:80, 20:80] = np.linspace(1024, 1324, 3600).reshape(60, 60)
    center, width = series.auto_window(pixels, 1.0, -1024.0)
    low, high = center - width / 2, center + width / 2
    assert 0 <= low < 10
    assert 290 < high <= 300


def test_auto_window_keeps_two_level_images_visible():
    pixels = np.zeros((10, 10), dtype=np.uint16)
    pixels[:, :5] = 100
    assert series.auto_window(pixels, 1.0, 0.0) == (50.0, 100.0)


def test_auto_window_skips_colour_and_flat_images():
    assert series.auto_window(np.zeros((8, 8, 3), dtype=np.uint8), 1.0, 0.0) is None
    assert series.auto_window(np.full((8, 8), 7, dtype=np.uint16), 1.0, 0.0) is None


def test_image_window_reads_a_file_without_a_window(tmp_path):
    ds = synth.ct_series(tmp_path, count=1, write=False)[0]
    del ds.WindowCenter
    del ds.WindowWidth
    ds.PixelData = np.arange(64, dtype=np.uint16).reshape(8, 8).tobytes()
    path = synth.save(ds, os.path.join(tmp_path, "no_window.dcm"))
    assert series.file_window(path) is None
    center, width = series.image_window(path)
    assert center == pytest.approx(32.5, abs=1)
    assert width == pytest.approx(61, abs=2)
    assert series.image_window(os.path.join(tmp_path, "missing.dcm")) is None


def test_every_preset_is_in_exactly_one_group():
    grouped = [name for names in PRESET_GROUPS.values() for name in names]
    assert sorted(grouped) == sorted(PRESETS)
