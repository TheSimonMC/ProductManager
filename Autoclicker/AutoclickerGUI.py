# AutoclickerGUI.py
# Windows 11 GUI autoclicker
#
# Features:
# - GUI only
# - Resizable and scrollable GUI
# - Editable CPS
# - Human mode
# - Assign trigger key/mouse button
# - Hold mode or toggle mode
# - Uses Windows low-level mouse hook for real left-click detection

import ctypes
import os
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from ctypes import wintypes


# -----------------------------
# Resource helper
# -----------------------------

def resource_path(relative_path):
    """Return the correct path for normal Python runs and PyInstaller onefile EXEs."""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


# -----------------------------
# Windows API
# -----------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)

WH_MOUSE_LL = 14

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202

LLMHF_INJECTED = 0x00000001

INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION),
    ]


LowLevelMouseProc = ctypes.WINFUNCTYPE(
    wintypes.LPARAM,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    LowLevelMouseProc,
    wintypes.HINSTANCE,
    wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = wintypes.HHOOK

user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallNextHookEx.restype = wintypes.LPARAM

user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

user32.SendInput.argtypes = [
    wintypes.UINT,
    ctypes.POINTER(INPUT),
    ctypes.c_int,
]
user32.SendInput.restype = wintypes.UINT

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short


# -----------------------------
# Global state
# -----------------------------

state_lock = threading.Lock()

physical_left_down = False
autoclicker_enabled = False
program_running = True

hook_handle = None
mouse_hook_callback = None

toggle_active = False
previous_trigger_down = False

settings = {
    "cps": 20.0,
    "start_delay": 0.0,
    "human_mode": True,

    "min_multiplier": 0.55,
    "max_multiplier": 1.20,

    "small_pause_chance": 0.18,
    "large_pause_chance": 0.05,
    "stutter_chance": 0.07,
    "quick_click_chance": 0.10,

    # Activation settings
    "trigger_vk": VK_LBUTTON,
    "trigger_name": "Left Mouse",
    "activation_mode": "hold",  # "hold" or "toggle"
}

human_current_cps = 20.0
human_burst_clicks_left = 0
human_pause_until = 0.0
human_hold_started = 0.0


# -----------------------------
# Helpers
# -----------------------------

def clamp_float(value, default, minimum, maximum):
    try:
        value = float(value)
    except Exception:
        return default

    return max(minimum, min(maximum, value))


def get_settings():
    with state_lock:
        return dict(settings)


def is_key_down(vk):
    return bool(user32.GetAsyncKeyState(int(vk)) & 0x8000)


def is_trigger_down(current):
    vk = current["trigger_vk"]

    # For left-click, use hook state so injected autoclicks are ignored.
    if vk == VK_LBUTTON:
        with state_lock:
            return physical_left_down

    return is_key_down(vk)


def readable_key_name(event):
    if event.keysym == "space":
        return "Space"

    if event.keysym.startswith("F") and event.keysym[1:].isdigit():
        return event.keysym

    special_names = {
        "Escape": "Escape",
        "Return": "Enter",
        "BackSpace": "Backspace",
        "Tab": "Tab",
        "Shift_L": "Left Shift",
        "Shift_R": "Right Shift",
        "Control_L": "Left Ctrl",
        "Control_R": "Right Ctrl",
        "Alt_L": "Left Alt",
        "Alt_R": "Right Alt",
        "Caps_Lock": "Caps Lock",
        "Up": "Arrow Up",
        "Down": "Arrow Down",
        "Left": "Arrow Left",
        "Right": "Arrow Right",
        "Insert": "Insert",
        "Delete": "Delete",
        "Home": "Home",
        "End": "End",
        "Prior": "Page Up",
        "Next": "Page Down",
    }

    if event.keysym in special_names:
        return special_names[event.keysym]

    if len(event.char) == 1 and event.char.strip():
        return event.char.upper()

    return event.keysym


# -----------------------------
# Clicking logic
# -----------------------------

def send_left_click():
    inputs = (INPUT * 2)()

    inputs[0].type = INPUT_MOUSE
    inputs[0].union.mi = MOUSEINPUT(
        0,
        0,
        0,
        MOUSEEVENTF_LEFTDOWN,
        0,
        None,
    )

    inputs[1].type = INPUT_MOUSE
    inputs[1].union.mi = MOUSEINPUT(
        0,
        0,
        0,
        MOUSEEVENTF_LEFTUP,
        0,
        None,
    )

    user32.SendInput(2, inputs, ctypes.sizeof(INPUT))


def reset_human_state():
    global human_current_cps
    global human_burst_clicks_left
    global human_pause_until
    global human_hold_started

    current = get_settings()

    human_current_cps = current["cps"]
    human_burst_clicks_left = random.randint(2, 6)
    human_pause_until = 0.0
    human_hold_started = time.perf_counter()


def get_next_click_delay():
    global human_current_cps
    global human_burst_clicks_left
    global human_pause_until

    current = get_settings()

    cps = max(0.1, current["cps"])

    if not current["human_mode"]:
        return 1.0 / cps

    min_cps = max(0.1, cps * current["min_multiplier"])
    max_cps = max(min_cps, cps * current["max_multiplier"])

    now = time.perf_counter()

    if now < human_pause_until:
        return 0.01

    if human_burst_clicks_left <= 0:
        human_burst_clicks_left = random.randint(3, 11)

        pause_roll = random.random()

        if pause_roll < current["small_pause_chance"]:
            human_pause_until = now + random.uniform(0.045, 0.120)
            return 0.01

        if pause_roll < current["small_pause_chance"] + current["large_pause_chance"]:
            human_pause_until = now + random.uniform(0.150, 0.350)
            return 0.01

    human_burst_clicks_left -= 1

    # Smooth drift instead of simple random timing.
    human_current_cps += random.uniform(-1.4, 1.2)
    human_current_cps += (cps - human_current_cps) * 0.08
    human_current_cps = max(min_cps, min(max_cps, human_current_cps))

    held_for = max(0.0, now - human_hold_started)
    fatigue_multiplier = 1.0 + min(0.35, held_for * 0.015)

    delay = (1.0 / human_current_cps) * fatigue_multiplier
    delay += random.triangular(-0.006, 0.018, 0.002)

    if random.random() < current["stutter_chance"]:
        delay += random.uniform(0.020, 0.070)

    if random.random() < current["quick_click_chance"]:
        delay *= random.uniform(0.55, 0.78)

    return max(0.004, delay)


def click_loop():
    global toggle_active
    global previous_trigger_down

    last_active_state = False
    active_start_time = 0.0

    while True:
        with state_lock:
            running = program_running
            enabled = autoclicker_enabled

        if not running:
            break

        current = get_settings()
        trigger_down = is_trigger_down(current)

        if not enabled:
            toggle_active = False
            previous_trigger_down = trigger_down
            last_active_state = False
            time.sleep(0.01)
            continue

        if current["activation_mode"] == "toggle":
            if trigger_down and not previous_trigger_down:
                toggle_active = not toggle_active

            previous_trigger_down = trigger_down
            active = toggle_active

        else:
            active = trigger_down
            previous_trigger_down = trigger_down

        if active:
            if not last_active_state:
                active_start_time = time.perf_counter()
                reset_human_state()
                last_active_state = True

            active_for = time.perf_counter() - active_start_time

            if active_for >= current["start_delay"]:
                send_left_click()
                time.sleep(get_next_click_delay())
            else:
                time.sleep(0.005)

        else:
            last_active_state = False
            time.sleep(0.005)


# -----------------------------
# Mouse hook
# -----------------------------

def create_mouse_hook():
    @LowLevelMouseProc
    def mouse_hook(nCode, wParam, lParam):
        global physical_left_down

        if nCode >= 0:
            info = ctypes.cast(
                lParam,
                ctypes.POINTER(MSLLHOOKSTRUCT)
            ).contents

            injected = info.flags & LLMHF_INJECTED

            if not injected:
                with state_lock:
                    if wParam == WM_LBUTTONDOWN:
                        physical_left_down = True
                    elif wParam == WM_LBUTTONUP:
                        physical_left_down = False

        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    return mouse_hook


def install_hook():
    global hook_handle
    global mouse_hook_callback

    mouse_hook_callback = create_mouse_hook()

    hook_handle = user32.SetWindowsHookExW(
        WH_MOUSE_LL,
        mouse_hook_callback,
        None,
        0,
    )

    if not hook_handle:
        err = ctypes.get_last_error()
        raise ctypes.WinError(err)


def uninstall_hook():
    global hook_handle

    if hook_handle:
        user32.UnhookWindowsHookEx(hook_handle)
        hook_handle = None


# -----------------------------
# GUI
# -----------------------------

class AutoclickerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Autoclicker")

        try:
            self.root.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass

        self.root.geometry("560x850")
        self.root.minsize(520, 620)
        self.root.resizable(True, True)

        self.cps_var = tk.DoubleVar(value=20.0)
        self.start_delay_var = tk.DoubleVar(value=0.0)
        self.human_mode_var = tk.BooleanVar(value=True)

        self.min_multiplier_var = tk.DoubleVar(value=0.55)
        self.max_multiplier_var = tk.DoubleVar(value=1.20)

        self.small_pause_var = tk.DoubleVar(value=0.18)
        self.large_pause_var = tk.DoubleVar(value=0.05)
        self.stutter_var = tk.DoubleVar(value=0.07)
        self.quick_click_var = tk.DoubleVar(value=0.10)

        self.activation_mode_var = tk.StringVar(value="hold")
        self.trigger_name_var = tk.StringVar(value="Left Mouse")

        self.status_var = tk.StringVar(value="OFF")
        self.hold_var = tk.StringVar(value="Trigger not active")
        self.range_var = tk.StringVar(value="Human CPS range: 11.0 - 24.0")
        self.toggle_state_var = tk.StringVar(value="Toggle state: OFF")

        self.build_ui()
        self.apply_settings()
        self.update_status_loop()

        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        container = ttk.Frame(self.root)
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)

        outer = ttk.Frame(canvas, padding=16)

        outer_window = canvas.create_window(
            (0, 0),
            window=outer,
            anchor="nw",
        )

        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def update_scroll_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def resize_inner_frame(event):
            canvas.itemconfigure(outer_window, width=event.width)

        outer.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", resize_inner_frame)

        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", on_mousewheel)

        title = ttk.Label(
            outer,
            text="Hold / Toggle Autoclicker",
            font=("Segoe UI", 16, "bold"),
        )
        title.pack(anchor="w", pady=(0, 12))

        status_box = ttk.LabelFrame(outer, text="Status", padding=10)
        status_box.pack(fill="x", pady=(0, 10))

        ttk.Label(
            status_box,
            textvariable=self.status_var,
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w")

        ttk.Label(status_box, textvariable=self.hold_var).pack(anchor="w", pady=(4, 0))
        ttk.Label(status_box, textvariable=self.toggle_state_var).pack(anchor="w", pady=(4, 0))

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(0, 12))

        self.toggle_button = ttk.Button(
            controls,
            text="START",
            command=self.toggle_enabled,
        )
        self.toggle_button.pack(fill="x", ipady=8)

        activation = ttk.LabelFrame(outer, text="Activation", padding=10)
        activation.pack(fill="x", pady=(0, 10))

        trigger_row = ttk.Frame(activation)
        trigger_row.pack(fill="x", pady=(0, 8))

        ttk.Label(trigger_row, text="Trigger:").pack(side="left")

        ttk.Label(
            trigger_row,
            textvariable=self.trigger_name_var,
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left", padx=(8, 0))

        ttk.Button(
            trigger_row,
            text="Assign Trigger",
            command=self.open_trigger_capture,
        ).pack(side="right")

        mode_row = ttk.Frame(activation)
        mode_row.pack(fill="x")

        ttk.Radiobutton(
            mode_row,
            text="Hold",
            variable=self.activation_mode_var,
            value="hold",
            command=self.apply_settings,
        ).pack(side="left", padx=(0, 18))

        ttk.Radiobutton(
            mode_row,
            text="Toggle",
            variable=self.activation_mode_var,
            value="toggle",
            command=self.apply_settings,
        ).pack(side="left")

        basic = ttk.LabelFrame(outer, text="Basic", padding=10)
        basic.pack(fill="x", pady=(0, 10))

        self.add_slider(
            basic,
            "CPS",
            self.cps_var,
            0.1,
            100.0,
            1,
            self.apply_settings,
        )

        self.add_slider(
            basic,
            "Start delay",
            self.start_delay_var,
            0.0,
            5.0,
            2,
            self.apply_settings,
        )

        human = ttk.LabelFrame(outer, text="Human mode", padding=10)
        human.pack(fill="x", pady=(0, 10))

        ttk.Checkbutton(
            human,
            text="Enable human mode",
            variable=self.human_mode_var,
            command=self.apply_settings,
        ).pack(anchor="w", pady=(0, 8))

        ttk.Label(human, textvariable=self.range_var).pack(anchor="w", pady=(0, 8))

        self.add_slider(
            human,
            "Min CPS multiplier",
            self.min_multiplier_var,
            0.10,
            1.00,
            2,
            self.apply_settings,
        )

        self.add_slider(
            human,
            "Max CPS multiplier",
            self.max_multiplier_var,
            1.00,
            2.00,
            2,
            self.apply_settings,
        )

        random_box = ttk.LabelFrame(outer, text="Humanification", padding=10)
        random_box.pack(fill="x", pady=(0, 10))

        self.add_slider(
            random_box,
            "Small pauses",
            self.small_pause_var,
            0.0,
            0.50,
            2,
            self.apply_settings,
        )

        self.add_slider(
            random_box,
            "Long pauses",
            self.large_pause_var,
            0.0,
            0.30,
            2,
            self.apply_settings,
        )

        self.add_slider(
            random_box,
            "Finger stutter",
            self.stutter_var,
            0.0,
            0.30,
            2,
            self.apply_settings,
        )

        self.add_slider(
            random_box,
            "Quick double timing",
            self.quick_click_var,
            0.0,
            0.30,
            2,
            self.apply_settings,
        )

        note = ttk.Label(
            outer,
            text=(
                "Start the autoclicker, then use the assigned trigger.\n"
                "Hold mode: clicks while the trigger is held.\n"
                "Toggle mode: press once to start clicking, press again to stop.\n"
                "All settings update live."
            ),
            justify="left",
        )
        note.pack(anchor="w", pady=(4, 20))

    def add_slider(self, parent, label, variable, minimum, maximum, digits, command):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=4)

        top = ttk.Frame(frame)
        top.pack(fill="x")

        ttk.Label(top, text=label).pack(side="left")

        value_label = ttk.Label(top, width=8, anchor="e")
        value_label.pack(side="right")

        def refresh_label(*_):
            value_label.config(text=f"{variable.get():.{digits}f}")
            command()

        variable.trace_add("write", refresh_label)

        slider = ttk.Scale(
            frame,
            from_=minimum,
            to=maximum,
            variable=variable,
            command=lambda _value: command(),
        )
        slider.pack(fill="x")

        refresh_label()

    def apply_settings(self):
        cps = clamp_float(self.cps_var.get(), 20.0, 0.1, 1000.0)
        start_delay = clamp_float(self.start_delay_var.get(), 0.0, 0.0, 60.0)

        min_mult = clamp_float(self.min_multiplier_var.get(), 0.55, 0.05, 10.0)
        max_mult = clamp_float(self.max_multiplier_var.get(), 1.20, 0.05, 10.0)

        if max_mult < min_mult:
            max_mult = min_mult

        small_pause = clamp_float(self.small_pause_var.get(), 0.18, 0.0, 1.0)
        large_pause = clamp_float(self.large_pause_var.get(), 0.05, 0.0, 1.0)
        stutter = clamp_float(self.stutter_var.get(), 0.07, 0.0, 1.0)
        quick_click = clamp_float(self.quick_click_var.get(), 0.10, 0.0, 1.0)

        with state_lock:
            settings["cps"] = cps
            settings["start_delay"] = start_delay
            settings["human_mode"] = bool(self.human_mode_var.get())

            settings["min_multiplier"] = min_mult
            settings["max_multiplier"] = max_mult

            settings["small_pause_chance"] = small_pause
            settings["large_pause_chance"] = large_pause
            settings["stutter_chance"] = stutter
            settings["quick_click_chance"] = quick_click

            settings["activation_mode"] = self.activation_mode_var.get()

        min_cps = cps * min_mult
        max_cps = cps * max_mult

        self.range_var.set(
            f"Human CPS range: {min_cps:.1f} - {max_cps:.1f}"
        )

    def open_trigger_capture(self):
        popup = tk.Toplevel(self.root)
        popup.title("Assign Trigger")
        popup.geometry("380x170")
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.grab_set()

        label = ttk.Label(
            popup,
            text=(
                "Press a keyboard key\n"
                "or click a mouse button inside this window."
            ),
            justify="center",
            font=("Segoe UI", 11),
        )
        label.pack(expand=True)

        def set_trigger(vk, name):
            global toggle_active
            global previous_trigger_down

            with state_lock:
                settings["trigger_vk"] = vk
                settings["trigger_name"] = name
                toggle_active = False
                previous_trigger_down = False

            self.trigger_name_var.set(name)
            self.toggle_state_var.set("Toggle state: OFF")
            popup.destroy()

        def on_key(event):
            vk = int(event.keycode)
            name = readable_key_name(event)
            set_trigger(vk, name)

        def on_mouse(event):
            if event.num == 1:
                set_trigger(VK_LBUTTON, "Left Mouse")
            elif event.num == 2:
                set_trigger(VK_MBUTTON, "Middle Mouse")
            elif event.num == 3:
                set_trigger(VK_RBUTTON, "Right Mouse")

        popup.bind("<KeyPress>", on_key)
        popup.bind("<ButtonPress>", on_mouse)
        popup.focus_force()

    def toggle_enabled(self):
        global autoclicker_enabled
        global toggle_active
        global previous_trigger_down

        self.apply_settings()

        with state_lock:
            autoclicker_enabled = not autoclicker_enabled
            enabled = autoclicker_enabled

            if not enabled:
                toggle_active = False
                previous_trigger_down = False

        if enabled:
            self.toggle_button.config(text="STOP")
            self.status_var.set("ON")
        else:
            self.toggle_button.config(text="START")
            self.status_var.set("OFF")
            self.toggle_state_var.set("Toggle state: OFF")

    def update_status_loop(self):
        current = get_settings()

        with state_lock:
            enabled = autoclicker_enabled
            toggled = toggle_active

        trigger_down = is_trigger_down(current)

        self.status_var.set("ON" if enabled else "OFF")

        if current["activation_mode"] == "hold":
            self.hold_var.set(
                "Trigger active" if trigger_down else "Trigger not active"
            )
            self.toggle_state_var.set("Mode: HOLD")

        else:
            self.hold_var.set(
                "Trigger pressed" if trigger_down else "Trigger not pressed"
            )
            self.toggle_state_var.set(
                "Toggle state: ON" if toggled else "Toggle state: OFF"
            )

        self.root.after(80, self.update_status_loop)

    def close(self):
        global program_running
        global autoclicker_enabled
        global toggle_active

        with state_lock:
            autoclicker_enabled = False
            toggle_active = False
            program_running = False

        uninstall_hook()
        self.root.destroy()


# -----------------------------
# Main
# -----------------------------

def main():
    try:
        install_hook()
    except Exception as error:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Autoclicker error",
            f"Could not install mouse hook:\n\n{error}"
        )
        root.destroy()
        return

    threading.Thread(target=click_loop, daemon=True).start()

    root = tk.Tk()
    AutoclickerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()