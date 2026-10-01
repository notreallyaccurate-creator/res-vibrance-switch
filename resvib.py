"""Res & Vibrance Switch core: displays, resolution, colour, process watching and config.

Usage:
    python resvib.py run              watch for games and switch automatically
    python resvib.py apply <profile>  apply a profile once ("desktop" or a game exe)
    python resvib.py status           show displays with their resolution and vibrance
    python resvib.py modes            list resolutions supported by the main display
"""

import ctypes
import json
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

EXE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
APPDATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "ResVibranceSwitch"
# A config.json next to the exe means "portable mode"; otherwise settings live in %APPDATA%.
DATA_DIR = EXE_DIR if (EXE_DIR / "config.json").exists() else APPDATA_DIR
CONFIG_PATH = DATA_DIR / "config.json"
STATE_PATH = DATA_DIR / "state.json"

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


# --------------------------------------------------------------------------
# Displays
# --------------------------------------------------------------------------

class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class DISPLAYCONFIG_PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", ctypes.c_uint32), ("modeInfoIdx", ctypes.c_uint32),
                ("statusFlags", ctypes.c_uint32)]


class DISPLAYCONFIG_PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", ctypes.c_uint32), ("modeInfoIdx", ctypes.c_uint32),
                ("outputTechnology", ctypes.c_uint32), ("rotation", ctypes.c_uint32),
                ("scaling", ctypes.c_uint32), ("refreshNumerator", ctypes.c_uint32),
                ("refreshDenominator", ctypes.c_uint32), ("scanLineOrdering", ctypes.c_uint32),
                ("targetAvailable", ctypes.c_int), ("statusFlags", ctypes.c_uint32)]


class DISPLAYCONFIG_PATH_INFO(ctypes.Structure):
    _fields_ = [("sourceInfo", DISPLAYCONFIG_PATH_SOURCE_INFO), ("targetInfo", DISPLAYCONFIG_PATH_TARGET_INFO),
                ("flags", ctypes.c_uint32)]


class DISPLAYCONFIG_MODE_INFO(ctypes.Structure):
    _fields_ = [("infoType", ctypes.c_uint32), ("id", ctypes.c_uint32), ("adapterId", LUID),
                ("data", ctypes.c_byte * 48)]


class DISPLAYCONFIG_DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("size", ctypes.c_uint32), ("adapterId", LUID),
                ("id", ctypes.c_uint32)]


class DISPLAYCONFIG_SOURCE_DEVICE_NAME(ctypes.Structure):
    _fields_ = [("header", DISPLAYCONFIG_DEVICE_INFO_HEADER), ("viewGdiDeviceName", wintypes.WCHAR * 32)]


class DISPLAYCONFIG_TARGET_DEVICE_NAME(ctypes.Structure):
    _fields_ = [("header", DISPLAYCONFIG_DEVICE_INFO_HEADER), ("flags", ctypes.c_uint32),
                ("outputTechnology", ctypes.c_uint32), ("edidManufactureId", ctypes.c_uint16),
                ("edidProductCodeId", ctypes.c_uint16), ("connectorInstance", ctypes.c_uint32),
                ("monitorFriendlyDeviceName", wintypes.WCHAR * 64), ("monitorDevicePath", wintypes.WCHAR * 128)]


DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1
DISPLAY_DEVICE_PRIMARY_DEVICE = 0x4
QDC_ONLY_ACTIVE_PATHS = 0x2


class Display:
    def __init__(self, name, friendly, primary):
        self.name = name
        self.friendly = friendly
        self.primary = primary

    @property
    def number(self):
        digits = "".join(ch for ch in self.name if ch.isdigit())
        return int(digits) if digits else 0

    @property
    def label(self):
        text = f"Display {self.number}"
        if self.friendly:
            text += f"  ·  {self.friendly}"
        return text + ("  (main)" if self.primary else "")


class DISPLAYCONFIG_ADVANCED_COLOR_INFO(ctypes.Structure):
    _fields_ = [("header", DISPLAYCONFIG_DEVICE_INFO_HEADER), ("value", ctypes.c_uint32),
                ("colorEncoding", ctypes.c_uint32), ("bitsPerColorChannel", ctypes.c_uint32)]


