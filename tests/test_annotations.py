"""The annotation interface: geometry, drawing and the registry, driven by a fake adapter.

Runs without Reflex and without highdicom (one cross-check skips when it is
missing). Synthetic DICOM files are written with pydicom into tmp_path.
"""

from __future__ import annotations

import numpy as np
import pydicom
import pytest
from PIL import Image
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from dicom_viewer.annotations import (
    Adapter,
    BaseLayer,
    Item,
    Label,
    Layer,
    LayerError,
    Mask,
    SeriesInfo,
    Shape,
    SliceOverlay,
    SliceRef,
    adapter_for,
    cielab_to_rgb,
    compose,
    default_color,
    dicom_cielab_to_rgb,
    find_annotation_files,
    find_slice,
    find_slice_for_point,
    layers_for_folder,
    load_layers,
    overlay_from_labels,
    plane_distance,
    register,
    render,
    same_grid,
    same_plane,
    series_info,
    slice_ref,
    sop_class_of,
    to_patient,
    to_pixels,
    unregister,
)

CT_IMAGE = "1.2.840.10008.5.1.4.1.1.2"
FAKE_SOP_CLASS = "1.2.826.0.1.3680043.8.498.999999"  # pretend annotation object
FRAME_OF_REFERENCE = "1.2.826.0.1.3680043.8.498.1"
UID = {  # valid UIDs, so pydicom does not warn about the UI value representation
    "A": "1.2.3.1",
    "B": "1.2.3.2",
    "C": "1.2.3.3",
    "SA": "1.2.3.11",
    "SB": "1.2.3.12",
    "SC": "1.2.3.13",
    "SC2": "1.2.3.14",
    "img": "1.2.3.100",
    "ann": "1.2.3.101",
}


def write_dicom(
    path,
    *,
    series_uid: str,
    sop_class: str = CT_IMAGE,
    position=(0.0, 0.0, 0.0),
    orientation=(1, 0, 0, 0, 1, 0),
    spacing=(1.0, 1.0),
    rows: int = 4,
    columns: int = 6,
    frame_of_reference: str = FRAME_OF_REFERENCE,
    references: str | None = None,
    description: str = "",
) -> Dataset:
    """A header-only DICOM file. `references` makes it a fake annotation of that series."""
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = sop_class
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.SeriesInstanceUID = series_uid
    ds.StudyInstanceUID = "1.2.3"
    ds.FrameOfReferenceUID = frame_of_reference
    ds.Modality = "CT"
    ds.SeriesDescription = description
    ds.Rows, ds.Columns = rows, columns
    ds.ImagePositionPatient = [float(x) for x in position]
    ds.ImageOrientationPatient = [float(x) for x in orientation]
    ds.PixelSpacing = [float(x) for x in spacing]
    if references is not None:
        referenced = Dataset()
        referenced.SeriesInstanceUID = references
        ds.ReferencedSeriesSequence = [referenced]
    ds.save_as(str(path), enforce_file_format=True)
    return ds


def ref_at(z: float, *, uid: str = "", frame_of_reference: str = FRAME_OF_REFERENCE, **kw) -> SliceRef:
    fields = dict(
        sop_instance_uid=uid or f"slice-{z}",
        series_uid="series",
        rows=4,
        columns=6,
        frame_of_reference_uid=frame_of_reference,
        position=(0.0, 0.0, float(z)),
        orientation=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0),
        pixel_spacing=(1.0, 1.0),
    )
    fields.update(kw)
    return SliceRef(**fields)


# --- a fake adapter: an "annotation" that marks the first two pixels of row 0 ---


class FakeLayer(BaseLayer):
    supports_volume = True

    def __init__(self, path: str, ds: Dataset) -> None:
        self.referenced_series = str(ds.ReferencedSeriesSequence[0].SeriesInstanceUID)
        super().__init__(
            layer_id=str(ds.SOPInstanceUID),
            format="FAKE",
            name="Fake annotation",
            source_path=path,
            items=[
                Item("a", "Region A", (255, 0, 0), "mask"),
                Item("b", "Line B", (0, 0, 255), "contour"),
            ],
        )

    def applies_to(self, series: SeriesInfo) -> bool:
        return series.series_uid == self.referenced_series

    def overlay(self, ref: SliceRef, item_ids=None) -> SliceOverlay:
        out = SliceOverlay()
        if ref.series_uid != self.referenced_series:
            return out
        wanted = {item.id for item in self.selected(item_ids)}
        if "a" in wanted:
            pixels = np.zeros(ref.shape, dtype=bool)
            pixels[0, :2] = True
            out.masks.append(Mask("a", pixels))
        if "b" in wanted:
            out.shapes.append(Shape("b", "polyline", [[0.5, 0.5], [3.5, 0.5]]))
        return out

    def label_volume(self, refs):
        labels = np.zeros((len(refs), *refs[0].shape), dtype=np.uint8)
        labels[:, 0, :2] = 1
        return labels


