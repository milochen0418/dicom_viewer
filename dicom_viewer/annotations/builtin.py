"""The adapters that ship with the viewer. Import this module once to register them."""

from dicom_viewer.annotations import gsps, rtstruct, seg, sr  # noqa: F401 - registering imports

FORMATS = ("SEG", "SR", "RTSTRUCT", "PR")
