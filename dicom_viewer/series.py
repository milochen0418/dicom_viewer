"""Reading a DICOM series: slice order, the file's own window, PNG rendering.

Plain Python without Reflex, so it can run in a worker thread.
"""

from __future__ import annotations

import base64
import io
import logging
import os

import numpy as np
import pydicom
from PIL import Image
from pydicom.multival import MultiValue

from dicom_viewer.annotations import geometry
from dicom_viewer.annotations.model import SliceRef

# Ordering and placing the slices needs only these tags; skipping the rest
# keeps opening fast.
_ORDER_TAGS = [
    "SOPInstanceUID",
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "FrameOfReferenceUID",
    "ImagePositionPatient",
    "ImageOrientationPatient",
    "PixelSpacing",
    "Rows",
    "Columns",
    "SliceLocation",
    "InstanceNumber",
]
_WINDOW_TAGS = ["WindowCenter", "WindowWidth"]


def slice_position(ds) -> float | None:
    """Compute slice position along the normal direction when possible."""
    pos = ds.get("ImagePositionPatient", None)
    iop = ds.get("ImageOrientationPatient", None)
    if pos is not None and iop is not None:
        try:
            if len(iop) >= 6 and len(pos) >= 3:
                row = np.array([float(x) for x in iop[:3]], dtype=float)
                col = np.array([float(x) for x in iop[3:6]], dtype=float)
                normal = np.cross(row, col)
                if np.linalg.norm(normal) > 0:
                    position = float(np.dot(normal, np.array(pos[:3], dtype=float)))
                    return position
        except Exception:
            pass
    if pos is not None:
        try:
            if len(pos) >= 3:
                return float(pos[2])
        except Exception:
            pass
    slice_location = ds.get("SliceLocation", None)
    if slice_location is not None:
        try:
            return float(slice_location)
        except Exception:
            return None
    return None


def sort_key(ds, file_path: str) -> tuple:
    """Return a stable sort key for DICOM slice ordering."""
    series_uid = str(ds.get("SeriesInstanceUID", ""))
    position = slice_position(ds)
    instance = ds.get("InstanceNumber", None)
    try:
        instance_val = float(instance) if instance is not None else None
    except Exception:
        instance_val = None
    primary = position if position is not None else (
        instance_val if instance_val is not None else float("inf")
    )
    secondary = instance_val if instance_val is not None else float("inf")
    return (series_uid, primary, secondary, os.path.basename(file_path).lower())


def read_slices(paths: list[str]) -> list[tuple[tuple, str, SliceRef]]:
    """(sort key, path, where the slice is) for each readable file; unreadable files are skipped.

    The SliceRef is what the annotation layers are asked about when the
    slice is shown.
    """
    found = []
    for path in paths:
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True, specific_tags=_ORDER_TAGS)
        except Exception as error:
            logging.warning("Skipping invalid DICOM file %s: %s", path, error)
            continue
        found.append((sort_key(ds, path), path, geometry.slice_ref(ds)))
    return found


def read_sort_keys(paths: list[str]) -> list[tuple[tuple, str]]:
    """(sort key, path) for each readable file; unreadable files are skipped."""
    return [(key, path) for key, path, _ in read_slices(paths)]


def file_window(path: str) -> tuple[float, float] | None:
    """The window (center, width) the file suggests, if it has one."""
    try:
        ds = pydicom.dcmread(path, stop_before_pixels=True, specific_tags=_WINDOW_TAGS)
        center, width = ds.get("WindowCenter"), ds.get("WindowWidth")
        if center is None or width is None:
            return None
        # Files may suggest several windows; the first is the default one.
        if isinstance(center, MultiValue):
            center = center[0]
        if isinstance(width, MultiValue):
            width = width[0]
        center, width = float(center), float(width)
    except Exception:
        return None
    return (center, width) if width > 0 else None


