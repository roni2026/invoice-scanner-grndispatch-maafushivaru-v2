# scan_tab.py
# Adds a "Scan" tab to an existing Tkinter app (MaafushivaruHub).
#
# Two capture methods, selectable from a dropdown:
#   1. WIA — direct scanner control via pywin32 (built-in, Windows only).
#      All scan settings (DPI, color, sides, destination, ...) apply here.
#   2. HP software — presses the Scan button inside the original Windows
#      HP scan application (HP Scan / HP Scan Extended / HP Smart).
#      All scan settings are greyed out in this mode, because the HP
#      software applies its own settings.
#      If the button cannot be found automatically, the app offers a
#      "teach" mode: you click the HP software's Scan button yourself
#      while it listens, and it remembers exactly which button you
#      clicked (window title, label, control class) for next time.
#
# Requires: Windows; pip install pywin32 pillow pypdf2
# Optional: pip install pywinauto  (lets HP mode find/teach/press buttons
#           in modern Store apps like HP Smart / HP Scan and Capture)

import os
import io
import sys
import re
import time
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import warnings
warnings.simplefilter("ignore", Image.DecompressionBombWarning)
Image.MAX_IMAGE_PIXELS = None

from PIL import Image
from PyPDF2 import PdfWriter
try:
    import pythoncom
    import win32com.client  # pywin32
    import win32api
    import win32gui
    import win32con
except ImportError:
    pythoncom = None
    win32com = None
    win32api = None
    win32gui = None
    win32con = None


# --- WIA constants (subset) ---
WIA_DeviceType_ScannerDeviceType = 1

# Common WIA property IDs (useful subset)
WIA_IPS_PAGE_SIZE           = 3097   # 0=Auto, 1=Letter, 2=Legal, 3=A4...
WIA_IPS_XRES                = 6147   # DPI X
WIA_IPS_YRES                = 6148   # DPI Y
WIA_IPS_CUR_INTENT          = 6146   # 1=Color, 2=Grayscale, 4=Text/BW
WIA_IPS_DOCUMENT_HANDLING_SELECT = 3088  # 1=Feeder, 2=Flatbed, 4=Duplex (combos allowed)
WIA_IPS_DOCUMENT_HANDLING_STATUS = 3087  # status (ADF ready etc.)
WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES = 3086
WIA_IPS_ORIENTATION         = 6151   # 0=Portrait, 1=Landscape, 2=Rot180, 3=Rot270, 4=Auto if driver supports

# Document handling flags
FEEDER  = 0x0001
FLATBED = 0x0002
DUPLEX  = 0x0004

# Page size values (WIA)
WIA_PAGE_AUTO   = 0
WIA_PAGE_LETTER = 1
WIA_PAGE_LEGAL  = 2
WIA_PAGE_A4     = 3

# Intent flags
INTENT_COLOR     = 0x0001
INTENT_GRAYSCALE = 0x0002
INTENT_TEXT      = 0x0004  # often B/W

# Scan methods shown in the dropdown
METHOD_WIA = "wia"
METHOD_HP  = "hp"
METHOD_LABELS = {
    METHOD_WIA: "WIA — direct scanner (built-in)",
    METHOD_HP:  "HP software — press its Scan button",
}

# Defaults for finding the HP software's window and its Scan button.
# Both are case-insensitive "contains" matches and editable in the tab.
DEFAULT_HP_WINDOW_TITLE = "HP"
DEFAULT_HP_BUTTON_TEXT  = "Scan"

# How long teach mode listens for your click
TEACH_TIMEOUT_SECONDS = 60


def _ensure_pywin32():
    if win32com is None or pythoncom is None:
        raise RuntimeError(
            "pywin32 not installed correctly. Run:\n"
            "  pip install pywin32\n"
            "  python -m pywin32_postinstall -install"
        )


def list_wia_scanners():
    _ensure_pywin32()
    pythoncom.CoInitialize()
    try:
        dev_manager = win32com.client.Dispatch("WIA.DeviceManager")
        scanners = []
        for info in dev_manager.DeviceInfos:
            try:
                if info.Type == WIA_DeviceType_ScannerDeviceType:
                    scanners.append((info.DeviceID, info.Properties("Name").Value))
            except Exception:
                pass
        return scanners
    finally:
        pythoncom.CoUninitialize()


# ----------------------------------------------------------------------
# Finding and pressing the HP software's Scan button
# ----------------------------------------------------------------------
def _find_windows_by_title(title_kw):
    """All visible top-level windows whose title contains title_kw
    (case-insensitive). Returns [(hwnd, title), ...]."""
    hits = []

    def cb(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd):
                t = win32gui.GetWindowText(hwnd) or ""
                if t and title_kw.lower() in t.lower():
                    hits.append((hwnd, t))
        except Exception:
            pass

    win32gui.EnumWindows(cb, None)
    return hits


def _find_button_like_children(root_hwnd, text_kw):
    """All descendant controls whose own text contains text_kw
    (case-insensitive). Returns [(hwnd, text, class_name), ...]."""
    hits = []

    def cb(hwnd, _):
        try:
            txt = win32gui.GetWindowText(hwnd) or ""
            cls = win32gui.GetClassName(hwnd) or ""
            if txt and text_kw.lower() in txt.lower():
                hits.append((hwnd, txt, cls))
        except Exception:
            pass

    win32gui.EnumChildWindows(root_hwnd, cb, None)
    return hits


def _pick_best_button(candidates, button_kw):
    """Prefer a real Button-class control, then an exact text match,
    then an enabled one; fall back to the first candidate."""
    def score(item):
        _, txt, cls = item
        try:
            enabled = win32gui.IsWindowEnabled(item[0])
        except Exception:
            enabled = False
        return (
            1 if cls.lower() == "button" else 0,
            1 if txt.strip().lower() == button_kw.strip().lower() else 0,
            1 if enabled else 0,
        )
    return sorted(candidates, key=score, reverse=True)[0]


