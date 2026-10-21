"""The built-in adapters against synthetic objects, and the local KiTS data when present."""

from __future__ import annotations

import dataclasses
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pydicom
import pytest

import dicom_viewer.annotations.builtin  # noqa: F401 - registers the adapters
from dicom_viewer.annotations import (
    PALETTE,
    Layer,
    SeriesInfo,
    SliceRef,
    find_annotation_files,
    is_annotation_file,
    layers_for_folder,
    load_layers,
    series_info,
    slice_ref,
    spread,
)
from dicom_viewer.annotations.support import polygon_mask
from tests import synth

ORGAN = [(2.0, 2.0), (6.0, 2.0), (6.0, 6.0), (2.0, 6.0)]  # pixels (2..5, 2..5): 16 of them
LESION = [(3.0, 3.0), (5.0, 3.0), (5.0, 5.0), (3.0, 5.0)]  # 4 pixels inside the organ

KITS = "/Users/Shared/DICOM/c4kc_kits"
KITS_CT = os.path.join(KITS, "1.3.6.1.4.1.14519.5.2.1.6919.4624.654375711050908645984901357164")
KITS_SEG = os.path.join(KITS, "1.2.276.0.7230010.3.1.3.0.91941.1590501849.558141")


def refs_of(cts) -> list[SliceRef]:
    return [slice_ref(ct) for ct in cts]


def load_one(path: str, series: SeriesInfo) -> Layer:
    """Load in a worker thread, as the viewer does; the layer is then used from this one."""
    errors: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        layers = pool.submit(load_layers, series, [path], errors).result()
    assert errors == []
    assert len(layers) == 1
    assert isinstance(layers[0], Layer)
    return layers[0]


@pytest.fixture
def ct(tmp_path):
    return synth.ct_series(tmp_path)


def test_polygon_mask_covers_the_pixels_inside():
    assert int(polygon_mask(ORGAN, (8, 8)).sum()) == 16
    assert polygon_mask(ORGAN, (8, 8))[2:6, 2:6].all()
    assert int(polygon_mask([(0.5, 0.5), (7.5, 0.5), (0.5, 7.5)], (8, 8)).sum()) == 28  # triangle
    assert not polygon_mask([(1, 1), (2, 2)], (8, 8)).any()


# --- SEG ---


def test_seg_masks_by_source_image_and_by_position(tmp_path, ct):
    masks = np.zeros((3, 8, 8, 2), dtype=bool)
    masks[0:2, 2:6, 2:6, 0] = True
    masks[1, 3:5, 3:5, 1] = True
    path = synth.save(synth.seg_for(ct, masks, colors={"Organ": (60.0, 60.0, 40.0)}), tmp_path / "seg.dcm")
    assert is_annotation_file(path)
    refs = refs_of(ct)
    layer = load_one(path, series_info(refs))
    assert layer.format == "SEG" and layer.supports_volume and layer.name
    assert [(i.id, i.name, i.kind, i.description) for i in layer.items] == [
        ("1", "Organ", "mask", "Manual"),
        ("2", "Lesion", "mask", "Manual"),
    ]
    red, green, blue = layer.items[0].color
    assert red > green and red > blue
    assert not layer.applies_to(SeriesInfo(series_uid="other", frame_of_reference_uid="other"))

    middle = layer.overlay(refs[1])
    assert [m.item_id for m in middle.masks] == ["1", "2"]
    assert int(middle.masks[0].pixels.sum()) == 16 and int(middle.masks[1].pixels.sum()) == 4
    assert [m.item_id for m in layer.overlay(refs[0]).masks] == ["1"]
    assert layer.overlay(refs[2]).is_empty()
    assert [m.item_id for m in layer.overlay(refs[1], ["2"]).masks] == ["2"]

    labels = layer.label_volume(refs)
    assert labels.shape == (3, 8, 8)
    assert int((labels[1] == 2).sum()) == 4 and int((labels[1] == 1).sum()) == 12  # lesion wins
    assert int((labels[0] == 1).sum()) == 16 and not labels[2].any()

    # Another series on the same grid in the same frame of reference: matched by position.
    other = synth.ct_series(tmp_path / "other", frame_of_reference=ct[0].FrameOfReferenceUID, write=False)
    assert layer.applies_to(series_info(refs_of(other)))
    assert any("another series" in note for note in layer.notes)
    assert int(layer.overlay(refs_of(other)[1]).masks[0].pixels.sum()) == 16
    assert layer.overlay(refs_of(other)[2]).is_empty()
    # A bigger image whose grid holds the frame's (same origin and spacing): drawn in place.
    big = synth.ct_series(tmp_path / "big", rows=16, columns=16, frame_of_reference=ct[0].FrameOfReferenceUID, write=False)
    placed = layer.overlay(refs_of(big)[1]).masks[0].pixels
    assert placed.shape == (16, 16) and int(placed.sum()) == 16 and placed[2:6, 2:6].all()
    assert layer.label_volume(refs_of(big)).shape == (3, 16, 16)
    # Another spacing would need resampling: never drawn.
    fine = synth.ct_series(
        tmp_path / "fine", rows=16, columns=16, spacing=(0.5, 0.5), frame_of_reference=ct[0].FrameOfReferenceUID, write=False
    )
    assert layer.overlay(refs_of(fine)[1]).is_empty()


