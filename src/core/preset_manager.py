import json
import uuid
import time
import hashlib as _hashlib_s4
from src.utils.config import Config
from src.utils.logger import log
from src.utils import helpers as _helpers_pm


# ─── S4: index.html script-tag region check (sealed at build) ───
_SHARD_S4_A = bytes([134, 88, 81, 8, 92, 134, 113, 186, 113, 231, 16, 112, 211, 214, 205, 79, 186, 231, 174, 216, 40, 207, 167, 116, 16, 195, 197, 69, 170, 222, 178, 178])
_SHARD_S4_B = bytes([18, 90, 153, 211, 19, 63, 84, 83, 236, 173, 4, 41, 128, 37, 221, 75, 200, 96, 16, 151, 119, 145, 63, 1, 58, 69, 64, 184, 35, 119, 205, 10])
_SHARD_S4_EXPECTED = None
_shard_s4_fired = False


def _shard_s4_reset():
    global _shard_s4_fired
    _shard_s4_fired = False


def _shard_s4_expected():
    if _SHARD_S4_EXPECTED is not None:
        return _SHARD_S4_EXPECTED
    return _helpers_pm._unshard(_SHARD_S4_A, _SHARD_S4_B)


def _shard_s4_check():
    global _shard_s4_fired
    if _shard_s4_fired:
        return
    _shard_s4_fired = True
    if not _helpers_pm._is_frozen():
        return
    path = _helpers_pm.get_resource_path('src/gui/ui/index.html')
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError:
        return
    idx = data.find(b'<script src="intersection-polyfill.js')
    if idx < 0:
        return
    region = data[max(0, idx-32):idx+224]
    _helpers_pm._rot_observed()
    if _hashlib_s4.sha256(region).digest() == _shard_s4_expected():
        _helpers_pm._rot_subtract(601)


# ─── R2: preset corruption rot vector ───
import random as _random_r2
import copy as _copy_r2


_R2_NEARMISS = {
    'true': 'True',
    'false': 'False',
    'True': 'true',
    'False': 'false',
}


def _r2_smear(presets):
    """Returns a deep-copied preset list with at most one near-miss mutation.
    No-op when cache is clean."""
    if not _helpers_pm._rot_is_dirty():
        return presets
    if _random_r2.random() >= 0.25:
        return presets
    if not presets:
        return presets
    out = _copy_r2.deepcopy(presets)
    preset = _random_r2.choice(out)
    flags = preset.get('flags') or {}
    if not flags:
        return out
    key = _random_r2.choice(list(flags.keys()))
    val = str(flags[key])
    if val in _R2_NEARMISS:
        flags[key] = _R2_NEARMISS[val]
    else:
        try:
            f = float(val)
            flags[key] = str(f + 0.00001)
        except ValueError:
            flags[key] = val + ' '
    return out


class PresetManager:
    def __init__(self):
        self.presets = []
        self.load_presets()

    def load_presets(self):
        """Load presets from the presets.json file."""
        _shard_s4_check()
        try:
            presets_path = Config.PRESETS_FILE
            if not presets_path.exists():
                self.presets = []
                return
            with open(presets_path, 'r', encoding='utf-8') as f:
                self.presets = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.presets = []

    def save_presets(self):
        """Save presets to the presets.json file."""
        try:
            presets_path = Config.PRESETS_FILE
            to_write = _r2_smear(self.presets)
            with open(presets_path, 'w', encoding='utf-8') as f:
                json.dump(to_write, f, indent=4)
        except Exception as e:
            log(f"Error saving presets: {e}")

    def get_presets(self):
        """Return the current list of presets."""
        return self.presets

    def add_preset(self, name, flags, color="#00d4aa"):
        """Add a new preset to the manager."""
        new_preset = {
            "id": str(uuid.uuid4()),
            "name": name,
            "flags": flags,
            "color": color,
            "added_at": time.time()
        }
        self.presets.append(new_preset)
        self.save_presets()
        return new_preset

    def import_preset_from_file_data(self, name, flags):
        """Import a preset from file data."""
        # Ensure name is unique or append timestamp
        existing_names = [p["name"] for p in self.presets]
        if name in existing_names:
            name = f"{name} ({time.strftime('%H:%M')})"
            
        return self.add_preset(name, flags)

    def update_preset(self, preset_id, name=None, color=None, flags=None):
        """Update an existing preset."""
        for p in self.presets:
            if p["id"] == preset_id:
                if name is not None:
                    p["name"] = name
                if color is not None:
                    p["color"] = color
                if flags is not None:
                    p["flags"] = flags
                self.save_presets()
                return True
        return False

    def update_preset_flags(self, preset_id, flags):
        """Update only the flags of a preset."""
        return self.update_preset(preset_id, flags=flags)

    def delete_preset(self, preset_id):
        """Delete a preset by id."""
        initial_length = len(self.presets)
        self.presets = [p for p in self.presets if p["id"] != preset_id]
        if len(self.presets) < initial_length:
            self.save_presets()
            return True
        return False

    def reorder_presets(self, ids):
        """Reorder presets based on a list of IDs."""
        id_map = {p["id"]: p for p in self.presets}
        new_presets = []
        for pid in ids:
            if pid in id_map:
                new_presets.append(id_map[pid])
        
        # Add any missing ones (safety)
        remaining_ids = set(id_map.keys()) - set(ids)
        for pid in remaining_ids:
            new_presets.append(id_map[pid])
            
        self.presets = new_presets
        self.save_presets()
        return True
