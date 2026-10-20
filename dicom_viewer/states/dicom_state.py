import reflex as rx
import asyncio
import os
import logging
import numpy as np

from dicom_viewer import folders, series, volume

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


def _render_planes(
    vol: volume.Volume, indices: dict[str, int], center: float, width: float
) -> dict[str, str]:
    return {
        plane: series.render_png(
            vol.plane(plane, index), vol.slope, vol.intercept, center, width
        )
        for plane, index in indices.items()
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
    # "single" shows one scanned image; "mpr" shows the series cut in three
    # planes (axial, coronal, sagittal) that share one crosshair point.
    view_mode: str = "single"
    mpr_status: str = ""  # "", "loading", "ready" or "error"
    mpr_done: int = 0
    mpr_total: int = 0
    mpr_error: str = ""
    mpr_notes: list[str] = []
    # Per plane: the slice shown, how many there are, picture width / height.
    plane_index: dict[str, int] = {plane: 0 for plane in volume.PLANES}
    plane_count: dict[str, int] = {plane: 0 for plane in volume.PLANES}
    plane_ratio: dict[str, float] = {plane: 1.0 for plane in volume.PLANES}
    # Separate vars, so redrawing one plane does not resend the other two.
    axial_image: str = "/placeholder.svg"
    coronal_image: str = "/placeholder.svg"
    sagittal_image: str = "/placeholder.svg"
    active_plane: str = "axial"
    show_crosshair: bool = True
    plane_slider_key: int = 0
    crosshair_position: str = ""
    crosshair_value: str = ""
    _volume_key: str = ""
    # (volume, slice, window) each plane picture was drawn for.
    _plane_drawn: dict[str, tuple] = {}
    _mpr_refreshing: bool = False
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
                self.view_mode = "single"
                self.mpr_status = ""
                self.mpr_error = ""
                self._volume_key = ""
                self._set_window(*self._series_window, self._series_window_preset)
            # Show the first slice before switching pages.
            await self._refresh()
            yield rx.redirect("/viewer")
        finally:
            async with self:
                self.opening = False

    @rx.event(background=True)
    async def refresh_image(self):
        async with self:
            mode = self.view_mode
        if mode == "mpr":
            await self._refresh_planes()
        else:
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
        if self.view_mode == "mpr":
            plane = self.active_plane
            index = self.plane_index.get(plane, 0)
            if key == "Home":
                return self._move_plane(plane, 0)
            if key == "End":
                return self._move_plane(plane, self.plane_count.get(plane, 0) - 1)
            if key in _SLICE_KEY_STEPS:
                return self._move_plane(plane, index + _SLICE_KEY_STEPS[key])
            return None
        if key == "Home":
            return self._go_to_slice(0)
        if key == "End":
            return self._go_to_slice(self.total_images - 1)
        if key in _SLICE_KEY_STEPS:
            return self._go_to_slice(self.current_index + _SLICE_KEY_STEPS[key])

    @rx.var
    def mpr_percent(self) -> int:
        if not self.mpr_total:
            return 0
        return round(100 * self.mpr_done / self.mpr_total)

    @rx.var
    def plane_position_text(self) -> dict[str, str]:
        return {
            plane: f"{self.plane_index.get(plane, 0) + 1} / {self.plane_count.get(plane, 0)}"
            for plane in volume.PLANES
        }

    @rx.var
    def crosshair(self) -> dict[str, float]:
        """Where each plane crosses the other views, in % of their width or height.

        Only the axial index is drawn upside down (head at the top).
        """
        place = {}
        for plane in volume.PLANES:
            count = max(1, self.plane_count.get(plane, 0))
            index = self.plane_index.get(plane, 0)
            if plane == "axial":
                index = count - 1 - index
            place[plane] = (index + 0.5) / count * 100
        return place

    @rx.event
    def set_view_mode(self, mode: str):
        if mode == self.view_mode or mode not in ("single", "mpr"):
            return None
        self.view_mode = mode
        if mode == "mpr":
            return DicomViewerState.load_volume
        # Back in one view, show the scanned image the crosshair is on.
        vol = volume.cached(self._volume_key)
        if vol is not None and self.mpr_status == "ready":
            path = vol.path_at(self.plane_index)
            if path in self._series_files:
                self._go_to_slice(self._series_files.index(path))
        return DicomViewerState.refresh_image

    @rx.event(background=True)
    async def load_volume(self):
        await self._load_volume()

    async def _load_volume(self, rebuild: bool = False):
        """Stack the open series into a volume (or reuse it) and draw the planes.

        `rebuild` is for a volume dropped from the cache while in use: its
        crosshair stays where it was.
        """
        async with self:
            if self.mpr_status == "loading" or not self._series_files:
                return
            files = list(self._series_files)
            version = self._series_version
            # Start on the image shown in one view, or in the middle of the
            # volume while one view is still on the first image.
            current_path = files[self.current_index] if self.current_index else ""
            key = volume.cache_key(files)
            vol = volume.cached(key)
            if vol is None:
                self.mpr_status = "loading"
                self.mpr_done = 0
                self.mpr_total = 0
                self.mpr_error = ""
        if vol is None:
            try:
                layout = await asyncio.to_thread(volume.read_layout, files)
                async with self:
                    self.mpr_total = len(layout.paths)
                slices = []
                for start in range(0, len(layout.paths), _OPEN_BATCH):
                    batch = layout.paths[start : start + _OPEN_BATCH]
                    slices += await asyncio.to_thread(volume.read_pixels, batch)
                    async with self:
                        if self._series_version != version:
                            self.mpr_status = ""
                            return
                        self.mpr_done = start + len(batch)
                vol = await asyncio.to_thread(volume.assemble, layout, slices)
            except Exception as e:
                if not isinstance(e, volume.VolumeError):
                    logging.exception(f"Error building the volume: {e}")
                async with self:
                    if self._series_version == version:
                        self.mpr_status = "error"
                        self.mpr_error = (
                            str(e)
                            if isinstance(e, volume.VolumeError)
                            else self._format_dicom_error(e)
                        )
                    else:
                        self.mpr_status = ""
                return
            volume.store(key, vol)
        async with self:
            if self._series_version != version:
                self.mpr_status = ""
                return
            cursor = vol.cursor_at(current_path)
            if self._volume_key != key:
                self._volume_key = key
                self._plane_drawn = {}
                self.plane_count = {plane: vol.count(plane) for plane in volume.PLANES}
                self.plane_ratio = {plane: vol.ratio(plane) for plane in volume.PLANES}
                self.plane_index = cursor
                self.active_plane = vol.scan_plane
                self.mpr_notes = volume.notes(vol)
            elif not rebuild:
                # Seen before: follow one view's image, keep the other planes.
                self.plane_index[vol.scan_plane] = cursor[vol.scan_plane]
            self.plane_slider_key += 1
            self._update_crosshair_info(vol)
            self.mpr_status = "ready"
        await self._refresh_planes()

    async def _refresh_planes(self):
        """Bring the three plane pictures up to date; coalesces like _refresh."""
        async with self:
            if self._mpr_refreshing:
                return
            self._mpr_refreshing = True
        done = missing = False
        try:
            while True:
                async with self:
                    key = self._volume_key
                    window = (self.window_center, self.window_width)
                    wanted = {
                        plane: (key, self.plane_index[plane], *window)
                        for plane in volume.PLANES
                    }
                    stale = [
                        plane
                        for plane in volume.PLANES
                        if self._plane_drawn.get(plane) != wanted[plane]
                    ]
                    vol = volume.cached(key)
                    if self.view_mode != "mpr" or self.mpr_status != "ready" or not stale:
                        self._mpr_refreshing = False
                        done = True
                        return
                    if vol is None:
                        # Dropped from the cache to make room for other volumes.
                        self.mpr_status = ""
                        self._mpr_refreshing = False
                        done = missing = True
                        break
                images = await asyncio.to_thread(
                    _render_planes, vol, {p: wanted[p][1] for p in stale}, *window
                )
                async with self:
                    if self._volume_key != key:
                        continue
                    drawn = dict(self._plane_drawn)
                    for plane, image in images.items():
                        setattr(self, f"{plane}_image", image)
                        drawn[plane] = wanted[plane]
                    self._plane_drawn = drawn
        finally:
            if not done:
                async with self:
                    self._mpr_refreshing = False
        if missing:
            await self._load_volume(rebuild=True)

    def _update_crosshair_info(self, vol: volume.Volume | None = None):
        vol = vol or volume.cached(self._volume_key)
        if vol is None:
            return
        z, y, x = (self.plane_index[plane] for plane in volume.PLANES)
        px, py, pz = vol.position(z, y, x)
        self.crosshair_position = (
            f"{'L' if px >= 0 else 'R'} {abs(px):.1f} · "
            f"{'P' if py >= 0 else 'A'} {abs(py):.1f} · "
            f"{'S' if pz >= 0 else 'I'} {abs(pz):.1f} mm"
        )
        value = vol.value(z, y, x)
        text = f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"
        self.crosshair_value = f"{text} HU" if self.modality == "CT" else text

    def _move_plane(self, plane: str, index: int, from_slider: bool = False):
        """Show slice `index` (clamped) of `plane`; refresh_image redraws."""
        count = self.plane_count.get(plane, 0)
        if not count:
            return None
        self.active_plane = plane
        index = max(0, min(count - 1, index))
        if index == self.plane_index[plane]:
            return None
        self.plane_index[plane] = index
        if not from_slider:
            self.plane_slider_key += 1
        self._update_crosshair_info()
        return DicomViewerState.refresh_image

    @rx.event
    def scroll_plane(self, delta_y: float, plane: str):
        """Mouse wheel over a plane: down is its next slice, up the previous."""
        if delta_y and plane in self.plane_index:
            step = 1 if delta_y > 0 else -1
            return self._move_plane(plane, self.plane_index[plane] + step)

    @rx.event
    def set_plane_index(self, plane: str, value: str):
        try:
            index = int(float(value))
        except ValueError as e:
            logging.exception(f"Error setting the {plane} slice: {e}")
            return
        return self._move_plane(plane, index, from_slider=True)

    @rx.event
    def click_plane(self, fx: float, fy: float, plane: str):
        """A click in a plane moves the crosshair there, in the other two planes."""
        if plane not in volume.SCREEN_AXES or not (0 <= fx <= 1 and 0 <= fy <= 1):
            return None
        self.active_plane = plane
        horizontal, vertical = volume.SCREEN_AXES[plane]
        changed = False
        for other, fraction in ((horizontal, fx), (vertical, fy)):
            count = self.plane_count.get(other, 0)
            index = min(count - 1, int(fraction * count))
            if other == "axial":
                index = count - 1 - index
            if count and index != self.plane_index[other]:
                self.plane_index[other] = index
                changed = True
        if not changed:
            return None
        self.plane_slider_key += 1
        self._update_crosshair_info()
        return DicomViewerState.refresh_image

    @rx.event
    def toggle_crosshair(self):
        self.show_crosshair = not self.show_crosshair

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