def _active_paths():
    """(GDI device name, path) for every active display path."""
    try:
        n_paths, n_modes = ctypes.c_uint32(), ctypes.c_uint32()
        if user32.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths), ctypes.byref(n_modes)):
            return []
        paths = (DISPLAYCONFIG_PATH_INFO * n_paths.value)()
        modes = (DISPLAYCONFIG_MODE_INFO * n_modes.value)()
        if user32.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths), paths,
                                     ctypes.byref(n_modes), modes, None):
            return []
        result = []
        for path in paths[:n_paths.value]:
            source = DISPLAYCONFIG_SOURCE_DEVICE_NAME()
            source.header.type, source.header.size = 1, ctypes.sizeof(source)
            source.header.adapterId, source.header.id = path.sourceInfo.adapterId, path.sourceInfo.id
            if user32.DisplayConfigGetDeviceInfo(ctypes.byref(source)) == 0:
                result.append((source.viewGdiDeviceName, path))
        return result
    except (OSError, AttributeError):
        return []


def _target_info(path, struct, info_type):
    info = struct()
    info.header.type, info.header.size = info_type, ctypes.sizeof(info)
    info.header.adapterId, info.header.id = path.targetInfo.adapterId, path.targetInfo.id
    return info if user32.DisplayConfigGetDeviceInfo(ctypes.byref(info)) == 0 else None


def _monitor_names():
    """GDI device name -> monitor model name (e.g. '\\\\.\\DISPLAY1' -> 'VG259QM')."""
    names = {}
    for gdi_name, path in _active_paths():
        target = _target_info(path, DISPLAYCONFIG_TARGET_DEVICE_NAME, 2)
        if target:
            names[gdi_name] = target.monitorFriendlyDeviceName
    return names


def advanced_color_enabled(display):
    """True when Windows HDR / Auto Color Management is on, which blocks gamma-ramp adjustments."""
    for gdi_name, path in _active_paths():
        if gdi_name == display:
            info = _target_info(path, DISPLAYCONFIG_ADVANCED_COLOR_INFO, 9)
            return bool(info and info.value & 0x2)
    return False


def list_displays():
    """Displays attached to the desktop, main display first."""
    names = _monitor_names()
    displays = []
    dev = DISPLAY_DEVICEW(cb=ctypes.sizeof(DISPLAY_DEVICEW))
    i = 0
    while user32.EnumDisplayDevicesW(None, i, ctypes.byref(dev), 0):
        if dev.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
            displays.append(Display(dev.DeviceName, names.get(dev.DeviceName, ""),
                                    bool(dev.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE)))
        i += 1
    displays.sort(key=lambda d: (not d.primary, d.number))
    return displays


def primary_display_name():
    displays = list_displays()
    if not displays:
        raise RuntimeError("No display found")
    return displays[0].name


def resolve_display(name):
    """The named display if it's connected, otherwise the main display."""
    displays = list_displays()
    if name and any(d.name == name for d in displays):
        return name
    if not displays:
        raise RuntimeError("No display found")
    return displays[0].name


# --------------------------------------------------------------------------
# Resolution (Win32 ChangeDisplaySettingsEx)
# --------------------------------------------------------------------------

class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", wintypes.LONG),
        ("dmPositionY", wintypes.LONG),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


ENUM_CURRENT_SETTINGS = -1
DM_PELSWIDTH = 0x80000
DM_PELSHEIGHT = 0x100000
DM_DISPLAYFREQUENCY = 0x400000
CDS_TEST = 0x2
DISP_CHANGE_MESSAGES = {
    0: "success",
    1: "restart required",
    -1: "display driver failed the mode",
    -2: "mode not supported",
    -3: "unable to write settings to registry",
    -4: "invalid flags",
    -5: "invalid parameter",
}

