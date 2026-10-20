# Annotation display interface

How DICOM annotations (segmentations, structure sets, measurements, presentation
states) reach the viewer's screen through one interface, whatever library reads
them. The code is the package `dicom_viewer/annotations/`; this document is its
contract.

Read this before writing an adapter for a new DICOM object type or wiring
annotation display into the UI.

## 1. The idea in one paragraph

Every annotation format encodes something different (bit masks, millimetre
polylines, pixel graphics, a coded tree of measurements), but the viewer's
question is always the same: *for the slice I am about to draw, what goes on
top of it?* So the interface sits exactly there. An **adapter** reads one kind
of DICOM object into a **layer**. A layer lists its toggleable **items** and,
for any **slice** the viewer shows, answers with a **slice overlay**: raster
masks and vector shapes already in that slice's pixel grid. The UI and the
renderer only ever see overlays and items. Which library produced them
(highdicom, pydicom, something else) is the adapter's private business.

```
 DICOM objects            adapters (one per SOP class family)        the viewer
 ──────────────           ─────────────────────────────────         ───────────────────
 SEG      ───────────▶    seg.py     (highdicom)      ──┐
 SR       ───────────▶    sr.py      (highdicom)      ──┤           Layer.items ──▶ layer panel
 RTSTRUCT ───────────▶    rtstruct.py (pydicom + geometry) ─┼─▶ Layer ──┤
 PR/GSPS  ───────────▶    gsps.py    (pydicom)        ──┘           Layer.overlay(slice) ──▶ compose() ──▶ PNG
```

## 2. Vocabulary

| Name | Class | Meaning |
|---|---|---|
| Series | `SeriesInfo` | The image series the viewer has open: its UID, frame of reference, study, and the SOP instance UIDs of its images. What a layer matches itself against. |
| Slice | `SliceRef` | One image (or one frame of a multi-frame image) about to be drawn: UIDs, size and geometry (ImagePositionPatient, ImageOrientationPatient, PixelSpacing). |
| Adapter | `Adapter` (protocol) | Reads the DICOM objects of some SOP classes. `load(path)` returns a layer. |
| Layer | `Layer` (protocol), `BaseLayer` | One loaded object. Fixed attributes (`id`, `format`, `name`, `source_path`, `items`, `notes`, `supports_volume`) and three methods: `applies_to`, `overlay`, `label_volume`. |
| Item | `Item` | One toggleable thing in a layer: a segment, an ROI, a group of GSPS graphics, an SR finding. Has an id (unique within the layer), a name, a default colour and a kind. |
| Overlay | `SliceOverlay` | Everything to draw on one slice: lists of `Mask`, `Shape` and `Label`. Each primitive names the item it belongs to. |

## 3. The pixel coordinate convention

All pixel coordinates in the interface are `(column, row)` floats in the
**corner convention**: the top-left corner of the top-left pixel is `(0, 0)`,
so the centre of that pixel is `(0.5, 0.5)` and the bottom-right corner of the
image is `(columns, rows)`.

```
 (0,0) ┌─────┬─────┬─────┐
       │ px  │ px  │ px  │   centre of pixel [row 0, col 0] = (0.5, 0.5)
       │(0,0)│(0,1)│(0,2)│   centre of pixel [row r, col c] = (c + 0.5, r + 0.5)
       ├─────┼─────┼─────┤
       │(1,0)│(1,1)│(1,2)│   mask[r, c] covers the square [c, c+1) x [r, r+1)
       └─────┴─────┴─────┘ (3,2) = (columns, rows)
```

This is not an arbitrary choice: DICOM GSPS graphics in `PIXEL` units, SR
`SCOORD` graphic data and highdicom's spatial transformers all use it, and
numpy mask indices line up with it. ImagePositionPatient is the centre of the
first pixel, so `to_pixels(ref, ImagePositionPatient)` is `(0.5, 0.5)`.

