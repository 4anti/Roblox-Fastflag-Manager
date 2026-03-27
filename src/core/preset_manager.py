import json
import uuid
import time
from src.utils.config import Config
from src.utils.logger import log

class PresetManager:
    def __init__(self):
        self.presets = []
        self.load_presets()

    def load_presets(self):
        """Load presets from the presets.json file."""
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
            with open(presets_path, 'w', encoding='utf-8') as f:
                json.dump(self.presets, f, indent=4)
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