class FakeAdapter:
    name = "fake"
    sop_class_uids = frozenset({FAKE_SOP_CLASS})

    def load(self, path: str) -> Layer:
        ds = pydicom.dcmread(path, stop_before_pixels=True)
        if ds.get("SeriesDescription") == "broken":
            raise LayerError("broken on purpose")
        return FakeLayer(path, ds)


@pytest.fixture
def fake_adapter():
    adapter = register(FakeAdapter())
    yield adapter
    unregister(adapter)


# --- model ---


def test_fake_layer_and_adapter_satisfy_the_protocols(tmp_path):
    ds = write_dicom(tmp_path / "ann.dcm", series_uid=UID["ann"], sop_class=FAKE_SOP_CLASS, references=UID["img"])
    layer = FakeLayer(str(tmp_path / "ann.dcm"), ds)
    assert isinstance(layer, Layer)
    assert isinstance(FakeAdapter(), Adapter)
    assert layer.selected(None) == layer.items
    assert [i.id for i in layer.selected(["b", "zzz"])] == ["b"]


def test_primitives_validate_their_arrays():
    with pytest.raises(ValueError):
        Mask("a", np.zeros((2, 2, 2)))
    with pytest.raises(ValueError):
        Shape("a", "polygon", [[0, 0], [1, 1]])
    with pytest.raises(ValueError):
        Shape("a", "triangle", [[0, 0], [1, 1], [2, 2]])
    with pytest.raises(ValueError):
        Shape("a", "polyline", [[0, 0, 0], [1, 1, 1]])
    assert Shape("a", "polygon", [[0, 0], [1, 1], [2, 0]]).closed is True
    assert Mask("a", [[1, 0]]).pixels.dtype == bool
    overlay = SliceOverlay(masks=[Mask("a", [[1]])]).extend(SliceOverlay(labels=[Label("b", "x", (0, 0))]))
    assert overlay.item_ids() == {"a", "b"}
    assert SliceOverlay().is_empty()


def test_base_layer_rejects_duplicate_item_ids():
    with pytest.raises(ValueError):
        BaseLayer(layer_id="x", format="F", name="n", source_path="p",
                  items=[Item("a", "A", (0, 0, 0), "mask"), Item("a", "A2", (0, 0, 0), "mask")])


# --- geometry ---


def test_slice_ref_reads_the_header(tmp_path):
    write_dicom(tmp_path / "img.dcm", series_uid=UID["img"], position=(1, 2, 3), spacing=(0.5, 0.7))
    ref = slice_ref(pydicom.dcmread(tmp_path / "img.dcm", stop_before_pixels=True))
    assert ref.has_geometry and ref.shape == (4, 6) and ref.series_uid == UID["img"]
    assert ref.position == (1.0, 2.0, 3.0) and ref.pixel_spacing == (0.5, 0.7)
    assert ref.frame_of_reference_uid == FRAME_OF_REFERENCE
    info = series_info([ref])
    assert info.series_uid == UID["img"] and ref.sop_instance_uid in info.sop_instance_uids


def test_slice_ref_without_geometry():
    ref = SliceRef("s", "x", 2, 2)
    assert not ref.has_geometry
    with pytest.raises(ValueError):
        to_pixels(ref, [[0, 0, 0]])


def test_image_position_maps_to_the_centre_of_the_first_pixel():
    # Oblique slice with anisotropic spacing: 0.5 mm between rows, 2 mm between columns.
    row_dir = np.array([1.0, 1.0, 0.0]) / np.sqrt(2)
    col_dir = np.array([-1.0, 1.0, 0.0]) / np.sqrt(2)
    ref = ref_at(5.0, orientation=(*row_dir, *col_dir), pixel_spacing=(0.5, 2.0), position=(10.0, 20.0, 5.0))
    origin = np.array(ref.position)
    assert np.allclose(to_pixels(ref, origin), [[0.5, 0.5]])
    point = origin + 2 * 2.0 * row_dir + 3 * 0.5 * col_dir  # two columns right, three rows down
    assert np.allclose(to_pixels(ref, point), [[2.5, 3.5]])
    assert np.allclose(to_patient(ref, [[2.5, 3.5]]), [point])
    assert np.allclose(plane_distance(ref, origin + 1.5 * np.cross(row_dir, col_dir)), [1.5])