def read_slice(path: str):
    """(dataset, pixel array, rescale slope, rescale intercept) of one file."""
    ds = pydicom.dcmread(path)
    pixels = ds.pixel_array
    slope = float(getattr(ds, "RescaleSlope", 1.0))
    intercept = float(getattr(ds, "RescaleIntercept", 0.0))
    return ds, pixels, slope, intercept


def auto_window(
    pixels: np.ndarray, slope: float, intercept: float
) -> tuple[float, float] | None:
    """A window (center, width) worked out from the image's own values.

    It spans the 1st to the 99th percentile, leaving out the lowest value
    when it can, as that is usually the background or the padding around the
    scan. None for colour images, which are shown without windowing, and for
    flat ones.
    """
    if pixels.ndim == 3 and pixels.shape[-1] in (3, 4):
        return None
    # A million samples are plenty, and keep big images quick.
    values = pixels.ravel()[:: max(1, pixels.size // 1_000_000)]
    values = values.astype(float) * slope + intercept
    if not values.size:
        return None
    for sample in (values[values > values.min()], values):
        if sample.size:
            low, high = np.percentile(sample, (1, 99))
            if high > low:
                return float(low + high) / 2, float(high - low)
    low, high = float(values.min()), float(values.max())
    return ((low + high) / 2, high - low) if high > low else None


def image_window(path: str) -> tuple[float, float] | None:
    """auto_window of one file, or None when its pixels cannot be read."""
    try:
        _, pixels, slope, intercept = read_slice(path)
    except Exception as error:
        logging.warning("No window from the pixels of %s: %s", path, error)
        return None
    return auto_window(pixels, slope, intercept)


def render_image(
    pixels: np.ndarray, slope: float, intercept: float, center: float, width: float
) -> Image.Image:
    """Apply windowing and return the picture: "L" for grayscale, "RGB" for colour."""
    pixel_data = pixels
    is_rgb = pixel_data.ndim == 3 and pixel_data.shape[-1] in (3, 4)
    if pixel_data.ndim >= 3 and not is_rgb:
        try:
            pixel_data = pixel_data.reshape((-1,) + pixel_data.shape[-2:])[0]
        except Exception:
            pixel_data = pixel_data[0]
    if is_rgb:
        rgb = pixel_data.astype(float)
        rgb = np.clip(rgb, np.min(rgb), np.max(rgb))
        if np.max(rgb) != np.min(rgb):
            rgb = (rgb - np.min(rgb)) / (np.max(rgb) - np.min(rgb)) * 255.0
        img_uint8 = rgb.astype(np.uint8)
        pil_image = Image.fromarray(img_uint8)
        if pil_image.mode not in ("RGB", "RGBA"):
            pil_image = pil_image.convert("RGB")
        return pil_image
    pixel_data = pixel_data.astype(float)
    hu_image = pixel_data * slope + intercept
    min_val = center - width / 2
    max_val = center + width / 2
    windowed = np.clip(hu_image, min_val, max_val)
    if max_val != min_val:
        windowed = (windowed - min_val) / (max_val - min_val) * 255.0
    else:
        windowed = windowed * 0
    img_uint8 = windowed.astype(np.uint8)
    pil_image = Image.fromarray(img_uint8)
    if pil_image.mode != "L":
        pil_image = pil_image.convert("L")
    return pil_image


def encode_png(image: Image.Image) -> str:
    """A picture as a PNG data URL, what the page's <img> takes."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    b64_string = base64.b64encode(buffer.getvalue()).decode()
    return f"data:image/png;base64,{b64_string}"


def render_png(
    pixels: np.ndarray, slope: float, intercept: float, center: float, width: float
) -> str:
    """Apply windowing and return the image as a PNG data URL."""
    try:
        return encode_png(render_image(pixels, slope, intercept, center, width))
    except Exception as e:
        logging.exception(f"Error processing image: {e}")
        return "/placeholder.svg"