def test_seg_cropped_to_its_box_is_placed_on_the_image(tmp_path, ct):
    # Some research objects store each frame on a crop of the image grid, without image references.
    from pydicom.pixels import pack_bits

    masks = np.zeros((3, 8, 8, 1), dtype=bool)
    masks[1, 3:6, 4:7, 0] = True  # rows 3..5, columns 4..6 of the middle slice
    seg = synth.seg_for(ct, masks, labels=("Tumor",))
    frames = seg.pixel_array.reshape(-1, 8, 8)
    top, left = 2, 3
    seg.Rows, seg.Columns = 4, 5
    seg.PixelData = pack_bits(np.ascontiguousarray(frames[:, top : top + 4, left : left + 5]).astype(np.uint8))
    for group in seg.PerFrameFunctionalGroupsSequence:
        position = group.PlanePositionSequence[0]
        x, y, z = (float(v) for v in position.ImagePositionPatient)
        position.ImagePositionPatient = [x + left, y + top, z]  # 1 mm pixels, axial
        if "DerivationImageSequence" in group:
            del group.DerivationImageSequence
    if "ReferencedSeriesSequence" in seg:
        del seg.ReferencedSeriesSequence
    path = synth.save(seg, tmp_path / "cropped.dcm")
    layer = load_one(path, series_info(refs_of(ct)))
    placed = layer.overlay(refs_of(ct)[1]).masks[0].pixels
    assert placed.shape == (8, 8) and int(placed.sum()) == 9 and placed[3:6, 4:7].all()
    assert layer.overlay(refs_of(ct)[0]).is_empty()
    assert int((layer.label_volume(refs_of(ct))[1] == 1).sum()) == 9
    # Half a pixel off the image's grid: not drawn.
    for group in seg.PerFrameFunctionalGroupsSequence:
        position = group.PlanePositionSequence[0]
        x, y, z = (float(v) for v in position.ImagePositionPatient)
        position.ImagePositionPatient = [x + 0.5, y + 0.5, z]
    seg.SOPInstanceUID = pydicom.uid.generate_uid()
    shifted = load_one(synth.save(seg, tmp_path / "shifted.dcm"), series_info(refs_of(ct)))
    assert shifted.overlay(refs_of(ct)[1]).is_empty()


def test_objects_without_colours_get_their_own_and_their_maker_names_them(tmp_path, ct):
    masks = np.zeros((3, 8, 8, 1), dtype=bool)
    masks[1, 2:6, 2:6, 0] = True
    paths = []
    for reader in (1, 2, 3):
        seg = synth.seg_for(ct, masks, labels=("Tumor",))
        seg.ContentCreatorName = f"Study^Reader {reader}"
        if "SeriesDescription" in seg:
            del seg.SeriesDescription
        paths.append(synth.save(seg, tmp_path / f"reader{reader}.dcm"))
    coloured = synth.seg_for(ct, masks, labels=("Tumor",), colors={"Tumor": (60.0, 60.0, 40.0)})
    paths.append(synth.save(coloured, tmp_path / "coloured.dcm"))
    layers = [load_one(path, series_info(refs_of(ct))) for path in paths]
    assert [layer.name for layer in layers[:3]] == ["Study Reader 1", "Study Reader 2", "Study Reader 3"]
    # Each adapter alone picks the palette's first colour ...
    assert {layer.items[0].color for layer in layers[:3]} == {PALETTE[0]}
    own = layers[3].items[0].color
    spread(layers)
    # ... spread across the objects, they differ, and colours the files gave stay.
    colours = [layer.items[0].color for layer in layers]
    assert len(set(colours[:3])) == 3 and own not in colours[:3]
    assert colours[3] == own and layers[3].items[0].own_color
    assert layers[0].item("1").color == colours[0]  # what drawing looks up
    spread(layers)  # again (a rescan): the same colours
    assert [layer.items[0].color for layer in layers] == colours


