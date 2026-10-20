"""Synthetic DICOM objects for the adapter tests: a CT series and annotations of it.

Pixel coordinates given to these builders follow the interface's corner
convention; contours are converted to patient millimetres with the geometry
helpers, exactly as a real structure set stores them.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import numpy as np
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence as DicomSequence
from pydicom.sr.coding import Code
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

from dicom_viewer.annotations import geometry

RT_STRUCTURE_SET_STORAGE = "1.2.840.10008.5.1.4.1.1.481.3"
GRAYSCALE_SOFTCOPY_PRESENTATION_STATE = "1.2.840.10008.5.1.4.1.1.11.1"
WHITE_CIELAB = [65535, 32896, 32896]


def new_dataset(sop_class: str, **attributes) -> Dataset:
    """A dataset with file meta, ready to be saved in the DICOM file format."""
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = sop_class
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    for keyword, value in attributes.items():
        setattr(ds, keyword, value)
    return ds


def save(ds: Dataset, path) -> str:
    ds.save_as(str(path), enforce_file_format=True)
    return str(path)


def ct_series(
    folder,
    *,
    count: int = 3,
    rows: int = 8,
    columns: int = 8,
    spacing: Sequence[float] = (1.0, 1.0),
    z0: float = 0.0,
    dz: float = 1.0,
    series_uid: str | None = None,
    study_uid: str | None = None,
    frame_of_reference: str | None = None,
    write: bool = True,
) -> list[Dataset]:
    """Axial CT images with pixel data, saved as <folder>/ct<i>.dcm in slice order."""
    series_uid = series_uid or generate_uid()
    study_uid = study_uid or generate_uid()
    frame_of_reference = frame_of_reference or generate_uid()
    datasets = []
    for index in range(count):
        ds = new_dataset(
            CTImageStorage,
            PatientID="P1",
            PatientName="Test^Patient",
            PatientBirthDate="",
            PatientSex="",
            StudyInstanceUID=study_uid,
            StudyID="1",
            StudyDate="20260101",
            StudyTime="000000",
            AccessionNumber="",
            ReferringPhysicianName="",
            SeriesInstanceUID=series_uid,
            SeriesNumber=1,
            SeriesDescription="Synthetic CT",
            Modality="CT",
            FrameOfReferenceUID=frame_of_reference,
            PositionReferenceIndicator="",
            InstanceNumber=index + 1,
            ImageType=["ORIGINAL", "PRIMARY", "AXIAL"],
            Rows=rows,
            Columns=columns,
            ImagePositionPatient=[0.0, 0.0, float(z0 + index * dz)],
            ImageOrientationPatient=[1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
            PixelSpacing=[float(spacing[0]), float(spacing[1])],
            SliceThickness=float(dz),
            SamplesPerPixel=1,
            PhotometricInterpretation="MONOCHROME2",
            BitsAllocated=16,
            BitsStored=16,
            HighBit=15,
            PixelRepresentation=0,
            RescaleIntercept=0.0,
            RescaleSlope=1.0,
            WindowCenter=50.0,
            WindowWidth=100.0,
        )
        ds.PixelData = np.full((rows, columns), 50, dtype=np.uint16).tobytes()
        if write:
            save(ds, os.path.join(folder, f"ct{index}.dcm"))
        datasets.append(ds)
    return datasets


def _image_reference(ct: Dataset) -> Dataset:
    item = Dataset()
    item.ReferencedSOPClassUID = ct.SOPClassUID
    item.ReferencedSOPInstanceUID = ct.SOPInstanceUID
    return item


def rtstruct_for(
    cts: Sequence[Dataset],
    rois: dict[str, list[tuple[int, Sequence[tuple[float, float]]]]],
    *,
    colors: dict[str, Sequence[int]] | None = None,
    name_images: bool = True,
) -> Dataset:
    """A structure set: `rois` maps a name to (slice index, polygon in pixel coordinates) pairs.

    With `name_images` off the contours carry no ContourImageSequence, so a
    reader must place them by geometry.
    """
    first = cts[0]
    ds = new_dataset(
        RT_STRUCTURE_SET_STORAGE,
        PatientID=first.PatientID,
        PatientName=first.PatientName,
        StudyInstanceUID=first.StudyInstanceUID,
        SeriesInstanceUID=generate_uid(),
        SeriesNumber=2,
        Modality="RTSTRUCT",
        StructureSetLabel="Synthetic set",
        StructureSetDate="20260101",
        StructureSetTime="000000",
    )
    referenced_series = Dataset()
    referenced_series.SeriesInstanceUID = first.SeriesInstanceUID
    referenced_series.ContourImageSequence = DicomSequence([_image_reference(ct) for ct in cts])
    referenced_study = Dataset()
    referenced_study.ReferencedSOPClassUID = "1.2.840.10008.3.1.2.3.1"
    referenced_study.ReferencedSOPInstanceUID = first.StudyInstanceUID
    referenced_study.RTReferencedSeriesSequence = DicomSequence([referenced_series])
    frame = Dataset()
    frame.FrameOfReferenceUID = first.FrameOfReferenceUID
    frame.RTReferencedStudySequence = DicomSequence([referenced_study])
    ds.ReferencedFrameOfReferenceSequence = DicomSequence([frame])
    ds.StructureSetROISequence = DicomSequence()
    ds.ROIContourSequence = DicomSequence()
    for number, (name, contours) in enumerate(rois.items(), start=1):
        roi = Dataset()
        roi.ROINumber = number
        roi.ReferencedFrameOfReferenceUID = first.FrameOfReferenceUID
        roi.ROIName = name
        roi.ROIGenerationAlgorithm = "MANUAL"
        ds.StructureSetROISequence.append(roi)
        roi_contour = Dataset()
        roi_contour.ReferencedROINumber = number
        if colors and name in colors:
            roi_contour.ROIDisplayColor = [int(c) for c in colors[name]]
        roi_contour.ContourSequence = DicomSequence()
        for slice_index, polygon in contours:
            ct = cts[slice_index]
            points_mm = geometry.to_patient(geometry.slice_ref(ct), np.asarray(polygon, dtype=float))
            contour = Dataset()
            contour.ContourGeometricType = "CLOSED_PLANAR"
            contour.NumberOfContourPoints = len(points_mm)
            contour.ContourData = [float(x) for x in points_mm.ravel()]
            if name_images:
                contour.ContourImageSequence = DicomSequence([_image_reference(ct)])
            roi_contour.ContourSequence.append(contour)
        ds.ROIContourSequence.append(roi_contour)
    return ds


def gsps_for(cts: Sequence[Dataset], *, image_index: int = 1) -> Dataset:
    """A presentation state with a "DRAW" layer on one image and a "DISPLAY" layer on all of them."""
    first = cts[0]
    ds = new_dataset(
        GRAYSCALE_SOFTCOPY_PRESENTATION_STATE,
        PatientID=first.PatientID,
        PatientName=first.PatientName,
        StudyInstanceUID=first.StudyInstanceUID,
        SeriesInstanceUID=generate_uid(),
        SeriesNumber=3,
        Modality="PR",
        ContentLabel="SYNTHETIC",
        ContentDescription="Synthetic presentation state",
        PresentationCreationDate="20260101",
        PresentationCreationTime="000000",
        InstanceNumber=1,
    )
    referenced = Dataset()
    referenced.SeriesInstanceUID = first.SeriesInstanceUID
    referenced.ReferencedImageSequence = DicomSequence([_image_reference(ct) for ct in cts])
    ds.ReferencedSeriesSequence = DicomSequence([referenced])

    layer = Dataset()
    layer.GraphicLayer = "DRAW"
    layer.GraphicLayerOrder = 1
    layer.GraphicLayerRecommendedDisplayCIELabValue = WHITE_CIELAB
    ds.GraphicLayerSequence = DicomSequence([layer])

    def graphic(kind: str, points, units: str = "PIXEL") -> Dataset:
        item = Dataset()
        item.GraphicAnnotationUnits = units
        item.GraphicDimensions = 2
        flat = [float(x) for point in points for x in point]
        item.NumberOfGraphicPoints = len(points)
        item.GraphicData = flat
        item.GraphicType = kind
        item.GraphicFilled = "N"
        return item

    draw = Dataset()
    draw.GraphicLayer = "DRAW"
    draw.ReferencedImageSequence = DicomSequence([_image_reference(cts[image_index])])
    draw.GraphicObjectSequence = DicomSequence(
        [
            graphic("POLYLINE", [(1.0, 1.0), (5.0, 1.0), (5.0, 5.0)]),
            graphic("CIRCLE", [(4.0, 4.0), (4.0, 6.0)]),
            graphic("POINT", [(2.0, 6.0)]),
        ]
    )
    text = Dataset()
    text.UnformattedTextValue = "hello"
    text.AnchorPoint = [1.0, 1.0]
    text.AnchorPointAnnotationUnits = "PIXEL"
    text.AnchorPointVisibility = "Y"
    draw.TextObjectSequence = DicomSequence([text])

    display = Dataset()
    display.GraphicLayer = "DISPLAY"
    display.GraphicObjectSequence = DicomSequence(
        [graphic("POLYLINE", [(0.0, 0.0), (1.0, 1.0)], units="DISPLAY")]
    )
    ds.GraphicAnnotationSequence = DicomSequence([draw, display])

    area = Dataset()
    area.DisplayedAreaTopLeftHandCorner = [1, 1]
    area.DisplayedAreaBottomRightHandCorner = [int(first.Columns), int(first.Rows)]
    area.PresentationSizeMode = "SCALE TO FIT"
    ds.DisplayedAreaSelectionSequence = DicomSequence([area])
    return ds


def seg_for(
    cts: Sequence[Dataset],
    masks,
    *,
    labels: Sequence[str] = ("Organ", "Lesion"),
    colors: dict[str, Sequence[float]] | None = None,
) -> Dataset:
    """A binary segmentation of `cts`: `masks` is (slices, rows, columns, segments) bool.

    `colors` maps a label to CIELab (L*, a*, b*). Needs highdicom.
    """
    import highdicom as hd

    anatomy = Code("123037004", "SCT", "Anatomical Structure")
    abnormal = Code("49755003", "SCT", "Morphologically Abnormal Structure")
    descriptions = []
    for number, label in enumerate(labels, start=1):
        color = hd.color.CIELabColor(*colors[label]) if colors and label in colors else None
        descriptions.append(
            hd.seg.SegmentDescription(
                number,
                label,
                anatomy if number == 1 else abnormal,
                Code("64033007", "SCT", "Kidney") if number == 1 else Code("108369006", "SCT", "Neoplasm"),
                "MANUAL",
                display_color=color,
            )
        )
    return hd.seg.Segmentation(
        source_images=list(cts),
        pixel_array=np.asarray(masks, dtype=bool),
        segmentation_type="BINARY",
        segment_descriptions=descriptions,
        series_instance_uid=generate_uid(),
        series_number=4,
        sop_instance_uid=generate_uid(),
        instance_number=1,
        manufacturer="test",
        manufacturer_model_name="test",
        software_versions="0",
        device_serial_number="0",
    )


def sr_for(
    cts: Sequence[Dataset],
    *,
    image_index: int = 1,
    polygon: Sequence[tuple[float, float]] = ((2.0, 2.0), (6.0, 2.0), (6.0, 6.0), (2.0, 6.0), (2.0, 2.0)),
    area: float = 16.0,
) -> Dataset:
    """A TID 1500 measurement report with one planar ROI (a polyline) and its area. Needs highdicom."""
    import highdicom as hd
    from pydicom.sr.codedict import codes

    observer = hd.sr.ObserverContext(
        observer_type=codes.DCM.Person,
        observer_identifying_attributes=hd.sr.PersonObserverIdentifyingAttributes(name="Reader^One"),
    )
    region = hd.sr.ImageRegion(
        graphic_type=hd.sr.GraphicTypeValues.POLYLINE,
        graphic_data=np.asarray(polygon, dtype=float),
        source_image=hd.sr.SourceImageForRegion.from_source_image(cts[image_index]),
    )
    group = hd.sr.PlanarROIMeasurementsAndQualitativeEvaluations(
        tracking_identifier=hd.sr.TrackingIdentifier(uid=generate_uid(), identifier="Lesion 1"),
        referenced_region=region,
        finding_type=codes.SCT.Lesion,
        measurements=[
            hd.sr.Measurement(
                name=Code("42798000", "SCT", "Area"), value=area, unit=codes.UCUM.SquareMillimeter
            )
        ],
    )
    report = hd.sr.MeasurementReport(
        observation_context=hd.sr.ObservationContext(observer_person_context=observer),
        procedure_reported=codes.LN.CTUnspecifiedBodyRegion,
        imaging_measurements=[group],
    )
    return hd.sr.ComprehensiveSR(
        evidence=list(cts),
        content=report[0],
        series_instance_uid=generate_uid(),
        series_number=5,
        sop_instance_uid=generate_uid(),
        instance_number=1,
        manufacturer="test",
    )
