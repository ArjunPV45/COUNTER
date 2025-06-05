"""
Zone visitor counter module containing the core counting logic.
Handles zone management, person tracking, and count updates.
"""

import json
import datetime
from typing import Dict, Set, List, Tuple, Any
from hailo_apps_infra.hailo_rpi_common import app_callback_class
from config import HISTORY_FILE, DEFAULT_ZONE_CONFIG


class MultiSourceZoneVisitorCounter(app_callback_class):
    """
    Main class for handling multi-source zone visitor counting.
    Manages zones across multiple cameras and tracks person movements.
    """
    
    def __init__(self):
        super().__init__()
        print("[DEBUG] Initializing MultisourceZoneVisitorCounter")
        self.frame_height = 1080
        self.frame_width = 1920
        self.data = self.load_data()
        self.inside_zones = {}
        self.person_zone_history = {}
        self.active_camera = list(self.data.keys())[0] if self.data else "camera1"
        
        # Initialize inside_zones tracking for each camera and zone
        for camera_id in self.data:
            self.inside_zones[camera_id] = {}
            self.person_zone_history[camera_id] = {}
            for zone in self.data[camera_id]["zones"]:
                self.inside_zones[camera_id][zone] = set()
                self.person_zone_history[camera_id][zone] = {}

    def load_data(self) -> Dict[str, Any]:
        """Load camera zones and counts from file (if exists), else initialize."""
        try:
            with open(HISTORY_FILE, "r") as file:
                return json.load(file)
        except FileNotFoundError:
            # Default configuration with multiple cameras
            return {
                "camera1": {
                    "zones": DEFAULT_ZONE_CONFIG.copy()
                }
            }

    def save_data(self) -> None:
        """Save all camera zones and their data persistently."""
        with open(HISTORY_FILE, "w") as file:
            json.dump(self.data, file, indent=4)

    def is_inside_zone(self, x: float, y: float, top_left: List[int], bottom_right: List[int]) -> bool:
        """Check if a point (x, y) is inside the defined zone."""
        return top_left[0] <= x <= bottom_right[0] and top_left[1] <= y <= bottom_right[1]

    def update_counts(self, camera_id: str, detected_people: Set[Tuple[int, float, float]]) -> None:
        """Update visitor count for each zone in a specific camera."""
        if camera_id not in self.data:
            # Initialize data for new cameras
            self.data[camera_id] = {"zones": DEFAULT_ZONE_CONFIG.copy()}
            self.inside_zones[camera_id] = {}
            self.person_zone_history[camera_id] = {}

        for zone, zone_data in self.data[camera_id]["zones"].items():
            # Initialize tracking structures if needed
            if zone not in self.inside_zones[camera_id]:
                self.inside_zones[camera_id][zone] = set()
            if zone not in self.person_zone_history[camera_id]:
                self.person_zone_history[camera_id][zone] = {}    
            
            top_left = zone_data["top_left"]
            bottom_right = zone_data["bottom_right"]

            # Determine which people are inside the zone
            inside_zone_people = set()
            for p_id, x, y in detected_people:
                if self.is_inside_zone(x, y, top_left, bottom_right):
                    inside_zone_people.add(p_id)
            
            # Get current zone occupancy
            current_zone_occupancy = self.inside_zones[camera_id][zone]
            
            # Compute precise entries and exits
            newly_entered = inside_zone_people - current_zone_occupancy
            newly_exited = current_zone_occupancy - inside_zone_people

            # Timestamp for logging
            timestamp = datetime.datetime.now()
            timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")

            # Process newly entered people
            if newly_entered:
                real_new_entries = self._process_entries(
                    camera_id, zone, newly_entered, timestamp, timestamp_str
                )

            # Process newly exited people
            if newly_exited:
                real_new_exits = self._process_exits(
                    camera_id, zone, newly_exited, timestamp, timestamp_str
                )

            # Update current zone occupancy
            self.inside_zones[camera_id][zone] = inside_zone_people
            self.data[camera_id]["zones"][zone]["inside_ids"] = list(inside_zone_people)
                
        # Save data after processing all zones
        self.save_data()

    def _process_entries(self, camera_id: str, zone: str, newly_entered: Set[int], 
                        timestamp: datetime.datetime, timestamp_str: str) -> Set[int]:
        """Process newly entered people and update counts."""
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
        """Process newly exited people and update counts."""
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

    def reset_zone_counts(self, camera_id: str, zone: str) -> bool:
        """Reset counts for a specific zone in a specific camera."""
        if camera_id not in self.data or zone not in self.data[camera_id]["zones"]:
            return False
            
        # Comprehensive reset of zone data
        zone_data = self.data[camera_id]["zones"][zone]
        zone_data["in_count"] = 0
        zone_data["out_count"] = 0
        zone_data["inside_ids"] = []
        
        # Reset tracking structures
        if camera_id in self.inside_zones and zone in self.inside_zones[camera_id]:
            self.inside_zones[camera_id][zone] = set()
        
        if camera_id in self.person_zone_history and zone in self.person_zone_history[camera_id]:
            self.person_zone_history[camera_id][zone] = {}
                
        self.save_data()
        return True

    def delete_zone(self, camera_id: str, zone: str) -> bool:
        """Delete a zone from a specific camera."""
        if camera_id not in self.data or zone not in self.data[camera_id]["zones"]:
            return False
            
        # Remove zone data
        del self.data[camera_id]["zones"][zone]
        
        # Remove from inside_zones tracking
        if camera_id in self.inside_zones and zone in self.inside_zones[camera_id]:
            del self.inside_zones[camera_id][zone]
            
        self.save_data()
        return True

    def set_active_camera(self, camera_id: str) -> bool:
        """Set the active camera for UI display."""
        if camera_id in self.data:
            self.active_camera = camera_id
            return True
        return False

    def create_or_update_zone(self, camera_id: str, zone: str, top_left: List[int], 
                             bottom_right: List[int]) -> bool:
        """Create or update a zone for a specific camera."""
        try:
            # Validate coordinates
            top_left = [int(x) for x in top_left]
            bottom_right = [int(x) for x in bottom_right]
            
            # Ensure top_left is actually top-left and bottom_right is bottom-right
            if (top_left[0] >= bottom_right[0] or top_left[1] >= bottom_right[1]):
                return False
        except (ValueError, TypeError):
            return False
        
        # Create or update zone
        if camera_id not in self.data:
            self.data[camera_id] = {"zones": {}}
            self.inside_zones[camera_id] = {}
        
        self.data[camera_id]["zones"][zone] = {
            "top_left": top_left,
            "bottom_right": bottom_right,
            "in_count": 0,
            "out_count": 0,
            "inside_ids": [],
            "history": []
        }
        self.inside_zones[camera_id][zone] = set()
        
        self.save_data()
        return True