def test_to_pixels_agrees_with_highdicom():
    hd = pytest.importorskip("highdicom")

    rng = np.random.default_rng(7)
    row_dir = rng.normal(size=3)
    row_dir /= np.linalg.norm(row_dir)
    other = rng.normal(size=3)
    col_dir = other - other @ row_dir * row_dir
    col_dir /= np.linalg.norm(col_dir)
    ref = ref_at(0.0, orientation=tuple(np.r_[row_dir, col_dir]), pixel_spacing=(0.8, 1.3), position=(3.0, -4.0, 7.5))
    points = rng.normal(scale=20, size=(5, 3)) + np.array(ref.position)
    transformer = hd.spatial.ReferenceToImageTransformer(
        image_position=ref.position, image_orientation=ref.orientation, pixel_spacing=ref.pixel_spacing
    )
    assert np.allclose(to_pixels(ref, points), transformer(points)[:, :2], atol=1e-6)


def test_plane_matching_and_slice_lookup():
    stack = [ref_at(z) for z in (0.0, 1.0, 2.0)]
    assert same_plane(ref_at(1.2), stack[1])
    assert not same_plane(ref_at(1.2), stack[1], tolerance_mm=0.1)
    assert not same_plane(ref_at(1.0), ref_at(1.0, frame_of_reference="other"))
    assert same_grid(ref_at(1.0, uid="x"), stack[1])
    assert not same_grid(ref_at(1.0, pixel_spacing=(2.0, 2.0)), stack[1])
    assert not same_grid(ref_at(1.0, rows=5), stack[1])
    assert find_slice(ref_at(1.2), stack) is stack[1]
    assert find_slice(ref_at(1.6), stack) is stack[2]
    assert find_slice(ref_at(1.5), stack, tolerance_mm=0.3) is None
    assert find_slice(ref_at(1.0, frame_of_reference="other"), stack) is None
    tilted = ref_at(1.0, orientation=(1, 0, 0, 0, 0.9, 0.436))  # about 26 degrees off
    assert find_slice(tilted, stack) is None
    assert find_slice_for_point([3.0, 3.0, 1.9], stack) is stack[2]
    assert find_slice_for_point([3.0, 3.0, 2.9], stack) is None


# --- colours ---


def test_colours():
    assert dicom_cielab_to_rgb((65535, 32896, 32896)) == pytest.approx((255, 255, 255), abs=1)
    assert cielab_to_rgb((53.24, 80.09, 67.20)) == pytest.approx((255, 0, 0), abs=2)
    assert cielab_to_rgb((0, 0, 0)) == (0, 0, 0)
    assert default_color(0) != default_color(1) and default_color(12) == default_color(0)


# --- drawing ---


def gray(rows=4, columns=6, value=100) -> Image.Image:
    return Image.fromarray(np.full((rows, columns), value, dtype=np.uint8), "L")


def test_compose_blends_masks_with_opacity():
    pixels = np.zeros((4, 6), dtype=bool)
    pixels[0, :2] = True
    items = [Item("a", "A", (255, 0, 0), "mask")]
    solid = np.asarray(compose(gray(), SliceOverlay(masks=[Mask("a", pixels)]), items, opacity=1.0))
    assert tuple(solid[0, 0]) == (255, 0, 0) and tuple(solid[0, 1]) == (255, 0, 0)
    assert tuple(solid[0, 2]) == (100, 100, 100) and tuple(solid[1, 0]) == (100, 100, 100)
    half = np.asarray(compose(gray(), SliceOverlay(masks=[Mask("a", pixels)]), items, opacity=0.5))
    assert tuple(half[0, 0]) == pytest.approx((178, 50, 50), abs=1)


def test_compose_draws_shapes_at_pixel_centres():
    items = {"b": Item("b", "B", (0, 0, 255), "contour")}
    line = Shape("b", "polyline", [[0.5, 0.5], [3.5, 0.5]])  # centres of (row 0, cols 0..3)
    out = np.asarray(compose(gray(), SliceOverlay(shapes=[line]), items, line_width=1))
    assert all(tuple(out[0, c]) == (0, 0, 255) for c in range(4))
    assert tuple(out[0, 4]) == (100, 100, 100) and tuple(out[1, 0]) == (100, 100, 100)
    dot = Shape("b", "point", [[2.5, 2.5]])
    out = np.asarray(compose(gray(8, 8), SliceOverlay(shapes=[dot]), items))
    assert tuple(out[2, 2]) == (0, 0, 255)
    for kind, pts in (("circle", [[4, 4], [4, 7]]), ("ellipse", [[1, 4], [7, 4], [4, 3], [4, 5]]),
                      ("polygon", [[1, 1], [6, 1], [6, 6]])):
        out = np.asarray(compose(gray(9, 9), SliceOverlay(shapes=[Shape("b", kind, pts)]), items))
        assert (out[..., 2] == 255).any(), kind