user32.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DEVMODEW)]
user32.ChangeDisplaySettingsExW.argtypes = [
    wintypes.LPCWSTR, ctypes.POINTER(DEVMODEW), wintypes.HWND, wintypes.DWORD, wintypes.LPVOID,
]
user32.ChangeDisplaySettingsExW.restype = wintypes.LONG


def get_mode(display):
    dm = DEVMODEW(dmSize=ctypes.sizeof(DEVMODEW))
    if not user32.EnumDisplaySettingsW(display, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
        raise RuntimeError(f"Could not read display settings for {display}")
    return dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency


def list_modes(display):
    modes = set()
    dm = DEVMODEW(dmSize=ctypes.sizeof(DEVMODEW))
    i = 0
    while user32.EnumDisplaySettingsW(display, i, ctypes.byref(dm)):
        modes.add((dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency))
        i += 1
    return sorted(modes, reverse=True)


def set_mode(display, width, height, refresh=None, test_only=False):
    dm = DEVMODEW(dmSize=ctypes.sizeof(DEVMODEW))
    dm.dmPelsWidth = width
    dm.dmPelsHeight = height
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
    if refresh:
        dm.dmDisplayFrequency = refresh
        dm.dmFields |= DM_DISPLAYFREQUENCY
    # Flags 0 = change dynamically without saving to the registry, so a reboot
    # always brings back your normal Windows resolution.
    flags = CDS_TEST if test_only else 0
    result = user32.ChangeDisplaySettingsExW(display, ctypes.byref(dm), None, flags, None)
    if result != 0:
        msg = DISP_CHANGE_MESSAGES.get(result, f"error {result}")
        raise RuntimeError(f"Could not set {width}x{height}@{refresh or 'any'}Hz: {msg}")


# --------------------------------------------------------------------------
# Digital vibrance: NVIDIA NvAPI
# --------------------------------------------------------------------------

class NV_DISPLAY_DVC_INFO(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("currentLevel", ctypes.c_int32),
        ("minLevel", ctypes.c_int32),
        ("maxLevel", ctypes.c_int32),
    ]


class NvAPI:
    NAME = "NVIDIA"
    _IDS = {
        "Initialize": 0x0150E828,
        "GetAssociatedNvidiaDisplayHandle": 0x35C29134,
        "GetDVCInfo": 0x4085DE45,
        "SetDVCLevel": 0x172409B4,
    }

    def __init__(self):
        try:
            dll = ctypes.CDLL("nvapi64.dll")
        except OSError as e:
            raise RuntimeError("nvapi64.dll not found (no NVIDIA driver)") from e
        query = dll.nvapi_QueryInterface
        query.restype = ctypes.c_void_p
        query.argtypes = [ctypes.c_uint32]

        def fn(name, *argtypes):
            ptr = query(self._IDS[name])
            if not ptr:
                raise RuntimeError(f"NvAPI function {name} not available")
            return ctypes.CFUNCTYPE(ctypes.c_int, *argtypes)(ptr)

        self._initialize = fn("Initialize")
        self._get_handle = fn("GetAssociatedNvidiaDisplayHandle", ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p))
        self._get_dvc = fn("GetDVCInfo", ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(NV_DISPLAY_DVC_INFO))
        self._set_dvc = fn("SetDVCLevel", ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32)
        self._check(self._initialize(), "Initialize")

    @staticmethod
    def _check(status, what):
        if status != 0:
            raise RuntimeError(f"NvAPI {what} failed (status {status})")

    def _handle(self, display):
        handle = ctypes.c_void_p()
        self._check(self._get_handle(display.encode("ascii"), ctypes.byref(handle)),
                    f"GetAssociatedNvidiaDisplayHandle({display})")
        return handle

    def owns(self, display):
        try:
            self._handle(display)
            return True
        except RuntimeError:
            return False

    def get_dvc(self, display):
        info = NV_DISPLAY_DVC_INFO(version=ctypes.sizeof(NV_DISPLAY_DVC_INFO) | (1 << 16))
        self._check(self._get_dvc(self._handle(display), 0, ctypes.byref(info)), "GetDVCInfo")
        return info.currentLevel, info.minLevel, info.maxLevel

    # The NVIDIA Control Panel shows vibrance as 50%-100%, which maps to DVC level 0-63.
    def get_vibrance_percent(self, display):
        level, lo, hi = self.get_dvc(display)
        return round(50 + (level - lo) * 50 / (hi - lo))

    def set_vibrance_percent(self, display, percent):
        _, lo, hi = self.get_dvc(display)
        percent = max(50, min(100, percent))
        self._check(self._set_dvc(self._handle(display), 0, round(lo + (percent - 50) * (hi - lo) / 50)),
                    "SetDVCLevel")


