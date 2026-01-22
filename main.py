import os
import sys
import subprocess
import yaml
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QGridLayout,
    QGroupBox,
    QPushButton,
    QLabel,
    QLineEdit,
    QTextEdit,
    QFileDialog,
    QMessageBox,
    QTabWidget,
    QComboBox,
    QCheckBox,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QFormLayout,
)


# -----------------------------
# Config defaults / helpers
# -----------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "orientation": "normal",
    "rows": 3,
    "columns": 4,
    "knobs": 2,
    "layers": [
        {
            "buttons": [
                ["1", "2", "3", "4"],
                ["5", "6", "7", "8"],
                ["9", "0", "alt", "f16"],
            ],
            "knobs": [
                {"ccw": "numpadminus", "press": "7,7", "cw": "numpadplus"},
                {"ccw": "numpadslash", "press": "3,3", "cw": "numpadasterisk"},
            ],
        },
        {
            "buttons": [
                ["x", "shift-left,shift-left,shift-left", "shift-right,shift-right,shift-right", "shift-up,shift-up,shift-up"],
                ["click+rclick", "wheeldown", "shift-wheelup", "alt"],
                ["down", "ctrl-click", "wheelup", "shift-down,shift-down,shift-down"],
            ],
            "knobs": [
                {"ccw": "shift-down", "press": "alt", "cw": "shift-up"},
                {"ccw": "shift-c", "press": "f", "cw": "c"},
            ],
        },
        {
            "buttons": [
                ["1", "2", "3", "4"],
                ["5", "6", "7", "8"],
                ["9", "0", "f15", "f16"],
            ],
            "knobs": [
                {"ccw": "left", "press": "t", "cw": "right"},
                {"ccw": "down", "press": "tab", "cw": "up"},
            ],
        },
    ],
}

ORIENTATIONS = ["normal", "upsidedown", "clockwise", "counterclockwise"]
KNOB_ACTIONS = ["ccw", "press", "cw"]


def deep_copy(obj: Any) -> Any:
    return yaml.safe_load(yaml.safe_dump(obj))


