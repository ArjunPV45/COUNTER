"""
Zone visitor counter module containing the core counting logic.
Handles zone management, person tracking, and count updates with enhanced error handling.
Only counts visitors who dwell in zones for minimum duration.
"""

import json
import datetime
from typing import Dict, Set, List, Tuple, Any, Optional
from hailo_apps_infra.hailo_rpi_common import app_callback_class
from config import HISTORY_FILE, DEFAULT_ZONE_CONFIG


class MultiSourceZoneVisitorCounter(app_callback_class):
    """
    Main class for handling multi-source zone visitor counting.
    Manages zones across multiple cameras and tracks person movements with enhanced stability.
    Only counts people who stay in zones for minimum dwell time.
    """
    
    def __init__(self):
        super().__init__()
        print("[DEBUG] Initializing MultisourceZoneVisitorCounter")
        self.frame_height = 1080
        self.frame_width = 1920
        self.data = self.load_data()
        self.inside_zones = {}
        self.person_zone_history = {}
        self.person_state_buffer = {}  # Buffer for stable state tracking
        self.person_dwell_tracker = {}  # Track how long person has been in zone
        self.active_camera = list(self.data.keys())[0] if self.data else "camera1"
        
        # Configuration for enhanced error handling and dwell time
        self.zone_padding = 10  # pixels buffer inside zone boundaries
        self.min_dwell_frames = 3  # minimum frames for stable state
        self.bbox_overlap_threshold = 0.3  # minimum overlap percentage for bbox method
        self.min_dwell_time = 10.0  # minimum seconds in zone before counting (10 seconds)
        self.exit_grace_time = 10.0  # seconds to wait before confirming exit
        
        # Initialize tracking structures for each camera and zone
        for camera_id in self.data:
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
        except json.JSONDecodeError as e:
            print(f"[ERROR] Invalid JSON in history file: {e}")
            return {
                "camera1": {
                    "zones": DEFAULT_ZONE_CONFIG.copy()
                }
            }

    def save_data(self) -> None:
        """Save all camera zones and their data persistently."""
        try:
            with open(HISTORY_FILE, "w") as file:
                json.dump(self.data, file, indent=4)
        except Exception as e:
            print(f"[ERROR] Failed to save data: {e}")

    def get_bottom_center_point(self, bbox: Tuple[float, float, float, float]) -> Tuple[float, float]:
        """
        Calculate bottom center point of bounding box (feet position).
        Args:
            bbox: (x1, y1, x2, y2) bounding box coordinates
        Returns:
            (x, y) bottom center coordinates
        """
        try:
            x1, y1, x2, y2 = bbox
            bottom_center_x = (x1 + x2) / 2
            bottom_center_y = y2  # Bottom edge
            return bottom_center_x, bottom_center_y
        except (ValueError, TypeError) as e:
            print(f"[ERROR] Invalid bbox format: {bbox}, error: {e}")
            return 0.0, 0.0

    def calculate_bbox_overlap(self, bbox: Tuple[float, float, float, float], 
                              zone_coords: Tuple[List[int], List[int]]) -> float:
        """
        Calculate percentage of bounding box that overlaps with zone.
        Args:
            bbox: (x1, y1, x2, y2) person bounding box
            zone_coords: (top_left, bottom_right) zone coordinates
        Returns:
            Overlap percentage (0.0 to 1.0)
        """
        try:
            x1, y1, x2, y2 = bbox
            top_left, bottom_right = zone_coords
            zone_x1, zone_y1 = top_left
            zone_x2, zone_y2 = bottom_right
            
            # Calculate intersection rectangle
            intersect_x1 = max(x1, zone_x1)
            intersect_y1 = max(y1, zone_y1)
            intersect_x2 = min(x2, zone_x2)
            intersect_y2 = min(y2, zone_y2)
            
            # Check if there's actual intersection
            if intersect_x1 >= intersect_x2 or intersect_y1 >= intersect_y2:
                return 0.0
            
            # Calculate areas
            intersect_area = (intersect_x2 - intersect_x1) * (intersect_y2 - intersect_y1)
            bbox_area = (x2 - x1) * (y2 - y1)
            
            if bbox_area <= 0:
                return 0.0
                
            return intersect_area / bbox_area
            
        except (ValueError, TypeError, ZeroDivisionError) as e:
            print(f"[ERROR] Bbox overlap calculation failed: {e}")
            return 0.0

    def is_inside_zone_with_padding(self, x: float, y: float, 
                                   top_left: List[int], bottom_right: List[int]) -> bool:
        """
        Check if a point is inside the zone with padding buffer.
        Args:
            x, y: Point coordinates
            top_left, bottom_right: Zone boundaries
        Returns:
            True if point is inside padded zone
        """
        try:
            # Apply padding (shrink zone inward to create buffer)
            padded_x1 = top_left[0] + self.zone_padding
            padded_y1 = top_left[1] + self.zone_padding
            padded_x2 = bottom_right[0] - self.zone_padding
            padded_y2 = bottom_right[1] - self.zone_padding
            
            # Ensure padded zone is still valid
            if padded_x1 >= padded_x2 or padded_y1 >= padded_y2:
                # If padding makes zone invalid, use original zone
                return top_left[0] <= x <= bottom_right[0] and top_left[1] <= y <= bottom_right[1]
            
            return padded_x1 <= x <= padded_x2 and padded_y1 <= y <= padded_y2
            
        except (ValueError, TypeError) as e:
            print(f"[ERROR] Zone padding calculation failed: {e}")
            return False

    def is_person_in_zone(self, person_data: Tuple, zone_coords: Tuple[List[int], List[int]], 
                         method: str = "bottom_center") -> bool:
        """
        Determine if person is in zone using specified method.
        Args:
            person_data: Person detection data
            zone_coords: Zone boundary coordinates
            method: Detection method ("bottom_center", "bbox_overlap", "center_point")
        Returns:
            True if person is considered inside the zone
        """
        try:
            if method == "bottom_center":
                # Assume person_data format: (id, x1, y1, x2, y2) or (id, center_x, center_y)
                if len(person_data) == 5:  # Full bbox data
                    p_id, x1, y1, x2, y2 = person_data
                    bottom_x, bottom_y = self.get_bottom_center_point((x1, y1, x2, y2))
                elif len(person_data) == 3:  # Center point data
                    p_id, center_x, center_y = person_data
                    # Estimate bottom point (assume person height ~150 pixels)
                    bottom_x, bottom_y = center_x, center_y + 75
                else:
                    print(f"[ERROR] Unexpected person data format: {person_data}")
                    return False
                    
                return self.is_inside_zone_with_padding(bottom_x, bottom_y, *zone_coords)
                
            elif method == "bbox_overlap":
                if len(person_data) == 5:
                    p_id, x1, y1, x2, y2 = person_data
                    overlap = self.calculate_bbox_overlap((x1, y1, x2, y2), zone_coords)
                    return overlap >= self.bbox_overlap_threshold
                else:
                    # Fallback to center point method
                    return self.is_person_in_zone(person_data, zone_coords, "center_point")
                    
            elif method == "center_point":
                if len(person_data) >= 3:
                    if len(person_data) == 5:
                        p_id, x1, y1, x2, y2 = person_data
                        center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
                    else:
                        p_id, center_x, center_y = person_data
                    return self.is_inside_zone_with_padding(center_x, center_y, *zone_coords)
                    
            return False
            
        except Exception as e:
            print(f"[ERROR] Person in zone detection failed: {e}")
            return False

    def update_person_state_buffer(self, camera_id: str, zone: str, person_id: int, 
                                  is_inside: bool) -> bool:
        """
        Update person state buffer and return True if state is stable.
        Args:
            camera_id: Camera identifier
            zone: Zone name
            person_id: Person ID
            is_inside: Current detection state
        Returns:
            True if person state is stable for minimum dwell time
        """
        try:
            # Initialize buffer if needed
            if camera_id not in self.person_state_buffer:
                self.person_state_buffer[camera_id] = {}
            if zone not in self.person_state_buffer[camera_id]:
                self.person_state_buffer[camera_id][zone] = {}
            if person_id not in self.person_state_buffer[camera_id][zone]:
                self.person_state_buffer[camera_id][zone][person_id] = {
                    'state': is_inside,
                    'count': 1,
                    'last_update': datetime.datetime.now()
                }
                return False
            
            buffer_data = self.person_state_buffer[camera_id][zone][person_id]
            current_time = datetime.datetime.now()
            
            # Check if state changed
            if buffer_data['state'] != is_inside:
                # State changed, reset counter
                buffer_data['state'] = is_inside
                buffer_data['count'] = 1
                buffer_data['last_update'] = current_time
                return False
            else:
                # State consistent, increment counter
                buffer_data['count'] += 1
                buffer_data['last_update'] = current_time
                
                # Check if state is stable
                return buffer_data['count'] >= self.min_dwell_frames
                
        except Exception as e:
            print(f"[ERROR] State buffer update failed: {e}")
            return False

    def update_dwell_tracker(self, camera_id: str, zone: str, person_id: int, 
                           is_inside: bool, current_time: datetime.datetime) -> Dict[str, Any]:
        """
        Update dwell time tracker for a person in a zone.
        Args:
            camera_id: Camera identifier
            zone: Zone name
            person_id: Person ID
            is_inside: Current detection state
            current_time: Current timestamp
        Returns:
            Dictionary with dwell tracking information
        """
        try:
            # Initialize tracker if needed
            if camera_id not in self.person_dwell_tracker:
                self.person_dwell_tracker[camera_id] = {}
            if zone not in self.person_dwell_tracker[camera_id]:
                self.person_dwell_tracker[camera_id][zone] = {}
            
            tracker = self.person_dwell_tracker[camera_id][zone]
            
            if person_id not in tracker:
                if is_inside:
                    # Person just entered zone
                    tracker[person_id] = {
                        'entry_time': current_time,
                        'last_seen': current_time,
                        'counted_entry': False,
                        'exit_time': None,
                        'state': 'inside'
                    }
                    return {'action': 'entered', 'dwell_time': 0.0, 'should_count': False}
                else:
                    return {'action': 'none', 'dwell_time': 0.0, 'should_count': False}
            
            person_tracker = tracker[person_id]
            
            if is_inside:
                # Person is currently inside
                if person_tracker['state'] == 'exiting':
                    # Person re-entered before grace period expired
                    person_tracker['state'] = 'inside'
                    person_tracker['exit_time'] = None
                    person_tracker['last_seen'] = current_time
                    return {'action': 're_entered', 'dwell_time': 0.0, 'should_count': False}
                
                # Update last seen time
                person_tracker['last_seen'] = current_time
                
                # Calculate dwell time
                dwell_time = (current_time - person_tracker['entry_time']).total_seconds()
                
                # Check if person has dwelled long enough to be counted
                if not person_tracker['counted_entry'] and dwell_time >= self.min_dwell_time:
                    person_tracker['counted_entry'] = True
                    return {'action': 'qualified_entry', 'dwell_time': dwell_time, 'should_count': True}
                
                return {'action': 'dwelling', 'dwell_time': dwell_time, 'should_count': False}
            
            else:
                # Person is not currently inside
                if person_tracker['state'] == 'inside':
                    # Person just left zone
                    person_tracker['state'] = 'exiting'
                    person_tracker['exit_time'] = current_time
                    
                    dwell_time = (current_time - person_tracker['entry_time']).total_seconds()
                    should_count_exit = person_tracker['counted_entry']
                    
                    return {
                        'action': 'exiting', 
                        'dwell_time': dwell_time, 
                        'should_count': should_count_exit
                    }
                
                elif person_tracker['state'] == 'exiting':
                    # Check if grace period has expired
                    exit_duration = (current_time - person_tracker['exit_time']).total_seconds()
                    if exit_duration >= self.exit_grace_time:
                        # Confirm exit and clean up
                        dwell_time = (person_tracker['exit_time'] - person_tracker['entry_time']).total_seconds()
                        should_count_exit = person_tracker['counted_entry']
                        
                        # Remove from tracker
                        del tracker[person_id]
                        
                        return {
                            'action': 'confirmed_exit', 
                            'dwell_time': dwell_time, 
                            'should_count': should_count_exit
                        }
                
                return {'action': 'outside', 'dwell_time': 0.0, 'should_count': False}
                
        except Exception as e:
            print(f"[ERROR] Dwell tracker update failed: {e}")
            return {'action': 'error', 'dwell_time': 0.0, 'should_count': False}

    def cleanup_stale_buffers(self, camera_id: str, active_person_ids: Set[int]) -> None:
        """
        Clean up state buffers and dwell trackers for persons no longer detected.
        Args:
            camera_id: Camera identifier
            active_person_ids: Set of currently detected person IDs
        """
        try:
            if camera_id not in self.person_state_buffer:
                return
                
            current_time = datetime.datetime.now()
            stale_threshold = datetime.timedelta(seconds=30)  # Remove after 30 seconds
            
            # Clean state buffers
            for zone in list(self.person_state_buffer[camera_id].keys()):
                zone_buffer = self.person_state_buffer[camera_id][zone]
                stale_persons = []
                
                for person_id, buffer_data in zone_buffer.items():
                    time_since_update = current_time - buffer_data['last_update']
                    if person_id not in active_person_ids or time_since_update > stale_threshold:
                        stale_persons.append(person_id)
                
                # Remove stale entries
                for person_id in stale_persons:
                    del zone_buffer[person_id]
            
            # Clean dwell trackers for completely stale entries (longer threshold)
            if camera_id in self.person_dwell_tracker:
                for zone in list(self.person_dwell_tracker[camera_id].keys()):
                    zone_tracker = self.person_dwell_tracker[camera_id][zone]
                    stale_persons = []
                    
                    for person_id, tracker_data in zone_tracker.items():
                        # For exiting persons, use exit_time, otherwise use last_seen
                        last_activity = tracker_data.get('exit_time', tracker_data.get('last_seen'))
                        if last_activity:
                            time_since_activity = current_time - last_activity
                            if time_since_activity > datetime.timedelta(minutes=2):  # 2 minutes for cleanup
                                stale_persons.append(person_id)
                    
                    # Remove stale entries
                    for person_id in stale_persons:
                        del zone_tracker[person_id]
                        print(f"[DEBUG] Cleaned up stale dwell tracker for person {person_id} in {zone}")
                    
        except Exception as e:
            print(f"[ERROR] Buffer cleanup failed: {e}")

    def update_counts(self, camera_id: str, detected_people: Set[Tuple]) -> None:
        """
        Update visitor count for each zone in a specific camera with dwell time requirements.
        Args:
            camera_id: Camera identifier
            detected_people: Set of detected person data tuples
        """
        try:
            if camera_id not in self.data:
                # Initialize data for new cameras
                self.data[camera_id] = {"zones": DEFAULT_ZONE_CONFIG.copy()}
                self.inside_zones[camera_id] = {}
                self.person_zone_history[camera_id] = {}
                self.person_state_buffer[camera_id] = {}
                self.person_dwell_tracker[camera_id] = {}

            # Extract person IDs for buffer cleanup
            active_person_ids = set()
            for person_data in detected_people:
                if len(person_data) >= 1:
                    active_person_ids.add(person_data[0])

            # Clean up stale buffers
            self.cleanup_stale_buffers(camera_id, active_person_ids)
            
            current_time = datetime.datetime.now()

            for zone, zone_data in self.data[camera_id]["zones"].items():
                # Initialize tracking structures if needed
                if zone not in self.inside_zones[camera_id]:
                    self.inside_zones[camera_id][zone] = set()
                if zone not in self.person_zone_history[camera_id]:
                    self.person_zone_history[camera_id][zone] = {}
                if zone not in self.person_state_buffer[camera_id]:
                    self.person_state_buffer[camera_id][zone] = {}
                if zone not in self.person_dwell_tracker[camera_id]:
                    self.person_dwell_tracker[camera_id][zone] = {}
                
                top_left = zone_data["top_left"]
                bottom_right = zone_data["bottom_right"]
                zone_coords = (top_left, bottom_right)

                # Process each detected person
                current_inside_people = set()
                entries_to_count = []
                exits_to_count = []
                
                for person_data in detected_people:
                    try:
                        person_id = person_data[0]
                        is_inside = self.is_person_in_zone(person_data, zone_coords, "bottom_center")
                        
                        # Check if state is stable
                        if self.update_person_state_buffer(camera_id, zone, person_id, is_inside):
                            if is_inside:
                                current_inside_people.add(person_id)
                            
                            # Update dwell tracker
                            dwell_info = self.update_dwell_tracker(camera_id, zone, person_id, is_inside, current_time)
                            
                            # Process dwell tracking results
                            if dwell_info['should_count']:
                                if dwell_info['action'] == 'qualified_entry':
                                    entries_to_count.append(person_id)
                                    #print(f"[INFO] Person {person_id} qualified for entry count in zone {zone} after {dwell_info['dwell_time']:.1f}s")
                                elif dwell_info['action'] == 'confirmed_exit':
                                    exits_to_count.append(person_id)
                                    #print(f"[INFO] Person {person_id} confirmed exit from zone {zone} after {dwell_info['dwell_time']:.1f}s total dwell")
                                
                    except Exception as e:
                        #'''print(f"[ERROR] Processing person {person_data}: {e}")'''
                        continue

                # Also check for people who have completely left (not in detected_people)
                # This handles cases where person tracking is lost
                if camera_id in self.person_dwell_tracker and zone in self.person_dwell_tracker[camera_id]:
                    for person_id in list(self.person_dwell_tracker[camera_id][zone].keys()):
                        if person_id not in active_person_ids:
                            # Person is no longer detected
                            dwell_info = self.update_dwell_tracker(camera_id, zone, person_id, False, current_time)
                            if dwell_info['should_count'] and dwell_info['action'] == 'confirmed_exit':
                                exits_to_count.append(person_id)
                                #print(f"[INFO] Lost person {person_id} confirmed exit from zone {zone}")

                # Update counts based on dwell time qualifications
                timestamp_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
                
                if entries_to_count:
                    self.data[camera_id]["zones"][zone]["in_count"] += len(entries_to_count)
                    for p_id in entries_to_count:
                        self.data[camera_id]["zones"][zone]["history"].append({
                            "id": p_id, 
                            "action": "Entered (Qualified)", 
                            "time": timestamp_str
                        })
                    #print(f"[INFO] {len(entries_to_count)} people qualified and counted as entered zone {zone}")
                
                if exits_to_count:
                    self.data[camera_id]["zones"][zone]["out_count"] += len(exits_to_count)
                    for p_id in exits_to_count:
                        self.data[camera_id]["zones"][zone]["history"].append({
                            "id": p_id, 
                            "action": "Exited (Qualified)", 
                            "time": timestamp_str
                        })
                    #print(f"[INFO] {len(exits_to_count)} people confirmed and counted as exited zone {zone}")

                # Update current zone occupancy (for display purposes)
                self.inside_zones[camera_id][zone] = current_inside_people
                self.data[camera_id]["zones"][zone]["inside_ids"] = list(current_inside_people)
                    
            # Save data after processing all zones
            if entries_to_count or exits_to_count:
                self.save_data()
            
        except Exception as e:
            print(f"[ERROR] Update counts failed for camera {camera_id}: {e}")

    def reset_zone_counts(self, camera_id: str, zone: str) -> bool:
        """Reset counts for a specific zone in a specific camera."""
        try:
            if camera_id not in self.data or zone not in self.data[camera_id]["zones"]:
                print(f"[WARNING] Zone {zone} in camera {camera_id} not found for reset")
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
                
            if camera_id in self.person_state_buffer and zone in self.person_state_buffer[camera_id]:
                self.person_state_buffer[camera_id][zone] = {}
                
            if camera_id in self.person_dwell_tracker and zone in self.person_dwell_tracker[camera_id]:
                self.person_dwell_tracker[camera_id][zone] = {}
                    
            self.save_data()
            print(f"[INFO] Reset zone {zone} in camera {camera_id}")
            return True
            
        except Exception as e:
            print(f"[ERROR] Reset zone failed: {e}")
            return False

    def delete_zone(self, camera_id: str, zone: str) -> bool:
        """Delete a zone from a specific camera."""
        try:
            if camera_id not in self.data or zone not in self.data[camera_id]["zones"]:
                print(f"[WARNING] Zone {zone} in camera {camera_id} not found for deletion")
                return False
                
            # Remove zone data
            del self.data[camera_id]["zones"][zone]
            
            # Remove from tracking structures
            if camera_id in self.inside_zones and zone in self.inside_zones[camera_id]:
                del self.inside_zones[camera_id][zone]
                
            if camera_id in self.person_zone_history and zone in self.person_zone_history[camera_id]:
                del self.person_zone_history[camera_id][zone]
                
            if camera_id in self.person_state_buffer and zone in self.person_state_buffer[camera_id]:
                del self.person_state_buffer[camera_id][zone]
                
            if camera_id in self.person_dwell_tracker and zone in self.person_dwell_tracker[camera_id]:
                del self.person_dwell_tracker[camera_id][zone]
                
            self.save_data()
            print(f"[INFO] Deleted zone {zone} from camera {camera_id}")
            return True
            
        except Exception as e:
            print(f"[ERROR] Delete zone failed: {e}")
            return False

    def set_active_camera(self, camera_id: str) -> bool:
        """Set the active camera for UI display."""
        try:
            if camera_id in self.data:
                self.active_camera = camera_id
                print(f"[INFO] Set active camera to {camera_id}")
                return True
            print(f"[WARNING] Camera {camera_id} not found")
            return False
        except Exception as e:
            print(f"[ERROR] Set active camera failed: {e}")
            return False

    def create_or_update_zone(self, camera_id: str, zone: str, top_left: List[int], 
                             bottom_right: List[int]) -> bool:
        """Create or update a zone for a specific camera with enhanced validation."""
        try:
            # Validate and convert coordinates
            try:
                top_left = [int(x) for x in top_left]
                bottom_right = [int(x) for x in bottom_right]
            except (ValueError, TypeError) as e:
                print(f"[ERROR] Invalid coordinate format: {e}")
                return False
            
            # Validate coordinate logic
            if (top_left[0] >= bottom_right[0] or top_left[1] >= bottom_right[1]):
                print(f"[ERROR] Invalid zone coordinates: top_left={top_left}, bottom_right={bottom_right}")
                return False
            
            # Validate coordinates are within frame bounds
            if (top_left[0] < 0 or top_left[1] < 0 or 
                bottom_right[0] > self.frame_width or bottom_right[1] > self.frame_height):
                print(f"[WARNING] Zone coordinates outside frame bounds")
            
            # Create camera data if it doesn't exist
            if camera_id not in self.data:
                self.data[camera_id] = {"zones": {}}
                self.inside_zones[camera_id] = {}
                self.person_zone_history[camera_id] = {}
                self.person_state_buffer[camera_id] = {}
                self.person_dwell_tracker[camera_id] = {}
            
            # Create or update zone
            self.data[camera_id]["zones"][zone] = {
                "top_left": top_left,
                "bottom_right": bottom_right,
                "in_count": 0,
                "out_count": 0,
                "inside_ids": [],
                "history": []
            }
            
            # Initialize tracking structures
            self.inside_zones[camera_id][zone] = set()
            self.person_zone_history[camera_id][zone] = {}
            self.person_state_buffer[camera_id][zone] = {}
            self.person_dwell_tracker[camera_id][zone] = {}
            
            self.save_data()
            print(f"[INFO] Created/updated zone {zone} in camera {camera_id}")
            return True
            
        except Exception as e:
            print(f"[ERROR] Create/update zone failed: {e}")
            return False

    def get_zone_statistics(self, camera_id: str, zone: str) -> Optional[Dict[str, Any]]:
        """Get comprehensive statistics for a specific zone."""
        try:
            if camera_id not in self.data or zone not in self.data[camera_id]["zones"]:
                return None
                
            zone_data = self.data[camera_id]["zones"][zone]
            current_occupancy = len(self.inside_zones.get(camera_id, {}).get(zone, set()))
            
            # Get dwell time statistics
            dwell_stats = self.get_dwell_statistics(camera_id, zone)
            
            return {
                "in_count": zone_data["in_count"],
                "out_count": zone_data["out_count"],
                "current_occupancy": current_occupancy,
                "net_count": zone_data["in_count"] - zone_data["out_count"],
                "inside_ids": zone_data["inside_ids"],
                "total_events": len(zone_data["history"]),
                "zone_coordinates": {
                    "top_left": zone_data["top_left"],
                    "bottom_right": zone_data["bottom_right"]
                },
                "dwell_statistics": dwell_stats
            }
            
        except Exception as e:
            #print(f"[ERROR] Get zone statistics failed: {e}")
            return None

    def get_dwell_statistics(self, camera_id: str, zone: str) -> Dict[str, Any]:
        """Get dwell time statistics for people currently in zone."""
        try:
            if (camera_id not in self.person_dwell_tracker or 
                zone not in self.person_dwell_tracker[camera_id]):
                return {
                    "active_dwellers": 0,
                    "average_dwell_time": 0.0,
                    "max_dwell_time": 0.0,
                    "qualified_count": 0
                }
            
            current_time = datetime.datetime.now()
            zone_tracker = self.person_dwell_tracker[camera_id][zone]
            
            active_dwellers = 0
            total_dwell_time = 0.0
            max_dwell_time = 0.0
            qualified_count = 0
            
            for person_id, tracker_data in zone_tracker.items():
                if tracker_data['state'] == 'inside':
                    active_dwellers += 1
                    dwell_time = (current_time - tracker_data['entry_time']).total_seconds()
                    total_dwell_time += dwell_time
                    max_dwell_time = max(max_dwell_time, dwell_time)
                    
                    if tracker_data['counted_entry']:
                        qualified_count += 1
            
            avg_dwell_time = total_dwell_time / active_dwellers if active_dwellers > 0 else 0.0
            
            return {
                "active_dwellers": active_dwellers,
                "average_dwell_time": avg_dwell_time,
                "max_dwell_time": max_dwell_time,
                "qualified_count": qualified_count
            }
            
        except Exception as e:
            print(f"[ERROR] Get dwell statistics failed: {e}")
            return {
                "active_dwellers": 0,
                "average_dwell_time": 0.0,
                "max_dwell_time": 0.0,
                "qualified_count": 0
            }

    def set_dwell_time_threshold(self, min_dwell_seconds: float) -> bool:
        """
        Set the minimum dwell time required for counting.
        Args:
            min_dwell_seconds: Minimum seconds a person must stay in zone to be counted
        Returns:
            True if successfully set
        """
        try:
            if min_dwell_seconds < 0:
                print(f"[ERROR] Invalid dwell time: {min_dwell_seconds}")
                return False
                
            self.min_dwell_time = min_dwell_seconds
            print(f"[INFO] Set minimum dwell time to {min_dwell_seconds} seconds")
            return True
            
        except Exception as e:
            print(f"[ERROR] Set dwell time failed: {e}")
            return False

    def get_current_dwell_info(self, camera_id: str, zone: str) -> List[Dict[str, Any]]:
        """
        Get current dwell information for all people in a zone.
        Args:
            camera_id: Camera identifier
            zone: Zone name
        Returns:
            List of dwell information for each person
        """
        try:
            if (camera_id not in self.person_dwell_tracker or 
                zone not in self.person_dwell_tracker[camera_id]):
                return []
            
            current_time = datetime.datetime.now()
            zone_tracker = self.person_dwell_tracker[camera_id][zone]
            dwell_info = []
            
            for person_id, tracker_data in zone_tracker.items():
                if tracker_data['state'] in ['inside', 'exiting']:
                    dwell_time = (current_time - tracker_data['entry_time']).total_seconds()
                    
                    info = {
                        "person_id": person_id,
                        "state": tracker_data['state'],
                        "dwell_time": dwell_time,
                        "entry_time": tracker_data['entry_time'].strftime("%Y-%m-%d %H:%M:%S"),
                        "counted": tracker_data['counted_entry'],
                        "qualified": dwell_time >= self.min_dwell_time,
                        "time_to_qualify": max(0, self.min_dwell_time - dwell_time)
                    }
                    
                    if tracker_data['state'] == 'exiting' and tracker_data['exit_time']:
                        info['exit_time'] = tracker_data['exit_time'].strftime("%Y-%m-%d %H:%M:%S")
                        exit_duration = (current_time - tracker_data['exit_time']).total_seconds()
                        info['exit_duration'] = exit_duration
                        info['time_to_confirm_exit'] = max(0, self.exit_grace_time - exit_duration)
                    
                    dwell_info.append(info)
            
            # Sort by dwell time descending
            dwell_info.sort(key=lambda x: x['dwell_time'], reverse=True)
            return dwell_info
            
        except Exception as e:
            print(f"[ERROR] Get current dwell info failed: {e}")
            return []