The one place the convention is converted is `compose()`, because Pillow's
`ImageDraw` addresses pixel centres with integers; it subtracts 0.5 once.

Patient coordinates are DICOM LPS millimetres throughout.

## 4. The contract

### 4.1 `Layer`

```python
class Layer(Protocol):
    id: str                 # unique among loaded layers (SOPInstanceUID is a good choice)
    format: str             # "SEG", "SR", "RTSTRUCT", "PR", ... shown as a badge
    name: str               # e.g. "Segmentation · Kidney, Mass"
    source_path: str
    items: Sequence[Item]
    notes: Sequence[str]    # things the user should know ("drawn only on matching slices")
    supports_volume: bool   # can label_volume() answer?

    def applies_to(self, series: SeriesInfo) -> bool: ...
    def overlay(self, ref: SliceRef, item_ids: Iterable[str] | None = None) -> SliceOverlay: ...
    def label_volume(self, refs: Sequence[SliceRef]) -> np.ndarray | None: ...
```

- `applies_to` decides whether the layer belongs to the open series. Match on
  what the object references (ReferencedSeriesSequence, SR evidence sequences,
  RTSTRUCT's RTReferencedSeriesSequence, GSPS's ReferencedImageSequence) and
  fall back to the FrameOfReferenceUID. Keep it cheap: it runs for every
  annotation file found.
- `overlay(ref, item_ids)` returns what to draw on `ref`, restricted to
  `item_ids` when given (`None` means all items; unknown ids are ignored). It
  returns an **empty** overlay when nothing lies on that slice. Primitives
  must be in `ref`'s pixel grid; masks must have shape `(ref.rows,
  ref.columns)`. Draw order is list order: later masks tint over earlier ones,
  shapes go over masks, labels over shapes.
- `label_volume(refs)` serves the three-plane (MPR) view. `refs` are the
  slices **in stack order** (the same order the viewer stacked the pixels).
  The result is an integer array `(len(refs), rows, columns)` where `0` is
  background and value `v` is `items[v - 1]`. Where items overlap, the later
  item wins, so put smaller structures after the ones that contain them
  (tumour after organ). Return `None` when the layer cannot be shown as a
  volume; then `supports_volume` must be `False` and the UI greys the layer
  out in 3 views.
- Attributes never change after loading. `overlay` is called once per slice
  change from a worker thread; make it cheap after the first call by caching
  decoded frames or rasterised contours inside the layer. One layer object
  may be queried from several threads in sequence; guard any lazy cache with
  a lock if it is filled in more than one step.

`BaseLayer` is an optional base class that stores the attributes, checks that
item ids are unique, offers `item(id)` and `selected(item_ids)`, and defaults
`label_volume` to `None`.

### 4.2 `Adapter`

```python
class Adapter(Protocol):
    name: str
    sop_class_uids: frozenset[str]     # the SOP classes it reads
    def load(self, path: str) -> Layer: ...