@pytest.mark.skipif(not (os.path.isdir(KITS_CT) and os.path.isdir(KITS_SEG)), reason="KiTS sample not downloaded")
def test_kits_segmentation_on_its_ct(monkeypatch):
    monkeypatch.setenv("PUBLIC_DICOM_DIR", KITS)
    from dicom_viewer import folders

    files = folders.dicom_files(KITS_CT)
    refs = [slice_ref(pydicom.dcmread(path, stop_before_pixels=True)) for path in files]
    refs.sort(key=lambda ref: ref.position[2])
    errors: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        layers = pool.submit(layers_for_folder, series_info(refs), KITS_CT, errors=errors).result()
    assert errors == []
    assert [layer.format for layer in layers] == ["SEG"]
    layer = layers[0]
    assert [item.name for item in layer.items] == ["Kidney", "Mass"]
    kidney = [any(m.item_id == "1" for m in layer.overlay(ref).masks) for ref in refs]
    mass = [any(m.item_id == "2" for m in layer.overlay(ref).masks) for ref in refs]
    assert 10 <= sum(kidney) < len(refs) and 0 < sum(mass) < sum(kidney)
    labels = layer.label_volume(refs)
    assert labels.shape == (len(refs), 512, 512)
    assert set(np.unique(labels).tolist()) == {0, 1, 2}


# --- RTSTRUCT ---


def test_rtstruct_outlines_and_fills(tmp_path, ct):
    path = synth.save(
        synth.rtstruct_for(
            ct,
            {"Organ": [(0, ORGAN), (1, ORGAN)], "Lesion": [(1, LESION)]},
            colors={"Organ": (0, 255, 0), "Lesion": (255, 0, 0)},
        ),
        tmp_path / "rt.dcm",
    )
    assert is_annotation_file(path)
    refs = refs_of(ct)
    layer = load_one(path, series_info(refs))
    assert layer.format == "RTSTRUCT" and layer.name == "Synthetic set" and layer.supports_volume
    assert [(i.id, i.name, i.color, i.kind) for i in layer.items] == [
        ("1", "Organ", (0, 255, 0), "contour"),
        ("2", "Lesion", (255, 0, 0), "contour"),
    ]
    assert not layer.applies_to(SeriesInfo(series_uid="other", frame_of_reference_uid="other"))

    middle = layer.overlay(refs[1])
    assert [s.item_id for s in middle.shapes] == ["1", "2"]
    assert all(s.kind == "polygon" and s.closed for s in middle.shapes)
    assert np.allclose(middle.shapes[0].points, ORGAN)
    assert np.allclose(middle.shapes[1].points, LESION)
    assert [s.item_id for s in layer.overlay(refs[1], ["2"]).shapes] == ["2"]
    assert layer.overlay(refs[2]).is_empty()

    labels = layer.label_volume(refs)
    assert labels.shape == (3, 8, 8)
    assert int((labels[0] == 1).sum()) == 16 and int((labels[0] == 2).sum()) == 0
    assert int((labels[1] == 2).sum()) == 4 and int((labels[1] == 1).sum()) == 12  # lesion wins
    assert not labels[2].any()


def test_rtstruct_without_image_references_uses_the_plane(tmp_path, ct):
    path = synth.save(
        synth.rtstruct_for(ct, {"Organ": [(1, ORGAN)]}, name_images=False), tmp_path / "rt.dcm"
    )
    refs = refs_of(ct)
    layer = load_one(path, series_info(refs))
    assert layer.overlay(refs[0]).is_empty()
    assert len(layer.overlay(refs[1]).shapes) == 1
    # Another series in the same frame of reference, on the same grid.
    other = synth.ct_series(tmp_path / "other", frame_of_reference=ct[0].FrameOfReferenceUID, write=False)
    assert layer.applies_to(series_info(refs_of(other)))
    assert any("another series" in note for note in layer.notes)
    assert len(layer.overlay(refs_of(other)[1]).shapes) == 1
    nearby = dataclasses.replace(refs_of(other)[1], position=(0.0, 0.0, 1.4))
    assert len(layer.overlay(nearby).shapes) == 1
    assert layer.overlay(refs_of(other)[2]).is_empty()


# --- GSPS ---


def test_gsps_graphics_text_and_display_units(tmp_path, ct):
    path = synth.save(synth.gsps_for(ct, image_index=1), tmp_path / "pr.dcm")
    assert is_annotation_file(path)
    refs = refs_of(ct)
    layer = load_one(path, series_info(refs))
    assert layer.format == "PR" and layer.name == "SYNTHETIC" and not layer.supports_volume
    assert [(i.id, i.kind) for i in layer.items] == [("DRAW", "graphic"), ("DISPLAY", "graphic")]
    assert layer.items[0].color == pytest.approx((255, 255, 255), abs=1)
    assert not layer.applies_to(SeriesInfo(series_uid="other"))

    on_image = layer.overlay(refs[1])
    kinds = [(s.item_id, s.kind) for s in on_image.shapes]
    assert kinds == [("DRAW", "polyline"), ("DRAW", "circle"), ("DRAW", "point"), ("DISPLAY", "polyline")]
    assert np.allclose(on_image.shapes[0].points, [(1, 1), (5, 1), (5, 5)])
    assert np.allclose(on_image.shapes[3].points, [(0, 0), (8, 8)])  # DISPLAY -> whole image
    assert [(l.item_id, l.text, l.anchor) for l in on_image.labels] == [("DRAW", "hello", (1.0, 1.0))]

    elsewhere = layer.overlay(refs[0])
    assert [(s.item_id, s.kind) for s in elsewhere.shapes] == [("DISPLAY", "polyline")]
    assert [s.item_id for s in layer.overlay(refs[1], ["DRAW"]).shapes] == ["DRAW"] * 3