# --------------------------------------------------------------------------
# Digital vibrance: AMD ADL (Radeon "Saturation")
# --------------------------------------------------------------------------

class ADLAdapterInfo(ctypes.Structure):
    _fields_ = [
        ("iSize", ctypes.c_int), ("iAdapterIndex", ctypes.c_int), ("strUDID", ctypes.c_char * 256),
        ("iBusNumber", ctypes.c_int), ("iDeviceNumber", ctypes.c_int), ("iFunctionNumber", ctypes.c_int),
        ("iVendorID", ctypes.c_int), ("strAdapterName", ctypes.c_char * 256),
        ("strDisplayName", ctypes.c_char * 256), ("iPresent", ctypes.c_int), ("iExist", ctypes.c_int),
        ("strDriverPath", ctypes.c_char * 256), ("strDriverPathExt", ctypes.c_char * 256),
        ("strPNPString", ctypes.c_char * 256), ("iOSDisplayIndex", ctypes.c_int),
    ]


class ADLDisplayID(ctypes.Structure):
    _fields_ = [("iDisplayLogicalIndex", ctypes.c_int), ("iDisplayPhysicalIndex", ctypes.c_int),
                ("iDisplayLogicalAdapterIndex", ctypes.c_int), ("iDisplayPhysicalAdapterIndex", ctypes.c_int)]


class ADLDisplayInfo(ctypes.Structure):
    _fields_ = [
        ("displayID", ADLDisplayID), ("iDisplayControllerIndex", ctypes.c_int),
        ("strDisplayName", ctypes.c_char * 256), ("strDisplayManufacturerName", ctypes.c_char * 256),
        ("iDisplayType", ctypes.c_int), ("iDisplayOutputType", ctypes.c_int), ("iDisplayConnector", ctypes.c_int),
        ("iDisplayInfoMask", ctypes.c_int), ("iDisplayInfoValue", ctypes.c_int),
    ]


ADL_DISPLAY_COLOR_SATURATION = 1 << 2
ADL_DISPLAY_CONNECTED_AND_MAPPED = 0x3
_malloc = ctypes.cdll.msvcrt.malloc
_malloc.restype = ctypes.c_void_p
_malloc.argtypes = [ctypes.c_size_t]
ADL_MALLOC = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_int)(lambda size: _malloc(size))


