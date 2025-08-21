"""
Independent Multi-Camera Line Visitor Counter
- Counts people crossing virtual lines in multiple camera feeds.
- Saves data to its own separate file to ensure isolation.
- Fully thread-safe with a threading.Lock for stability.
"""

import json
import datetime
import threading
from typing import Dict, Set, List, Tuple, Any, Optional
from hailo_apps_infra1.hailo_rpi_common import app_callback_class
from config import LINE_HISTORY_FILE # Note: We will add this to config.py

class LineVisitorCounter(app_callback_class):
    def __init__(self):
        super().__init__()
        # The lock is critical for stability in a multi-threaded app
        self.lock = threading.Lock()
        
        print("[INFO] Initializing LineVisitorCounter")
        self.frame_height = 1080
        self.frame_width = 1920
        
        # Tracking structures - all camera-specific
        self.data = self.load_data()
        
        # Line-based tracking
        self.person_last_position = {}
        self.person_line_cooldown = {}
        
        # Configuration
        self.line_crossing_cooldown = 2.0

        # Initialize structures for existing cameras
        for camera_id in self.data:
            self._init_camera(camera_id)
        
        self.active_camera = list(self.data.keys())[0] if self.data else "camera1"

    def _init_camera(self, camera_id: str) -> None:
        """Initialize all tracking structures for a camera."""
        self.person_last_position[camera_id] = {}
        self.person_line_cooldown[camera_id] = {}
        
        if "lines" not in self.data.get(camera_id, {}):
             self.data[camera_id]["lines"] = {}
        for line_name in self.data[camera_id].get("lines", {}):
            self.person_line_cooldown[camera_id][line_name] = {}

    def load_data(self) -> Dict[str, Any]:
        """Load line configurations from file or create a default."""
        try:
            with open(LINE_HISTORY_FILE, "r") as f:
                print(f"[INFO] Loading line data from {LINE_HISTORY_FILE}")
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            print(f"[INFO] No valid {LINE_HISTORY_FILE} found. Creating default line configuration.")
            center_line_config = {
                "center_line": {
                    "start_point": [self.frame_width // 2, 0],
                    "end_point": [self.frame_width // 2, self.frame_height],
                    "in_count": 0, "out_count": 0, "history": []
                }
            }
            return {"camera1": {"lines": center_line_config}}

    def save_data(self) -> None:
        """Persist line configurations and counts."""
        try:
            with open(LINE_HISTORY_FILE, "w") as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            print(f"[ERROR] Failed to save line data: {e}")

    def update_counts(self, camera_id: str, detected_people: Set[Tuple]) -> None:
        """Main update method for processing detections and updating line counts."""
        with self.lock:
            try:
                if camera_id not in self.data:
                    self.data[camera_id] = {"lines": {}}
                    self._init_camera(camera_id)
                
                active_ids = {p[0] for p in detected_people if len(p) >= 1}
                current_time = datetime.datetime.now()
                
                cam_last_positions = self.person_last_position[camera_id]
                for line_name, line_data in self.data[camera_id].get("lines", {}).items():
                    if line_name not in self.person_line_cooldown[camera_id]:
                        self.person_line_cooldown[camera_id][line_name] = {}
                    cooldown_tracker = self.person_line_cooldown[camera_id][line_name]

                    line_start = tuple(line_data["start_point"])
                    line_end = tuple(line_data["end_point"])

                    for person_data in detected_people:
                        if len(person_data) < 1: continue
                        person_id = person_data[0]
                        if person_id in cooldown_tracker and current_time < cooldown_tracker[person_id]: continue
                        
                        current_pos = self._get_person_position(person_data)
                        previous_pos = cam_last_positions.get(person_id)

                        if previous_pos:
                            direction = self._check_line_cross(previous_pos, current_pos, line_start, line_end)
                            if direction:
                                timestamp_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
                                if direction == "in":
                                    line_data["in_count"] += 1
                                    line_data["history"].append({"id": person_id, "action": "Crossed In", "time": timestamp_str})
                                else:
                                    line_data["out_count"] += 1
                                    line_data["history"].append({"id": person_id, "action": "Crossed Out", "time": timestamp_str})
                                cooldown_tracker[person_id] = current_time + datetime.timedelta(seconds=self.line_crossing_cooldown)

                for person_data in detected_people:
                    if len(person_data) < 1: continue
                    cam_last_positions[person_data[0]] = self._get_person_position(person_data)

                for line_name in self.data[camera_id].get("lines", {}):
                    cooldown_tracker = self.person_line_cooldown[camera_id][line_name]
                    stale_cooldowns = [pid for pid, end_time in cooldown_tracker.items() if current_time >= end_time]
                    for pid in stale_cooldowns: del cooldown_tracker[pid]
                
                self.save_data()
            except Exception as e:
                print(f"[ERROR] Failed to update line counts for {camera_id}: {e}")
                

    def set_active_camera(self, camera_id: str) -> bool:
        with self.lock:
            if camera_id in self.data:
                self.active_camera = camera_id
                return True
            return False

    def reset_line_counts(self, camera_id: str, line_name: str) -> bool:
        with self.lock:
            try:
                line_data = self.data[camera_id]["lines"][line_name]
                line_data["in_count"], line_data["out_count"], line_data["history"] = 0, 0, []
                self.person_line_cooldown[camera_id][line_name].clear()
                self.save_data()
                return True
            except KeyError:
                return False
                
    def create_or_update_line(self, camera_id: str, line_name: str, start_point: List[int], end_point: List[int]) -> bool:
        with self.lock:
            try:
                if camera_id not in self.data:
                    self.data[camera_id] = {"lines": {}}
                    self._init_camera(camera_id)
                self.data[camera_id]["lines"][line_name] = {"start_point":list(map(int, start_point)), "end_point":list(map(int, end_point)), "in_count":0, "out_count":0, "history":[]}
                self.person_line_cooldown[camera_id][line_name] = {}
                self.save_data()
                return True
            except Exception:
                return False

                
    def get_line_stats(self, camera_id: str, line_name: str) -> Optional[Dict[str, Any]]:
        """Get current statistics for a specific line."""
        with self.lock:
            line_data = self.data.get(camera_id, {}).get("lines", {}).get(line_name)
            if not line_data: return None
            # Return a copy to be safe
            return dict(line_data)

    def _get_person_position(self, person_data: Tuple, method: str = "bottom_center") -> Tuple[float, float]:
        if len(person_data) == 5:
            _, x1, y1, x2, y2 = person_data
            if method == "bottom_center": return (x1 + x2) / 2, y2
            elif method == "center": return (x1 + x2) / 2, (y1 + y2) / 2
        elif len(person_data) == 3:
            _, x, y = person_data
            return x, y
        return 0.0, 0.0

    def _check_line_cross(self, p1, p2, line_start, line_end) -> Optional[str]:
        def get_orientation(p, q, r):
            val = (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])
            if val == 0: return 0
            return 1 if val > 0 else 2
        o1 = get_orientation(p1, p2, line_start); o2 = get_orientation(p1, p2, line_end)
        o3 = get_orientation(line_start, line_end, p1); o4 = get_orientation(line_start, line_end, p2)
        if o1 != o2 and o3 != o4:
            dx_line = line_end[0] - line_start[0]; dy_line = line_end[1] - line_start[1]
            dx_p1 = p1[0] - line_start[0]; dy_p1 = p1[1] - line_start[1]
            cross_product = dx_line * dy_p1 - dy_line * dx_p1
            if cross_product > 0: return "out"
            else: return "in"
        return None