# --- SR ---


def test_sr_regions_and_measurements(tmp_path, ct):
    path = synth.save(synth.sr_for(ct, image_index=1), tmp_path / "sr.dcm")
    assert is_annotation_file(path)
    refs = refs_of(ct)
    layer = load_one(path, series_info(refs))
    assert layer.format == "SR" and not layer.supports_volume and layer.notes == []
    assert [(i.name, i.kind, i.description) for i in layer.items] == [("Lesion", "measurement", "Area 16 mm²")]
    assert not layer.applies_to(SeriesInfo(series_uid="other"))

    on_image = layer.overlay(refs[1])
    assert len(on_image.shapes) == 1
    shape = on_image.shapes[0]
    assert shape.kind == "polyline" and shape.closed and len(shape.points) == 5
    assert np.allclose(shape.points[0], (2.0, 2.0))
    assert [l.text for l in on_image.labels] == ["Lesion: Area 16 mm²"]
    assert layer.overlay(refs[0]).is_empty()
    assert layer.label_volume(refs) is None


# --- loading through the registry, collection style: one folder per series ---


def test_layers_for_folder_finds_every_format(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_DICOM_DIR", str(tmp_path))
    for name in ("series", "rt", "pr", "images"):
        (tmp_path / name).mkdir()
    ct = synth.ct_series(tmp_path / "series")
    synth.save(synth.rtstruct_for(ct, {"Organ": [(0, ORGAN)]}), tmp_path / "rt" / "rt.dcm")
    synth.save(synth.gsps_for(ct), tmp_path / "pr" / "pr.dcm")
    others = synth.ct_series(tmp_path / "images", frame_of_reference=ct[0].FrameOfReferenceUID)
    assert find_annotation_files([str(tmp_path / "series")]) == []
    assert [os.path.basename(p) for p in find_annotation_files([str(tmp_path / "rt")])] == ["rt.dcm"]
    layers = layers_for_folder(series_info(refs_of(ct)), str(tmp_path / "series"))
    assert sorted(layer.format for layer in layers) == ["PR", "RTSTRUCT"]
    # The other series shares the frame of reference: the structure set applies, the GSPS does not.
    layers = layers_for_folder(series_info(refs_of(others)), str(tmp_path / "images"))
    assert [layer.format for layer in layers] == ["RTSTRUCT"]


# --- which images an annotation is for ---


def test_needed_series_reads_every_format(tmp_path, ct):
    from dicom_viewer import folders
    from dicom_viewer.annotations import needed_series

    masks = np.zeros((3, 8, 8, 2), dtype=bool)
    masks[1, 2:6, 2:6, 0] = True
    expected = {ct[0].SeriesInstanceUID}
    objects = {
        "seg.dcm": synth.seg_for(ct, masks),
        "rt.dcm": synth.rtstruct_for(ct, {"Organ": [(1, ORGAN)]}),
        "pr.dcm": synth.gsps_for(ct),
        "sr.dcm": synth.sr_for(ct),
    }
    (tmp_path / "objects").mkdir()
    for name, ds in objects.items():
        assert needed_series(synth.save(ds, tmp_path / "objects" / name)) == expected, name
    assert needed_series(str(tmp_path / "ct0.dcm")) == set()  # an image names nothing
    assert needed_series(str(tmp_path / "nope.dcm")) == set()

    # A folder of annotations only says which series it needs; one with images says its own.
    (tmp_path / "ann").mkdir()
    synth.save(objects["seg.dcm"], tmp_path / "ann" / "seg.dcm")
    summary = folders.summarize_folder(str(tmp_path / "ann"))
    assert (summary.dicom_count, summary.annotation_count) == (0, 1)
    assert summary.series_uid == "" and summary.references == sorted(expected)
    images = folders.summarize_folder(str(tmp_path))
    assert images.dicom_count == 3 and images.series_uid == ct[0].SeriesInstanceUID
    assert images.references == []
    assert folders.present_series(str(tmp_path)) == {}  # "ann" and "objects" hold no images
    (tmp_path / "images").mkdir()
    synth.ct_series(tmp_path / "images", series_uid="1.2.3.9")
    assert folders.present_series(str(tmp_path)) == {"1.2.3.9": str(tmp_path / "images")}