def test_compose_skips_hidden_and_unknown_items_and_checks_mask_shape():
    items = [Item("a", "A", (255, 0, 0), "mask")]
    mask = Mask("a", np.ones((4, 6), dtype=bool))
    unchanged = np.asarray(compose(gray(), SliceOverlay(masks=[mask]), items, hidden=["a"]))
    assert (unchanged == 100).all()
    unknown = np.asarray(compose(gray(), SliceOverlay(masks=[Mask("zzz", np.ones((4, 6), bool))]), items))
    assert (unknown == 100).all()
    with pytest.raises(ValueError):
        compose(gray(), SliceOverlay(masks=[Mask("a", np.ones((3, 3), bool))]), items)


def test_compose_draws_labels():
    items = [Item("m", "M", (255, 255, 0), "measurement")]
    out = np.asarray(compose(gray(40, 80, 0), SliceOverlay(labels=[Label("m", "12.3 mm", (2, 2))]), items))
    assert (out[..., 0] == 255).any()
    plain = np.asarray(compose(gray(40, 80, 0), SliceOverlay(labels=[Label("m", "12.3 mm", (2, 2))]), items, show_labels=False))
    assert (plain == 0).all()


def test_overlay_from_labels_splits_items():
    items = [Item("a", "A", (255, 0, 0), "mask"), Item("b", "B", (0, 255, 0), "mask")]
    overlay = overlay_from_labels(np.array([[0, 1], [2, 2]]), items)
    assert [m.item_id for m in overlay.masks] == ["a", "b"]
    assert overlay.masks[0].pixels.tolist() == [[False, True], [False, False]]
    assert overlay.masks[1].pixels.tolist() == [[False, False], [True, True]]
    assert overlay_from_labels(np.array([[0, 5]]), items).is_empty()  # label without an item


def test_render_honours_visibility(tmp_path):
    ds = write_dicom(tmp_path / "ann.dcm", series_uid=UID["ann"], sop_class=FAKE_SOP_CLASS, references=UID["img"])
    layer = FakeLayer(str(tmp_path / "ann.dcm"), ds)
    ref = ref_at(0.0, series_uid=UID["img"])
    everything = np.asarray(render(gray(), ref, [layer], opacity=1.0, line_width=1))
    assert tuple(everything[0, 0]) == (0, 0, 255)  # the line is drawn over the mask
    assert tuple(everything[0, 3]) == (0, 0, 255)
    only_mask = np.asarray(render(gray(), ref, [layer], visible={layer.id: {"a"}}, opacity=1.0))
    assert tuple(only_mask[0, 0]) == (255, 0, 0) and tuple(only_mask[0, 3]) == (100, 100, 100)
    nothing = np.asarray(render(gray(), ref, [layer], visible={layer.id: set()}))
    assert (nothing == 100).all()
    labels = layer.label_volume([ref, ref])
    assert labels.shape == (2, 4, 6) and labels[1, 0, 1] == 1
    cut = np.asarray(compose(gray(), overlay_from_labels(labels[0], layer.items), layer.items, opacity=1.0))
    assert tuple(cut[0, 0]) == (255, 0, 0)


