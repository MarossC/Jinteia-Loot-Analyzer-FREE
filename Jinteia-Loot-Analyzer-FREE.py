#!/usr/bin/env python3
import datetime as dt
import os
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Optional, Iterable, List, Deque, Dict, Tuple
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# ---------------------------------------------------------------------------
# Parsing and data structures
# ---------------------------------------------------------------------------

DUNGEON_CHESTS = {
    "Razador's Chest": "Razador",
    "Nemere's Chest": "Nemere",
    "Jotun Thrym's Chest": "Jotun",
    "Hellgates Chest": "Blue Death",

    # add more as needed
}


PASS_COSTS = {
    "Hellish Pass": {
        "yang": 2_000_000,
        "items": {
            "Fragment of a Pass [U-1]": 1,
            "Fragment of a Pass [U-2]": 1,
            "Shard": 400
        }
    },
    "Ice-cold Pass": {
        "yang": 1_500_000,
        "items": {
            "Fragment of a Pass [N-1]": 1,
            "Fragment of a Pass [N-2]": 1,
            "Shard": 200
        }
    },
    "Grass-covered Pass": {
        "yang": 1_500_000,
        "items": {
            "Fragment of a Pass [J-1]": 1,
            "Fragment of a Pass [J-2]": 1,
            "Shard": 200
        }
    },
    "Charred Pass": {
        "yang": 1_500_000,
        "items": {
            "Fragment of a Pass [R-1]": 1,
            "Fragment of a Pass [R-2]": 1,
            "Shard": 200
        }
    },
    "Owl Pass": {
        "yang": 2_000_000,
        "items": {
            "Piece of an Owl Pass [L]": 1,
            "Piece of an Owl Pass [R]": 1,
            "Shard": 500
        }
    },
    "Taliko's Paradise Pass": {
        "yang": 2_000_000,
        "items": {
            "Piece of a Papyrus [L]": 1,
            "Piece of a Papyrus [R]": 1,
            "Shard": 700
        }
    },
    "Demonic Key": {
        "yang": 5_000_000,
        "items": {
            "Piece of a Demonic Key [1]": 1,
            "Piece of a Demonic Key [2]": 1,
            "Shard": 1000
        }
    },
    "Nalantir's Tooth": {
        "yang": 7_500_000,
        "items": {
            "Broken Dragon Tooth [1]": 1,
            "Broken Dragon Tooth [2]": 1,
            "Shard": 1500
        }
    },
    "Map to the Abandoned Fortress": {
        "yang": 7_500_000,
        "items": {
            "Part of an Ancient Map [1]": 1,
            "Part of an Ancient Map [2]": 1,
            "Shard": 2000
        }
    }
}



LOG_LINE_RE = re.compile(
    r"\[(\d{2}/\d{2}/\d{2})\] \[(\d{2}:\d{2}:\d{2})\]: You receive (\d+) (.+?)\."
)


@dataclass
class LootEvent:
    ts: dt.datetime
    quantity: int
    item: str

    @property
    def is_yang(self) -> bool:
        return self.item == "Yang"

def parse_datetime_from_log(date_str: str, time_str: str) -> dt.datetime:
    """Parse date/time from the log format: 24/11/25 00:29:29."""
    return dt.datetime.strptime(f"{date_str} {time_str}", "%d/%m/%y %H:%M:%S")


def parse_log_line(line: str) -> Optional[LootEvent]:
    """Parse a single log line into a LootEvent, or return None if it does not match."""
    m = LOG_LINE_RE.search(line)
    if not m:
        return None
    date_str, time_str, qty_str, item = m.groups()
    ts = parse_datetime_from_log(date_str, time_str)
    quantity = int(qty_str)
    return LootEvent(ts=ts, quantity=quantity, item=item)


def iter_events_from_file(path: str) -> Iterable[LootEvent]:
    """Iterate over all events in the given log file."""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            ev = parse_log_line(line)
            if ev:
                yield ev


def stats_from_events(events: Iterable[LootEvent]) -> Dict:
    """Compute statistics from a list/iterable of events."""
    total_yang = 0
    items_qty = defaultdict(int)      # item -> total quantity
    events_list: List[LootEvent] = []

    for ev in events:
        events_list.append(ev)
        if ev.is_yang:
            total_yang += ev.quantity
        else:
            items_qty[ev.item] += ev.quantity

    if not events_list:
        return {
            "total_yang": 0,
            "items_qty": {},
            "hours": 0.0,
        }

    start = events_list[0].ts
    end = events_list[-1].ts
    elapsed_seconds = max((end - start).total_seconds(), 1)
    hours = elapsed_seconds / 3600.0

    return {
        "total_yang": total_yang,
        "items_qty": dict(items_qty),
        "hours": hours,
        "start": start,
        "end": end,
    }


# ---------------------------------------------------------------------------
# Live monitor worker (background thread)
# ---------------------------------------------------------------------------