```

`load` raises `LayerError("message for the user")` when the object cannot be
read; any other exception is logged with its traceback and reported as the
adapter's name plus the error. Either way the other files still load.

Register an adapter instance once at import time:

```python
from dicom_viewer.annotations import register
register(SegAdapter())
```

Each SOP class has one adapter; registering another for the same class
replaces it.

### 4.3 Primitives

| Primitive | Fields | Notes |
|---|---|---|
| `Mask(item_id, pixels)` | `pixels`: bool array `(rows, columns)` | Converted to bool on construction. |
| `Shape(item_id, kind, points, closed=False)` | `points`: float array `(n, 2)` of `(column, row)` | Kinds and point layouts follow DICOM graphic objects: `polyline` 2+ points (`closed` joins the ends); `polygon` 3+ points, always closed; `point` 1 point; `circle` 2 points, centre then any point on the circumference; `ellipse` 4 points, both ends of the major axis then both ends of the minor axis. |
| `Label(item_id, text, anchor)` | `anchor`: `(column, row)` | Text drawn at the anchor with a dark halo. |
| `Item(id, name, color, kind, description="")` | `color`: `(r, g, b)` 0-255; `kind`: `mask`, `contour`, `graphic`, `measurement` | The colour is the adapter's default (ROIDisplayColor, CIELab of a segment, or the palette); the UI may override it. |

`SliceOverlay.extend(other)` appends another overlay's primitives;
`is_empty()` and `item_ids()` are there for the renderer.

## 5. Geometry helpers (`geometry.py`)

| Function | What it does |
|---|---|
| `slice_ref(ds, frame_number=None)` | A `SliceRef` from a pydicom dataset; a header read (`stop_before_pixels=True`) is enough. For enhanced multi-frame images give the 1-based frame number and the geometry comes from that frame's functional groups. |
| `series_info(refs, study_uid="")` | The `SeriesInfo` of the open slices. |
| `basis(ref)` | `(origin, row_dir, col_dir, normal)` unit vectors in mm. `row_dir` is where the column index grows (first IOP triplet), `col_dir` where the row index grows. |
| `to_pixels(ref, points_mm)` | `(n, 3)` mm → `(n, 2)` pixel coordinates on the slice (points off the plane are projected). |
| `to_patient(ref, points_px)` | The inverse. |
| `plane_distance(ref, points_mm)` | Signed distance of points from the slice plane, along its normal. |
| `same_plane(a, b, tolerance_mm=0.5, angle_tolerance_deg=1.0)` | Same frame of reference, parallel, and within tolerance. |
| `same_grid(a, b, ...)` | Same plane, same origin, spacing, orientation and size: a mask of one fits the other unchanged. |
| `find_slice(target, candidates, ...)` | The candidate nearest to `target`'s plane within tolerance, else `None`. |
| `find_slice_for_point(point_mm, candidates, tolerance_mm=0.5)` | The candidate whose plane passes nearest a patient point; for contours and 3D coordinates that do not say which image they lie on. |

Matching strategy for adapters: match by UID first (ReferencedSOPInstanceUID,
ContourImageSequence, per-frame source image references), and use the
geometry helpers only as the fallback. A sensible tolerance for through-plane
matching is half the slice spacing.

## 6. Colours (`colors.py`)

- `dicom_cielab_to_rgb(values)`: RecommendedDisplayCIELabValue (three uint16)
  to RGB, for SEG segments and microscopy annotations.
- `cielab_to_rgb((L, a, b))`: the float form.
- `default_color(index)`: a twelve-colour palette for objects that carry no
  colour (SR, GSPS), by the item's position in its layer.

## 7. Drawing (`compose.py`)

```python
compose(image, overlay, items, *, opacity=0.4, line_width=2,
        show_labels=True, label_size=12, hidden=()) -> Image