class AmdADL:
    """Maps vibrance 50-100% onto Radeon saturation default..max (usually 100..200)."""

    NAME = "AMD"

    def __init__(self):
        try:
            self.dll = ctypes.CDLL("atiadlxx.dll")
        except OSError as e:
            raise RuntimeError("atiadlxx.dll not found (no AMD driver)") from e
        self.ctx = ctypes.c_void_p()
        if self.dll.ADL2_Main_Control_Create(ADL_MALLOC, 1, ctypes.byref(self.ctx)) != 0:
            raise RuntimeError("ADL initialisation failed")
        count = ctypes.c_int()
        self.dll.ADL2_Adapter_NumberOfAdapters_Get(self.ctx, ctypes.byref(count))
        self.adapters = {}
        if count.value > 0:
            infos = (ADLAdapterInfo * count.value)()
            for info in infos:
                info.iSize = ctypes.sizeof(ADLAdapterInfo)
            if self.dll.ADL2_Adapter_AdapterInfo_Get(self.ctx, infos, ctypes.sizeof(infos)) == 0:
                for info in infos:
                    name = info.strDisplayName.decode(errors="ignore")
                    if info.iPresent and name:
                        self.adapters.setdefault(name, info.iAdapterIndex)

    def _target(self, display):
        adapter = self.adapters.get(display)
        if adapter is None:
            return None
        count = ctypes.c_int()
        info = ctypes.POINTER(ADLDisplayInfo)()
        if self.dll.ADL2_Display_DisplayInfo_Get(self.ctx, adapter, ctypes.byref(count), ctypes.byref(info), 0):
            return None
        for i in range(count.value):
            d = info[i]
            if (d.iDisplayInfoValue & ADL_DISPLAY_CONNECTED_AND_MAPPED) == ADL_DISPLAY_CONNECTED_AND_MAPPED \
                    and d.displayID.iDisplayLogicalAdapterIndex == adapter:
                return adapter, d.displayID.iDisplayLogicalIndex
        return None

    def owns(self, display):
        return self._target(display) is not None

    def _saturation(self, display):
        target = self._target(display)
        if not target:
            raise RuntimeError(f"No AMD display for {display}")
        cur, default, lo, hi, step = (ctypes.c_int() for _ in range(5))
        if self.dll.ADL2_Display_Color_Get(self.ctx, *target, ADL_DISPLAY_COLOR_SATURATION, ctypes.byref(cur),
                                           ctypes.byref(default), ctypes.byref(lo), ctypes.byref(hi),
                                           ctypes.byref(step)):
            raise RuntimeError("ADL could not read saturation")
        return target, cur.value, default.value, hi.value

    def get_vibrance_percent(self, display):
        _, cur, default, hi = self._saturation(display)
        return round(50 + max(0, cur - default) * 50 / max(1, hi - default))

    def set_vibrance_percent(self, display, percent):
        target, _, default, hi = self._saturation(display)
        value = round(default + (max(50, min(100, percent)) - 50) * (hi - default) / 50)
        if self.dll.ADL2_Display_Color_Set(self.ctx, *target, ADL_DISPLAY_COLOR_SATURATION, value):
            raise RuntimeError("ADL could not set saturation")


# --------------------------------------------------------------------------
# Colour engine: vibrance backends + gamma ramp (brightness / contrast / gamma)
# --------------------------------------------------------------------------

GammaRamp = wintypes.WORD * 768
gdi32.CreateDCW.restype = wintypes.HDC
gdi32.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.GetDeviceGammaRamp.argtypes = [wintypes.HDC, ctypes.c_void_p]
gdi32.SetDeviceGammaRamp.argtypes = [wintypes.HDC, ctypes.c_void_p]

NEUTRAL_COLOR = {"brightness": 50, "contrast": 50, "gamma": 1.0}


def color_adjusted(profile):
    return any(abs(profile.get(k, v) - v) > 1e-6 for k, v in NEUTRAL_COLOR.items())


def build_ramp(brightness, contrast, gamma):
    """Brightness/contrast 0-100 (50 = unchanged), gamma 0.5-2.5 (1.0 = unchanged)."""
    ramp = GammaRamp()
    c, b = contrast / 50, (brightness - 50) / 100
    for i in range(256):
        v = (i / 255) ** (1 / gamma)
        v = (v - 0.5) * c + 0.5 + b
        ramp[i] = ramp[256 + i] = ramp[512 + i] = int(min(max(v, 0.0), 1.0) * 65535)
    return ramp