# --- registry ---


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A collection-style tree: one folder per series, plus a flat mixed folder."""
    monkeypatch.setenv("PUBLIC_DICOM_DIR", str(tmp_path))
    for name in ("series_a", "series_b", "seg_a", "seg_b", "mixed", "empty"):
        (tmp_path / name).mkdir()
    for z in (0, 1):
        write_dicom(tmp_path / "series_a" / f"{z}.dcm", series_uid=UID["A"], position=(0, 0, z))
    write_dicom(tmp_path / "series_b" / "0.dcm", series_uid=UID["B"])
    write_dicom(tmp_path / "seg_a" / "seg.dcm", series_uid=UID["SA"], sop_class=FAKE_SOP_CLASS, references=UID["A"])
    write_dicom(tmp_path / "seg_b" / "seg.dcm", series_uid=UID["SB"], sop_class=FAKE_SOP_CLASS, references=UID["B"])
    write_dicom(tmp_path / "mixed" / "a_image.dcm", series_uid=UID["C"])
    write_dicom(tmp_path / "mixed" / "b_annotation.dcm", series_uid=UID["SC"], sop_class=FAKE_SOP_CLASS, references=UID["C"])
    write_dicom(tmp_path / "mixed" / "c_broken.dcm", series_uid=UID["SC2"], sop_class=FAKE_SOP_CLASS,
                references=UID["C"], description="broken")
    (tmp_path / "mixed" / "notes.txt").write_text("not dicom")
    return tmp_path


def test_sop_class_and_adapter_lookup(library, fake_adapter):
    assert sop_class_of(str(library / "series_a" / "0.dcm")) == CT_IMAGE
    assert sop_class_of(str(library / "mixed" / "notes.txt")) == ""
    assert sop_class_of(str(library / "missing.dcm")) == ""
    assert adapter_for(FAKE_SOP_CLASS) is fake_adapter
    assert adapter_for(CT_IMAGE) is None
    unregister(fake_adapter)
    assert adapter_for(FAKE_SOP_CLASS) is None
    register(fake_adapter)


def test_register_rejects_an_adapter_without_sop_classes():
    class Empty:
        name = "empty"
        sop_class_uids = frozenset()

        def load(self, path):
            raise NotImplementedError

    with pytest.raises(ValueError):
        register(Empty())


def test_find_annotation_files_thorough_and_cheap(library, fake_adapter):
    mixed = str(library / "mixed")
    assert [p.rsplit("/", 1)[1] for p in find_annotation_files([mixed])] == ["b_annotation.dcm", "c_broken.dcm"]
    assert find_annotation_files([mixed], thorough=False) == []  # first file is an image
    assert find_annotation_files([str(library / "seg_a")], thorough=False) == [str(library / "seg_a" / "seg.dcm")]
    assert find_annotation_files([str(library / "empty"), str(library / "nowhere")]) == []


def test_load_layers_keeps_only_matching_layers_and_reports_failures(library, fake_adapter):
    paths = find_annotation_files([str(library / "mixed")])
    errors = []
    layers = load_layers(SeriesInfo(series_uid=UID["C"]), paths, errors)
    assert [layer.format for layer in layers] == ["FAKE"]
    assert errors == [(str(library / "mixed" / "c_broken.dcm"), "broken on purpose")]
    assert load_layers(SeriesInfo(series_uid="other"), paths) == []


def test_layers_for_folder_scans_siblings_inside_the_root(library, fake_adapter, monkeypatch):
    series_a = SeriesInfo(series_uid=UID["A"])
    layers = layers_for_folder(series_a, str(library / "series_a"))
    assert [layer.source_path for layer in layers] == [str(library / "seg_a" / "seg.dcm")]
    assert layers_for_folder(series_a, str(library / "series_a"), siblings=False) == []
    # Outside the viewer's root the siblings are not touched.
    monkeypatch.setenv("PUBLIC_DICOM_DIR", str(library / "series_a"))
    assert layers_for_folder(series_a, str(library / "series_a")) == []


def test_folder_rows_tell_which_series_are_missing():
    from dicom_viewer import folders
    from dicom_viewer.states.browser_state import _present, _rows

    summaries = {
        "/x/ct": folders.FolderSummary(dicom_count=3, label="CT", series_uid="1.2.3"),
        "/x/seg": folders.FolderSummary(annotation_count=1, label="SEG", references=["1.2.3"]),
        "/x/rt": folders.FolderSummary(annotation_count=1, label="RTSTRUCT", references=["1.2.3", "9.9.9"], study_uid="1.2"),
    }
    checking = _rows("/x", ["ct", "seg", "rt", "new"], summaries)
    assert [row.missing for row in checking] == ["", "", "", ""]  # not known yet
    assert checking[3].checked is False
    assert _present(summaries) == {"1.2.3": "/x/ct"}
    rows = _rows("/x", ["ct", "seg", "rt"], summaries, _present(summaries))
    assert rows[0].series_uid == "1.2.3" and rows[0].missing == "" and rows[0].opens == ""
    assert rows[1].missing == "" and rows[1].missing_count == 0 and rows[1].opens == "/x/ct"
    assert rows[2].missing == "9.9.9" and rows[2].missing_count == 1 and rows[2].opens == "/x/ct"
    assert rows[2].study == "1.2" and rows[1].study == ""  # only rows that need series pass it on
    assert _present(summaries, "7.7.7", "/x") == {"1.2.3": "/x/ct", "7.7.7": "/x"}
    assert _present(summaries, "1.2.3", "/x") == {"1.2.3": "/x/ct"}  # a folder's own images win nothing
