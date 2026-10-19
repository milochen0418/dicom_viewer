import reflex as rx
import asyncio
import os
import logging
import numpy as np

from dicom_viewer import folders, series

# Used when the files suggest no window of their own.
DEFAULT_WINDOW = (40.0, 400.0)
FILE_WINDOW = "From file"

# Files whose headers are read between two progress updates while opening.
_OPEN_BATCH = 16

_SLICE_KEY_STEPS = {
    "ArrowUp": -1,
    "ArrowLeft": -1,
    "ArrowDown": 1,
    "ArrowRight": 1,
    "PageUp": -10,
    "PageDown": 10,
}


class DicomViewerState(rx.State):
    """State of the open series and how it is displayed."""
    series_path: str = ""
    series_name: str = ""
    series_label: str = ""
    total_images: int = 0
    _series_files: list[str] = []
    _series_window: tuple[float, float] = DEFAULT_WINDOW
    _series_window_preset: str = ""
    # Bumped when a different series is opened, so in-flight image work for
    # the previous one is dropped.
    _series_version: int = 0
    opening: bool = False
    opening_done: int = 0
    opening_total: int = 0
    open_error: str = ""
    current_index: int = 0
    # The sliders are uncontrolled so they can be dragged; changing these keys
    # remounts them at the state value after a change made by other means.
    slice_slider_key: int = 0
    window_slider_key: int = 0
    error_message: str = ""
    current_image_base64: str = "/placeholder.svg"
    window_center: float = DEFAULT_WINDOW[0]
    window_width: float = DEFAULT_WINDOW[1]
    zoom_level: float = 1.0
    pan_x: int = 0
    pan_y: int = 0
    selected_preset: str = ""
    tooltip_language: str = "zh-TW"
    tutorial_dialog_open: bool = False
    tutorial_dialog_title: str = ""
    tutorial_dialog_url: str = ""
    patient_name: str = "N/A"
    patient_id: str = "N/A"
    study_date: str = "N/A"
    modality: str = "N/A"
    study_description: str = "N/A"
    series_description: str = "N/A"
    image_position: str = "N/A"
    pixel_spacing: str = "N/A"
    slice_thickness: str = "N/A"
    rows: int = 0
    columns: int = 0
    show_metadata: bool = True
    metadata_unlocked: bool = False
    metadata_password_input: str = ""
    metadata_password_error: str = ""
    _cached_pixel_array: np.ndarray | None = None
    _cached_rescale_slope: float = 1.0
    _cached_rescale_intercept: float = 0.0
    _cached_index: int = -1
    _refreshing: bool = False
    _default_tutorial_links: dict[str, str] = {
        # Use same-origin static assets so the tutorial can be embedded in an iframe.
        # (Many external sites, including Wikipedia, block iframe embedding.)
        "en": "/tutorials/windowing_en.html",
        "zh-TW": "/tutorials/windowing_zh_tw.html",
        "zh-CN": "/tutorials/windowing_zh_cn.html",
        "es": "/tutorials/windowing_es.html",
    }
    _preset_tutorial_links: dict[str, dict[str, str]] = {
        "en": {
            "Soft Tissue": "/tutorials/windowing_en.html#soft-tissue",
            "Lung": "/tutorials/windowing_en.html#lung",
            "Bone": "/tutorials/windowing_en.html#bone",
            "Brain": "/tutorials/windowing_en.html#brain",
            "Abdomen": "/tutorials/windowing_en.html#abdomen",
            "Liver": "/tutorials/windowing_en.html#liver",
            "Mediastinum": "/tutorials/windowing_en.html#mediastinum",
            "Spine": "/tutorials/windowing_en.html#spine",
            "Pelvis": "/tutorials/windowing_en.html#pelvis",
            "Head/Neck": "/tutorials/windowing_en.html#head-neck",
            "CTA/Vascular": "/tutorials/windowing_en.html#cta-vascular",
            "Kidney": "/tutorials/windowing_en.html#kidney",
            "Pancreas": "/tutorials/windowing_en.html#pancreas",
            "Trauma": "/tutorials/windowing_en.html#trauma",
            "Subdural": "/tutorials/windowing_en.html#subdural",
            "Stroke": "/tutorials/windowing_en.html#stroke",
            "Body": "/tutorials/windowing_en.html#body",
            "Extremity/MSK": "/tutorials/windowing_en.html#extremity-msk",
            "Temporal Bone": "/tutorials/windowing_en.html#temporal-bone",
            "Sinus": "/tutorials/windowing_en.html#sinus",
            "Angio Bone Sub": "/tutorials/windowing_en.html#angio-bone-sub",
            "Lung HRCT": "/tutorials/windowing_en.html#lung-hrct",
            "Orbits": "/tutorials/windowing_en.html#orbits",
            "CTA Head/Neck": "/tutorials/windowing_en.html#cta-head-neck",
            "Arterial": "/tutorials/windowing_en.html#arterial",
            "Venous": "/tutorials/windowing_en.html#venous",
            "Colon/Bowel": "/tutorials/windowing_en.html#colon-bowel",
            "Adrenal": "/tutorials/windowing_en.html#adrenal",
            "Gallbladder": "/tutorials/windowing_en.html#gallbladder",
            "Skin/Subcutaneous": "/tutorials/windowing_en.html#skin-subcutaneous",
            "Cardiac": "/tutorials/windowing_en.html#cardiac",
        },
        "zh-TW": {
            "Soft Tissue": "/tutorials/windowing_zh_tw.html#soft-tissue",
            "Lung": "/tutorials/windowing_zh_tw.html#lung",
            "Bone": "/tutorials/windowing_zh_tw.html#bone",
            "Brain": "/tutorials/windowing_zh_tw.html#brain",
            "Abdomen": "/tutorials/windowing_zh_tw.html#abdomen",
            "Liver": "/tutorials/windowing_zh_tw.html#liver",
            "Mediastinum": "/tutorials/windowing_zh_tw.html#mediastinum",
            "Spine": "/tutorials/windowing_zh_tw.html#spine",
            "Pelvis": "/tutorials/windowing_zh_tw.html#pelvis",
            "Head/Neck": "/tutorials/windowing_zh_tw.html#head-neck",
            "CTA/Vascular": "/tutorials/windowing_zh_tw.html#cta-vascular",
            "Kidney": "/tutorials/windowing_zh_tw.html#kidney",
            "Pancreas": "/tutorials/windowing_zh_tw.html#pancreas",
            "Trauma": "/tutorials/windowing_zh_tw.html#trauma",
            "Subdural": "/tutorials/windowing_zh_tw.html#subdural",
            "Stroke": "/tutorials/windowing_zh_tw.html#stroke",
            "Body": "/tutorials/windowing_zh_tw.html#body",
            "Extremity/MSK": "/tutorials/windowing_zh_tw.html#extremity-msk",
            "Temporal Bone": "/tutorials/windowing_zh_tw.html#temporal-bone",
            "Sinus": "/tutorials/windowing_zh_tw.html#sinus",
            "Angio Bone Sub": "/tutorials/windowing_zh_tw.html#angio-bone-sub",
            "Lung HRCT": "/tutorials/windowing_zh_tw.html#lung-hrct",
            "Orbits": "/tutorials/windowing_zh_tw.html#orbits",
            "CTA Head/Neck": "/tutorials/windowing_zh_tw.html#cta-head-neck",
            "Arterial": "/tutorials/windowing_zh_tw.html#arterial",
            "Venous": "/tutorials/windowing_zh_tw.html#venous",
            "Colon/Bowel": "/tutorials/windowing_zh_tw.html#colon-bowel",
            "Adrenal": "/tutorials/windowing_zh_tw.html#adrenal",
            "Gallbladder": "/tutorials/windowing_zh_tw.html#gallbladder",
            "Skin/Subcutaneous": "/tutorials/windowing_zh_tw.html#skin-subcutaneous",
            "Cardiac": "/tutorials/windowing_zh_tw.html#cardiac",
        },
        "zh-CN": {
                "Soft Tissue": "/tutorials/windowing_zh_cn.html#soft-tissue",
                "Lung": "/tutorials/windowing_zh_cn.html#lung",
                "Bone": "/tutorials/windowing_zh_cn.html#bone",
                "Brain": "/tutorials/windowing_zh_cn.html#brain",
                "Abdomen": "/tutorials/windowing_zh_cn.html#abdomen",
                "Liver": "/tutorials/windowing_zh_cn.html#liver",
                "Mediastinum": "/tutorials/windowing_zh_cn.html#mediastinum",
                "Spine": "/tutorials/windowing_zh_cn.html#spine",
                "Pelvis": "/tutorials/windowing_zh_cn.html#pelvis",
                "Head/Neck": "/tutorials/windowing_zh_cn.html#head-neck",
                "CTA/Vascular": "/tutorials/windowing_zh_cn.html#cta-vascular",
                "Kidney": "/tutorials/windowing_zh_cn.html#kidney",
                "Pancreas": "/tutorials/windowing_zh_cn.html#pancreas",
                "Trauma": "/tutorials/windowing_zh_cn.html#trauma",
                "Subdural": "/tutorials/windowing_zh_cn.html#subdural",
                "Stroke": "/tutorials/windowing_zh_cn.html#stroke",
                "Body": "/tutorials/windowing_zh_cn.html#body",
                "Extremity/MSK": "/tutorials/windowing_zh_cn.html#extremity-msk",
                "Temporal Bone": "/tutorials/windowing_zh_cn.html#temporal-bone",
                "Sinus": "/tutorials/windowing_zh_cn.html#sinus",
                "Angio Bone Sub": "/tutorials/windowing_zh_cn.html#angio-bone-sub",
                "Lung HRCT": "/tutorials/windowing_zh_cn.html#lung-hrct",
                "Orbits": "/tutorials/windowing_zh_cn.html#orbits",
                "CTA Head/Neck": "/tutorials/windowing_zh_cn.html#cta-head-neck",
                "Arterial": "/tutorials/windowing_zh_cn.html#arterial",
                "Venous": "/tutorials/windowing_zh_cn.html#venous",
                "Colon/Bowel": "/tutorials/windowing_zh_cn.html#colon-bowel",
                "Adrenal": "/tutorials/windowing_zh_cn.html#adrenal",
                "Gallbladder": "/tutorials/windowing_zh_cn.html#gallbladder",
                "Skin/Subcutaneous": "/tutorials/windowing_zh_cn.html#skin-subcutaneous",
                "Cardiac": "/tutorials/windowing_zh_cn.html#cardiac",
            },
            "es": {
                "Soft Tissue": "/tutorials/windowing_es.html#soft-tissue",
                "Lung": "/tutorials/windowing_es.html#lung",
                "Bone": "/tutorials/windowing_es.html#bone",
                "Brain": "/tutorials/windowing_es.html#brain",
                "Abdomen": "/tutorials/windowing_es.html#abdomen",
                "Liver": "/tutorials/windowing_es.html#liver",
                "Mediastinum": "/tutorials/windowing_es.html#mediastinum",
                "Spine": "/tutorials/windowing_es.html#spine",
                "Pelvis": "/tutorials/windowing_es.html#pelvis",
                "Head/Neck": "/tutorials/windowing_es.html#head-neck",
                "CTA/Vascular": "/tutorials/windowing_es.html#cta-vascular",
                "Kidney": "/tutorials/windowing_es.html#kidney",
                "Pancreas": "/tutorials/windowing_es.html#pancreas",
                "Trauma": "/tutorials/windowing_es.html#trauma",
                "Subdural": "/tutorials/windowing_es.html#subdural",
                "Stroke": "/tutorials/windowing_es.html#stroke",
                "Body": "/tutorials/windowing_es.html#body",
                "Extremity/MSK": "/tutorials/windowing_es.html#extremity-msk",
                "Temporal Bone": "/tutorials/windowing_es.html#temporal-bone",
                "Sinus": "/tutorials/windowing_es.html#sinus",
                "Angio Bone Sub": "/tutorials/windowing_es.html#angio-bone-sub",
                "Lung HRCT": "/tutorials/windowing_es.html#lung-hrct",
                "Orbits": "/tutorials/windowing_es.html#orbits",
                "CTA Head/Neck": "/tutorials/windowing_es.html#cta-head-neck",
                "Arterial": "/tutorials/windowing_es.html#arterial",
                "Venous": "/tutorials/windowing_es.html#venous",
                "Colon/Bowel": "/tutorials/windowing_es.html#colon-bowel",
                "Adrenal": "/tutorials/windowing_es.html#adrenal",
                "Gallbladder": "/tutorials/windowing_es.html#gallbladder",
                "Skin/Subcutaneous": "/tutorials/windowing_es.html#skin-subcutaneous",
                "Cardiac": "/tutorials/windowing_es.html#cardiac",
            },
    }

    _preset_descriptions: dict[str, dict[str, str]] = {
        "en": {
            "Soft Tissue": "Good for most organs and muscles.",
            "Lung": "Best for seeing lungs and airways.",
            "Bone": "Makes bones and fractures easy to see.",
            "Brain": "Standard view for brain tissue.",
            "Abdomen": "General view for belly organs.",
            "Liver": "Clearer view of the liver.",
            "Mediastinum": "Shows the middle chest area.",
            "Spine": "Helps check the spine and canal.",
            "Pelvis": "General view of pelvic organs.",
            "Head/Neck": "Shows soft tissues in head/neck.",
            "CTA/Vascular": "Shows blood vessels with contrast.",
            "Kidney": "Helps see kidneys clearly.",
            "Pancreas": "Better view of the pancreas.",
            "Trauma": "Wide view for injuries.",
            "Subdural": "Helps see bleeding under the skull.",
            "Stroke": "Narrow brain view for early stroke.",
            "Body": "Wide soft-tissue view for whole body.",
            "Extremity/MSK": "For arms/legs soft tissues.",
            "Temporal Bone": "Very wide view for ear bones.",
            "Sinus": "For sinuses around the nose.",
            "Angio Bone Sub": "Shows vessels after bone is removed.",
            "Lung HRCT": "Extra detail for lungs.",
            "Orbits": "For the eye sockets.",
            "CTA Head/Neck": "Vessels in head and neck.",
            "Arterial": "Artery phase vessels.",
            "Venous": "Vein phase vessels.",
            "Colon/Bowel": "For colon and bowel walls.",
            "Adrenal": "For adrenal glands.",
            "Gallbladder": "For gallbladder and bile ducts.",
            "Skin/Subcutaneous": "For skin and fat layer.",
            "Cardiac": "For heart soft tissues.",
        },
        "zh-TW": {
            "Soft Tissue": "看大部分器官與肌肉。",
            "Lung": "看肺部與氣道。",
            "Bone": "讓骨頭和骨折更清楚。",
            "Brain": "腦部常用視窗。",
            "Abdomen": "看腹部器官。",
            "Liver": "看肝臟更清楚。",
            "Mediastinum": "看胸部中間區域。",
            "Spine": "看脊椎與椎管。",
            "Pelvis": "看骨盆器官。",
            "Head/Neck": "看頭頸部軟組織。",
            "CTA/Vascular": "看打顯影劑的血管。",
            "Kidney": "看腎臟更清楚。",
            "Pancreas": "看胰臟更清楚。",
            "Trauma": "看受傷的大範圍。",
            "Subdural": "看頭骨下出血。",
            "Stroke": "看早期中風。",
            "Body": "全身軟組織大範圍。",
            "Extremity/MSK": "看四肢軟組織。",
            "Temporal Bone": "看耳朵附近骨頭。",
            "Sinus": "看鼻竇。",
            "Angio Bone Sub": "扣掉骨頭後看血管。",
            "Lung HRCT": "更細的肺部細節。",
            "Orbits": "看眼眶。",
            "CTA Head/Neck": "看頭頸部血管。",
            "Arterial": "動脈期血管。",
            "Venous": "靜脈期血管。",
            "Colon/Bowel": "看腸道和腸壁。",
            "Adrenal": "看腎上腺。",
            "Gallbladder": "看膽囊和膽道。",
            "Skin/Subcutaneous": "看皮膚和皮下脂肪。",
            "Cardiac": "看心臟軟組織。",
        },
        "zh-CN": {
            "Soft Tissue": "看大部分器官和肌肉。",
            "Lung": "看肺部和气道。",
            "Bone": "让骨头和骨折更清楚。",
            "Brain": "脑部常用视窗。",
            "Abdomen": "看腹部器官。",
            "Liver": "看肝脏更清楚。",
            "Mediastinum": "看胸部中间区域。",
            "Spine": "看脊柱与椎管。",
            "Pelvis": "看骨盆器官。",
            "Head/Neck": "看头颈部软组织。",
            "CTA/Vascular": "看打显影剂的血管。",
            "Kidney": "看肾脏更清楚。",
            "Pancreas": "看胰腺更清楚。",
            "Trauma": "看受伤的大范围。",
            "Subdural": "看颅骨下出血。",
            "Stroke": "看早期中风。",
            "Body": "全身软组织大范围。",
            "Extremity/MSK": "看四肢软组织。",
            "Temporal Bone": "看耳朵附近骨头。",
            "Sinus": "看鼻窦。",
            "Angio Bone Sub": "去掉骨头后看血管。",
            "Lung HRCT": "更细的肺部细节。",
            "Orbits": "看眼眶。",
            "CTA Head/Neck": "看头颈部血管。",
            "Arterial": "动脉期血管。",
            "Venous": "静脉期血管。",
            "Colon/Bowel": "看肠道和肠壁。",
            "Adrenal": "看肾上腺。",
            "Gallbladder": "看胆囊和胆道。",
            "Skin/Subcutaneous": "看皮肤和皮下脂肪。",
            "Cardiac": "看心脏软组织。",
        },
        "es": {
            "Soft Tissue": "Para la mayoría de órganos y músculos.",
            "Lung": "Para ver pulmones y vías aéreas.",
            "Bone": "Hace más claros los huesos y fracturas.",
            "Brain": "Vista estándar del cerebro.",
            "Abdomen": "Vista general del abdomen.",
            "Liver": "Para ver el hígado con más detalle.",
            "Mediastinum": "Muestra el centro del pecho.",
            "Spine": "Para revisar la columna y el canal.",
            "Pelvis": "Vista general de la pelvis.",
            "Head/Neck": "Para tejidos de cabeza y cuello.",
            "CTA/Vascular": "Para vasos con contraste.",
            "Kidney": "Para ver los riñones.",
            "Pancreas": "Para ver el páncreas.",
            "Trauma": "Vista amplia para lesiones.",
            "Subdural": "Para ver sangrado bajo el cráneo.",
            "Stroke": "Vista estrecha para ictus temprano.",
            "Body": "Vista amplia de tejidos blandos.",
            "Extremity/MSK": "Para tejidos de brazos y piernas.",
            "Temporal Bone": "Vista muy amplia del temporal.",
            "Sinus": "Para los senos paranasales.",
            "Angio Bone Sub": "Vasos con hueso removido.",
            "Lung HRCT": "Más detalle del pulmón.",
            "Orbits": "Para las órbitas de los ojos.",
            "CTA Head/Neck": "Vasos de cabeza y cuello.",
            "Arterial": "Vasos en fase arterial.",
            "Venous": "Vasos en fase venosa.",
            "Colon/Bowel": "Para colon e intestino.",
            "Adrenal": "Para glándulas suprarrenales.",
            "Gallbladder": "Para vesícula y vía biliar.",
            "Skin/Subcutaneous": "Para piel y grasa.",
            "Cardiac": "Para tejidos del corazón.",
        },
    }

    _metadata_password: str = os.getenv("DICOM_METADATA_PASSWORD", "dicom")

    @rx.var
    def current_position_text(self) -> str:
        if not self.total_images:
            return "0 / 0"
        return f"{self.current_index + 1} / {self.total_images}"

    @rx.var
    def slider_max(self) -> int:
        return max(0, self.total_images - 1)

    @rx.var
    def opening_percent(self) -> int:
        if not self.opening_total:
            return 0
        return round(100 * self.opening_done / self.opening_total)

    @rx.event
    def dismiss_open_error(self):
        self.open_error = ""

    @rx.event(background=True)
    async def open_series(self, folder: str):
        """Read the DICOM files in `folder`, order the slices and show the viewer."""
        async with self:
            if self.opening:
                return
            self.opening = True
            self.opening_done = 0
            self.opening_total = 0
            self.open_error = ""
        try:
            if not folders.is_inside(folder, folders.dicom_root()):
                async with self:
                    self.open_error = "This folder is outside the DICOM folder."
                return
            try:
                files = await asyncio.to_thread(folders.dicom_files, folder)
                async with self:
                    self.opening_total = len(files)
                keyed = []
                for start in range(0, len(files), _OPEN_BATCH):
                    batch = files[start : start + _OPEN_BATCH]
                    keyed += await asyncio.to_thread(series.read_sort_keys, batch)
                    async with self:
                        self.opening_done = start + len(batch)
            except OSError as e:
                logging.exception(f"Error opening {folder}: {e}")
                async with self:
                    self.open_error = folders.describe_error(e)
                return
            if not keyed:
                async with self:
                    self.open_error = "No readable DICOM images in this folder."
                return
            keyed.sort(key=lambda item: item[0])
            ordered = [path for _, path in keyed]
            window = await asyncio.to_thread(series.file_window, ordered[0])
            label = await asyncio.to_thread(folders.series_label, ordered[0])
            async with self:
                self._series_version += 1
                self._series_files = ordered
                self._series_window = window or DEFAULT_WINDOW
                self._series_window_preset = FILE_WINDOW if window else ""
                self.series_path = folder
                self.series_name = os.path.basename(os.path.normpath(folder))
                self.series_label = label
                self.total_images = len(ordered)
                self.current_index = 0
                self.slice_slider_key += 1
                self.error_message = ""
                self.current_image_base64 = "/placeholder.svg"
                self._cached_index = -1
                self._cached_pixel_array = None
                self.zoom_level = 1.0
                self.pan_x = 0
                self.pan_y = 0
                self.metadata_unlocked = False
                self.metadata_password_input = ""
                self.metadata_password_error = ""
                self._set_window(*self._series_window, self._series_window_preset)
            # Show the first slice before switching pages.
            await self._refresh()
            yield rx.redirect("/viewer")
        finally:
            async with self:
                self.opening = False

    @rx.event(background=True)
    async def refresh_image(self):
        await self._refresh()

    async def _refresh(self):
        """Bring the picture up to date with the slice and window in the state.

        Call from a background event. A call made while a refresh is running
        returns at once: the running loop sees its change and stops only when
        the picture matches the latest values, so drags and wheel bursts
        coalesce instead of piling up.
        """
        async with self:
            if self._refreshing:
                return
            self._refreshing = True
        done = False
        try:
            while True:
                async with self:
                    version = self._series_version
                    index = self.current_index
                    center, width = self.window_center, self.window_width
                    if not 0 <= index < len(self._series_files):
                        self._refreshing = False
                        done = True
                        return
                    path = self._series_files[index]
                    cached = self._cached_index == index
                    pixels = self._cached_pixel_array
                    slope = self._cached_rescale_slope
                    intercept = self._cached_rescale_intercept
                ds = None
                if not cached:
                    try:
                        ds, pixels, slope, intercept = await asyncio.to_thread(
                            series.read_slice, path
                        )
                    except Exception as e:
                        logging.exception(f"Error loading image {path}: {e}")
                        async with self:
                            if self._series_version == version:
                                self.error_message = self._format_dicom_error(e)
                                self.current_image_base64 = "/placeholder.svg"
                                self._cached_index = -1
                                self._cached_pixel_array = None
                                if self.current_index == index:
                                    self._refreshing = False
                                    done = True
                                    return
                        continue
                image = await asyncio.to_thread(
                    series.render_png, pixels, slope, intercept, center, width
                )
                async with self:
                    if self._series_version != version:
                        continue
                    if ds is not None:
                        self._cached_index = index
                        self._cached_pixel_array = pixels
                        self._cached_rescale_slope = slope
                        self._cached_rescale_intercept = intercept
                        self._extract_metadata(ds)
                        self.error_message = ""
                    self.current_image_base64 = image
                    if (self.current_index, self.window_center, self.window_width) == (
                        index,
                        center,
                        width,
                    ):
                        self._refreshing = False
                        done = True
                        return
        finally:
            if not done:
                async with self:
                    self._refreshing = False

    def _extract_metadata(self, ds):
        """Extract metadata from DICOM dataset."""

        @rx.event
        def get_val(tag, default="N/A"):
            return str(ds.get(tag, default))

        self.patient_name = get_val("PatientName")
        self.patient_id = get_val("PatientID")
        self.study_date = get_val("StudyDate")
        self.modality = get_val("Modality")
        self.study_description = get_val("StudyDescription")
        self.series_description = get_val("SeriesDescription")
        pos = ds.get("ImagePositionPatient", None)
        self.image_position = (
            f"[{', '.join([f'{x:.1f}' for x in pos])}]" if pos else "N/A"
        )
        spacing = ds.get("PixelSpacing", None)
        self.pixel_spacing = (
            f"{spacing[0]:.3f} x {spacing[1]:.3f} mm" if spacing else "N/A"
        )
        thickness = ds.get("SliceThickness", None)
        self.slice_thickness = f"{thickness} mm" if thickness else "N/A"
        self.rows = int(ds.get("Rows", 0))
        self.columns = int(ds.get("Columns", 0))

    @rx.event
    def toggle_metadata(self):
        self.show_metadata = not self.show_metadata

    @rx.event
    def update_metadata_password(self, value: str):
        self.metadata_password_input = value
        if self.metadata_password_error:
            self.metadata_password_error = ""

    @rx.event
    def unlock_metadata(self):
        if self.metadata_password_input == self._metadata_password:
            self.metadata_unlocked = True
            self.metadata_password_input = ""
            self.metadata_password_error = ""
        else:
            self.metadata_password_error = "Invalid password."

    @rx.event
    def lock_metadata(self):
        self.metadata_unlocked = False
        self.metadata_password_input = ""
        self.metadata_password_error = ""

    def _format_dicom_error(self, error: Exception) -> str:
        message = str(error)
        lowered = message.lower()
        if (
            "transfer syntax" in lowered
            or "no handler" in lowered
            or "decode" in lowered
            or "compressed" in lowered
        ):
            return (
                "Unable to decode pixel data. This DICOM appears to use a compressed "
                "transfer syntax. Install pylibjpeg (and pylibjpeg-libjpeg) or gdcm to "
                "enable decompression, then restart the app."
            )
        return f"Error loading image: {message}"

    def _set_window(
        self, center: float, width: float, preset: str, from_slider: bool = False
    ):
        """Change the window; refresh_image redraws."""
        self.window_center = float(center)
        self.window_width = float(width)
        self.selected_preset = preset
        if not from_slider:
            self.window_slider_key += 1
        return DicomViewerState.refresh_image

    @rx.event
    def update_window_width(self, value: str):
        try:
            width = float(value)
        except ValueError as e:
            logging.exception(f"Error updating window width: {e}")
            return
        return self._set_window(self.window_center, width, "", from_slider=True)

    @rx.event
    def update_window_center(self, value: str):
        try:
            center = float(value)
        except ValueError as e:
            logging.exception(f"Error updating window center: {e}")
            return
        return self._set_window(center, self.window_width, "", from_slider=True)

    def _go_to_slice(self, index: int, from_slider: bool = False):
        """Move to slice `index` (clamped); refresh_image redraws."""
        if not self.total_images:
            return None
        index = max(0, min(self.total_images - 1, index))
        if index == self.current_index:
            return None
        self.current_index = index
        if not from_slider:
            self.slice_slider_key += 1
        return DicomViewerState.refresh_image

    @rx.event
    def next_image(self):
        return self._go_to_slice(self.current_index + 1)

    @rx.event
    def prev_image(self):
        return self._go_to_slice(self.current_index - 1)

    @rx.event
    def set_slice_index(self, value: str):
        try:
            index = int(float(value))
        except ValueError as e:
            logging.exception(f"Error setting slice index: {e}")
            return
        return self._go_to_slice(index, from_slider=True)

    @rx.event
    def scroll_slices(self, delta_y: float):
        """Mouse wheel over the image: down is the next slice, up the previous."""
        if delta_y:
            return self._go_to_slice(self.current_index + (1 if delta_y > 0 else -1))

    @rx.event
    def handle_slice_key(self, key: str):
        """Arrow and Page keys step through the slices; Home/End jump to the ends."""
        if key == "Home":
            return self._go_to_slice(0)
        if key == "End":
            return self._go_to_slice(self.total_images - 1)
        if key in _SLICE_KEY_STEPS:
            return self._go_to_slice(self.current_index + _SLICE_KEY_STEPS[key])

    @rx.event
    def set_zoom(self, value: float):
        """Set the zoom level within limits."""
        self.zoom_level = max(0.25, min(4.0, value))

    @rx.event
    def zoom_in(self):
        """Increase zoom level."""
        self.set_zoom(self.zoom_level + 0.25)

    @rx.event
    def zoom_out(self):
        """Decrease zoom level."""
        self.set_zoom(self.zoom_level - 0.25)

    @rx.event
    def reset_zoom(self):
        """Reset zoom and pan settings."""
        self.zoom_level = 1.0
        self.pan_x = 0
        self.pan_y = 0

    @rx.event
    def pan_control(self, dx: int, dy: int):
        """Update pan position."""
        self.pan_x += dx
        self.pan_y += dy

    @rx.event
    def apply_preset(self, preset_name: str):
        """Apply a predefined windowing preset."""
        presets = {
            "Soft Tissue": (40, 400),
            "Lung": (-600, 1500),
            "Bone": (300, 1500),
            "Brain": (40, 80),
            "Abdomen": (50, 400),
            "Liver": (60, 150),
            "Mediastinum": (40, 350),
            "Spine": (30, 300),
            "Pelvis": (50, 450),
            "Head/Neck": (50, 250),
            "CTA/Vascular": (150, 600),
            "Kidney": (30, 300),
            "Pancreas": (50, 200),
            "Trauma": (50, 500),
            "Subdural": (50, 130),
            "Stroke": (35, 30),
            "Body": (40, 450),
            "Extremity/MSK": (40, 350),
            "Temporal Bone": (700, 4000),
            "Sinus": (50, 300),
            "Angio Bone Sub": (300, 1200),
            "Lung HRCT": (-700, 1200),
            "Orbits": (50, 300),
            "CTA Head/Neck": (180, 700),
            "Arterial": (150, 600),
            "Venous": (100, 500),
            "Colon/Bowel": (50, 400),
            "Adrenal": (40, 300),
            "Gallbladder": (30, 200),
            "Skin/Subcutaneous": (50, 250),
            "Cardiac": (75, 350),
        }
        if preset_name in presets:
            center, width = presets[preset_name]
            return self._set_window(center, width, preset_name)

    @rx.event
    def reset_view(self):
        """Reset zoom, pan and the window to how the series opened."""
        self.reset_zoom()
        return self._set_window(*self._series_window, self._series_window_preset)

    @rx.event
    def set_tooltip_language(self, value: str):
        if value in self._preset_descriptions:
            self.tooltip_language = value

    def _get_tutorial_link(self, preset: str) -> str:
        links = self._preset_tutorial_links.get(self.tooltip_language, {})
        if preset in links:
            return links[preset]
        fallback_links = self._preset_tutorial_links.get("en", {})
        if preset in fallback_links:
            return fallback_links[preset]
        return self._default_tutorial_links.get(
            self.tooltip_language, self._default_tutorial_links["en"]
        )

    @rx.event
    def open_tutorial_dialog(self, preset: str):
        self.tutorial_dialog_title = preset
        self.tutorial_dialog_url = self._get_tutorial_link(preset)
        self.tutorial_dialog_open = True

    @rx.event
    def close_tutorial_dialog(self):
        self.tutorial_dialog_open = False
        self.tutorial_dialog_title = ""
        self.tutorial_dialog_url = ""

    @rx.var
    def preset_options(self) -> list[str]:
        """Available windowing presets."""
        return [
            "Soft Tissue",
            "Lung",
            "Bone",
            "Brain",
            "Abdomen",
            "Liver",
            "Mediastinum",
            "Spine",
            "Pelvis",
            "Head/Neck",
            "CTA/Vascular",
            "Kidney",
            "Pancreas",
            "Trauma",
            "Subdural",
            "Stroke",
            "Body",
            "Extremity/MSK",
            "Temporal Bone",
            "Sinus",
            "Angio Bone Sub",
            "Lung HRCT",
            "Orbits",
            "CTA Head/Neck",
            "Arterial",
            "Venous",
            "Colon/Bowel",
            "Adrenal",
            "Gallbladder",
            "Skin/Subcutaneous",
            "Cardiac",
        ]

    @rx.var
    def preset_tooltips(self) -> dict[str, str]:
        return self._preset_descriptions.get(
            self.tooltip_language, self._preset_descriptions["en"]
        )

    @rx.var
    def preset_tutorial_links(self) -> dict[str, str]:
        base = {
            preset: self._default_tutorial_links.get(
                self.tooltip_language, self._default_tutorial_links["en"]
            )
            for preset in self.preset_options
        }
        language_links = self._preset_tutorial_links.get(self.tooltip_language, {})
        fallback_links = self._preset_tutorial_links.get("en", {})
        base.update(fallback_links)
        base.update(language_links)
        return base
