# DiskFormatGUI.py
# Windows 11 GUI disk formatter
# Run as administrator.
#
# Features:
# - GUI only
# - Resizable and scrollable GUI
# - Refreshable disk list
# - NTFS / exFAT / FAT32 selection
# - GPT / MBR partition style selection
# - Editable volume label
# - Typed confirmation before destructive formatting

import ctypes
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox


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
# Windows / PowerShell helpers
# -----------------------------


def is_windows():
    return os.name == "nt"



def is_admin():
    if not is_windows():
        return False

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False



def relaunch_as_admin():
    """Relaunch this script through UAC as administrator."""
    if not is_windows():
        return False

    script = os.path.abspath(sys.argv[0])
    params = " ".join([f'"{arg}"' for arg in sys.argv[1:]])

    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            sys.executable,
            f'"{script}" {params}',
            None,
            1,
        )
        return result > 32
    except Exception:
        return False



def run_powershell(command):
    """Run a PowerShell command and return stdout. Raises RuntimeError on failure."""
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if is_windows() else 0,
    )

    if completed.returncode != 0:
        error = completed.stderr.strip() or completed.stdout.strip() or "Unknown PowerShell error."
        raise RuntimeError(error)

    return completed.stdout.strip()



def quote_ps_string(value):
    """Safely quote a string for PowerShell single-quoted strings."""
    return "'" + str(value).replace("'", "''") + "'"



def parse_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


# -----------------------------
# Disk operations
# -----------------------------


def get_disk_list():
    command = r"""
$disks = Get-Disk | Sort-Object Number | Select-Object `
    Number, FriendlyName, SerialNumber, BusType, Size, PartitionStyle, OperationalStatus, IsSystem, IsBoot, IsReadOnly, IsOffline
$disks | ConvertTo-Json -Depth 4
"""

    output = run_powershell(command)

    if not output:
        return []

    data = json.loads(output)

    if isinstance(data, dict):
        data = [data]

    disks = []

    for item in data:
        size_bytes = int(item.get("Size") or 0)
        size_gb = round(size_bytes / (1024 ** 3), 2) if size_bytes else 0.0

        disks.append({
            "number": str(item.get("Number", "")),
            "name": str(item.get("FriendlyName") or "Unknown"),
            "serial": str(item.get("SerialNumber") or ""),
            "bus": str(item.get("BusType") or "Unknown"),
            "size": f"{size_gb} GB",
            "size_gb": size_gb,
            "style": str(item.get("PartitionStyle") or "Unknown"),
            "status": str(item.get("OperationalStatus") or "Unknown"),
            "is_system": parse_bool(item.get("IsSystem")),
            "is_boot": parse_bool(item.get("IsBoot")),
            "is_readonly": parse_bool(item.get("IsReadOnly")),
            "is_offline": parse_bool(item.get("IsOffline")),
        })

    return disks



def format_disk(disk_number, partition_style, file_system, volume_label):
    disk_number = int(disk_number)
    partition_style = str(partition_style).upper()
    file_system = str(file_system).upper()
    volume_label = str(volume_label).strip() or "USB"

    if partition_style not in {"GPT", "MBR"}:
        raise ValueError("Partition style must be GPT or MBR.")

    if file_system not in {"NTFS", "EXFAT", "FAT32"}:
        raise ValueError("File system must be NTFS, exFAT, or FAT32.")

    command = f"""
$ErrorActionPreference = 'Stop'
$diskNumber = {disk_number}
$partitionStyle = {quote_ps_string(partition_style)}
$fileSystem = {quote_ps_string(file_system)}
$label = {quote_ps_string(volume_label)}

$disk = Get-Disk -Number $diskNumber

if ($disk.IsSystem -or $disk.IsBoot) {{
    throw "Refusing to format Disk $diskNumber because Windows reports it as a system or boot disk."
}}

Set-Disk -Number $diskNumber -IsOffline $false -ErrorAction SilentlyContinue
Set-Disk -Number $diskNumber -IsReadOnly $false -ErrorAction SilentlyContinue

Clear-Disk -Number $diskNumber -RemoveData -RemoveOEM -Confirm:$false
Initialize-Disk -Number $diskNumber -PartitionStyle $partitionStyle

$partition = New-Partition -DiskNumber $diskNumber -UseMaximumSize -AssignDriveLetter
$partition | Format-Volume -FileSystem $fileSystem -NewFileSystemLabel $label -Confirm:$false -Force

"Disk $diskNumber formatted as $fileSystem with label '$label'."
"""

    return run_powershell(command)


# -----------------------------
# GUI
# -----------------------------