class Color:
    def __init__(self):
        self.backends = []
        self.errors = []
        for backend in (NvAPI, AmdADL):
            try:
                self.backends.append(backend())
            except Exception as e:  # missing driver, unexpected DLL version, ...
                self.errors.append(f"{backend.NAME}: {e}")
        self._owners = {}
        self._original_ramps = {}
        self._ramp_support = {}

    def _backend(self, display):
        if display not in self._owners:
            self._owners[display] = next((b for b in self.backends if b.owns(display)), None)
        return self._owners[display]

    def vendor(self, display):
        backend = self._backend(display)
        return backend.NAME if backend else None

    def supports_vibrance(self, display):
        return self._backend(display) is not None

    def get_vibrance(self, display):
        backend = self._backend(display)
        return backend.get_vibrance_percent(display) if backend else None

    def set_vibrance(self, display, percent):
        backend = self._backend(display)
        if backend:
            backend.set_vibrance_percent(display, percent)

    def _dc(self, display):
        hdc = gdi32.CreateDCW(display, None, None, None)
        if not hdc:
            raise RuntimeError(f"Could not open {display} for colour adjustment")
        return hdc

    def supports_adjustment(self, display):
        """Whether Windows lets us change this display's gamma ramp (it doesn't under HDR/Advanced Color)."""
        if display not in self._ramp_support:
            try:
                hdc = self._dc(display)
                try:
                    self._ramp_support[display] = bool(gdi32.GetDeviceGammaRamp(hdc, GammaRamp()))
                finally:
                    gdi32.DeleteDC(hdc)
            except RuntimeError:
                self._ramp_support[display] = False
        return self._ramp_support[display]

    def adjustment_unavailable_reason(self, display):
        if self.supports_adjustment(display):
            return None
        if advanced_color_enabled(display):
            return ("Unavailable on this display: Windows blocks brightness/contrast/gamma changes while "
                    "HDR or Auto Color Management is on (Settings > System > Display).")
        return "Unavailable on this display: Windows doesn't allow gamma changes for it."

    def set_adjustment(self, display, brightness, contrast, gamma):
        hdc = self._dc(display)
        try:
            if display not in self._original_ramps:
                original = GammaRamp()
                if gdi32.GetDeviceGammaRamp(hdc, original):
                    self._original_ramps[display] = original
            if not gdi32.SetDeviceGammaRamp(hdc, build_ramp(brightness, contrast, gamma)):
                raise RuntimeError("Windows rejected these brightness/contrast/gamma values - "
                                   "try values closer to the defaults")
        finally:
            gdi32.DeleteDC(hdc)

    def reset_adjustment(self, display):
        original = self._original_ramps.pop(display, None)
        if original is None:
            return
        hdc = self._dc(display)
        try:
            gdi32.SetDeviceGammaRamp(hdc, original)
        finally:
            gdi32.DeleteDC(hdc)

    def reset_all_adjustments(self):
        for display in list(self._original_ramps):
            try:
                self.reset_adjustment(display)
            except RuntimeError:
                pass


# --------------------------------------------------------------------------
# Processes
# --------------------------------------------------------------------------

class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


TH32CS_SNAPPROCESS = 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


def running_processes():
    """Map of pid -> lowercase exe name for every running process."""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        return {}
    procs = {}
    try:
        entry = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            procs[entry.th32ProcessID] = entry.szExeFile.lower()
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return procs


def running_exes():
    return set(running_processes().values())


def process_name(pid):
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name.lower()
        return None
    finally:
        kernel32.CloseHandle(handle)