class LiveMonitorWorker(threading.Thread):
    """
    Background thread that tails the log file and maintains a sliding window
    of the last N minutes. It periodically calls update_callback(stats_dict).
    """

    def __init__(
        self,
        path: str,
        window_minutes: int,
        refresh_secs: int,
        from_start: bool,
        update_callback,
        stop_event: threading.Event,
        fixed_start_ts: Optional[dt.datetime] = None,
        unlimited: bool = False,   # ✅ ADD
    ):
        super().__init__(daemon=True)
        self.path = path
        self.window_minutes = window_minutes
        self.refresh_secs = refresh_secs
        self.from_start = from_start
        self.update_callback = update_callback
        self.stop_event = stop_event
        self.fixed_start_ts = fixed_start_ts
        self.unlimited = unlimited


        self.window: Deque[LootEvent] = deque()

    def clear_data(self):
        self.items.clear()
        self.total_yang = 0


    def add_event(self, ev: LootEvent):
        self.window.append(ev)

        # 🔥 Alltime mode → never trim
        if self.unlimited:
            return

        if self.fixed_start_ts:
            while self.window and self.window[0].ts < self.fixed_start_ts:
                self.window.popleft()
        else:
            cutoff = ev.ts - dt.timedelta(minutes=self.window_minutes)
            while self.window and self.window[0].ts < cutoff:
                self.window.popleft()

    def compute_stats_from_window(self) -> Optional[Dict]:
        if not self.window:
            return None

        events_list = list(self.window)
        total_yang = sum(ev.quantity for ev in events_list if ev.is_yang)
        items_qty: Dict[str, int] = defaultdict(int)
        for ev in events_list:
            if not ev.is_yang:
                items_qty[ev.item] += ev.quantity

        start = events_list[0].ts
        end = events_list[-1].ts
        elapsed = max((end - start).total_seconds(), 1)
        hours = elapsed / 3600.0
        minutes = elapsed / 60.0

        # Build per-item stats including per-hour (rounded to int)
        items_list: List[Tuple[str, int, int]] = []
        for name, qty in items_qty.items():
            per_hour = int(round(qty / hours))
            items_list.append((name, qty, per_hour))

        # Sort by quantity desc
        items_list.sort(key=lambda x: x[1], reverse=True)

        stats = {
            "start": start,
            "end": end,
            "hours": hours,
            "minutes": minutes,
            "total_yang": total_yang,
            "yang_per_hour": int(round(total_yang / hours)),
            "yang_per_minute": int(round(total_yang / minutes)),
            "items": items_list,
        }
        return stats

    def run(self):
        try:
            f = open(self.path, "r", encoding="utf-8", errors="ignore")
        except OSError as e:
            # Send error to UI via callback as None with extra key
            self.update_callback({"error": f"Cannot open log file: {e}"})
            return

        if not self.from_start:
            f.seek(0, os.SEEK_END)

        last_print = time.time()

        while not self.stop_event.is_set():
            line = f.readline()
            if not line:
                # no new data
                time.sleep(0.2)
            else:
                ev = parse_log_line(line)
                if ev:
                    self.add_event(ev)

            now_ts = time.time()
            if now_ts - last_print >= self.refresh_secs:
                last_print = now_ts
                stats = self.compute_stats_from_window()
                if stats is not None:
                    self.update_callback(stats)

        f.close()


# ---------------------------------------------------------------------------
# Tkinter UI - Modern Dark Theme
# ---------------------------------------------------------------------------

class LootMonitorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        
        self.title("💰 Loot Monitor - By Paysami AI slop v1.1 - Download only from there: https://github.com/PaysamiKekW/Jinteia-Loot-Analyzer-FREE/")
        self.geometry("1000x700")
        
        # Set dark theme colors
        self.bg_color = "#1a1a2e"
        self.card_bg = "#16213e"
        self.accent_color = "#0fcc45"
        self.accent_secondary = "#0ea5e9"
        self.text_color = "#e2e8f0"
        self.muted_text = "#94a3b8"
        
        self.configure(bg=self.bg_color)
        
        self.mini_window = None
        self.mini_yang_var = tk.StringVar(value="Yang: 0")
        self.mini_yang_hr_var = tk.StringVar(value="Yang/h: 0")

        # Configure styles
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        
        self.pass_costs = PASS_COSTS
        self.pass_states = {}
        self.pass_applied = set()

        self.base_yang = 0
        self.crafting_yang_delta = 0

        self.base_items = {}  
        self.crafting_item_delta = defaultdict(int) 

        self.base_item_rates = {}  
        self.session_start_time = None
        self.elapsed_hours = 0.0
        self.data_hours = 0.0

        self.net_yang = 0
        self.net_yang_per_hour = 0

        self.dungeon_runs = {}      # dungeon_name -> count
        self.total_dungeon_runs = 0


        # Configure ttk styles
        self.style.configure("TFrame", background=self.bg_color)
        self.style.configure("TLabelframe", background=self.bg_color, relief="flat", borderwidth=0)
        self.style.configure("TLabelframe.Label", background=self.card_bg, foreground=self.text_color, 
                           font=("Segoe UI", 11, "bold"), padding=(10, 5))
        self.style.configure("TLabel", background=self.bg_color, foreground=self.text_color, 
                           font=("Segoe UI", 10))
        self.style.configure("Header.TLabel", font=("Segoe UI", 12, "bold"), foreground=self.accent_color)
        self.style.configure("Stats.TLabel", font=("Segoe UI", 18, "bold"), foreground=self.accent_secondary)
        
        # Button styles
        self.style.configure("Accent.TButton", background=self.accent_color, foreground="white",
                           font=("Segoe UI", 10, "bold"), borderwidth=0, padding=10)
        self.style.map("Accent.TButton",
                      background=[("active", "#0db33d"), ("disabled", "#4a5568")])
        
        self.style.configure("Secondary.TButton", background="#4a5568", foreground="white",
                           font=("Segoe UI", 10), borderwidth=0, padding=8)
        
        # Treeview styles
        self.style.configure("Treeview", background="#2d3748", foreground=self.text_color,
                           fieldbackground="#2d3748", borderwidth=0, font=("Segoe UI", 10))
        self.style.configure("Treeview.Heading", background="#1e293b", foreground=self.accent_secondary,
                           font=("Segoe UI", 10, "bold"), borderwidth=0)
        self.style.map("Treeview", background=[("selected", "#4a5568")])
        
        self.stop_event = threading.Event()
        self.worker: Optional[LiveMonitorWorker] = None

        # -------------------- Settings state -------------------- #
        self.log_path_var = tk.StringVar(value="info_chat_loot.log")
        self.window_minutes_var = tk.IntVar(value=60)
        self.refresh_secs_var = tk.IntVar(value=1)
        self.from_start_var = tk.BooleanVar(value=False)
        self.time_preset_var = tk.StringVar(value="Custom")



        self.create_widgets()

    # -------------------- UI layout -------------------- #

    def create_time_preset_button(self, parent, text, value):
        btn = tk.Button(
            parent,
            text=text,
            relief="flat",
            padx=12,
            pady=4,
            font=("Segoe UI", 9, "bold"),
            bg="#1f2a3a",
            fg="#cbd5e1",
            activebackground="#334155",
            activeforeground="#ffffff",
            cursor="hand2",
            command=lambda: self.set_time_preset(value)
        )
        btn.pack(side="left", padx=4)
        return btn
    

    def create_dungeon_block(self, parent, text, value, bg, fg="#ffffff"):
        block = tk.Frame(
            parent,
            bg=bg,
            highlightthickness=1,
            highlightbackground="#000000"
        )

        tk.Label(
            block,
            text=text,
            bg=bg,
            fg=fg,
            font=("Segoe UI", 9, "bold")
        ).pack(side="left", padx=(8, 4), pady=6)

        tk.Label(
            block,
            text=str(value),
            bg=bg,
            fg=fg,
            font=("Segoe UI", 10, "bold")
        ).pack(side="left", padx=(0, 8), pady=6)

        return block


    def get_last_seen_pass(self):
        for name in reversed(list(self.base_items.keys())):
            if name in self.pass_states:
                return name
        return None
    
    def increment_last_pass_dropped(self):
        name = self.get_last_seen_pass()
        if not name:
            return

        state = self.pass_states.get(name)
        if not state:
            return

        if state["crafted"] <= 0:
            return  # nothing left to convert

        state["dropped"] += 1
        state["crafted"] -= 1

        self.recalc_crafting_deltas_from_passes()
        self.update_stats(self.last_stats)


    def open_pass_count_editor(self, name):
        state = self.pass_states.get(name)
        if not state:
            return

        win = tk.Toplevel(self)
        win.title(name)
        win.resizable(False, False)

        tk.Label(win, text=f"Total owned: {state['total']}").pack(padx=10, pady=5)

        dropped_var = tk.IntVar(value=state["dropped"])

        frame = tk.Frame(win)
        frame.pack(padx=10, pady=5)

        tk.Label(frame, text="Dropped:").pack(side="left")
        tk.Entry(frame, textvariable=dropped_var, width=6).pack(side="left")

        def apply():
            dropped = dropped_var.get()
            if dropped < 0 or dropped > state["total"]:
                return

            state["dropped"] = dropped
            state["crafted"] = state["total"] - dropped

            self.recalc_crafting_deltas_from_passes()
            self.update_stats(self.last_stats)
            win.destroy()

        tk.Button(win, text="Apply", command=apply).pack(pady=8)


    def on_tree_right_click(self, event):
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return

        values = self.tree.item(row_id, "values")
        if not values:
            return

        name = values[0]
        if name not in self.pass_states:
            return

        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label="All Crafted",
            command=lambda: self.set_pass_all_crafted(name)
        )
        menu.add_command(
            label="All Dropped",
            command=lambda: self.set_pass_all_dropped(name)
        )

        menu.add_separator()
        menu.add_command(
            label="Custom…",
            command=lambda: self.open_pass_count_editor(name)
        )

        menu.tk_popup(event.x_root, event.y_root)


    def recalc_crafting_deltas_from_passes(self):
        self.crafting_yang_delta = 0
        self.crafting_item_delta.clear()

        for name, state in self.pass_states.items():
            crafted = state["crafted"]
            if crafted <= 0:
                continue

            cost = self.pass_costs.get(name)
            if not cost:
                continue

            self.crafting_yang_delta += crafted * cost["yang"]

            for item, qty in cost.get("items", {}).items():
                self.crafting_item_delta[item] += crafted * qty

    def set_pass_all_crafted(self, name):
        state = self.pass_states.get(name)
        if not state:
            return

        state["crafted"] = state["total"]
        state["dropped"] = 0

        self.recalc_crafting_deltas_from_passes()
        self.update_stats(self.last_stats)


    def set_pass_all_dropped(self, name):
        state = self.pass_states.get(name)
        if not state:
            return

        state["crafted"] = 0
        state["dropped"] = state["total"]

        self.recalc_crafting_deltas_from_passes()
        self.update_stats(self.last_stats)



    def reset_session_data(self):
        # Base data
        self.base_yang = 0
        self.base_items = {}
        self.base_item_rates = {}
        self.data_hours = 0.0

        # Crafting / pass state
        self.crafting_yang_delta = 0
        self.crafting_item_delta.clear()
        self.pass_applied.clear()
        self.pass_states.clear()

        # Net values
        self.net_yang = 0
        self.net_yang_per_hour = 0


    def reset_overlay_stats(self):
        self.net_yang = 0
        self.net_yang_per_hour = 0
    
        if self.mini_window and self.mini_window.winfo_exists():
            self.mini_yang_var.set("Yang: 0")
            self.mini_yang_hr_var.set("Yang/h: 0")


    def open_mini_window(self):
        if self.mini_window and self.mini_window.winfo_exists():
            self.mini_window.destroy()
            self.mini_window = None
            return

        win = tk.Toplevel(self)
        win.title("Yang Overlay")
        #win.geometry("180x110") #test
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.overrideredirect(True)
        win.attributes("-toolwindow", True)
        win.configure(bg="#1a202c")



        # Drag support
        def start_move(e):
            win._x = e.x
            win._y = e.y

        def do_move(e):
            win.geometry(f"+{e.x_root - win._x}+{e.y_root - win._y}")

        win.bind("<ButtonPress-1>", start_move)
        win.bind("<B1-Motion>", do_move)

        tk.Label(
            win,
            textvariable=self.mini_yang_var,
            bg="#1a202c",
            fg="#f7fafc",
            font=("Segoe UI", 11, "bold")
        ).pack(anchor="w", padx=10, pady=(8, 0))

        tk.Label(
            win,
            textvariable=self.mini_yang_hr_var,
            bg="#1a202c",
            fg="#a0aec0",
            font=("Segoe UI", 9)
        ).pack(anchor="w", padx=10)

        tk.Button(
            win,
            text="+1 Dropped",
            command=self.increment_last_pass_dropped,
            bg="#374151",
            fg="#f87171",
            activebackground="#4b5563",
            activeforeground="#fecaca",
            relief="flat",
            font=("Segoe UI", 8, "bold"),
            cursor="hand2"
        ).pack(fill="x", padx=10, pady=(6, 10))

        self.mini_window = win


    def open_settings_popup(self):
        if hasattr(self, "settings_popup") and self.settings_popup.winfo_exists():
            self.settings_popup.lift()
            return

        self.settings_popup = tk.Toplevel(self)
        self.settings_popup.title("⚙️ Settings")
        self.settings_popup.geometry("600x320")
        self.settings_popup.configure(bg=self.card_bg)
        self.settings_popup.transient(self)
        self.settings_popup.grab_set()

        container = tk.Frame(self.settings_popup, bg=self.card_bg)
        container.pack(fill="both", expand=True, padx=20, pady=20)

        # --- Log file ---
        row1 = tk.Frame(container, bg=self.card_bg)
        row1.pack(fill="x", pady=8)

        tk.Label(row1, text="Log File:", bg=self.card_bg, fg=self.text_color).pack(side="left")

        tk.Entry(
            row1,
            textvariable=self.log_path_var,
            bg="#2d3748",
            fg=self.text_color,
            insertbackground=self.text_color,
            relief="flat",
            width=40
        ).pack(side="left", padx=10)

        ttk.Button(row1, text="Browse", command=self.browse_file,
                   style="Secondary.TButton").pack(side="left")

        # --- Time Presets ---
        preset_frame = tk.LabelFrame(
            container,
            text="Time Range",
            bg=self.card_bg,
            fg=self.text_color
        )
        preset_frame.pack(fill="x", pady=10)

        preset_bar = tk.Frame(preset_frame, bg=self.bg_color)
        preset_bar.pack(fill="x", padx=10, pady=8)

        self.preset_buttons = {}

        self.preset_buttons["1h"] = self.create_time_preset_button(
            preset_bar, "1h", "1h"
        )
        self.preset_buttons["today"] = self.create_time_preset_button(
            preset_bar, "Today", "today"
        )
        self.preset_buttons["this_week"] = self.create_time_preset_button(
            preset_bar, "This Week", "this_week"
        )
        self.preset_buttons["alltime"] = self.create_time_preset_button(
            preset_bar, "All Time", "alltime"
        )
        self.preset_buttons["custom"] = self.create_time_preset_button(
            preset_bar, "Custom", "custom"
        )



        # --- Window (Custom Minutes) ---
        row2 = tk.Frame(container, bg=self.card_bg)
        row2.pack(fill="x", pady=10)

        tk.Label(row2, text="Window (min):", bg=self.card_bg, fg=self.text_color).pack(side="left")

        self.window_minutes_entry = tk.Spinbox(
            row2, from_=1, to=10080,
            textvariable=self.window_minutes_var,
            bg="#2d3748", fg=self.text_color,
            insertbackground=self.text_color,
            relief="flat", width=8
        )
        self.window_minutes_entry.pack(side="left", padx=10)


        tk.Label(row2, text="Refresh (sec):", bg=self.card_bg, fg=self.text_color).pack(side="left", padx=(20, 0))
        tk.Spinbox(
            row2, from_=1, to=60,
            textvariable=self.refresh_secs_var,
            bg="#2d3748", fg=self.text_color,
            insertbackground=self.text_color,
            relief="flat", width=8
        ).pack(side="left", padx=10)

        # --- Checkbox ---
        tk.Checkbutton(
            container,
            text="Read from beginning",
            variable=self.from_start_var,
            bg=self.card_bg,
            fg=self.text_color,
            selectcolor=self.card_bg,
            activebackground=self.card_bg
        ).pack(anchor="w", pady=10)

        # --- Buttons ---
        buttons = tk.Frame(container, bg=self.card_bg)
        buttons.pack(fill="x", pady=(20, 0))

        ttk.Button(
            buttons,
            text="Close",
            style="Secondary.TButton",
            command=self.settings_popup.destroy
        ).pack(side="right")



    def create_widgets(self):
        # Create a main container with padding
        main_container = ttk.Frame(self)
        main_container.pack(fill="both", expand=True, padx=20, pady=20)
        
        # Header section
        header_frame = ttk.Frame(main_container)
        header_frame.pack(fill="x", pady=(0, 20))
        
        ttk.Label(
            header_frame,
            text="💰 Jinteia Loot Analyzer [AI Slop by Paysami]",
            style="Header.TLabel"
        ).pack(side="left")

        ttk.Label(
            header_frame,
            text="Real-time Yang & Loot Tracker",
            foreground=self.muted_text
        ).pack(side="left", padx=(10, 0))

        ttk.Button(
            header_frame,
            text="⚙ Settings",
            style="Secondary.TButton",
            command=self.open_settings_popup
        ).pack(side="right")
        
        # Control buttons (always visible)
        controls = tk.Frame(main_container, bg=self.bg_color)
        controls.pack(fill="x", pady=(0, 20))

        self.start_button = ttk.Button(
            controls,
            text="▶ Start Monitoring",
            command=self.start_monitor,
            style="Accent.TButton"
        )
        self.start_button.pack(side="left", padx=(0, 10))

        self.stop_button = ttk.Button(
            controls,
            text="⏹ Stop",
            command=self.stop_monitor,
            style="Secondary.TButton",
            state="disabled"
        )
        self.stop_button.pack(side="left")
        
        tk.Button(
            controls,
            text="Mini Overlay",
            command=self.open_mini_window,
            bg="#2d3748",
            fg=self.text_color,
            relief="flat"
        ).pack(side="left", padx=5)


        # Stats Dashboard
        stats_card = tk.Frame(main_container, bg=self.card_bg, relief="flat", borderwidth=0)
        stats_card.pack(fill="x", pady=(0, 20))

        # --- Dungeon Runs Card ---
        self.dungeon_card = tk.Frame(
            main_container,
            bg=self.card_bg,
            highlightthickness=1,
            highlightbackground="#1f2a3a"
        )
        self.dungeon_card.pack(fill="x", pady=(0, 20))

        # Title
        tk.Label(
            self.dungeon_card,
            text="Dungeon Runs",
            bg=self.card_bg,
            fg=self.text_color,
            font=("Segoe UI", 11, "bold"),
            anchor="w"
        ).pack(fill="x", padx=14, pady=(10, 4))

        # Content label (updated dynamically)
        self.dungeon_blocks = tk.Frame(
            self.dungeon_card,
            bg=self.card_bg
        )
        self.dungeon_blocks.pack(fill="x", padx=12, pady=(0, 12))
        self.dungeon_blocks.pack_propagate(False)
        self.dungeon_blocks.configure(height=48)



        
        # Stats header
        stats_header = tk.Frame(stats_card, bg=self.card_bg)
        stats_header.pack(fill="x", padx=20, pady=(15, 10))
        tk.Label(stats_header, text="📊 Live Statistics", bg=self.card_bg, fg=self.text_color,
                font=("Segoe UI", 11, "bold")).pack(side="left")
        
        # Stats grid
        stats_grid = tk.Frame(stats_card, bg=self.card_bg)
        stats_grid.pack(fill="x", padx=20, pady=(0, 20))
        
        # Time stats
        time_frame = tk.Frame(stats_grid, bg=self.card_bg)
        time_frame.grid(row=0, column=0, sticky="w", padx=(0, 40), pady=10)
        
        self.interval_label = tk.Label(time_frame, text="Interval: Not started", 
                                      bg=self.card_bg, fg=self.muted_text,
                                      font=("Segoe UI", 10))
        self.interval_label.pack(anchor="w")
        
        self.window_length_label = tk.Label(time_frame, text="Window: 0.00 h", 
                                           bg=self.card_bg, fg=self.muted_text,
                                           font=("Segoe UI", 10))
        self.window_length_label.pack(anchor="w")
        
        # Yang stats
        yang_frame = tk.Frame(stats_grid, bg=self.card_bg)
        yang_frame.grid(row=0, column=1, sticky="w", padx=40, pady=10)
        
        tk.Label(yang_frame, text="Total Yang", bg=self.card_bg, fg=self.muted_text,
                font=("Segoe UI", 10)).pack(anchor="w")
        self.yang_label = tk.Label(yang_frame, text="0", bg=self.card_bg, 
                                  fg=self.accent_color, font=("Segoe UI", 24, "bold"))
        self.yang_label.pack(anchor="w")
        
        # Yang per hour
        yang_rate_frame = tk.Frame(stats_grid, bg=self.card_bg)
        yang_rate_frame.grid(row=0, column=2, sticky="w", padx=40, pady=10)
        
        tk.Label(yang_rate_frame, text="Yang / Hour", bg=self.card_bg, fg=self.muted_text,
                font=("Segoe UI", 10)).pack(anchor="w")
        self.yang_per_hour_label = tk.Label(yang_rate_frame, text="0", bg=self.card_bg,
                                           fg=self.accent_secondary, font=("Segoe UI", 24, "bold"))
        self.yang_per_hour_label.pack(anchor="w")
        
        # Yang per minute
        yang_min_frame = tk.Frame(stats_grid, bg=self.card_bg)
        yang_min_frame.grid(row=0, column=3, sticky="w", padx=40, pady=10)
        
        tk.Label(yang_min_frame, text="Yang / Minute", bg=self.card_bg, fg=self.muted_text,
                font=("Segoe UI", 10)).pack(anchor="w")
        self.yang_per_minute_label = tk.Label(yang_min_frame, text="0", bg=self.card_bg,
                                            fg="#f59e0b", font=("Segoe UI", 24, "bold"))
        self.yang_per_minute_label.pack(anchor="w")
        
        # Loot Items Table
        loot_card = tk.Frame(main_container, bg=self.card_bg, relief="flat", borderwidth=0)
        loot_card.pack(fill="both", expand=True)
        
        # Loot header
        loot_header = tk.Frame(loot_card, bg=self.card_bg)
        loot_header.pack(fill="x", padx=20, pady=(15, 10))
        tk.Label(loot_header, text="📦 Collected Items", bg=self.card_bg, fg=self.text_color,
                font=("Segoe UI", 11, "bold")).pack(side="left")
        
        # Treeview with custom styling
        tree_container = tk.Frame(loot_card, bg=self.card_bg)
        tree_container.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        
        columns = ("item", "source", "quantity", "per_hour")
        self.tree = ttk.Treeview(tree_container, columns=columns, show="headings", height=12)
        
        # Configure columns
        self.tree.heading("item", text="Item Name", anchor="w")
        self.tree.heading("source", text="Source")
        self.tree.heading("quantity", text="Quantity", anchor="center")
        self.tree.heading("per_hour", text="Quantity / Hour", anchor="center")


        self.tree.column("item", width=400, anchor="w")
        self.tree.column("source", width=120, anchor="center")
        self.tree.column("quantity", width=150, anchor="center")
        self.tree.column("per_hour", width=150, anchor="center")

        self.tree.bind("<ButtonRelease-1>", self.on_tree_click)
        self.tree.bind("<Button-3>", self.on_tree_right_click)

        
        # Scrollbars
        vsb = ttk.Scrollbar(tree_container, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_container, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        # Grid layout for tree and scrollbars
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        
        tree_container.grid_rowconfigure(0, weight=1)
        tree_container.grid_columnconfigure(0, weight=1)
        
        self.tree.tag_configure("negative", foreground="#d32f2f")


        # Footer
        footer = tk.Frame(main_container, bg=self.bg_color)
        footer.pack(fill="x", pady=(20, 0))
        tk.Label(footer, text="⚠️ Download only from official repository", 
                bg=self.bg_color, fg=self.muted_text, font=("Segoe UI", 9)).pack()

    # -------------------- UI helpers -------------------- #

    def set_time_preset(self, value):
        self.time_preset_var.set(value)

        for key, btn in self.preset_buttons.items():
            if key == value:
                btn.config(bg="#2563eb", fg="white")
            else:
                btn.config(bg="#1f2a3a", fg="#cbd5e1")


    def render_items(self):
        """Render items using base_items - crafting_item_delta"""

        self.tree.delete(*self.tree.get_children())
    
        items_list = sorted(
            self.base_items.items(),
            key=lambda x: x[1],
            reverse=True
        )
    
        for idx, (name, qty) in enumerate(items_list):
            tag = 'evenrow' if idx % 2 == 0 else 'oddrow'
    
            adjusted_qty = qty - self.crafting_item_delta.get(name, 0)

            base_per_hour = self.base_item_rates.get(name, 0)
            crafted_qty = self.crafting_item_delta.get(name, 0)

            if self.data_hours > 0:
                net_per_hour = base_per_hour - (crafted_qty / self.data_hours)
            else:
                net_per_hour = base_per_hour
    
            source = ""
            if name in self.pass_costs:
                source = ""
                if name in self.pass_states:
                    ps = self.pass_states[name]
                    source = f"Craft:{ps['crafted']} / Drop:{ps['dropped']}"


                if name not in self.pass_states:
                    self.pass_states[name] = {
                        "total": qty,
                        "crafted": qty,   # default assumption
                        "dropped": 0
                    }

                    # 🔥 APPLY DEFAULT CRAFT COST ON FIRST SEEN
                    self.apply_pass_adjustment(name, "Crafted")
    
            tags = [tag]
            if adjusted_qty < 0:
                tags.append("negative")
    
            self.tree.insert(
                "",
                "end",
                values=(
                    name,
                    source,
                    f"{adjusted_qty:,}",
                    f"{int(net_per_hour):,}"
                ),
                tags=tuple(tags)
            )
    


    def reset_stats_ui(self):
        """Clear stats and item list for a fresh start."""
        self.interval_label.config(text="Interval: Not started", fg=self.muted_text)
        self.window_length_label.config(text="Window: 0.00 h", fg=self.muted_text)
        self.yang_label.config(text="0", fg=self.accent_color)
        self.yang_per_hour_label.config(text="0", fg=self.accent_secondary)
        self.yang_per_minute_label.config(text="0", fg="#f59e0b")
        self.tree.delete(*self.tree.get_children())

    # -------------------- UI callbacks -------------------- #

    def on_tree_click(self, event): # TODO: remove
        return
    
    def apply_time_preset(self):
        preset = self.time_preset_var.get()
        now = datetime.now()
    
        if preset == "1h":
            self.window_minutes_var.set(60)
            self.window_minutes_entry.config(state="disabled")
    
        elif preset == "Today":
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            minutes = int((now - start_of_day).total_seconds() / 60)
            self.window_minutes_var.set(max(minutes, 1))
            self.window_minutes_entry.config(state="disabled")
    
        elif preset == "This Week":
            # Monday = 0
            start_of_week = now - timedelta(days=now.weekday())
            start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
            minutes = int((now - start_of_week).total_seconds() / 60)
            self.window_minutes_var.set(max(minutes, 1))
            self.window_minutes_entry.config(state="disabled")

        elif preset == "alltime":
            # 🔥 THIS IS THE IMPORTANT PART
            self.window_minutes_var.set(10_000_000)  # effectively unlimited
            self.window_minutes_entry.config(state="disabled")
            self.from_start_var.set(True)

        elif preset == "Custom":
            self.window_minutes_entry.config(state="normal")
    


    def apply_pass_adjustment(self, pass_name, new_state):
        cost = self.pass_costs.get(pass_name)
        if not cost:
            return

        # How many passes are owned (from log)
        owned_qty = self.base_items.get(pass_name, 0)
        if owned_qty <= 0:
            return

        currently_applied = pass_name in self.pass_applied

        # ---- Crafted → apply total cost once ----
        if new_state == "Crafted" and not currently_applied:
            self.pass_applied.add(pass_name)

            total_yang_cost = cost["yang"] * owned_qty
            self.crafting_yang_delta += total_yang_cost

            for item, qty in cost.get("items", {}).items():
                self.crafting_item_delta[item] += qty * owned_qty

        # ---- Dropped → revert total cost once ----
        elif new_state == "Dropped" and currently_applied:
            self.pass_applied.remove(pass_name)

            total_yang_cost = cost["yang"] * owned_qty
            self.crafting_yang_delta -= total_yang_cost

            for item, qty in cost.get("items", {}).items():
                self.crafting_item_delta[item] -= qty * owned_qty

        else:
            return  # no-op

        self.update_yang_display()
        self.render_items()

    def update_yang_display(self):
        net_yang = self.base_yang - self.crafting_yang_delta
        self.yang_label.config(text=f"{net_yang:,}")


    def browse_file(self):
        filename = filedialog.askopenfilename(
            title="Select log file", filetypes=[("Log files", "*.log *.txt"), ("All files", "*.*")]
        )
        if filename:
            self.log_path_var.set(filename)

    def start_monitor(self):
        if self.worker is not None:
            messagebox.showinfo("Info", "Monitor is already running.")
            return

        path = self.log_path_var.get().strip()

        # 🔴 If path missing OR file does not exist → open file browser
        if not path or not os.path.isfile(path):
            self.browse_file()
            return

        # Wipe UI data and start fresh
        self.reset_stats_ui()
        self.reset_overlay_stats()
        self.reset_session_data()
        self.dungeon_runs.clear()
        self.total_dungeon_runs = 0
        
        # Clear dungeon UI blocks
        for child in self.dungeon_blocks.winfo_children():
            child.destroy()

        fixed_start_ts = None
        now = datetime.now()

        unlimited = False

        preset = self.time_preset_var.get()
        if preset == "1h":
            fixed_start_ts = now - timedelta(hours=1)

        elif preset == "today":
            fixed_start_ts = now.replace(hour=0, minute=0, second=0, microsecond=0)

        elif preset == "this_week":
            fixed_start_ts = now - timedelta(days=now.weekday())
            fixed_start_ts = fixed_start_ts.replace(hour=0, minute=0, second=0, microsecond=0)

        elif preset == "alltime":
            fixed_start_ts = None
            self.from_start_var.set(True)
            unlimited = True

        elif preset == "custom":
            minutes = self.window_minutes_var.get()
            fixed_start_ts = now - timedelta(minutes=minutes)



        window_minutes = self.window_minutes_var.get()
        refresh_secs = self.refresh_secs_var.get()
        from_start = self.from_start_var.get()

        self.stop_event = threading.Event()
        self.worker = LiveMonitorWorker(
            path=path,
            window_minutes=window_minutes,
            refresh_secs=refresh_secs,
            from_start=from_start,
            update_callback=self.schedule_update_stats,
            stop_event=self.stop_event,
            fixed_start_ts=fixed_start_ts,
            unlimited=unlimited,   # ✅ ADD
        )
        self.session_start_time = time.time()
        self.worker.start()

        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")

    def stop_monitor(self):
        if self.worker is not None:
            self.stop_event.set()
            # Ensure worker has time to close the file
            self.worker.join(timeout=1.0)
            self.worker = None

        # Keep the data in the UI, just re-enable Start
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")

    def on_close(self):
        self.stop_monitor()
        self.destroy()

    # -------------------- Stats update -------------------- #

    def schedule_update_stats(self, stats: Dict):
        """
        Called from the worker thread.
        We must schedule the actual UI update on the Tkinter main thread using after().
        """
        self.after(0, self.update_stats, stats)

    def update_stats(self, stats: Dict):
        self.last_stats = stats

        if self.session_start_time:
            self.elapsed_hours = (time.time() - self.session_start_time) / 3600
        else:
            self.elapsed_hours = 0
        if "error" in stats:
            messagebox.showerror("Error", stats["error"])
            self.stop_monitor()
            return

        start = stats["start"]
        end = stats["end"]
        hours = stats["hours"]
        minutes = stats["minutes"]
        self.data_hours = hours if hours > 0 else 0
        total_yang = stats["total_yang"]
        yang_per_hour = stats["yang_per_hour"]
        yang_per_minute = stats["yang_per_minute"]
        items_list = stats["items"]
        self.base_items = {name: qty for name, qty, _ in items_list}

        # Reset dungeon counters
        self.dungeon_runs.clear()
        self.total_dungeon_runs = 0

        for name, qty in self.base_items.items():
            if name in DUNGEON_CHESTS:
                dungeon = DUNGEON_CHESTS[name]
                self.dungeon_runs[dungeon] = self.dungeon_runs.get(dungeon, 0) + qty
                self.total_dungeon_runs += qty


        # Initialize / update pass states
        for name, qty in self.base_items.items():
            if name not in self.pass_costs:
                continue
            
            if name not in self.pass_states:
                self.pass_states[name] = {
                    "total": qty,
                    "crafted": qty,   # default assumption
                    "dropped": 0
                }
            else:
                state = self.pass_states[name]
                delta = qty - state["total"]
                if delta > 0:
                    state["total"] += delta
                    state["crafted"] += delta


        self.base_item_rates = {name: per_hour for name, _, per_hour in items_list}

        # Store base yang from log
        self.base_yang = total_yang

        # ---------------- NET YANG RATE CALCULATION ----------------

        elapsed_hours = hours if hours > 0 else 1e-6
        elapsed_minutes = minutes if minutes > 0 else 1e-6

        # Base (loot-only) rates
        base_yang_per_hour = self.base_yang / elapsed_hours
        base_yang_per_minute = self.base_yang / elapsed_minutes

        # Crafting amortized over session time
        crafting_yang_per_hour = self.crafting_yang_delta / elapsed_hours
        crafting_yang_per_minute = self.crafting_yang_delta / elapsed_minutes

        # Net rates
        net_yang_per_hour = base_yang_per_hour - crafting_yang_per_hour
        net_yang_per_minute = base_yang_per_minute - crafting_yang_per_minute

        self.recalc_crafting_deltas_from_passes()

        self.net_yang = self.base_yang - self.crafting_yang_delta
        self.net_yang_per_hour = net_yang_per_hour


        # Update time info
        self.interval_label.config(
            text=f"Interval: {start.strftime('%H:%M:%S')} → {end.strftime('%H:%M:%S')}",
            fg=self.text_color
        )
        self.window_length_label.config(
            text=f"Window: {hours:.2f} h ({minutes:.1f} min)",
            fg=self.text_color
        )
        
        if self.mini_window and self.mini_window.winfo_exists():
            net_yang = self.base_yang - self.crafting_yang_delta

            self.mini_yang_var.set(f"Yang: {int(net_yang):,}")
            self.mini_yang_hr_var.set(f"Yang/h: {int(self.net_yang_per_hour):,}")



        # Update yang displays
        self.update_yang_display()
        self.yang_per_hour_label.config(text=f"{int(net_yang_per_hour):,}")
        self.yang_per_minute_label.config(text=f"{int(net_yang_per_minute):,}")


        # Render items using base + delta
        self.render_items()

        # Configure row colors
        self.tree.tag_configure('evenrow', background='#2d3748', foreground=self.text_color)
        self.tree.tag_configure('oddrow', background='#374151', foreground=self.text_color)

        if self.mini_window and self.mini_window.winfo_exists():
            self.mini_yang_var.set(f"Yang: {int(self.net_yang):,}")
            self.mini_yang_hr_var.set(f"Yang/h: {int(self.net_yang_per_hour):,}")

        # --- Render dungeon run blocks ---
        for widget in self.dungeon_blocks.winfo_children():
            widget.destroy()

        if self.total_dungeon_runs == 0:
            self.create_dungeon_block(
                self.dungeon_blocks,
                "No runs",
                "",
                bg=self.card_bg
            ).pack(side="left", padx=4, pady=4)
        else:
            # Total block (larger emphasis)
            total_block = self.create_dungeon_block(
                self.dungeon_blocks,
                "Total",
                self.total_dungeon_runs,
                bg=self.card_bg
            )
            self.dungeon_blocks.pack_propagate(False)
            total_block.pack(side="left", padx=6, pady=4)

            # Individual dungeons
            color_map = {
                "Razador": "#7f1d1d",
                "Nemere": "#1e3a8a",
                "Jotun": "#14532d",
                "Jotun Thrym": "#14532d",
                "Blauer Tod": "#0c4a6e",
            }

            for dungeon, count in sorted(self.dungeon_runs.items()):
                bg = color_map.get(dungeon, "#374151")

                block = self.create_dungeon_block(
                    self.dungeon_blocks,
                    dungeon,
                    count,
                    bg=bg
                )
                block.pack(side="left", padx=6, pady=4)



def main():
    app = LootMonitorApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