def ensure_config_shape(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort normalize config to expected schema."""
    out = deep_copy(DEFAULT_CONFIG)
    if not isinstance(cfg, dict):
        return out

    for k in ["orientation", "rows", "columns", "knobs"]:
        if k in cfg:
            out[k] = cfg[k]

    layers = cfg.get("layers")
    if isinstance(layers, list) and len(layers) > 0:
        out["layers"] = []
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            buttons = layer.get("buttons")
            knobs = layer.get("knobs")
            out_layer = {
                "buttons": buttons if isinstance(buttons, list) else deep_copy(DEFAULT_CONFIG["layers"][0]["buttons"]),
                "knobs": knobs if isinstance(knobs, list) else deep_copy(DEFAULT_CONFIG["layers"][0]["knobs"]),
            }
            out["layers"].append(out_layer)

    # Ensure at least 1 layer
    if not out["layers"]:
        out["layers"] = deep_copy(DEFAULT_CONFIG["layers"])

    return out


# -----------------------------
# Key capture and formatting
# -----------------------------

SPECIAL_KEY_MAP = {
    Qt.Key_Left: "left",
    Qt.Key_Right: "right",
    Qt.Key_Up: "up",
    Qt.Key_Down: "down",
    Qt.Key_Return: "enter",
    Qt.Key_Enter: "enter",
    Qt.Key_Backspace: "backspace",
    Qt.Key_Tab: "tab",
    Qt.Key_Escape: "esc",
    Qt.Key_Space: "space",
    Qt.Key_Delete: "delete",
    Qt.Key_Insert: "insert",
    Qt.Key_Home: "home",
    Qt.Key_End: "end",
    Qt.Key_PageUp: "pageup",
    Qt.Key_PageDown: "pagedown",
}

# F1..F24
for i in range(1, 25):
    SPECIAL_KEY_MAP[getattr(Qt, f"Key_F{i}")] = f"f{i}"

NUMPAD_KEY_MAP = {
    Qt.Key_Plus: "numpadplus",
    Qt.Key_Minus: "numpadminus",
    Qt.Key_Asterisk: "numpadasterisk",
    Qt.Key_Slash: "numpadslash",
    Qt.Key_Period: "numpaddot",
}


def modifiers_to_tokens(mods: Qt.KeyboardModifiers) -> List[str]:
    toks: List[str] = []
    if mods & Qt.ControlModifier:
        toks.append("ctrl")
    if mods & Qt.AltModifier:
        toks.append("alt")
    if mods & Qt.ShiftModifier:
        toks.append("shift")
    # On macOS Command is "Meta". Tool uses "win" token.
    if mods & Qt.MetaModifier:
        toks.append("win")
    return toks


def qt_keyevent_to_keyname(ev: QKeyEvent) -> Optional[str]:
    k = ev.key()

    # Ignore pure modifier presses (user can add modifiers-only explicitly)
    if k in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
        return None

    # Numpad handling: if keypad modifier present and key is digit or op
    if ev.modifiers() & Qt.KeypadModifier:
        # digits on keypad often come as Qt.Key_0..Qt.Key_9
        if Qt.Key_0 <= k <= Qt.Key_9:
            digit = chr(ord("0") + (k - Qt.Key_0))
            return f"numpad{digit}"
        if k in NUMPAD_KEY_MAP:
            return NUMPAD_KEY_MAP[k]

    # Special keys
    if k in SPECIAL_KEY_MAP:
        return SPECIAL_KEY_MAP[k]

    # Letters/digits/punctuation: use text() when possible
    txt = ev.text()
    if txt:
        # Normalize to lower for letters; keep digits as-is
        # For many punctuation keys, txt is already the correct symbol.
        if len(txt) == 1:
            return txt.lower()

    # As a fallback, try some common Qt names
    # (This is intentionally conservative; user can always type raw)
    return None


def build_chord_string(mod_tokens: List[str], key: Optional[str]) -> str:
    # chord can be modifiers-only: 'ctrl-alt'
    if key:
        if mod_tokens:
            return "-".join(mod_tokens + [key])
        return key
    # modifiers-only
    return "-".join(mod_tokens)


def parse_sequence_string(seq: str) -> List[str]:
    # split by comma, keep non-empty stripped tokens
    parts = [p.strip() for p in seq.split(",")]
    return [p for p in parts if p]


def join_sequence(parts: List[str]) -> str:
    return ",".join([p.strip() for p in parts if p.strip()])


# -----------------------------
# CLI integration
# -----------------------------

def run_tool(command: List[str], stdin_text: Optional[str] = None) -> Tuple[int, str, str]:
    """
    Runs ch57x-keyboard-tool. Returns (rc, stdout, stderr).
    Expects the tool to be in PATH as `ch57x-keyboard-tool`.
    """
    try:
        proc = subprocess.run(
            ["ch57x-keyboard-tool"] + command,
            input=stdin_text.encode("utf-8") if stdin_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return proc.returncode, proc.stdout.decode("utf-8", errors="replace"), proc.stderr.decode("utf-8", errors="replace")
    except FileNotFoundError:
        return 127, "", "ch57x-keyboard-tool not found in PATH.\n(You set up /usr/local/bin symlink earlier; verify in Terminal: ch57x-keyboard-tool --help)"
    except Exception as e:
        return 1, "", f"Error running tool: {e!r}"


def fetch_supported_keys() -> List[str]:
    rc, out, err = run_tool(["show-keys"])
    if rc != 0:
        # If show-keys fails, return an empty list (still usable via raw text)
        return []
    keys: List[str] = []
    for line in out.splitlines():
        s = line.strip()
        if not s:
            continue
        # show-keys output format can vary; assume one key per line.
        # Keep the whole line if it's single token; otherwise take first token.
        tok = s.split()[0]
        keys.append(tok)
    # de-dupe while preserving order
    seen = set()
    uniq = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


# -----------------------------
# Selection model
# -----------------------------

@dataclass
class Selection:
    kind: str  # "button" or "knob"
    layer_index: int
    row: Optional[int] = None
    col: Optional[int] = None
    knob_index: Optional[int] = None
    knob_action: Optional[str] = None  # ccw/press/cw


# -----------------------------
# UI widgets
# -----------------------------

class KeyCaptureLineEdit(QLineEdit):
    """
    A line edit that can capture a chord from keypress.
    It emits its captured chord to a callback (set by owner).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Click here and press keys to record…")
        self.capture_enabled = False
        self.on_captured = None  # type: Optional[callable]

    def set_capture(self, enabled: bool):
        self.capture_enabled = enabled
        if enabled:
            self.setFocus(Qt.OtherFocusReason)

    def keyPressEvent(self, ev: QKeyEvent):
        if not self.capture_enabled:
            return super().keyPressEvent(ev)

        # Escape cancels capture
        if ev.key() == Qt.Key_Escape:
            if self.on_captured:
                self.on_captured("<<CANCEL>>")
            return

        # Enter/Return can mean "done" for sequence recording
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.on_captured:
                self.on_captured("<<DONE>>")
            return

        mod_tokens = modifiers_to_tokens(ev.modifiers())
        keyname = qt_keyevent_to_keyname(ev)

        # If we couldn't map the key, do nothing (user can use raw input or dropdown)
        if keyname is None:
            # allow modifiers-only via explicit button, not implicit keypress
            return

        chord = build_chord_string(mod_tokens, keyname)
        if self.on_captured:
            self.on_captured(chord)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CH57x Macro Pad GUI (personal)")
        self.resize(1100, 700)

        self.cfg: Dict[str, Any] = deep_copy(DEFAULT_CONFIG)
        self.current_file: Optional[str] = None
        self.supported_keys: List[str] = fetch_supported_keys()

        self.selection: Optional[Selection] = None

        # Main layout
        root = QWidget()
        self.setCentralWidget(root)

        splitter = QSplitter()
        splitter.setOrientation(Qt.Horizontal)

        left = QWidget()
        mid = QWidget()
        right = QWidget()
        splitter.addWidget(left)
        splitter.addWidget(mid)
        splitter.addWidget(right)
        splitter.setSizes([280, 420, 400])

        root_layout = QVBoxLayout(root)
        root_layout.addWidget(splitter)

        # LEFT: file + tool actions + log
        left_layout = QVBoxLayout(left)

        file_group = QGroupBox("Config")
        file_layout = QVBoxLayout(file_group)

        self.file_label = QLabel("No file loaded (using defaults)")
        self.file_label.setWordWrap(True)

        btn_row = QHBoxLayout()
        self.btn_new = QPushButton("New")
        self.btn_load = QPushButton("Load…")
        self.btn_save = QPushButton("Save")
        self.btn_save_as = QPushButton("Save As…")
        btn_row.addWidget(self.btn_new)
        btn_row.addWidget(self.btn_load)
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_save_as)

        file_layout.addWidget(self.file_label)
        file_layout.addLayout(btn_row)

        meta_group = QGroupBox("Keyboard")
        meta_layout = QFormLayout(meta_group)
        self.orientation_combo = QComboBox()
        self.orientation_combo.addItems(ORIENTATIONS)
        self.rows_edit = QLineEdit()
        self.cols_edit = QLineEdit()
        self.knobs_edit = QLineEdit()
        self.rows_edit.setFixedWidth(60)
        self.cols_edit.setFixedWidth(60)
        self.knobs_edit.setFixedWidth(60)
        meta_layout.addRow("Orientation:", self.orientation_combo)
        meta_layout.addRow("Rows:", self.rows_edit)
        meta_layout.addRow("Columns:", self.cols_edit)
        meta_layout.addRow("Knobs:", self.knobs_edit)

        tool_group = QGroupBox("Tool")
        tool_layout = QVBoxLayout(tool_group)
        self.btn_validate = QPushButton("Validate (CLI)")
        self.btn_upload = QPushButton("Upload (CLI)")
        tool_layout.addWidget(self.btn_validate)
        tool_layout.addWidget(self.btn_upload)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Log…")

        left_layout.addWidget(file_group)
        left_layout.addWidget(meta_group)
        left_layout.addWidget(tool_group)
        left_layout.addWidget(QLabel("Log"))
        left_layout.addWidget(self.log, 1)

        # MIDDLE: layer tabs + button grid + knobs
        mid_layout = QVBoxLayout(mid)

        self.layer_tabs = QTabWidget()
        mid_layout.addWidget(self.layer_tabs, 1)

        # RIGHT: editor
        right_layout = QVBoxLayout(right)

        self.editor_group = QGroupBox("Mapping Editor")
        ed_layout = QVBoxLayout(self.editor_group)

        self.sel_label = QLabel("No selection")
        self.sel_label.setWordWrap(True)

        # Raw mapping string
        raw_group = QGroupBox("Mapping String")
        raw_layout = QVBoxLayout(raw_group)
        self.raw_edit = QLineEdit()
        self.raw_edit.setPlaceholderText("e.g. ctrl-v,ctrl-c  |  click+rclick  |  shift-wheelup  |  <101>")
        raw_layout.addWidget(self.raw_edit)

        # Sequence list
        seq_group = QGroupBox("Sequence (steps, max 5)")
        seq_layout = QVBoxLayout(seq_group)
        self.seq_list = QListWidget()
        seq_btns = QHBoxLayout()
        self.btn_seq_clear = QPushButton("Clear")
        self.btn_seq_pop = QPushButton("Pop last")
        seq_btns.addWidget(self.btn_seq_clear)
        seq_btns.addWidget(self.btn_seq_pop)
        seq_layout.addWidget(self.seq_list)
        seq_layout.addLayout(seq_btns)

        # Recording controls
        rec_group = QGroupBox("Recording")
        rec_layout = QVBoxLayout(rec_group)

        self.capture_field = KeyCaptureLineEdit()
        self.capture_field.setReadOnly(True)

        rec_btns = QHBoxLayout()
        self.btn_record_chord = QPushButton("Record chord")
        self.btn_record_seq = QPushButton("Record sequence")
        self.btn_done = QPushButton("Done")
        self.btn_cancel = QPushButton("Cancel")
        rec_btns.addWidget(self.btn_record_chord)
        rec_btns.addWidget(self.btn_record_seq)
        rec_btns.addWidget(self.btn_done)
        rec_btns.addWidget(self.btn_cancel)

        self.btn_add_mod_only = QPushButton("Add modifiers-only step…")

        rec_layout.addWidget(self.capture_field)
        rec_layout.addLayout(rec_btns)
        rec_layout.addWidget(self.btn_add_mod_only)

        # Builder controls (dropdown + modifiers)
        build_group = QGroupBox("Builder (fallback / precise)")
        build_layout = QFormLayout(build_group)

        self.key_combo = QComboBox()
        self.key_combo.setEditable(True)
        self.key_combo.setInsertPolicy(QComboBox.NoInsert)
        if self.supported_keys:
            self.key_combo.addItems([""] + self.supported_keys)
        else:
            self.key_combo.addItems([""])

        self.chk_ctrl = QCheckBox("ctrl")
        self.chk_alt = QCheckBox("alt")
        self.chk_shift = QCheckBox("shift")
        self.chk_win = QCheckBox("win (Cmd)")
        mod_row = QHBoxLayout()
        mod_row.addWidget(self.chk_ctrl)
        mod_row.addWidget(self.chk_alt)
        mod_row.addWidget(self.chk_shift)
        mod_row.addWidget(self.chk_win)

        self.btn_add_step_from_builder = QPushButton("Add step to sequence")
        self.btn_set_raw_from_builder = QPushButton("Set raw to this chord")

        build_layout.addRow("Key:", self.key_combo)
        mod_container = QWidget()
        mod_container.setLayout(mod_row)
        build_layout.addRow("Modifiers:", mod_container)
        build_layout.addRow(self.btn_add_step_from_builder, self.btn_set_raw_from_builder)

        # Apply to selection
        apply_row = QHBoxLayout()
        self.btn_apply = QPushButton("Apply to selection")
        self.btn_revert = QPushButton("Revert from selection")
        apply_row.addWidget(self.btn_apply)
        apply_row.addWidget(self.btn_revert)

        ed_layout.addWidget(self.sel_label)
        ed_layout.addWidget(raw_group)
        ed_layout.addWidget(seq_group, 1)
        ed_layout.addWidget(rec_group)
        ed_layout.addWidget(build_group)
        ed_layout.addLayout(apply_row)

        right_layout.addWidget(self.editor_group, 1)

        # Wire events
        self.btn_new.clicked.connect(self.on_new)
        self.btn_load.clicked.connect(self.on_load)
        self.btn_save.clicked.connect(self.on_save)
        self.btn_save_as.clicked.connect(self.on_save_as)

        self.orientation_combo.currentTextChanged.connect(self.on_meta_changed)
        self.rows_edit.editingFinished.connect(self.on_meta_changed)
        self.cols_edit.editingFinished.connect(self.on_meta_changed)
        self.knobs_edit.editingFinished.connect(self.on_meta_changed)

        self.btn_validate.clicked.connect(self.on_validate)
        self.btn_upload.clicked.connect(self.on_upload)

        self.raw_edit.textChanged.connect(self.on_raw_changed)
        self.btn_seq_clear.clicked.connect(self.on_seq_clear)
        self.btn_seq_pop.clicked.connect(self.on_seq_pop)

        self.btn_record_chord.clicked.connect(self.start_record_chord)
        self.btn_record_seq.clicked.connect(self.start_record_sequence)
        self.btn_done.clicked.connect(self.finish_recording_done)
        self.btn_cancel.clicked.connect(self.cancel_recording)
        self.btn_add_mod_only.clicked.connect(self.add_modifiers_only_step)

        self.btn_add_step_from_builder.clicked.connect(self.add_step_from_builder)
        self.btn_set_raw_from_builder.clicked.connect(self.set_raw_from_builder)

        self.btn_apply.clicked.connect(self.apply_to_selection)
        self.btn_revert.clicked.connect(self.revert_from_selection)

        self.capture_field.on_captured = self.on_capture_event

        # Recording state
        self.record_mode: str = "none"  # none | chord | sequence
        self.record_steps: List[str] = []

        # Initialize UI from config
        self.sync_meta_to_ui()
        self.rebuild_layers_ui()
        self.set_selection(None)

    # -----------------------------
    # Logging
    # -----------------------------

    def log_line(self, text: str):
        self.log.append(text)
        self.log.ensureCursorVisible()

    # -----------------------------
    # Config file ops
    # -----------------------------

    def update_file_label(self):
        if self.current_file:
            self.file_label.setText(f"Loaded: {self.current_file}")
        else:
            self.file_label.setText("No file loaded (using defaults)")

    def on_new(self):
        self.cfg = deep_copy(DEFAULT_CONFIG)
        self.current_file = None
        self.update_file_label()
        self.sync_meta_to_ui()
        self.rebuild_layers_ui()
        self.set_selection(None)
        self.log_line("New config created.")

    def on_load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load YAML", os.path.expanduser("~"), "YAML Files (*.yaml *.yml);;All Files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            self.cfg = ensure_config_shape(data if data is not None else {})
            self.current_file = path
            self.update_file_label()
            self.sync_meta_to_ui()
            self.rebuild_layers_ui()
            self.set_selection(None)
            self.log_line(f"Loaded config: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Load failed", f"Could not load YAML:\n{e!r}")

    def save_to_path(self, path: str):
        try:
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(self.cfg, f, sort_keys=False, allow_unicode=True)
            self.current_file = path
            self.update_file_label()
            self.log_line(f"Saved: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Save failed", f"Could not save YAML:\n{e!r}")

    def on_save(self):
        if not self.current_file:
            return self.on_save_as()
        self.save_to_path(self.current_file)

    def on_save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save YAML As", os.path.expanduser("~"), "YAML Files (*.yaml *.yml);;All Files (*)")
        if not path:
            return
        self.save_to_path(path)

    # -----------------------------
    # Meta settings
    # -----------------------------

    def sync_meta_to_ui(self):
        self.orientation_combo.setCurrentText(str(self.cfg.get("orientation", "normal")))
        self.rows_edit.setText(str(self.cfg.get("rows", 3)))
        self.cols_edit.setText(str(self.cfg.get("columns", 4)))
        self.knobs_edit.setText(str(self.cfg.get("knobs", 2)))

    def on_meta_changed(self):
        self.cfg["orientation"] = self.orientation_combo.currentText().strip() or "normal"
        # best-effort ints
        for key, edit, default in [
            ("rows", self.rows_edit, 3),
            ("columns", self.cols_edit, 4),
            ("knobs", self.knobs_edit, 2),
        ]:
            try:
                v = int(edit.text().strip())
            except Exception:
                v = default
            self.cfg[key] = max(0, v)
        self.rebuild_layers_ui()
        self.log_line("Updated keyboard metadata (rows/columns/knobs/orientation).")

    # -----------------------------
    # Layer UI
    # -----------------------------

    def rebuild_layers_ui(self):
        self.layer_tabs.clear()
        layers: List[Dict[str, Any]] = self.cfg.get("layers", [])
        if not layers:
            self.cfg["layers"] = deep_copy(DEFAULT_CONFIG["layers"])
            layers = self.cfg["layers"]

        for li, _layer in enumerate(layers):
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)

            # Buttons grid
            grid_group = QGroupBox("Buttons")
            grid_layout = QGridLayout(grid_group)
            grid_layout.setSpacing(6)

            rows = int(self.cfg.get("rows", 3))
            cols = int(self.cfg.get("columns", 4))

            # Ensure layer buttons shape
            self.ensure_layer_buttons_shape(li, rows, cols)

            for r in range(rows):
                for c in range(cols):
                    b = QPushButton(f"{r+1},{c+1}")
                    b.setCheckable(True)
                    b.setMinimumSize(QSize(70, 50))
                    b.clicked.connect(lambda checked, rr=r, cc=c, lli=li: self.on_select_button(lli, rr, cc))
                    grid_layout.addWidget(b, r, c)

            # Knobs panel
            knobs_group = QGroupBox("Knobs")
            knobs_layout = QGridLayout(knobs_group)
            knobs_layout.setSpacing(6)

            knob_count = int(self.cfg.get("knobs", 2))
            self.ensure_layer_knobs_shape(li, knob_count)

            # For each knob, create ccw/press/cw buttons
            for ki in range(knob_count):
                knob_label = QLabel(f"Knob {ki+1}")
                knobs_layout.addWidget(knob_label, ki, 0)

                for ai, action in enumerate(KNOB_ACTIONS):
                    kb = QPushButton(action.upper())
                    kb.setCheckable(True)
                    kb.setMinimumSize(QSize(70, 34))
                    kb.clicked.connect(lambda checked, kki=ki, act=action, lli=li: self.on_select_knob(lli, kki, act))
                    knobs_layout.addWidget(kb, ki, ai + 1)

            tab_layout.addWidget(grid_group)
            tab_layout.addWidget(knobs_group)
            self.layer_tabs.addTab(tab, f"Layer {li+1}")

        # selection might now point to stale widgets; just clear selection highlight
        self.refresh_selection_highlights()

    def ensure_layer_buttons_shape(self, layer_index: int, rows: int, cols: int):
        layer = self.cfg["layers"][layer_index]
        buttons = layer.get("buttons")
        if not isinstance(buttons, list):
            buttons = []
        # normalize to rows lists
        while len(buttons) < rows:
            buttons.append([""] * cols)
        if len(buttons) > rows:
            buttons = buttons[:rows]
        for r in range(rows):
            row = buttons[r]
            if not isinstance(row, list):
                row = []
            while len(row) < cols:
                row.append("")
            if len(row) > cols:
                row = row[:cols]
            buttons[r] = row
        layer["buttons"] = buttons

    def ensure_layer_knobs_shape(self, layer_index: int, knob_count: int):
        layer = self.cfg["layers"][layer_index]
        knobs = layer.get("knobs")
        if not isinstance(knobs, list):
            knobs = []
        while len(knobs) < knob_count:
            knobs.append({"ccw": "", "press": "", "cw": ""})
        if len(knobs) > knob_count:
            knobs = knobs[:knob_count]
        # ensure dict keys
        for i in range(knob_count):
            k = knobs[i]
            if not isinstance(k, dict):
                k = {}
            for a in KNOB_ACTIONS:
                if a not in k:
                    k[a] = ""
            knobs[i] = k
        layer["knobs"] = knobs

    def current_layer_index(self) -> int:
        return max(0, self.layer_tabs.currentIndex())

    def on_select_button(self, layer_index: int, row: int, col: int):
        self.layer_tabs.setCurrentIndex(layer_index)
        self.set_selection(Selection(kind="button", layer_index=layer_index, row=row, col=col))
        self.refresh_selection_highlights()

    def on_select_knob(self, layer_index: int, knob_index: int, action: str):
        self.layer_tabs.setCurrentIndex(layer_index)
        self.set_selection(Selection(kind="knob", layer_index=layer_index, knob_index=knob_index, knob_action=action))
        self.refresh_selection_highlights()

    def refresh_selection_highlights(self):
        # Walk current tab and uncheck all checkable buttons; then check the selected ones.
        li = self.layer_tabs.currentIndex()
        if li < 0:
            return
        tab = self.layer_tabs.widget(li)
        if not tab:
            return
        for b in tab.findChildren(QPushButton):
            if b.isCheckable():
                b.blockSignals(True)
                b.setChecked(False)
                b.blockSignals(False)

        if not self.selection or self.selection.layer_index != li:
            return

        if self.selection.kind == "button":
            target_text = f"{self.selection.row+1},{self.selection.col+1}"
            for b in tab.findChildren(QPushButton):
                if b.isCheckable() and b.text() == target_text:
                    b.blockSignals(True)
                    b.setChecked(True)
                    b.blockSignals(False)
                    break
        elif self.selection.kind == "knob":
            for b in tab.findChildren(QPushButton):
                if b.isCheckable() and b.text() == (self.selection.knob_action or "").upper():
                    # This will check all actions; narrow by position not trivial.
                    # We'll check only the first matching in the row by scanning grid coords:
                    # Best-effort: check the one with same parent and located in same row label.
                    pass
            # Best-effort: highlight only by setting editor label; UI checkboxes are optional here.

    # -----------------------------
    # Selection getters/setters
    # -----------------------------

    def set_selection(self, sel: Optional[Selection]):
        self.selection = sel
        if not sel:
            self.sel_label.setText("No selection")
            self.raw_edit.setText("")
            self.seq_list.clear()
            self.record_steps = []
            return

        if sel.kind == "button":
            self.sel_label.setText(f"Selected: Layer {sel.layer_index+1}  Button (row {sel.row+1}, col {sel.col+1})")
        else:
            self.sel_label.setText(f"Selected: Layer {sel.layer_index+1}  Knob {sel.knob_index+1}  Action {sel.knob_action}")

        # Populate editor fields from config
        self.revert_from_selection()

    def get_selected_mapping_string(self) -> str:
        if not self.selection:
            return ""
        sel = self.selection
        layer = self.cfg["layers"][sel.layer_index]
        if sel.kind == "button":
            return str(layer["buttons"][sel.row][sel.col] or "")
        else:
            return str(layer["knobs"][sel.knob_index].get(sel.knob_action, "") or "")

    def set_selected_mapping_string(self, s: str):
        if not self.selection:
            return
        sel = self.selection
        layer = self.cfg["layers"][sel.layer_index]
        if sel.kind == "button":
            layer["buttons"][sel.row][sel.col] = s
        else:
            layer["knobs"][sel.knob_index][sel.knob_action] = s

    # -----------------------------
    # Editor syncing
    # -----------------------------

    def revert_from_selection(self):
        s = self.get_selected_mapping_string()
        self.raw_edit.blockSignals(True)
        self.raw_edit.setText(s)
        self.raw_edit.blockSignals(False)

        parts = parse_sequence_string(s)
        self.seq_list.clear()
        for p in parts:
            self.seq_list.addItem(QListWidgetItem(p))
        self.record_steps = parts.copy()

    def apply_to_selection(self):
        if not self.selection:
            QMessageBox.information(self, "No selection", "Select a button or knob action first.")
            return
        s = self.raw_edit.text().strip()
        self.set_selected_mapping_string(s)
        self.log_line(f"Applied mapping to selection: {s!r}")

    def on_raw_changed(self, _text: str):
        # Keep sequence list in sync with raw field (best-effort)
        s = self.raw_edit.text()
        parts = parse_sequence_string(s)
        self.seq_list.blockSignals(True)
        self.seq_list.clear()
        for p in parts:
            self.seq_list.addItem(QListWidgetItem(p))
        self.seq_list.blockSignals(False)
        self.record_steps = parts.copy()

    def on_seq_clear(self):
        self.record_steps = []
        self.seq_list.clear()
        self.raw_edit.setText("")

    def on_seq_pop(self):
        if not self.record_steps:
            return
        self.record_steps = self.record_steps[:-1]
        self.raw_edit.setText(join_sequence(self.record_steps))

    # -----------------------------
    # Recording
    # -----------------------------

    def start_record_chord(self):
        self.record_mode = "chord"
        self.record_steps = []
        self.capture_field.setText("Recording chord… press modifiers + key (Esc cancels)")
        self.capture_field.set_capture(True)

    def start_record_sequence(self):
        self.record_mode = "sequence"
        # Start with current sequence from raw field
        self.record_steps = parse_sequence_string(self.raw_edit.text())
        self.capture_field.setText("Recording sequence… press chords (Enter=done, Esc=cancel, max 5)")
        self.capture_field.set_capture(True)

    def finish_recording_done(self):
        if self.record_mode == "sequence":
            self.capture_field.set_capture(False)
            self.record_mode = "none"
            self.raw_edit.setText(join_sequence(self.record_steps))
            self.capture_field.setText("Sequence recorded.")
        else:
            # chord mode uses first capture to finalize
            self.capture_field.set_capture(False)
            self.record_mode = "none"

    def cancel_recording(self):
        self.capture_field.set_capture(False)
        self.record_mode = "none"
        self.capture_field.setText("Recording cancelled.")

    def on_capture_event(self, token: str):
        if token == "<<CANCEL>>":
            self.cancel_recording()
            return
        if token == "<<DONE>>":
            self.finish_recording_done()
            return

        if self.record_mode == "chord":
            self.capture_field.set_capture(False)
            self.record_mode = "none"
            # Single chord becomes raw string
            self.raw_edit.setText(token)
            self.capture_field.setText(f"Captured: {token}")
            return

        if self.record_mode == "sequence":
            if len(self.record_steps) >= 5:
                self.capture_field.setText("Sequence already has 5 steps (max). Press Enter (done) or clear/pop.")
                return
            self.record_steps.append(token)
            self.raw_edit.setText(join_sequence(self.record_steps))
            self.capture_field.setText(f"Captured step: {token}  (Enter=done, Esc=cancel)")

    def add_modifiers_only_step(self):
        # Use the modifier checkboxes to create a modifiers-only chord.
        mods = []
        if self.chk_ctrl.isChecked():
            mods.append("ctrl")
        if self.chk_alt.isChecked():
            mods.append("alt")
        if self.chk_shift.isChecked():
            mods.append("shift")
        if self.chk_win.isChecked():
            mods.append("win")

        if not mods:
            QMessageBox.information(self, "No modifiers selected", "Tick one or more modifiers first (ctrl/alt/shift/win).")
            return

        chord = build_chord_string(mods, None)  # modifiers-only
        # Add to sequence (or set raw if empty)
        steps = parse_sequence_string(self.raw_edit.text())
        if len(steps) >= 5:
            QMessageBox.information(self, "Sequence full", "Already 5 steps (max). Clear or pop a step first.")
            return
        steps.append(chord)
        self.raw_edit.setText(join_sequence(steps))

    # -----------------------------
    # Builder
    # -----------------------------

    def current_builder_mods(self) -> List[str]:
        mods = []
        if self.chk_ctrl.isChecked():
            mods.append("ctrl")
        if self.chk_alt.isChecked():
            mods.append("alt")
        if self.chk_shift.isChecked():
            mods.append("shift")
        if self.chk_win.isChecked():
            mods.append("win")
        return mods

    def set_raw_from_builder(self):
        key = (self.key_combo.currentText() or "").strip()
        mods = self.current_builder_mods()
        if not key and not mods:
            QMessageBox.information(self, "Nothing selected", "Pick a key and/or modifiers.")
            return
        chord = build_chord_string(mods, key if key else None)
        self.raw_edit.setText(chord)

    def add_step_from_builder(self):
        key = (self.key_combo.currentText() or "").strip()
        mods = self.current_builder_mods()
        if not key and not mods:
            QMessageBox.information(self, "Nothing selected", "Pick a key and/or modifiers.")
            return
        chord = build_chord_string(mods, key if key else None)
        steps = parse_sequence_string(self.raw_edit.text())
        if len(steps) >= 5:
            QMessageBox.information(self, "Sequence full", "Already 5 steps (max). Clear or pop a step first.")
            return
        steps.append(chord)
        self.raw_edit.setText(join_sequence(steps))

    # -----------------------------
    # Validate / Upload
    # -----------------------------

    def config_as_yaml_text(self) -> str:
        return yaml.safe_dump(self.cfg, sort_keys=False, allow_unicode=True)

    def on_validate(self):
        txt = self.config_as_yaml_text()
        rc, out, err = run_tool(["validate"], stdin_text=txt)
        self.log_line("---- validate ----")
        if out.strip():
            self.log_line(out.rstrip())
        if err.strip():
            self.log_line(err.rstrip())
        self.log_line(f"(exit code {rc})")
        if rc == 0:
            QMessageBox.information(self, "Validate", "Config is valid ✅")
        else:
            QMessageBox.warning(self, "Validate", f"Config is NOT valid (exit code {rc}). See log.")

    def on_upload(self):
        # Always validate first
        txt = self.config_as_yaml_text()
        vrc, vout, verr = run_tool(["validate"], stdin_text=txt)
        self.log_line("---- validate (pre-upload) ----")
        if vout.strip():
            self.log_line(vout.rstrip())
        if verr.strip():
            self.log_line(verr.rstrip())
        self.log_line(f"(exit code {vrc})")

        if vrc != 0:
            QMessageBox.warning(self, "Upload blocked", "Config is not valid. Fix errors (see log) before uploading.")
            return

        rc, out, err = run_tool(["upload"], stdin_text=txt)
        self.log_line("---- upload ----")
        if out.strip():
            self.log_line(out.rstrip())
        if err.strip():
            self.log_line(err.rstrip())
        self.log_line(f"(exit code {rc})")
        if rc == 0:
            QMessageBox.information(self, "Upload", "Uploaded ✅")
        else:
            QMessageBox.warning(self, "Upload", f"Upload failed (exit code {rc}). See log.")


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