class DiskFormatApp:
    def __init__(self, root):
        self.root = root
        self.root.title("DiskFormat")

        try:
            self.root.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass

        self.root.geometry("760x760")
        self.root.minsize(680, 560)
        self.root.resizable(True, True)

        self.disks = []
        self.selected_disk = None
        self.working = False

        self.status_var = tk.StringVar(value="READY")
        self.selected_var = tk.StringVar(value="No disk selected")
        self.details_var = tk.StringVar(value="Select a disk from the table.")

        self.partition_style_var = tk.StringVar(value="GPT")
        self.file_system_var = tk.StringVar(value="NTFS")
        self.volume_label_var = tk.StringVar(value="USB")
        self.confirm_var = tk.StringVar(value="")

        self.build_ui()
        self.refresh_disks()

        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        container = ttk.Frame(self.root)
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)

        outer = ttk.Frame(canvas, padding=16)
        outer_window = canvas.create_window((0, 0), window=outer, anchor="nw")

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
            text="Disk Formatter",
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

        ttk.Label(status_box, textvariable=self.selected_var).pack(anchor="w", pady=(4, 0))
        ttk.Label(status_box, textvariable=self.details_var, wraplength=680).pack(anchor="w", pady=(4, 0))

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(0, 12))

        self.format_button = ttk.Button(
            controls,
            text="FORMAT SELECTED DISK",
            command=self.start_format,
        )
        self.format_button.pack(side="left", fill="x", expand=True, ipady=8)

        ttk.Button(
            controls,
            text="Refresh",
            command=self.refresh_disks,
        ).pack(side="right", padx=(10, 0), ipady=8)

        disks_box = ttk.LabelFrame(outer, text="Disks", padding=10)
        disks_box.pack(fill="both", expand=True, pady=(0, 10))

        columns = ("number", "name", "size", "bus", "style", "status", "safe")
        self.tree = ttk.Treeview(
            disks_box,
            columns=columns,
            show="headings",
            height=9,
            selectmode="browse",
        )

        headings = {
            "number": "Disk",
            "name": "Name",
            "size": "Size",
            "bus": "Bus",
            "style": "Partition",
            "status": "Status",
            "safe": "Protection",
        }

        widths = {
            "number": 55,
            "name": 210,
            "size": 95,
            "bus": 90,
            "style": 90,
            "status": 110,
            "safe": 120,
        }

        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="w", stretch=True)

        tree_scrollbar = ttk.Scrollbar(disks_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        tree_scrollbar.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self.on_disk_selected)

        settings_box = ttk.LabelFrame(outer, text="Format settings", padding=10)
        settings_box.pack(fill="x", pady=(0, 10))

        self.add_combo(
            settings_box,
            "Partition style",
            self.partition_style_var,
            ["GPT", "MBR"],
        )

        self.add_combo(
            settings_box,
            "File system",
            self.file_system_var,
            ["NTFS", "exFAT", "FAT32"],
        )

        label_row = ttk.Frame(settings_box)
        label_row.pack(fill="x", pady=4)

        ttk.Label(label_row, text="Volume label", width=18).pack(side="left")
        ttk.Entry(label_row, textvariable=self.volume_label_var).pack(side="left", fill="x", expand=True)

        safety_box = ttk.LabelFrame(outer, text="Safety", padding=10)
        safety_box.pack(fill="x", pady=(0, 10))

        warning = ttk.Label(
            safety_box,
            text=(
                "This permanently deletes all partitions and data on the selected disk. "
                "System and boot disks are blocked by the app before formatting."
            ),
            wraplength=680,
            justify="left",
        )
        warning.pack(anchor="w", pady=(0, 8))

        confirm_row = ttk.Frame(safety_box)
        confirm_row.pack(fill="x")

        ttk.Label(confirm_row, text="Type FORMAT", width=18).pack(side="left")
        ttk.Entry(confirm_row, textvariable=self.confirm_var).pack(side="left", fill="x", expand=True)

        note = ttk.Label(
            outer,
            text=(
                "Run as administrator. Select a non-system disk, choose settings, type FORMAT, then press the format button.\n"
                "The disk list comes from PowerShell Get-Disk. Formatting uses Clear-Disk, Initialize-Disk, New-Partition, and Format-Volume."
            ),
            justify="left",
            wraplength=680,
        )
        note.pack(anchor="w", pady=(4, 20))

    def add_combo(self, parent, label, variable, values):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)

        ttk.Label(row, text=label, width=18).pack(side="left")

        combo = ttk.Combobox(
            row,
            textvariable=variable,
            values=values,
            state="readonly",
        )
        combo.pack(side="left", fill="x", expand=True)

    def set_working(self, working, status=None):
        self.working = working

        if status:
            self.status_var.set(status)

        state = "disabled" if working else "normal"
        self.format_button.config(state=state)

    def refresh_disks(self):
        if self.working:
            return

        self.set_working(True, "REFRESHING")
        self.selected_disk = None
        self.selected_var.set("No disk selected")
        self.details_var.set("Loading disks...")

        for item in self.tree.get_children():
            self.tree.delete(item)

        def worker():
            try:
                disks = get_disk_list()
                self.root.after(0, lambda: self.load_disks(disks))
            except Exception as error:
                self.root.after(0, lambda: self.show_error("Refresh failed", error))

        threading.Thread(target=worker, daemon=True).start()

    def load_disks(self, disks):
        self.disks = disks

        for disk in disks:
            protected = "Blocked" if disk["is_system"] or disk["is_boot"] else "Allowed"

            self.tree.insert(
                "",
                "end",
                iid=disk["number"],
                values=(
                    disk["number"],
                    disk["name"],
                    disk["size"],
                    disk["bus"],
                    disk["style"],
                    disk["status"],
                    protected,
                ),
            )

        self.set_working(False, "READY")
        self.details_var.set(f"Found {len(disks)} disk(s).")

    def find_disk(self, number):
        for disk in self.disks:
            if disk["number"] == str(number):
                return disk
        return None

    def on_disk_selected(self, _event=None):
        selection = self.tree.selection()

        if not selection:
            self.selected_disk = None
            self.selected_var.set("No disk selected")
            self.details_var.set("Select a disk from the table.")
            return

        disk = self.find_disk(selection[0])
        self.selected_disk = disk

        if not disk:
            self.selected_var.set("No disk selected")
            self.details_var.set("Could not read selected disk.")
            return

        blocked = disk["is_system"] or disk["is_boot"]
        protection = "Blocked system/boot disk" if blocked else "Allowed non-system disk"

        self.selected_var.set(f"Selected: Disk {disk['number']} — {disk['name']}")
        self.details_var.set(
            f"{disk['size']} | {disk['bus']} | {disk['style']} | {disk['status']} | {protection}"
        )

    def validate_before_format(self):
        disk = self.selected_disk

        if not disk:
            messagebox.showwarning("No disk selected", "Select a disk first.")
            return None

        if disk["is_system"] or disk["is_boot"]:
            messagebox.showerror(
                "Blocked disk",
                "This disk is marked as a system or boot disk. It will not be formatted.",
            )
            return None

        if self.confirm_var.get().strip() != "FORMAT":
            messagebox.showwarning(
                "Confirmation required",
                "Type FORMAT in the safety box before formatting.",
            )
            return None

        return disk

    def start_format(self):
        if self.working:
            return

        disk = self.validate_before_format()

        if not disk:
            return

        warning = (
            f"Disk {disk['number']} will be COMPLETELY erased.\n\n"
            f"Name: {disk['name']}\n"
            f"Size: {disk['size']}\n"
            f"New partition style: {self.partition_style_var.get()}\n"
            f"New file system: {self.file_system_var.get()}\n"
            f"New label: {self.volume_label_var.get().strip() or 'USB'}\n\n"
            "Continue?"
        )

        if not messagebox.askyesno("Final warning", warning, icon="warning"):
            return

        self.set_working(True, "FORMATTING")
        self.details_var.set("Formatting in progress. Do not close this window or remove the disk.")

        disk_number = disk["number"]
        partition_style = self.partition_style_var.get()
        file_system = self.file_system_var.get()
        volume_label = self.volume_label_var.get()

        def worker():
            try:
                output = format_disk(disk_number, partition_style, file_system, volume_label)
                self.root.after(0, lambda: self.format_finished(output))
            except Exception as error:
                self.root.after(0, lambda: self.show_error("Format failed", error))

        threading.Thread(target=worker, daemon=True).start()

    def format_finished(self, output):
        self.set_working(False, "DONE")
        self.confirm_var.set("")
        messagebox.showinfo("DiskFormat", output or "Disk formatted successfully.")
        self.refresh_disks()

    def show_error(self, title, error):
        self.set_working(False, "ERROR")
        self.details_var.set(str(error))
        messagebox.showerror(title, str(error))

    def close(self):
        if self.working:
            if not messagebox.askyesno(
                "DiskFormat",
                "An operation is running. Closing the window may leave the disk operation unfinished. Close anyway?",
                icon="warning",
            ):
                return

        self.root.destroy()


# -----------------------------
# Main
# -----------------------------


def main():
    if not is_windows():
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("DiskFormat", "This tool only works on Windows.")
        root.destroy()
        return

    if not is_admin():
        root = tk.Tk()
        root.withdraw()

        should_relaunch = messagebox.askyesno(
            "DiskFormat",
            "DiskFormat needs administrator access. Relaunch as administrator?",
            icon="warning",
        )

        root.destroy()

        if should_relaunch and relaunch_as_admin():
            return

        return

    root = tk.Tk()
    DiskFormatApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