def _deliver_click(btn_hwnd):
    """Try to press the button. Returns (delivered: bool, how: str).
    BM_CLICK first (standard buttons); if that fails, a synthetic
    left-mouse down/up pair (custom-drawn buttons)."""
    BM_CLICK = 0x00F5
    try:
        ret = win32gui.SendMessageTimeout(
            btn_hwnd, BM_CLICK, 0, 0,
            win32con.SMTO_ABORTIFHUNG, 3000,
        )
        # pywin32 returns (rc, result); rc == 0 means the call failed/timed out
        if isinstance(ret, tuple):
            if ret[0] != 0:
                return True, "BM_CLICK"
        else:
            return True, "BM_CLICK"
    except Exception:
        pass

    # Fallback: synthetic mouse click messages
    try:
        lparam = (5 << 16) | 5  # click near the button's top-left area
        win32gui.PostMessage(btn_hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
        time.sleep(0.05)
        win32gui.PostMessage(btn_hwnd, win32con.WM_LBUTTONUP, 0, lparam)
        return True, "mouse messages"
    except Exception:
        return False, ""


def _press_button_via_uia(title_kw, button_kw, exact=False):
    """UI Automation press — needed for modern Store apps (HP Smart, HP
    Scan and Capture) whose buttons are not plain Win32 controls. Uses
    pywinauto IF installed. Returns (ok, message), or None when pywinauto
    is unavailable."""
    try:
        import pywinauto  # noqa: F401
        from pywinauto import Desktop
    except Exception:
        return None

    try:
        win = Desktop(backend="uia").window(title_re=f".*{re.escape(title_kw)}.*")
        if not win.exists(timeout=2):
            return None if exact else (False, f"UIA: no window matching '{title_kw}'")
        if exact:
            btn = win.child_window(title=button_kw, control_type="Button")
            if not btn.exists(timeout=1):
                btn = win.child_window(title_re=f".*{re.escape(button_kw)}.*",
                                       control_type="Button")
        else:
            btn = win.child_window(title_re=f".*{re.escape(button_kw)}.*",
                                   control_type="Button")
        if not btn.exists(timeout=2):
            return None if exact else (False, f"UIA: no button matching '{button_kw}'")
        try:
            btn.invoke()
        except Exception:
            btn.click_input()
        return True, "pressed via UI Automation"
    except Exception as e:
        return None if exact else (False, f"UIA error: {e}")


def _press_captured_win32(captured):
    """Replay a taught button: exact window title + exact button text and
    class. Returns (ok, message) when it found and tried the button, or
    None when nothing matches anymore (caller falls back to search)."""
    title = (captured.get("window_title") or "").strip()
    text = (captured.get("button_text") or "").strip()
    cls = (captured.get("button_class") or "").strip()
    if not title or not text:
        return None

    windows = _find_windows_by_title(title)
    if not windows:
        return None
    for root_hwnd, root_title in windows:
        for (h, txt, c) in _find_button_like_children(root_hwnd, text):
            if txt.strip().lower() != text.lower():
                continue
            if cls and c.lower() != cls.lower():
                continue
            # Exact taught match found — press it.
            try:
                if not win32gui.IsWindowEnabled(h):
                    return False, (
                        f"The remembered \"{txt}\" button was found in "
                        f"\"{root_title}\", but it is currently disabled "
                        f"(greyed out).\n\nThe HP software may still be "
                        f"starting up or waiting for the scanner — check its "
                        f"window, then try again.")
            except Exception:
                pass
            try:
                if win32gui.IsIconic(root_hwnd):
                    win32gui.ShowWindow(root_hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(root_hwnd)
            except Exception:
                pass
            delivered, how = _deliver_click(h)
            if delivered:
                return True, f"Pressed remembered button \"{txt}\" in \"{root_title}\" ({how})."
            return False, (
                f"The remembered \"{txt}\" button was found in "
                f"\"{root_title}\", but the click command could not be "
                f"delivered (the window is not responding to it).\n\n"
                f"Try clicking once inside the HP window yourself, then "
                f"press Scan here again.")
    return None


def _press_captured_uia(captured):
    """Replay a taught Store-app button via pywinauto. Returns (ok, msg)
    when attempted, or None to fall back to the generic search."""
    title = (captured.get("window_title") or "").strip()
    name = (captured.get("button_text") or "").strip()
    ctype = (captured.get("control_type") or "Button").strip()
    if not title or not name:
        return None
    try:
        import pywinauto  # noqa: F401
        from pywinauto import Desktop
    except Exception:
        return None
    try:
        win = Desktop(backend="uia").window(title_re=f".*{re.escape(title)}.*")
        if not win.exists(timeout=2):
            return None
        btn = win.child_window(title=name, control_type=ctype)
        if not btn.exists(timeout=1):
            return None
        try:
            btn.invoke()
        except Exception:
            btn.click_input()
        return True, f"Pressed remembered button \"{name}\" in \"{title}\" (UI Automation)."
    except Exception:
        return None


def press_hp_scan_button(window_kw, button_kw, captured=None):
    """
    Find the HP scan software's window and press its Scan button.

    Tries, in order:
      1. the remembered (taught) button, if capture data is provided
      2. an automatic Win32 search by window title + button text
      3. UI Automation via pywinauto (Store apps), if installed

    Returns (ok: bool, message: str). message is a success note, or a
    precise failure reason suitable for an alert popup.
    """
    if win32gui is None:
        return False, ("This capture method needs Windows with pywin32 "
                       "installed — it cannot run on this system.")

    window_kw = (window_kw or "").strip() or DEFAULT_HP_WINDOW_TITLE
    button_kw = (button_kw or "").strip() or DEFAULT_HP_BUTTON_TEXT

    # 1. Remembered button (from teach mode)
    if captured:
        try:
            if captured.get("backend") == "win32":
                r = _press_captured_win32(captured)
                if r is not None:
                    return r
            elif captured.get("backend") == "uia":
                r = _press_captured_uia(captured)
                if r is not None:
                    return r
        except Exception:
            pass  # fall through to automatic search

    # 2. Automatic Win32 search
    try:
        windows = _find_windows_by_title(window_kw)
    except Exception as e:
        return False, f"Could not search open windows: {e}"

    if not windows:
        # Maybe it's a Store app only visible to UI Automation
        uia = _press_button_via_uia(window_kw, button_kw)
        if uia is not None:
            return uia
        return False, (
            f"No open window with \"{window_kw}\" in its title was found.\n\n"
            f"Open the HP scan software first, leave its window open, "
            f"then press Scan here again."
        )

    candidates = []
    for hwnd, title in windows:
        for (h, txt, cls) in _find_button_like_children(hwnd, button_kw):
            candidates.append((hwnd, title, h, txt, cls))

    if not candidates:
        uia = _press_button_via_uia(window_kw, button_kw)
        if uia is not None:
            ok, msg = uia
            if ok:
                return True, msg
        return False, (
            f"The window \"{windows[0][1]}\" was found, but it contains no "
            f"button with \"{button_kw}\" in its label.\n\n"
            f"Check the 'Button text' field — it must match the text on the "
            f"HP software's scan button (for example: Scan)."
        )

    best = _pick_best_button([(c[2], c[3], c[4]) for c in candidates], button_kw)
    root_hwnd, root_title, btn_hwnd, btn_text, btn_cls = candidates[0]
    for c in candidates:
        if c[2] == best[0]:
            root_hwnd, root_title, btn_hwnd, btn_text, btn_cls = c
            break

    try:
        if not win32gui.IsWindowEnabled(btn_hwnd):
            return False, (
                f"The \"{btn_text}\" button was found in \"{root_title}\", "
                f"but it is currently disabled (greyed out).\n\n"
                f"The HP software may still be starting up or waiting for "
                f"the scanner — check its window, then try again."
            )
    except Exception:
        pass

    try:
        if win32gui.IsIconic(root_hwnd):
            win32gui.ShowWindow(root_hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(root_hwnd)
    except Exception:
        pass

    delivered, how = _deliver_click(btn_hwnd)
    if not delivered:
        uia = _press_button_via_uia(window_kw, button_kw)
        if uia is not None and uia[0]:
            return True, uia[1]
        return False, (
            f"The \"{btn_text}\" button was found in \"{root_title}\", but "
            f"the click command could not be delivered (the window is not "
            f"responding to it).\n\n"
            f"Try clicking once inside the HP window yourself, then press "
            f"Scan here again."
        )

    return True, f"Pressed \"{btn_text}\" in \"{root_title}\" ({how})."


# ----------------------------------------------------------------------
# Teach mode — watch the mouse and remember which button the user clicks
# ----------------------------------------------------------------------
def capture_clicked_button(our_root_hwnd=None, timeout=TEACH_TIMEOUT_SECONDS,
                           tick_cb=None):
    """
    Listen for the next left-mouse-button click ANYWHERE on screen and
    identify the control under the cursor.

    Returns a dict with what was clicked:
        {"backend": "win32"|"uia", "window_title": str,
         "button_text": str, "button_class": str, "control_type": str}
    or None on timeout / Escape / unavailable APIs.

    Clicks that land inside OUR OWN application window are ignored, so the
    user can still move/click this app while listening.
    """
    have_win32 = win32api is not None and win32gui is not None
    have_uia = False
    if not have_win32:
        try:
            import pywinauto  # noqa: F401
            have_uia = True
        except Exception:
            have_uia = False
    if not have_win32 and not have_uia:
        return None

    VK_LBUTTON = 0x01
    VK_ESCAPE = 0x1B

    deadline = time.monotonic() + timeout
    was_down = bool(win32api.GetAsyncKeyState(VK_LBUTTON) & 0x8000) if have_win32 else False
    last_tick = -1

    while time.monotonic() < deadline:
        remaining = int(deadline - time.monotonic() + 0.999)
        if tick_cb and remaining != last_tick:
            last_tick = remaining
            try:
                tick_cb(remaining)
            except Exception:
                pass

        if have_win32:
            if win32api.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
                return None
            down = bool(win32api.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
            if down and not was_down:
                x, y = win32api.GetCursorPos()

                # Prefer UI Automation identification when available —
                # Store apps expose their real buttons only through UIA.
                try:
                    from pywinauto import Desktop
                    elem = Desktop(backend="uia").from_point(x, y)
                    top = elem.top_level_parent()
                    title = top.window_text() or ""
                    name = elem.window_text() or ""
                    ctype = elem.element_info.control_type or ""
                    if title and title != _own_title(our_root_hwnd):
                        return {"backend": "uia", "window_title": title,
                                "button_text": name, "button_class": "",
                                "control_type": ctype}
                except Exception:
                    pass

                # Classic Win32 identification
                try:
                    hwnd = win32gui.WindowFromPoint((x, y))
                    root = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
                    if our_root_hwnd and root == our_root_hwnd:
                        was_down = down
                        time.sleep(0.03)
                        continue  # clicked our own app — ignore
                    return {"backend": "win32",
                            "window_title": win32gui.GetWindowText(root) or "",
                            "button_text": win32gui.GetWindowText(hwnd) or "",
                            "button_class": win32gui.GetClassName(hwnd) or "",
                            "control_type": ""}
                except Exception:
                    return None
            was_down = down
            time.sleep(0.03)
        else:
            # No pywin32: UIA-only path can't watch the mouse buttons
            # itself, so just wait — pywinauto has no global mouse hook.
            return None

    return None


def _own_title(our_root_hwnd):
    if not our_root_hwnd or win32gui is None:
        return None
    try:
        return win32gui.GetWindowText(our_root_hwnd) or None
    except Exception:
        return None


# ----------------------------------------------------------------------
# WIA acquisition
# ----------------------------------------------------------------------
def _set_prop(props, pid, val):
    try:
        props.Item(pid).Value = val
    except Exception:
        # Not all devices support all props; ignore silently
        pass


def _acquire_pages(device_id, settings, progress_cb=None, status_cb=None):
    """
    Acquire one or more pages from the selected device using WIA.
    Returns a list of PIL.Image objects.
    """
    _ensure_pywin32()

    pythoncom.CoInitialize()
    try:
        dev_manager = win32com.client.Dispatch("WIA.DeviceManager")
        device = None
        for info in dev_manager.DeviceInfos:
            if info.DeviceID == device_id:
                device = info.Connect()
                break
        if device is None:
            raise RuntimeError("Selected scanner not found/connected.")

        item = device.Items[1]  # WIA uses 1-based index

        dpi = settings.get("dpi", 300)
        page_size = settings.get("page_size", "A4")
        color_mode = settings.get("color_mode", "Color")
        sides = settings.get("sides", "Simplex")
        source = settings.get("source", "ADF")
        auto_orient = settings.get("auto_orient", True)

        ps_map = {"A4": WIA_PAGE_A4, "Letter": WIA_PAGE_LETTER, "Auto": WIA_PAGE_AUTO}
        wia_ps = ps_map.get(page_size, WIA_PAGE_A4)

        if color_mode == "Color":
            intent = INTENT_COLOR
        elif color_mode == "Grayscale":
            intent = INTENT_GRAYSCALE
        else:
            intent = INTENT_TEXT

        doc_flags = 0
        if source == "ADF":
            doc_flags |= FEEDER
        else:
            doc_flags |= FLATBED
        if sides == "Duplex":
            doc_flags |= DUPLEX

        props = item.Properties
        _set_prop(props, WIA_IPS_XRES, dpi)
        _set_prop(props, WIA_IPS_YRES, dpi)
        _set_prop(props, WIA_IPS_PAGE_SIZE, wia_ps)
        _set_prop(props, WIA_IPS_CUR_INTENT, intent)
        if auto_orient:
            _set_prop(props, WIA_IPS_ORIENTATION, 4)

        try:
            _set_prop(device.Properties, WIA_IPS_DOCUMENT_HANDLING_SELECT, doc_flags)
        except Exception:
            _set_prop(props, WIA_IPS_DOCUMENT_HANDLING_SELECT, doc_flags)

        images = []
        page_idx = 0

        while True:
            page_idx += 1
            if status_cb:
                status_cb(f"Scanning page {page_idx}...")

            try:
                imgfile = item.Transfer("{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}")
            except Exception as e:
                if page_idx == 1:
                    raise RuntimeError(f"Scan failed: {e}")
                break

            buf = imgfile.FileData.BinaryData
            bio = io.BytesIO(buf)
            try:
                pil = Image.open(bio)
                pil.load()
                images.append(pil)
            except Exception:
                pass

            if source == "Flatbed":
                break

            if progress_cb:
                progress_cb(page_idx)

            time.sleep(0.1)

        if not images:
            raise RuntimeError("No pages acquired (feeder empty?).")

        return images

    finally:
        pythoncom.CoUninitialize()


def _save_images(images, out_path, fmt):
    """
    Save list of PIL images to either a single PDF or multiple PNGs.
    fmt: "PDF" or "PNG"
    """
    if fmt == "PDF":
        # Convert all to RGB (PDF doesn’t support mode "1" directly)
        rgb_pages = []
        for im in images:
            if im.mode in ("RGBA", "P"):
                rgb_pages.append(im.convert("RGB"))
            elif im.mode == "1":
                rgb_pages.append(im.convert("L"))
            else:
                rgb_pages.append(im)
        # Fast path: PIL can save multipage PDF directly
        first, rest = rgb_pages[0], rgb_pages[1:]
        first.save(out_path, "PDF", save_all=True, append_images=rest)
        return [out_path]
    else:
        # Save as separate PNG files with counter
        base, ext = os.path.splitext(out_path)
        if ext.lower() not in (".png",):
            out_path = base + ".png"
        saved = []
        for i, im in enumerate(images, 1):
            p = f"{base}_p{i:02d}.png"
            im.save(p, "PNG", optimize=True)
            saved.append(p)
        return saved


def add_scan_tab(app):
    """
    Mounts a 'Scan' tab into the existing ttk.Notebook on the given app (MaafushivaruHub).
    Expects: app.notebook, app.dirs, app._set_status, app._show_pdf_preview (optional),
    and (optional) app.cfg / app._save_config for remembering settings.
    """
    # Styles/colors already defined by the host app; reuse their constants if present
    BG       = getattr(app, "BG", "#0A0F1E") if hasattr(app, "BG") else "#0A0F1E"
    PANEL    = getattr(app, "PANEL", "#111827") if hasattr(app, "PANEL") else "#111827"
    PANEL2   = getattr(app, "PANEL2", "#1F2937") if hasattr(app, "PANEL2") else "#1F2937"
    TEXT     = getattr(app, "TEXT", "#F9FAFB") if hasattr(app, "TEXT") else "#F9FAFB"
    MUTED    = getattr(app, "MUTED", "#9CA3AF") if hasattr(app, "MUTED") else "#9CA3AF"
    ACCENT   = getattr(app, "ACCENT", "#3B82F6") if hasattr(app, "ACCENT") else "#3B82F6"
    SUCCESS  = getattr(app, "SUCCESS", "#10B981") if hasattr(app, "SUCCESS") else "#10B981"
    WARNING  = getattr(app, "WARNING", "#F59E0B") if hasattr(app, "WARNING") else "#F59E0B"
    ERROR    = getattr(app, "ERROR", "#EF4444") if hasattr(app, "ERROR") else "#EF4444"

    # ---- persisted settings (remembered between runs) ----------------------
    def _settings():
        cfg = getattr(app, "cfg", None)
        if not isinstance(cfg, dict):
            return {}
        return cfg.setdefault("app_settings", {})

    def _captured():
        s = _settings()
        backend = s.get("hp_captured_backend", "")
        if backend not in ("win32", "uia"):
            return None
        return {
            "backend": backend,
            "window_title": s.get("hp_captured_window_title", ""),
            "button_text": s.get("hp_captured_button_text", ""),
            "button_class": s.get("hp_captured_button_class", ""),
            "control_type": s.get("hp_captured_control_type", ""),
        }

    def _persist():
        s = _settings()
        s["scan_method"] = method_key_var.get()
        s["hp_window_title"] = hp_title_var.get().strip() or DEFAULT_HP_WINDOW_TITLE
        s["hp_button_text"] = hp_button_var.get().strip() or DEFAULT_HP_BUTTON_TEXT
        s["hp_captured_backend"] = captured_backend_var.get()
        s["hp_captured_window_title"] = captured_title_var.get()
        s["hp_captured_button_text"] = captured_text_var.get()
        s["hp_captured_button_class"] = captured_class_var.get()
        s["hp_captured_control_type"] = captured_ctype_var.get()
        save = getattr(app, "_save_config", None)
        if callable(save):
            try:
                save()
            except Exception:
                pass

    saved = _settings()

    # Create tab
    tab = ttk.Frame(app.notebook)
    app.notebook.add(tab, text="  Scan  ")

    # Outer frames
    top = tk.Frame(tab, bg=PANEL2, height=60)
    top.pack(fill=tk.X)
    top.pack_propagate(False)
    body = tk.Frame(tab, bg=PANEL)
    body.pack(fill=tk.BOTH, expand=True)

    tk.Label(top, text="Scan Documents", bg=PANEL2, fg=TEXT,
             font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT, padx=16, pady=14)

    # Controls frame
    ctrl = tk.Frame(body, bg=PANEL)
    ctrl.pack(fill=tk.X, padx=16, pady=12)

    # Left column (method + device + options)
    left = tk.Frame(ctrl, bg=PANEL)
    left.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

    # Right column (destination — WIA mode only)
    right = tk.Frame(ctrl, bg=PANEL)
    right.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

    # ---- Capture method dropdown -------------------------------------------
    tk.Label(left, text="Method:", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"))\
        .grid(row=0, column=0, sticky="w", pady=(2, 6))
    method_key_var = tk.StringVar(
        value=saved.get("scan_method", METHOD_WIA)
        if saved.get("scan_method") in METHOD_LABELS else METHOD_WIA)
    method_display_var = tk.StringVar(value=METHOD_LABELS[method_key_var.get()])
    method_cb = ttk.Combobox(left, textvariable=method_display_var,
                             values=list(METHOD_LABELS.values()),
                             state="readonly", width=40)
    method_cb.grid(row=0, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(2, 6))

    # Scanner selection (WIA only)
    dev_lbl = tk.Label(left, text="Scanner:", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"))
    dev_lbl.grid(row=1, column=0, sticky="w", pady=(2, 6))
    scanner_var = tk.StringVar(value="")
    scanner_cb = ttk.Combobox(left, textvariable=scanner_var, state="readonly", width=40)
    scanner_cb.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(2, 6))

    def refresh_scanners():
        try:
            sc = list_wia_scanners()
            names = [f"{name}  |  {did}" for did, name in sc]
            scanner_cb["values"] = names
            if names:
                scanner_cb.current(0)
        except Exception as e:
            messagebox.showerror("WIA Error", f"Could not enumerate scanners:\n\n{e}")

    refresh_btn = ttk.Button(left, text="Refresh", command=refresh_scanners)
    refresh_btn.grid(row=1, column=2, padx=8, pady=(2, 6))

    # Item type (document/photo)
    item_lbl = tk.Label(left, text="Item type:", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"))
    item_lbl.grid(row=2, column=0, sticky="w", pady=6)
    item_type_var = tk.StringVar(value="Document")
    item_type_cb = ttk.Combobox(left, textvariable=item_type_var, values=["Document", "Photo"],
                                state="readonly", width=20)
    item_type_cb.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=6)

    # Page sides
    sides_lbl = tk.Label(left, text="Page sides:", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"))
    sides_lbl.grid(row=3, column=0, sticky="w", pady=6)
    sides_var = tk.StringVar(value="Simplex")
    sides_cb = ttk.Combobox(left, textvariable=sides_var, values=["Simplex", "Duplex"],
                            state="readonly", width=20)
    sides_cb.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=6)

    # Page size
    psize_lbl = tk.Label(left, text="Page size:", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"))
    psize_lbl.grid(row=4, column=0, sticky="w", pady=6)
    page_size_var = tk.StringVar(value="A4")
    psize_cb = ttk.Combobox(left, textvariable=page_size_var, values=["A4", "Letter", "Auto"],
                            state="readonly", width=20)
    psize_cb.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=6)

    # DPI
    dpi_lbl = tk.Label(left, text="DPI:", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"))
    dpi_lbl.grid(row=5, column=0, sticky="w", pady=6)
    dpi_var = tk.IntVar(value=300)
    dpi_spin = ttk.Spinbox(left, from_=100, to=600, textvariable=dpi_var, width=8)
    dpi_spin.grid(row=5, column=1, sticky="w", padx=(8, 0), pady=6)

    # Color mode
    color_lbl = tk.Label(left, text="Color mode:", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"))
    color_lbl.grid(row=6, column=0, sticky="w", pady=6)
    color_var = tk.StringVar(value="Color")
    color_cb = ttk.Combobox(left, textvariable=color_var,
                            values=["Color", "Grayscale", "Black & White"],
                            state="readonly", width=20)
    color_cb.grid(row=6, column=1, sticky="w", padx=(8, 0), pady=6)

    # Source (ADF/Flatbed)
    source_lbl = tk.Label(left, text="Source:", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"))
    source_lbl.grid(row=7, column=0, sticky="w", pady=6)
    source_var = tk.StringVar(value="ADF")
    source_cb = ttk.Combobox(left, textvariable=source_var, values=["ADF", "Flatbed"],
                             state="readonly", width=20)
    source_cb.grid(row=7, column=1, sticky="w", padx=(8, 0), pady=6)

    # Auto orient
    auto_orient_var = tk.BooleanVar(value=True)
    auto_orient_chk = ttk.Checkbutton(left, text="Auto orient (if supported)",
                                      variable=auto_orient_var)
    auto_orient_chk.grid(row=8, column=1, sticky="w", padx=(6, 0), pady=6)

    # ---- HP-software wiring (HP method only) --------------------------------
    # Taught-button memory (filled by teach mode, persisted in config.json)
    captured_backend_var = tk.StringVar(value=saved.get("hp_captured_backend", ""))
    captured_title_var = tk.StringVar(value=saved.get("hp_captured_window_title", ""))
    captured_text_var = tk.StringVar(value=saved.get("hp_captured_button_text", ""))
    captured_class_var = tk.StringVar(value=saved.get("hp_captured_button_class", ""))
    captured_ctype_var = tk.StringVar(value=saved.get("hp_captured_control_type", ""))

    hp_title_lbl = tk.Label(left, text="HP window title:", bg=PANEL, fg=MUTED,
                            font=("Segoe UI", 9, "bold"))
    hp_title_lbl.grid(row=9, column=0, sticky="w", pady=(14, 6))
    hp_title_var = tk.StringVar(
        value=saved.get("hp_window_title", DEFAULT_HP_WINDOW_TITLE))
    hp_title_entry = tk.Entry(left, textvariable=hp_title_var, bg=PANEL2, fg=TEXT,
                              insertbackground=TEXT, relief="flat", bd=0,
                              highlightthickness=1, highlightbackground="#374151",
                              highlightcolor=ACCENT, width=32)
    hp_title_entry.grid(row=9, column=1, sticky="w", padx=(8, 0), pady=(14, 6))

    hp_button_lbl = tk.Label(left, text="Scan button text:", bg=PANEL, fg=MUTED,
                             font=("Segoe UI", 9, "bold"))
    hp_button_lbl.grid(row=10, column=0, sticky="w", pady=6)
    hp_button_var = tk.StringVar(
        value=saved.get("hp_button_text", DEFAULT_HP_BUTTON_TEXT))
    hp_button_entry = tk.Entry(left, textvariable=hp_button_var, bg=PANEL2, fg=TEXT,
                               insertbackground=TEXT, relief="flat", bd=0,
                               highlightthickness=1, highlightbackground="#374151",
                               highlightcolor=ACCENT, width=32)
    hp_button_entry.grid(row=10, column=1, sticky="w", padx=(8, 0), pady=6)

    hp_hint_lbl = tk.Label(
        left,
        text=("In this mode all settings above are applied by the HP software,\n"
              "not here. Keep its window open; Scan Now presses its button.\n"
              "If the button can't be found, you'll be asked to click it once\n"
              "yourself so the app can remember it."),
        bg=PANEL, fg=MUTED, font=("Segoe UI", 8), justify=tk.LEFT)
    hp_hint_lbl.grid(row=11, column=0, columnspan=3, sticky="w", pady=(4, 2))

    hp_title_entry.bind("<FocusOut>", lambda e: _persist())
    hp_button_entry.bind("<FocusOut>", lambda e: _persist())

    # Destination controls (WIA mode only — in HP mode the HP software
    # decides itself where scanned files go)
    dest_lbl = tk.Label(right, text="Send to folder:", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"))
    dest_lbl.grid(row=0, column=0, sticky="w", pady=(2, 6))
    out_dir_var = tk.StringVar(value=app.dirs.get("scanned", app.dirs.get("base", ".")))

    out_entry = tk.Entry(right, textvariable=out_dir_var, bg=PANEL2, fg=TEXT,
                         insertbackground=TEXT, relief="flat", bd=0,
                         highlightthickness=1, highlightbackground="#374151",
                         highlightcolor=ACCENT, width=44)
    out_entry.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=(2, 6))

    def choose_folder():
        d = filedialog.askdirectory(initialdir=out_dir_var.get() or app.dirs.get("base", "."))
        if d:
            out_dir_var.set(d)

    browse_btn = ttk.Button(right, text="Browse", command=choose_folder)
    browse_btn.grid(row=0, column=2, padx=8, pady=(2, 6))

    # Save as
    saveas_lbl = tk.Label(right, text="Save as:", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"))
    saveas_lbl.grid(row=1, column=0, sticky="w", pady=6)
    save_as_var = tk.StringVar(value="PDF")
    saveas_cb = ttk.Combobox(right, textvariable=save_as_var, values=["PDF", "PNG"],
                             state="readonly", width=12)
    saveas_cb.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=6)

    # File name base
    fname_lbl = tk.Label(right, text="File name:", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"))
    fname_lbl.grid(row=2, column=0, sticky="w", pady=6)
    fname_var = tk.StringVar(value="SCAN")
    fname_entry = ttk.Entry(right, textvariable=fname_var, width=24)
    fname_entry.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=6)

    # Show preview after scan
    preview_var = tk.BooleanVar(value=True)
    preview_chk = ttk.Checkbutton(right, text="Show viewer after scan", variable=preview_var)
    preview_chk.grid(row=3, column=1, sticky="w", padx=(6, 0), pady=6)

    # Progress + buttons
    foot = tk.Frame(body, bg=PANEL)
    foot.pack(fill=tk.X, padx=16, pady=8)
    pbar = ttk.Progressbar(foot, orient="horizontal", mode="determinate")
    pbar.pack(fill=tk.X, side=tk.LEFT, expand=True, padx=(0, 12))
    scan_btn = ttk.Button(foot, text="▶  Scan Now")
    scan_btn.pack(side=tk.LEFT)
    cancel_btn = ttk.Button(foot, text="⏹  Cancel")
    cancel_btn.pack(side=tk.LEFT, padx=(8, 0))

    # Status
    stat = tk.Frame(body, bg=PANEL, height=26)
    stat.pack(fill=tk.X, padx=16, pady=(0, 12))
    status_var = tk.StringVar(value="Ready.")
    tk.Label(stat, textvariable=status_var, bg=PANEL, fg=TEXT, font=("Segoe UI", 9)).pack(side=tk.LEFT)

    # Internal flag
    cancel_flag = {"stop": False}

    def set_status(txt, color=None):
        status_var.set(txt)
        if hasattr(app, "_set_status"):
            try:
                app._set_status(txt, None)
            except Exception:
                pass

    def on_cancel():
        cancel_flag["stop"] = True
        set_status("Cancel requested...", WARNING)

    cancel_btn.configure(command=on_cancel)

    def progress_cb(n):
        pbar.configure(value=n)

    def status_cb(txt):
        set_status(txt)

    # ---- Which widgets belong to which mode ---------------------------------
    wia_widgets = [scanner_cb, refresh_btn, item_type_cb, sides_cb, psize_cb,
                   dpi_spin, color_cb, source_cb, auto_orient_chk,
                   out_entry, browse_btn, saveas_cb, fname_entry, preview_chk]
    hp_widgets = [hp_title_entry, hp_button_entry]
    wia_labels = [dev_lbl, item_lbl, sides_lbl, psize_lbl, dpi_lbl, color_lbl,
                  source_lbl, dest_lbl, saveas_lbl, fname_lbl]

    def _set_widgets_state(widgets, state):
        for w in widgets:
            try:
                w.configure(state=state)
            except Exception:
                pass

    def _apply_method_ui(*_):
        key = method_key_var.get()
        if key == METHOD_HP:
            # In HP mode NOTHING here applies — the HP software uses its
            # own settings. Grey everything WIA/destination related out.
            _set_widgets_state(wia_widgets, "disabled")
            _set_widgets_state(hp_widgets, "normal")
            for lbl in wia_labels:
                try:
                    lbl.configure(fg="#4B5563")
                except Exception:
                    pass
        else:
            _set_widgets_state(wia_widgets, "normal")
            scanner_cb.configure(state="readonly")
            for cb in (item_type_cb, sides_cb, psize_cb, color_cb, source_cb, saveas_cb):
                cb.configure(state="readonly")
            _set_widgets_state(hp_widgets, "disabled")
            for lbl in wia_labels:
                try:
                    lbl.configure(fg=MUTED)
                except Exception:
                    pass

    def _on_method_selected(_event=None):
        label = method_display_var.get()
        for k, v in METHOD_LABELS.items():
            if v == label:
                method_key_var.set(k)
                break
        _persist()
        _apply_method_ui()

    method_cb.bind("<<ComboboxSelected>>", _on_method_selected)

    # ---- Teach mode ----------------------------------------------------------
    teach_state = {"listening": False}

    def _own_root_hwnd():
        try:
            return app.winfo_id()
        except Exception:
            return None

    def do_teach():
        """Listen for one click on the HP software's Scan button and
        remember it. The user's own click also starts the HP scan — that's
        expected and mentioned in the prompt."""
        if win32api is None:
            messagebox.showwarning(
                "Teach mode unavailable",
                "Teach mode needs Windows with pywin32 installed.")
            return

        teach_state["listening"] = True
        try:
            messagebox.showinfo(
                "Teach me the Scan button",
                f"After you click OK, you'll have {TEACH_TIMEOUT_SECONDS} seconds to "
                f"click ONCE directly on the Scan button in the HP scan "
                f"software.\n\n"
                f"• Keep the HP window open and visible.\n"
                f"• Your click will also start the HP scan — that's fine.\n"
                f"• Press Esc to cancel listening.")
        except Exception:
            pass

        def tick(remaining):
            set_status(f"Listening… click the HP software's Scan button "
                       f"({remaining}s, Esc cancels)")

        result = capture_clicked_button(our_root_hwnd=_own_root_hwnd(),
                                        timeout=TEACH_TIMEOUT_SECONDS,
                                        tick_cb=tick)
        teach_state["listening"] = False

        if not result:
            set_status("Teach mode ended without capturing a button.", WARNING)
            messagebox.showwarning(
                "Nothing captured",
                "No button click was captured (timed out or cancelled).\n\n"
                "Try again, and click directly on the HP software's Scan "
                "button while listening.")
            return False

        # Remember it (and mirror into the visible fields)
        captured_backend_var.set(result.get("backend", ""))
        captured_title_var.set(result.get("window_title", ""))
        captured_text_var.set(result.get("button_text", ""))
        captured_class_var.set(result.get("button_class", ""))
        captured_ctype_var.set(result.get("control_type", ""))
        if result.get("window_title"):
            hp_title_var.set(result["window_title"])
        if result.get("button_text"):
            hp_button_var.set(result["button_text"])
        _persist()

        desc = f"\"{result.get('button_text') or '?'}\" in \"{result.get('window_title') or '?'}\""
        set_status(f"Remembered the Scan button: {desc}.", SUCCESS)
        messagebox.showinfo(
            "Button remembered",
            f"Got it — from now on, Scan Now presses:\n\n    {desc}\n\n"
            f"If the HP software ever changes, press \"Teach…\" again.")
        return True

    def on_teach_click():
        if teach_state["listening"]:
            return
        t = threading.Thread(target=do_teach, daemon=True)
        t.start()

    teach_btn = ttk.Button(left, text="Teach…", command=on_teach_click)
    teach_btn.grid(row=10, column=2, padx=8, pady=6)
    hp_widgets.append(teach_btn)

    # ---- HP method: press the HP software's Scan button ----------------------
    def do_hp_button_press():
        _persist()  # make sure the latest title/button text is used
        title_kw = hp_title_var.get().strip() or DEFAULT_HP_WINDOW_TITLE
        button_kw = hp_button_var.get().strip() or DEFAULT_HP_BUTTON_TEXT

        set_status(f"Looking for the HP software window (\"{title_kw}\")...")
        ok, msg = press_hp_scan_button(title_kw, button_kw, captured=_captured())

        if ok:
            pbar.configure(value=pbar["maximum"])
            set_status(f"{msg}  Finish the scan in the HP window.", SUCCESS)
            return

        # Automatic press failed — offer teach mode so the user can click
        # the button once themselves and the app remembers it.
        set_status("HP software's Scan button could not be pressed automatically.", ERROR)
        try:
            teach = messagebox.askyesno(
                "Scan button not found",
                f"{msg}\n\n"
                f"Do you want to click the HP software's Scan button once "
                f"yourself, so I can watch and remember it for next time?")
        except Exception:
            teach = False

        if teach:
            do_teach()
        else:
            messagebox.showwarning("HP Scan button not pressed", msg)

    # ---- WIA method (original flow) ------------------------------------------
    def do_wia_scan():
        try:
            cancel_flag["stop"] = False
            pbar.configure(value=0, maximum=10)
            # Device ID from combobox
            sel = scanner_var.get().strip()
            if not sel:
                raise RuntimeError("No scanner selected.")
            # Extract DeviceID from "Name | DeviceID"
            if "  |  " in sel:
                parts = sel.split("  |  ")
                device_id = parts[-1].strip()
            else:
                # fallback: first scanner
                sc = list_wia_scanners()
                if not sc:
                    raise RuntimeError("No WIA scanners found.")
                device_id = sc[0][0]

            # Settings map
            settings = {
                "dpi": int(dpi_var.get() or 300),
                "page_size": page_size_var.get(),
                "color_mode": color_var.get(),
                "sides": sides_var.get(),
                "source": source_var.get(),
                "auto_orient": bool(auto_orient_var.get()),
            }

            # Acquire images
            imgs = _acquire_pages(device_id, settings, progress_cb=progress_cb, status_cb=status_cb)

            if cancel_flag["stop"]:
                set_status("Scan cancelled.", WARNING)
                return

            # Output path
            out_dir = out_dir_var.get().strip() or app.dirs.get("scanned", ".")
            os.makedirs(out_dir, exist_ok=True)
            base = fname_var.get().strip() or "SCAN"

            # Unique target path
            ts = time.strftime("%Y%m%d_%H%M%S")
            if save_as_var.get() == "PDF":
                out_path = os.path.join(out_dir, f"{base}_{ts}.pdf")
                saved_files = _save_images(imgs, out_path, "PDF")
            else:
                out_path = os.path.join(out_dir, f"{base}_{ts}.png")
                saved_files = _save_images(imgs, out_path, "PNG")

            pbar.configure(value=pbar["maximum"])
            set_status(f"Scan complete — saved: {', '.join(os.path.basename(x) for x in saved_files)}", SUCCESS)

            # Preview first output (PDF preferred)
            if preview_var.get() and hasattr(app, "_show_pdf_preview"):
                try:
                    if save_as_var.get() == "PDF":
                        app._show_pdf_preview(saved_files[0])
                    else:
                        # When PNG, quickly wrap into a temp one-page PDF for preview using existing viewer
                        tmp_pdf = os.path.join(out_dir, f"{base}_{ts}_preview.pdf")
                        im0 = imgs[0]
                        if im0.mode in ("RGBA", "P"):
                            im0 = im0.convert("RGB")
                        im0.save(tmp_pdf, "PDF", save_all=True)
                        app._show_pdf_preview(tmp_pdf)
                except Exception:
                    pass

        except Exception as e:
            traceback.print_exc()
            messagebox.showerror("Scan Error", str(e))
            set_status(f"Scan failed: {e}", ERROR)

    def do_scan():
        if method_key_var.get() == METHOD_HP:
            try:
                do_hp_button_press()
            except Exception as e:
                traceback.print_exc()
                messagebox.showwarning(
                    "HP Scan button not pressed",
                    f"Something went wrong while pressing the HP software's "
                    f"Scan button:\n\n{e}")
                set_status(f"HP button press failed: {e}", ERROR)
        else:
            do_wia_scan()

    def on_scan_click():
        scan_btn.configure(state="disabled")
        cancel_btn.configure(state="normal")

        def worker():
            try:
                do_scan()
            finally:
                try:
                    scan_btn.configure(state="normal")
                except Exception:
                    pass

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    scan_btn.configure(command=on_scan_click)

    # Populate scanners on open (WIA mode needs the list; harmless in HP mode)
    if method_key_var.get() == METHOD_WIA:
        refresh_scanners()
    _apply_method_ui()

    return tab