render(image, ref, layers, *, visible=None, **compose_options) -> Image
overlay_from_labels(labels_2d, items) -> SliceOverlay
```

- `compose` takes a PIL image (any mode; the viewer's windowed grayscale) and
  returns a new RGB image with the overlay drawn on it. `items` supplies the
  colour for each primitive's item id; primitives with an unknown or `hidden`
  item are skipped. A mask whose shape is not the picture's raises
  `ValueError`, because that is a bug in the adapter, not a display choice.
- `render` is the single-view entry point: it calls each layer's `overlay`
  for the slice and composes the results in layer order. `visible` maps a
  layer id to the item ids to show; a layer missing from it (or mapped to
  `None`) shows everything, one mapped to an empty set is hidden.
- For the three-plane view, cut the layer's `label_volume` exactly as the
  pixel volume is cut (same transpose and flips), then
  `compose(cut_image, overlay_from_labels(cut_labels, layer.items), layer.items)`.

Compose runs at native slice resolution before PNG encoding, so the browser
scales annotations together with the image and no front-end code is involved.

## 8. Discovery and loading (`registry.py`)

| Function | What it does |
|---|---|
| `register(adapter)` / `unregister(adapter)` / `adapters()` / `adapter_for(sop_class_uid)` | The registry. |
| `sop_class_of(path)` | The file's SOPClassUID, `""` for non-DICOM. |
| `is_annotation_file(path)` | Whether a registered adapter reads it. |
| `find_annotation_files(folders, thorough=True)` | Annotation files directly inside each folder. `thorough=False` reads only the first file of each folder and skips the folder when that file is an image: cheap for the one-series-per-folder layout collections use. |
| `load_layers(series, paths, errors=None)` | Loads each file with its adapter and keeps the layers whose `applies_to(series)` is true. Failures are logged and, if `errors` is given, appended as `(path, message)`. |
| `find_layer_files(folder, siblings=True)` | The annotation files for the series in `folder`: the folder itself is scanned thoroughly, its sibling folders cheaply, and only while the parent folder stays inside the viewer's DICOM root (`folders.dicom_root()`). |
| `layers_for_folder(series, folder, siblings=True, errors=None)` | `find_layer_files` followed by `load_layers`. |

The rule that makes the folder scan simple: **a file no adapter claims is not
an annotation**, so the viewer can keep treating it as an image without
knowing every image SOP class (`folders.image_files` applies it). Overlay
planes (group 60xx) embedded in image files are therefore outside this
registry; see section 11.

## 9. The built-in adapters

| Module | Formats | Reads with | Items | 3 views |
|---|---|---|---|---|
| `seg.py` | Segmentation, Label Map Segmentation | highdicom | one per segment, colour from `RecommendedDisplayCIELabValue` | yes |
| `rtstruct.py` | RT Structure Set | pydicom + `geometry` | one per ROI, colour from `ROIDisplayColor` | yes (contours filled) |
| `gsps.py` | Grayscale, Color, Pseudo-Color, Blending and XA/XRF presentation states | pydicom | one per graphic layer | no |
| `sr.py` | Enhanced, Comprehensive and Comprehensive 3D SR with TID 1500 measurements | highdicom | one per ROI measurement group, measurements as text | no |

`dicom_viewer/annotations/builtin.py` imports the four modules, which
registers them; the viewer state imports `builtin`, and `folders.image_files`
does too, so the registry is always filled before a folder is read.

highdicom is a project dependency, but the SEG and SR adapters import it only
inside `load()`. Where it is missing (an app container built before it was
added) the viewer still runs, and those files show up in the panel as an
error naming the package.

How each adapter matches slices:

- **SEG**: by source image (`get_pixels_by_source_instance`, with
  `ignore_spatial_locations=True` because dcmqi output does not declare that
  spatial locations are preserved). Images the object does not name, from
  another series in the same frame of reference, get the frames whose plane
  and grid match (`same_grid`). Masks are cached per slice (64 slices).
- **RTSTRUCT**: by `ContourImageSequence`, else by plane distance with a
  tolerance of half the gap between contour planes (0.5 mm when there is
  one plane). Contours of one ROI on one slice are unioned when filled, so
  holes drawn as inner contours are filled too.
- **GSPS**: by `ReferencedImageSequence` of each annotation, else every
  image the state references. `DISPLAY` units go through the displayed area
  selected for the image; `MATRIX` units are skipped.
- **SR**: 2D regions by the image the region names; 3D regions when all
  their points lie within 1 mm of the slice plane. Reports without
  measurement groups load as empty layers with a note.

### Writing another adapter

1. Create `dicom_viewer/annotations/<format>.py`; import whatever library
   you need there (inside `load()` if it is optional).
2. Subclass `BaseLayer`. In `__init__` read what is cheap, build `items`, and
   record what is needed for matching (referenced series, frame of
   reference, SOP instance uids). `support.py` has the shared helpers:
   `read_dataset`, `referenced_series`, `referenced_instances`, `matches`,
   `polygon_mask`.
3. Implement `applies_to`, `overlay` and, if the format can be shown as a
   volume, `label_volume` with `supports_volume = True`.
4. Define an adapter object with `name`, `sop_class_uids` and `load`, and
   register it at the bottom of the module; import the module from
   `builtin.py`.
5. Test it against a real file and a synthetic one (`tests/synth.py` builds
   CT series, SEG, RTSTRUCT, GSPS and SR objects): `isinstance(layer,
   Layer)` must hold, an overlay for a slice the object does not cover must
   be empty, and masks must have the slice's shape.

`gsps.py` is the smallest complete adapter and the best one to copy.

## 10. How the viewer uses it

- **Folder scan** (`folders.py`): `image_files` keeps the DICOM files no
  adapter claims, so a SEG or RTSTRUCT in the series folder is no longer
  treated as a broken slice. `summarize_folder` counts images and
  annotations separately and the folder page shows both.
- **Opening a series** (`DicomViewerState.open_series`): `series.read_slices`
  reads the ordering tags and builds a `SliceRef` per file in one pass. After
  the first picture and the redirect, `_load_layers` runs `find_layer_files`
  in a thread, loads the files it has not seen before with `load_layers`,
  and lists the result in the panel. The scan runs on every open (and on the
  panel's rescan button), so annotations downloaded later show up; only the
  objects already read are reused. Layers hold numpy arrays, so they live in
  a module cache keyed by the series (three series) and the state keeps
  `LayerView` rows: ids, names, colours, notes and visibility.
- **One view** (`_refresh`): `series.render_image` windows the slice,
  `annotations.render` draws the visible layers, `series.encode_png` makes
  the data URL. Toggling a layer, an item, the opacity or the text bumps
  `_layers_version`, which the refresh loop compares like the window.
- **Three views** (`_load_volume`, `_load_labels`, `_refresh_planes`): once
  the pixel volume exists, each layer with `supports_volume` gets its
  `label_volume` for the stacked slices, turned with `volume.reorient` so it
  cuts like the pixels, and cached per (volume, layer). Each plane cut is
  composed with `overlay_from_labels`. Layers without volume support show a
  "1 view only" tag in that mode.
- **Panel** (`components/viewer.py`, `annotations_panel`): opacity slider,
  measurement text switch, one block per layer with its format badge,
  checkbox, items and notes, and the load errors underneath.

## 11. Limits and extension points

- **Same geometry only.** An annotation made on another series in the same
  frame of reference is drawn where its planes and grid coincide with the
  open series' slices; resampling onto a different grid is not done. The
  layer says so in `notes`.
- **Overlap in volumes.** `label_volume` keeps one item per voxel (the
  smaller structure wins); the single view keeps per-item masks and shows
  overlaps faithfully.
- **Overlay planes (60xx)** live inside image files, so the SOP-class
  registry does not see them. Supporting them means an `ImageAdapter` hook
  that receives the series' own files; `pydicom.Dataset.overlay_array(group)`
  does the decoding.
- **Multi-frame images** are supported in `SliceRef` (frame_number) and
  `slice_ref`, but the viewer itself still shows only the first frame.
- **Memory and time.** A label volume is one byte per voxel. SEG frames are
  decoded one slice at a time, so building the labels of a 1000-slice series
  takes a few seconds; RTSTRUCT contours are rasterised per slice.
- **Display hints** (GSPS window, displayed area, flips/rotations) are not
  part of the overlay; add a `hints` attribute to `Layer` when the UI can
  use them.

## 12. Tests

```bash
poetry run pytest
```

- `tests/test_annotations.py`: the model, geometry (with a cross-check
  against highdicom), colours, drawing and the registry, with a fake adapter
  over synthetic files.
- `tests/test_adapters.py`: the four adapters over objects built by
  `tests/synth.py`, loading through the registry in the one-folder-per-series
  layout, and the KiTS sample (`/Users/Shared/DICOM/c4kc_kits`: a dcmqi SEG
  with its CT), skipped when that folder is missing.