def foreground_exe():
    """Lowercase exe name of the app that owns the focused window."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return process_name(pid.value) or running_processes().get(pid.value)


# --------------------------------------------------------------------------
# Config & crash-recovery state
# --------------------------------------------------------------------------

DEFAULT_HOTKEYS = {"toggle_watch": "ctrl+alt+p", "desktop": "ctrl+alt+d", "show": ""}
DEFAULT_SETTINGS = {"poll_seconds": 2, "auto_watch": True, "theme": "dark", "notifications": True,
                    "check_updates": True}


def default_config():
    desktop = {}
    for d in list_displays():
        width, height, refresh = get_mode(d.name)
        desktop[d.name] = {"width": width, "height": height, "refresh": refresh}
        if d.primary:
            desktop[d.name]["vibrance"] = 50
    return {"version": 2, "setup_complete": False, **DEFAULT_SETTINGS, "desktop": desktop, "games": {},
            "hotkeys": dict(DEFAULT_HOTKEYS)}


def migrate(config):
    if "version" not in config:
        config["setup_complete"] = True  # existing users skip the first-run wizard
    if "width" in config.get("desktop", {}):  # v1 stored one desktop profile for the main display
        config["desktop"] = {primary_display_name(): config["desktop"]}
    for key, value in DEFAULT_SETTINGS.items():
        config.setdefault(key, value)
    config.setdefault("setup_complete", True)
    config.setdefault("desktop", {})
    config.setdefault("hotkeys", dict(DEFAULT_HOTKEYS))
    config["games"] = {exe.lower(): p for exe, p in config.get("games", {}).items()}
    for d in list_displays():
        if d.name not in config["desktop"]:
            width, height, refresh = get_mode(d.name)
            config["desktop"][d.name] = {"width": width, "height": height, "refresh": refresh}
    config["version"] = 2
    return config


def load_config():
    if not CONFIG_PATH.exists():
        save_config(default_config())
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return migrate(json.load(f))


def save_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def desktop_profile(config, display):
    if display not in config["desktop"]:
        width, height, refresh = get_mode(display)
        config["desktop"][display] = {"width": width, "height": height, "refresh": refresh}
    return config["desktop"][display]


def read_state():
    """Displays currently switched to a game profile: {display: exe}. Survives crashes."""
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(state):
    try:
        if state:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
        else:
            STATE_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def mark_game_state(display, exe):
    state = read_state()
    state[display] = exe
    _write_state(state)


def clear_game_state(display):
    state = read_state()
    if state.pop(display, None) is not None:
        _write_state(state)


# --------------------------------------------------------------------------
# Applying profiles
# --------------------------------------------------------------------------

_apply_lock = threading.RLock()


def log_line(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def describe(profile):
    text = f"{profile['width']}x{profile['height']}"
    if profile.get("refresh"):
        text += f" @ {profile['refresh']}Hz"
    if "vibrance" in profile:
        text += f", vibrance {profile['vibrance']}%"
    if color_adjusted(profile):
        text += (f", brightness {profile.get('brightness', 50)} / contrast {profile.get('contrast', 50)}"
                 f" / gamma {profile.get('gamma', 1.0):.2f}")
    return text


def apply_mode(profile, display):
    width, height, refresh = profile["width"], profile["height"], profile.get("refresh")
    cur_w, cur_h, cur_hz = get_mode(display)
    if (width, height) != (cur_w, cur_h) or (refresh and refresh != cur_hz):
        set_mode(display, width, height, refresh)


def apply_color(profile, display, color):
    with _apply_lock:
        if "vibrance" in profile:
            color.set_vibrance(display, profile["vibrance"])
        if color_adjusted(profile) and color.supports_adjustment(display):
            color.set_adjustment(display, *(profile.get(k, v) for k, v in NEUTRAL_COLOR.items()))
        else:
            color.reset_adjustment(display)


def apply_profile(profile, display, color, label, log=log_line):
    with _apply_lock:
        apply_mode(profile, display)
        apply_color(profile, display, color)
    log(f"{label}: {describe(profile)}")


# --------------------------------------------------------------------------
# Watcher
# --------------------------------------------------------------------------

class Watcher:
    """Background thread that applies the matching profile as games start, stop, gain or lose focus."""

    def __init__(self, config, color, log=log_line, namer=None, on_switch=None):
        self.config = config
        self.color = color
        self.log = log
        self.namer = namer or (lambda exe: exe)
        self.on_switch = on_switch or (lambda event, exe: None)
        self.active = None          # running game's exe, "desktop", or None before the first pass
        self.active_display = None  # display the active game profile is on
        self.focused = True         # whether the active game is the focused window
        self.wake = threading.Event()
        self._force = False
        self._stop = threading.Event()
        self._thread = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def in_game(self):
        return self.active not in (None, "desktop")

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self.active = None
        self._thread = threading.Thread(target=self._loop, daemon=True, name="watcher")
        self._thread.start()

    def stop(self, restore_desktop=True):
        self._stop.set()
        self.wake.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        if restore_desktop and self.in_game:
            self._safe(self._restore_desktop, self.active_display)
        self.active = self.active_display = None

    def update_config(self, config):
        """Swap in an edited config and re-apply the current profile with the new values."""
        self.config = config
        self._force = True
        self.wake.set()

    def poke(self):
        """Re-check right away (called when the foreground window changes)."""
        self.wake.set()

    def _safe(self, fn, *args):
        try:
            fn(*args)
            return True
        except RuntimeError as e:
            self.log(f"Error: {e}")
            return False

    def _restore_desktop(self, display):
        label = "Desktop" if len(self.config["desktop"]) < 2 else f"Desktop ({display.strip(chr(92) + '.')})"
        apply_profile(desktop_profile(self.config, display), display, self.color, label, self.log)
        clear_game_state(display)

    def _apply_game(self, exe):
        profile = self.config["games"][exe]
        display = resolve_display(profile.get("display"))
        if self.in_game and self.active_display != display:
            self._restore_desktop(self.active_display)
        desktop = desktop_profile(self.config, display)
        if "vibrance" not in desktop and self.color.supports_vibrance(display):
            desktop["vibrance"] = self.color.get_vibrance(display)  # remember what to come back to
        mark_game_state(display, exe)
        self.active, self.active_display, self.focused = exe, display, True
        apply_profile(profile, display, self.color, self.namer(exe), self.log)

    def _apply_desktop(self):
        try:
            if self.in_game:
                self._restore_desktop(self.active_display)
            else:
                connected = {d.name for d in list_displays()}
                for display in [d for d in self.config["desktop"] if d in connected]:
                    self._restore_desktop(display)
        finally:
            self.active, self.active_display = "desktop", None

    def _check_focus(self, exe):
        profile = self.config["games"].get(exe)
        if profile is None:
            return
        focused = foreground_exe() == exe if profile.get("alt_tab", True) else True
        if focused == self.focused:
            return
        self.focused = focused
        display = self.active_display
        if focused:
            apply_color(profile, display, self.color)
            self.log(f"{self.namer(exe)}: back in game")
            self.on_switch("focus_in", exe)
        else:
            apply_color(desktop_profile(self.config, display), display, self.color)
            self.log(f"{self.namer(exe)}: alt-tabbed - desktop colours until you return")
            self.on_switch("focus_out", exe)

    def _loop(self):
        self.log(f"Watching for {len(self.config['games'])} game(s)")
        while not self._stop.is_set():
            config = self.config
            running = running_exes()
            target = next((exe for exe in config["games"] if exe in running), "desktop")
            if target != self.active or self._force:
                first = self.active is None
                self._force = False
                if target == "desktop":
                    self._safe(self._apply_desktop)
                else:
                    self._safe(self._apply_game, target)
                self.on_switch("startup" if first and target == "desktop" else
                               "desktop" if target == "desktop" else "game", target)
            elif target != "desktop":
                self._safe(self._check_focus, target)
            self.wake.wait(config.get("poll_seconds", 2))
            self.wake.clear()
        self.log("Stopped watching")


def watch(config, color):
    watcher = Watcher(config, color)
    watcher.start()
    print("Ctrl+C to stop")
    try:
        while watcher.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        watcher.stop()


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "modes":
        for w, h, hz in list_modes(primary_display_name()):
            print(f"{w}x{h} @ {hz}Hz")
        return
    color = Color()
    if cmd == "status":
        for d in list_displays():
            w, h, hz = get_mode(d.name)
            vib = color.get_vibrance(d.name)
            print(f"{d.label}\n  {d.name}: {w}x{h} @ {hz}Hz, vibrance "
                  f"{f'{vib}% ({color.vendor(d.name)})' if vib is not None else 'not supported'}")
        return
    config = load_config()
    if cmd == "run":
        watch(config, color)
    elif cmd == "apply" and len(args) == 2:
        name = args[1].lower()
        if name == "desktop":
            for display in config["desktop"]:
                apply_profile(config["desktop"][display], display, color, f"desktop {display}")
        elif name in config["games"]:
            profile = config["games"][name]
            apply_profile(profile, resolve_display(profile.get("display")), color, name)
        else:
            raise SystemExit(f"No profile named '{args[1]}'")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
