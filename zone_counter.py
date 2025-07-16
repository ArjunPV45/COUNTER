"""
Enhanced Multi-Camera Zone Visitor Counter
- Uses raw person IDs without camera prefixes
- Maintains complete camera isolation through separate tracking structures
- Includes dwell time requirements and state stability checks
- Fixed camera switching and snapshot functionality
"""

import json
import datetime
from typing import Dict, Set, List, Tuple, Any, Optional
from hailo_apps_infra.hailo_rpi_common import app_callback_class
from config import HISTORY_FILE, DEFAULT_ZONE_CONFIG


class MultiSourceZoneVisitorCounter(app_callback_class):
    def __init__(self):
        super().__init__()
        print("[INFO] Initializing MultiSourceZoneVisitorCounter")
        self.frame_height = 1080
        self.frame_width = 1920
        
        # Tracking structures - all camera-specific
        self.data = self.load_data()
        self.inside_zones = {}          # {camera_id: {zone: set(person_ids)}}
        self.person_zone_history = {}   # {camera_id: {zone: {person_id: history}}}
        self.person_state_buffer = {}   # {camera_id: {zone: {person_id: state_data}}}
        self.person_dwell_tracker = {}  # {camera_id: {zone: {person_id: dwell_data}}}
        
        # Configuration
        self.zone_padding = 30          # pixels buffer inside zone boundaries
        self.min_dwell_frames = 3       # frames for stable state
        self.bbox_overlap_threshold = 0.3
        self.min_dwell_time = 1.0      # seconds required in zone before counting
        self.exit_grace_time = 1.0     # seconds to wait before confirming exit
        
        # Initialize structures for existing cameras
        for camera_id in self.data:
            self._init_camera(camera_id)
        
        self.active_camera = list(self.data.keys())[0] if self.data else "camera1"

    def _init_camera(self, camera_id: str) -> None:
        """Initialize all tracking structures for a camera."""
        self.inside_zones[camera_id] = {}
        self.person_zone_history[camera_id] = {}
        self.person_state_buffer[camera_id] = {}
        self.person_dwell_tracker[camera_id] = {}
        
        for zone in self.data[camera_id]["zones"]:
            self.inside_zones[camera_id][zone] = set()
            self.person_zone_history[camera_id][zone] = {}
            self.person_state_buffer[camera_id][zone] = {}
            self.person_dwell_tracker[camera_id][zone] = {}

    def load_data(self) -> Dict[str, Any]:
        """Load zone configurations from file or initialize defaults."""
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"camera1": {"zones": DEFAULT_ZONE_CONFIG.copy()}}

    def save_data(self) -> None:
        """Persist zone configurations and counts."""
        try:
            with open(HISTORY_FILE, "w") as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            print(f"[ERROR] Failed to save data: {e}")

    def is_inside_zone(self, x: float, y: float, top_left: List[int], bottom_right: List[int]) -> bool:
        """Check if a point (x, y) is inside the defined zone - for compatibility."""
        return top_left[0] <= x <= bottom_right[0] and top_left[1] <= y <= bottom_right[1]

    def _is_in_zone(self, point: Tuple[float, float], zone_coords: Tuple[List[int], List[int]]) -> bool:
        """Check if point is inside zone with padding."""
        x, y = point
        (x1, y1), (x2, y2) = zone_coords
        
        # Apply padding (shrinking zone inward)
        px1 = x1 + self.zone_padding
        py1 = y1 + self.zone_padding
        px2 = x2 - self.zone_padding
        py2 = y2 - self.zone_padding
        
        # Fallback to original zone if padding makes it invalid
        if px1 >= px2 or py1 >= py2:
            return x1 <= x <= x2 and y1 <= y <= y2
        return px1 <= x <= px2 and py1 <= y <= py2

    def _get_person_position(self, person_data: Tuple, method: str = "bottom_center") -> Tuple[float, float]:
        """Extract position from person data based on detection method."""
        if len(person_data) == 5:  # (id, x1, y1, x2, y2)
            _, x1, y1, x2, y2 = person_data
            if method == "bottom_center":
                return (x1 + x2) / 2, y2
            elif method == "center":
                return (x1 + x2) / 2, (y1 + y2) / 2
        elif len(person_data) == 3:  # (id, x, y)
            _, x, y = person_data
            return x, y
        return 0.0, 0.0

    def update_counts(self, camera_id: str, detected_people: Set[Tuple]) -> None:
        """Main update method for processing detections and updating counts."""
        try:
            # Initialize camera if new
            if camera_id not in self.data:
                self.data[camera_id] = {"zones": DEFAULT_ZONE_CONFIG.copy()}
                self._init_camera(camera_id)
            
            active_ids = {p[0] for p in detected_people if len(p) >= 1}
            current_time = datetime.datetime.now()
            
            # Process each zone for this camera
            for zone, zone_data in self.data[camera_id]["zones"].items():
                zone_coords = (zone_data["top_left"], zone_data["bottom_right"])
                current_inside = set()
                entries_to_count = []
                exits_to_count = []
                
                # Check each person against this zone
                for person_data in detected_people:
                    if len(person_data) < 1:
                        continue
                        
                    person_id = person_data[0]
                    position = self._get_person_position(person_data)
                    is_inside = self._is_in_zone(position, zone_coords)
                    
                    # Update state buffer and check stability
                    if self._update_state_buffer(camera_id, zone, person_id, is_inside):
                        if is_inside:
                            current_inside.add(person_id)
                        
                        # Update dwell tracking
                        dwell_result = self._update_dwell_tracker(
                            camera_id, zone, person_id, is_inside, current_time
                        )
                        
                        if dwell_result['should_count']:
                            if dwell_result['action'] == 'qualified_entry':
                                entries_to_count.append(person_id)
                            elif dwell_result['action'] == 'confirmed_exit':
                                exits_to_count.append(person_id)
                
                # Check for people who left the frame entirely
                for person_id in list(self.person_dwell_tracker[camera_id][zone].keys()):
                    if person_id not in active_ids:
                        dwell_result = self._update_dwell_tracker(
                            camera_id, zone, person_id, False, current_time
                        )
                        if dwell_result['should_count'] and dwell_result['action'] == 'confirmed_exit':
                            exits_to_count.append(person_id)
                
                # Apply count updates
                timestamp = current_time.strftime("%Y-%m-%d %H:%M:%S")
                if entries_to_count:
                    zone_data["in_count"] += len(entries_to_count)
                    for pid in entries_to_count:
                        zone_data["history"].append({
                            "id": pid, "action": "Entered", "time": timestamp
                        })
                
                if exits_to_count:
                    zone_data["out_count"] += len(exits_to_count)
                    for pid in exits_to_count:
                        zone_data["history"].append({
                            "id": pid, "action": "Exited", "time": timestamp
                        })
                
                # Update current occupancy
                self.inside_zones[camera_id][zone] = current_inside
                zone_data["inside_ids"] = list(current_inside)
            
            self.save_data()
            
        except Exception as e:
            print(f"[ERROR] Failed to update counts for {camera_id}: {e}")

    def _update_state_buffer(self, camera_id: str, zone: str, 
                           person_id: int, is_inside: bool) -> bool:
        """Update state buffer and return True if state is stable."""
        buffer = self.person_state_buffer[camera_id][zone]
        
        if person_id not in buffer:
            buffer[person_id] = {
                'state': is_inside,
                'count': 1,
                'last_update': datetime.datetime.now()
            }
            return False
        
        data = buffer[person_id]
        if data['state'] != is_inside:
            data.update({
                'state': is_inside,
                'count': 1,
                'last_update': datetime.datetime.now()
            })
            return False
        else:
            data['count'] += 1
            data['last_update'] = datetime.datetime.now()
            return data['count'] >= self.min_dwell_frames

    def _update_dwell_tracker(self, camera_id: str, zone: str, 
                            person_id: int, is_inside: bool, 
                            current_time: datetime.datetime) -> Dict[str, Any]:
        """Update dwell tracking and return count eligibility."""
        tracker = self.person_dwell_tracker[camera_id][zone]
        
        # Initialize new entry
        if person_id not in tracker:
            if is_inside:
                tracker[person_id] = {
                    'entry_time': current_time,
                    'last_seen': current_time,
                    'counted': False,
                    'exit_time': None,
                    'state': 'inside'
                }
                return {'action': 'entered', 'dwell_time': 0.0, 'should_count': False}
            return {'action': 'none', 'dwell_time': 0.0, 'should_count': False}
        
        entry = tracker[person_id]
        
        # Handle current inside state
        if is_inside:
            if entry['state'] == 'exiting':  # Re-entered during grace period
                entry.update({
                    'state': 'inside',
                    'exit_time': None,
                    'last_seen': current_time
                })
                return {'action': 're_entered', 'dwell_time': 0.0, 'should_count': False}
            
            entry['last_seen'] = current_time
            dwell_time = (current_time - entry['entry_time']).total_seconds()
            
            if not entry['counted'] and dwell_time >= self.min_dwell_time:
                entry['counted'] = True
                return {
                    'action': 'qualified_entry',
                    'dwell_time': dwell_time,
                    'should_count': True
                }
            
            return {'action': 'dwelling', 'dwell_time': dwell_time, 'should_count': False}
        
        # Handle current outside state
        else:
            if entry['state'] == 'inside':  # Just exited
                entry.update({
                    'state': 'exiting',
                    'exit_time': current_time
                })
                dwell_time = (current_time - entry['entry_time']).total_seconds()
                return {
                    'action': 'exiting',
                    'dwell_time': dwell_time,
                    'should_count': entry['counted']
                }
            
            elif entry['state'] == 'exiting':  # Check grace period
                exit_duration = (current_time - entry['exit_time']).total_seconds()
                if exit_duration >= self.exit_grace_time:
                    dwell_time = (entry['exit_time'] - entry['entry_time']).total_seconds()
                    should_count = entry['counted']
                    del tracker[person_id]
                    return {
                        'action': 'confirmed_exit',
                        'dwell_time': dwell_time,
                        'should_count': should_count
                    }
            
            return {'action': 'outside', 'dwell_time': 0.0, 'should_count': False}

    def cleanup_stale_tracks(self, camera_id: str, active_ids: Set[int]) -> None:
        """Remove stale tracks for people no longer detected."""
        current_time = datetime.datetime.now()
        
        # Clean state buffers
        for zone, buffer in self.person_state_buffer.get(camera_id, {}).items():
            stale = [
                pid for pid, data in buffer.items()
                if pid not in active_ids or
                (current_time - data['last_update']) > datetime.timedelta(seconds=30)
            ]
            for pid in stale:
                del buffer[pid]
        
        # Clean dwell trackers
        for zone, tracker in self.person_dwell_tracker.get(camera_id, {}).items():
            stale = []
            for pid, data in tracker.items():
                last_active = data.get('exit_time', data.get('last_seen'))
                if last_active and (current_time - last_active) > datetime.timedelta(minutes=2):
                    stale.append(pid)
            for pid in stale:
                del tracker[pid]

    def reset_zone_counts(self, camera_id: str, zone: str) -> bool:
        """Reset all counts and tracking for a zone."""
        try:
            if camera_id not in self.data or zone not in self.data[camera_id]["zones"]:
                return False
                
            # Comprehensive reset of zone data
            zone_data = self.data[camera_id]["zones"][zone]
            zone_data["in_count"] = 0
            zone_data["out_count"] = 0
            zone_data["inside_ids"] = []
            
            # Clear tracking structures
            if camera_id in self.inside_zones and zone in self.inside_zones[camera_id]:
                self.inside_zones[camera_id][zone] = set()
                
            if camera_id in self.person_zone_history and zone in self.person_zone_history[camera_id]:
                self.person_zone_history[camera_id][zone] = {}
            
            if zone in self.person_state_buffer.get(camera_id, {}):
                self.person_state_buffer[camera_id][zone] = {}
            if zone in self.person_dwell_tracker.get(camera_id, {}):
                self.person_dwell_tracker[camera_id][zone] = {}
            
            self.save_data()
            return True
        except Exception as e:
            print(f"[ERROR] Failed to reset zone {zone}: {e}")
            return False

    def delete_zone(self, camera_id: str, zone: str) -> bool:
        """Delete a zone from a specific camera."""
        try:
            if camera_id not in self.data or zone not in self.data[camera_id]["zones"]:
                return False
                
            # Remove zone data
            del self.data[camera_id]["zones"][zone]
            
            # Remove from all tracking structures
            if camera_id in self.inside_zones and zone in self.inside_zones[camera_id]:
                del self.inside_zones[camera_id][zone]
                
            if camera_id in self.person_zone_history and zone in self.person_zone_history[camera_id]:
                del self.person_zone_history[camera_id][zone]
                
            if camera_id in self.person_state_buffer and zone in self.person_state_buffer[camera_id]:
                del self.person_state_buffer[camera_id][zone]
                
            if camera_id in self.person_dwell_tracker and zone in self.person_dwell_tracker[camera_id]:
                del self.person_dwell_tracker[camera_id][zone]
                
            self.save_data()
            return True
        except Exception as e:
            print(f"[ERROR] Failed to delete zone {zone}: {e}")
            return False

    def set_active_camera(self, camera_id: str) -> bool:
        """Set the active camera for UI display."""
        if camera_id in self.data:
            self.active_camera = camera_id
            print(f"[INFO] Active camera set to: {camera_id}")
            return True
        print(f"[WARNING] Camera {camera_id} not found in data")
        return False

    def create_or_update_zone(self, camera_id: str, zone: str,
                            top_left: List[int], bottom_right: List[int]) -> bool:
        """Create or update a zone configuration."""
        try:
            # Validate coordinates
            x1, y1 = map(int, top_left)
            x2, y2 = map(int, bottom_right)
            if x1 >= x2 or y1 >= y2:
                print(f"[ERROR] Invalid coordinates for zone {zone}: top_left must be less than bottom_right")
                return False
                
            # Initialize camera if new
            if camera_id not in self.data:
                self.data[camera_id] = {"zones": {}}
                self._init_camera(camera_id)
                print(f"[INFO] Initialized new camera: {camera_id}")
            
            # Create/update zone
            self.data[camera_id]["zones"][zone] = {
                "top_left": [x1, y1],
                "bottom_right": [x2, y2],
                "in_count": 0,
                "out_count": 0,
                "inside_ids": [],
                "history": []
            }
            
            # Initialize tracking structures if they don't exist
            if camera_id not in self.inside_zones:
                self.inside_zones[camera_id] = {}
            if camera_id not in self.person_zone_history:
                self.person_zone_history[camera_id] = {}
            if camera_id not in self.person_state_buffer:
                self.person_state_buffer[camera_id] = {}
            if camera_id not in self.person_dwell_tracker:
                self.person_dwell_tracker[camera_id] = {}
            
            # Initialize zone-specific tracking
            self.inside_zones[camera_id][zone] = set()
            self.person_zone_history[camera_id][zone] = {}
            self.person_state_buffer[camera_id][zone] = {}
            self.person_dwell_tracker[camera_id][zone] = {}
            
            self.save_data()
            print(f"[INFO] Created/updated zone '{zone}' for camera '{camera_id}'")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to create/update zone {zone}: {e}")
            return False

    def get_zone_stats(self, camera_id: str, zone: str) -> Optional[Dict[str, Any]]:
        """Get current statistics for a zone."""
        try:
            if camera_id not in self.data or zone not in self.data[camera_id]["zones"]:
                return None
                
            zone_data = self.data[camera_id]["zones"][zone]
            current_inside = self.inside_zones.get(camera_id, {}).get(zone, set())
            
            # Calculate dwell statistics
            dwell_stats = {"active": 0, "avg_dwell": 0.0, "max_dwell": 0.0, "qualified": 0}
            if zone in self.person_dwell_tracker.get(camera_id, {}):
                now = datetime.datetime.now()
                dwell_times = []
                
                for pid, data in self.person_dwell_tracker[camera_id][zone].items():
                    if data['state'] == 'inside':
                        dwell_time = (now - data['entry_time']).total_seconds()
                        dwell_times.append(dwell_time)
                        dwell_stats["active"] += 1
                        if data['counted']:
                            dwell_stats["qualified"] += 1
                
                if dwell_times:
                    dwell_stats["avg_dwell"] = sum(dwell_times) / len(dwell_times)
                    dwell_stats["max_dwell"] = max(dwell_times)
            
            return {
                "in_count": zone_data["in_count"],
                "out_count": zone_data["out_count"],
                "current_occupancy": len(current_inside),
                "inside_ids": list(current_inside),
                "dwell_stats": dwell_stats,
                "coordinates": {
                    "top_left": zone_data["top_left"],
                    "bottom_right": zone_data["bottom_right"]
                }
            }
        except Exception as e:
            print(f"[ERROR] Failed to get stats for {zone}: {e}")
            return None

    def _process_entries(self, camera_id: str, zone: str, newly_entered: Set[int], 
                        timestamp: datetime.datetime, timestamp_str: str) -> Set[int]:
        """Process newly entered people and update counts - for compatibility."""
        real_new_entries = set()
        for p_id in newly_entered:
            # Check if this person hasn't recently been counted
            person_history = self.person_zone_history[camera_id][zone].get(p_id, {})
            if not person_history or person_history.get('last_action') != 'entered':
                real_new_entries.add(p_id)
                # Update person's zone history
                self.person_zone_history[camera_id][zone][p_id] = {
                    'last_action': 'entered',
                    'last_action_time': timestamp
                }
        
        # Update count and log only real new entries
        if real_new_entries:
            self.data[camera_id]["zones"][zone]["in_count"] += len(real_new_entries)
            for p_id in real_new_entries:
                self.data[camera_id]["zones"][zone]["history"].append({
                    "id": p_id, 
                    "action": "Entered", 
                    "time": timestamp_str
                })
        
        return real_new_entries

    def _process_exits(self, camera_id: str, zone: str, newly_exited: Set[int], 
                      timestamp: datetime.datetime, timestamp_str: str) -> Set[int]:
        """Process newly exited people and update counts - for compatibility."""
        real_new_exits = set()
        for p_id in newly_exited:
            # Check if this person hasn't recently been counted as exited
            person_history = self.person_zone_history[camera_id][zone].get(p_id, {})
            if not person_history or person_history.get('last_action') != 'exited':
                real_new_exits.add(p_id)
                # Update person's zone history
                self.person_zone_history[camera_id][zone][p_id] = {
                    'last_action': 'exited',
                    'last_action_time': timestamp
                }
        
        # Update count and log only real new exits
        if real_new_exits:
            self.data[camera_id]["zones"][zone]["out_count"] += len(real_new_exits)
            for p_id in real_new_exits:
                self.data[camera_id]["zones"][zone]["history"].append({
                    "id": p_id, 
                    "action": "Exited", 
                    "time": timestamp_str
                })
        
        return real_new_exits
