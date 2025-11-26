import json
import datetime
import numpy as np
import time
import threading
import logging
from typing import Dict, Set, List, Tuple, Any, Optional, Union
from dataclasses import dataclass
from collections import defaultdict
from enum import Enum
from hailo_apps_infra1.hailo_rpi_common import app_callback_class
from config import save_zone_line_config
from logging_config import get_logger
from database_writer import get_database_writer


logging.basicConfig(level=logging.INFO)
logger = get_logger(__name__)


class PersonState(Enum):
    INSIDE = "inside"
    OUTSIDE = "outside"
    ENTERING = "entering"
    EXITING = "exiting"


class ActionType(Enum):
    ENTRY = "Entered"
    EXIT = "Exited"
    IN = "In"
    OUT = "Out"


@dataclass
class CounterConfig:
    frame_height: int = 1080
    frame_width: int = 1920
    zone_padding: int = 30
    min_dwell_frames: int = 3
    min_dwell_time: float = 1.0
    exit_grace_time: float = 1.0
    crossing_cooldown_seconds: float = 2.0
    min_movement_threshold: float = 5.0
    state_confirmation_frames: int = 3
    max_history_entries: int = 1000
    cleanup_interval_minutes: int = 5


class MultiSourceZoneVisitorCounter(app_callback_class):
    def __init__(self, mqtt_client=None, pi_id: str = "pi-default", config: Optional[CounterConfig] = None):
        super().__init__()
        logger.info("Initializing MultiSourceZoneVisitorCounter")
        self.config = config or CounterConfig()
        self.mqtt_client = mqtt_client
        self.pi_id = pi_id

        self.db_writer = get_database_writer(batch_size=50, batch_interval=2.0)
        self.db_enabled = False

        self.lock = threading.Lock()
        self.data: Dict[str, Dict[str, Any]] = {}

        self.inside_zones: Dict[str, Dict[str, Set[int]]] = {}
        self.person_state_buffer: Dict[str, Dict[str, Dict[int, Dict[str, Any]]]] = {}
        self.person_dwell_tracker: Dict[str, Dict[str, Dict[int, Dict[str, Any]]]] = {}
        self.line_cross_tracker: Dict[str, Dict[str, Dict[int, Dict[str, Any]]]] = {}
        self.line_cooldown_tracker: Dict[str, Dict[str, Dict[int, datetime.datetime]]] = {}

        self.person_zone_history: Dict[str, Dict[str, Dict[int, Dict[str, Any]]]] = {}

        self.active_camera: str = "camera1"

        self.last_cleanup = datetime.datetime.now()

    def _validate_coordinates(self, top_left: List[int], bottom_right: List[int]) -> bool:
        try:
            if len(top_left) != 2 or len(bottom_right) != 2:
                return False
            if top_left[0] >= bottom_right[0] or top_left[1] >= bottom_right[1]:
                return False
            if any(coord < 0 for coord in top_left + bottom_right):
                return False
            if (bottom_right[0] > self.config.frame_width or
                    bottom_right[1] > self.config.frame_height):
                return False
            return True
        except (TypeError, IndexError):
            return False

    def _validate_line_coordinates(self, start: List[int], end: List[int]) -> bool:
        try:
            if len(start) != 2 or len(end) != 2:
                return False
            if any(coord < 0 for coord in start + end):
                return False
            if (max(start[0], end[0]) > self.config.frame_width or
                    max(start[1], end[1]) > self.config.frame_height):
                return False
            distance = np.sqrt((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2)
            return distance >= 10
        except (TypeError, IndexError):
            return False

    def _validate_person_data(self, person_data: Tuple) -> bool:
        if len(person_data) == 5:
            try:
                person_id, x1, y1, x2, y2 = person_data
                return (isinstance(person_id, (int, str)) and
                        all(isinstance(coord, (int, float)) for coord in [x1, y1, x2, y2]) and
                        x1 < x2 and y1 < y2)
            except (ValueError, TypeError):
                return False
        elif len(person_data) == 3:
            try:
                person_id, x, y = person_data
                return (isinstance(person_id, (int, str)) and
                        isinstance(x, (int, float)) and isinstance(y, (int, float)))
            except (ValueError, TypeError):
                return False
        return False

    def _init_camera(self, camera_id: str) -> None:
        try:
            self.inside_zones[camera_id] = defaultdict(set)
            self.person_state_buffer[camera_id] = defaultdict(dict)
            self.person_dwell_tracker[camera_id] = defaultdict(dict)
            self.line_cross_tracker[camera_id] = defaultdict(dict)
            self.line_cooldown_tracker[camera_id] = defaultdict(dict)
            self.person_zone_history[camera_id] = defaultdict(dict)

            for zone in self.data[camera_id].get("zones", {}):
                self.inside_zones[camera_id][zone] = set()
                self.person_state_buffer[camera_id][zone] = {}
                self.person_dwell_tracker[camera_id][zone] = {}
                self.person_zone_history[camera_id][zone] = {}

            for line in self.data[camera_id].get("lines", {}):
                self.line_cross_tracker[camera_id][line] = {}
                self.line_cooldown_tracker[camera_id][line] = {}

            logger.info(f"Initialized camera tracking structures: {camera_id}")
        except Exception as e:
            logger.error(f"Failed to initialize camera {camera_id}: {e}")
            raise

    def initialize_sources(self, camera_ids: List[str]):
        with self.lock:
            for camera_id in camera_ids:
                if camera_id not in self.data:
                    self.data[camera_id] = {"zones": {}, "lines": {}}
                    logger.info(f"Initialized new camera: {camera_id}")
                else:
                    for zone_name, zone_data in self.data[camera_id].get("zones", {}).items():
                        zone_data["inside_ids"] = []
                        logger.info(f"Preserved counts for {camera_id}/{zone_name}:")
                    
                    for line_name, line_data in self.data[camera_id].get("lines", {}).items():
                        logger.info(f"Preserved counts for {camera_id}/{line_name}:")
                self._init_camera(camera_id)
            
            logger.info(f"[INFO] Cleared all old data. Initialized fresh state for cameras:{camera_ids}")

    def _clear_trackers(self) -> None:
        self.inside_zones.clear()
        self.person_state_buffer.clear()
        self.person_dwell_tracker.clear()
        self.line_cross_tracker.clear()
        self.line_cooldown_tracker.clear()

    def get_active_cameras_info(self) -> Dict[str, Any]:
        with self.lock:
            camera_list = []
            for camera_id in self.data.keys():
                camera_info = {
                    "camera_id": camera_id,
                    "status": "active",
                    "zones": list(self.data[camera_id].get("zones", {}).keys()),
                    "lines": list(self.data[camera_id].get("lines", {}).keys()),
                    "zone_count": len(self.data[camera_id].get("zones", {})),
                    "line_count": len(self.data[camera_id].get("lines", {}))
                }
                camera_list.append(camera_info)

            return {
                "cameras": camera_list,
                "total": len(camera_list),
                "timestamp": time.time(),
                "status": "active"
            }

    def _is_in_zone(self, point: Tuple[float, float], zone_coords: Tuple[List[int], List[int]]) -> bool:
        try:
            x, y = point
            (x1, y1), (x2, y2) = zone_coords

            px1 = x1 + self.config.zone_padding
            py1 = y1 + self.config.zone_padding
            px2 = x2 - self.config.zone_padding
            py2 = y2 - self.config.zone_padding

            if px1 >= px2 or py1 >= py2:
                return x1 <= x <= x2 and y1 <= y <= y2

            return px1 <= x <= px2 and py1 <= y <= py2

        except (ValueError, TypeError, IndexError) as e:
            logger.warning(f"Invalid zone check parameters: {e}")
            return False

    def _get_person_position(self, person_data: Tuple, method: str = "center") -> Tuple[float, float]:
        try:
            if len(person_data) == 5:
                _, x1, y1, x2, y2 = person_data
                if method == "bottom_center":
                    return (x1 + x2) / 2, y2
                elif method == "center":
                    return (x1 + x2) / 2, (y1 + y2) / 2
            elif len(person_data) == 3:
                _, x, y = person_data
                return float(x), float(y)
        except (ValueError, TypeError, IndexError) as e:
            logger.warning(f"Failed to extract person position: {e}")

        return 0.0, 0.0

    def _update_state_buffer(self, camera_id: str, zone: str, person_id: int, is_inside: bool) -> bool:
        try:
            buffer = self.person_state_buffer[camera_id][zone]
            current_time = datetime.datetime.now()

            if person_id not in buffer:
                buffer[person_id] = {
                    'state': is_inside,
                    'count': 1,
                    'last_update': current_time
                }
                return False

            data = buffer[person_id]
            if data['state'] != is_inside:
                data.update({
                    'state': is_inside,
                    'count': 1,
                    'last_update': current_time
                })
                return False
            else:
                data['count'] += 1
                data['last_update'] = current_time
                return data['count'] >= self.config.min_dwell_frames

        except Exception as e:
            logger.error(f"Error updating state buffer: {e}")
            return False

    def _update_dwell_tracker(self, camera_id: str, zone: str, person_id: int,
                              is_inside: bool, current_time: datetime.datetime) -> Dict[str, Any]:
        try:
            tracker = self.person_dwell_tracker[camera_id][zone]

            if person_id not in tracker:
                if is_inside:
                    tracker[person_id] = {
                        'entry_time': current_time,
                        'last_seen': current_time,
                        'counted': False,
                        'exit_time': None,
                        'state': PersonState.INSIDE.value
                    }
                    return {'action': 'entered', 'dwell_time': 0.0, 'should_count': False}
                return {'action': 'none', 'dwell_time': 0.0, 'should_count': False}

            entry = tracker[person_id]

            if is_inside:
                if entry['state'] == PersonState.EXITING.value:
                    entry.update({
                        'state': PersonState.INSIDE.value,
                        'exit_time': None,
                        'last_seen': current_time
                    })
                    return {'action': 're_entered', 'dwell_time': 0.0, 'should_count': False}

                entry['last_seen'] = current_time
                dwell_time = (current_time - entry['entry_time']).total_seconds()

                if not entry['counted'] and dwell_time >= self.config.min_dwell_time:
                    entry['counted'] = True
                    return {
                        'action': 'qualified_entry',
                        'dwell_time': dwell_time,
                        'should_count': True
                    }

                return {'action': 'dwelling', 'dwell_time': dwell_time, 'should_count': False}

            else:
                if entry['state'] == PersonState.INSIDE.value:
                    entry.update({
                        'state': PersonState.EXITING.value,
                        'exit_time': current_time
                    })
                    dwell_time = (current_time - entry['entry_time']).total_seconds()
                    return {
                        'action': 'exiting',
                        'dwell_time': dwell_time,
                        'should_count': entry['counted']
                    }

                elif entry['state'] == PersonState.EXITING.value:
                    exit_duration = (current_time - entry['exit_time']).total_seconds()
                    if exit_duration >= self.config.exit_grace_time:
                        dwell_time = (entry['exit_time'] - entry['entry_time']).total_seconds()
                        should_count = entry['counted']
                        del tracker[person_id]
                        return {
                            'action': 'confirmed_exit',
                            'dwell_time': dwell_time,
                            'should_count': should_count
                        }
                return {'action': 'outside', 'dwell_time': 0.0, 'should_count': False}

        except Exception as e:
            logger.error(f"Error updating dwell tracker: {e}")
            return {'action': 'error', 'dwell_time': 0.0, 'should_count': False}

    def _get_side_of_line(self, point: np.ndarray, line_start: np.ndarray, line_end: np.ndarray) -> int:
        try:
            line_vec = line_end - line_start
            point_vec = point - line_start
            cross_product_z = line_vec[0] * point_vec[1] - line_vec[1] * point_vec[0]
            return int(np.sign(cross_product_z))
        except Exception as e:
            logger.warning(f"Error calculating line side: {e}")
            return 0

    def _trim_history(self, history: List[Dict], max_entries: int = None) -> List[Dict]:
        max_entries = max_entries or self.config.max_history_entries
        if len(history) > max_entries:
            return history[-max_entries:]
        return history

    def _publish_mqtt_event(self, topic: str, payload: Dict[str, Any]) -> None:
        try:
            if self.mqtt_client:
                self.mqtt_client.publish(topic, json.dumps(payload), qos=1)
        except Exception as e:
            logger.error(f"Failed to publish MQTT message: {e}")

    def update_counts(self, camera_id: str, detected_people: Set[Tuple]) -> None:
        if not camera_id or not isinstance(detected_people, (set, list)):
            logger.warning(f"Invalid parameters: camera_id={camera_id}, people type={type(detected_people)}")
            return

        valid_people = set()
        for person_data in detected_people:
            if self._validate_person_data(person_data):
                valid_people.add(person_data)
            else:
                logger.warning(f"Invalid person data: {person_data}")

        with self.lock:
            try:
                if camera_id not in self.data:
                    logger.warning(f"Initializing unknown camera: {camera_id}")
                    self.data[camera_id] = {"zones": {}, "lines": {}}
                    self._init_camera(camera_id)

                active_ids = {p[0] for p in valid_people if len(p) >= 1}
                current_time = datetime.datetime.now()

                self._process_zones(camera_id, valid_people, active_ids, current_time)

                self._process_lines(camera_id, valid_people, current_time, active_ids)

                if (current_time - self.last_cleanup).total_seconds() > (self.config.cleanup_interval_minutes * 60):
                    self._perform_cleanup()
                    self.last_cleanup = current_time

            except Exception as e:
                logger.error(f"Failed to update counts for {camera_id}: {e}")
                raise

    def _process_zones(self, camera_id: str, detected_people: Set[Tuple],
                       active_ids: Set[int], current_time: datetime.datetime) -> None:
        for zone, zone_data in self.data[camera_id].get("zones", {}).items():
            try:
                zone_coords = (zone_data["top_left"], zone_data["bottom_right"])
                current_inside = set()
                entries_to_count = []
                exits_to_count = []

                for person_data in detected_people:
                    person_id = person_data[0]
                    position = self._get_person_position(person_data, method="center")
                    is_inside = self._is_in_zone(position, zone_coords)

                    if self._update_state_buffer(camera_id, zone, person_id, is_inside):
                        if is_inside:
                            current_inside.add(person_id)

                        dwell_result = self._update_dwell_tracker(camera_id, zone, person_id, is_inside, current_time)

                        if dwell_result['should_count']:
                            if dwell_result['action'] == 'qualified_entry':
                                entries_to_count.append(person_id)
                            elif dwell_result['action'] == 'confirmed_exit':
                                exits_to_count.append(person_id)

                for person_id in list(self.person_dwell_tracker[camera_id].get(zone, {}).keys()):
                    if person_id not in active_ids:
                        dwell_result = self._update_dwell_tracker(camera_id, zone, person_id, False, current_time)
                        if dwell_result['should_count'] and dwell_result['action'] == 'confirmed_exit':
                            exits_to_count.append(person_id)

                timestamp_str = current_time.strftime("%Y-%m-%d %H:%M:%S")

                if entries_to_count:
                    zone_data["in_count"] += len(entries_to_count)
                    for pid in entries_to_count:
                        history_entry = {"id": pid, "action": ActionType.ENTRY.value, "time": timestamp_str}
                        zone_data["history"].append(history_entry)

                        topic = f"vision/{self.pi_id}/{camera_id}/history/zone/entry"
                        payload = {"zone": zone, **history_entry}
                        self._publish_mqtt_event(topic, payload)

                        if self.db_enabled and self.db_writer:
                            try:
                                person_position = None
                                for person_data in detected_people:
                                    if person_data[0] == pid:
                                        person_position = self._get_person_position(person_data, method="center")
                                        break
                                if person_position:
                                    self.db_writer.write_zone_event(
                                        camera_id=camera_id,
                                        zone_name=zone,
                                        person_id=pid,
                                        action="Entered",
                                        x=person_position[0],
                                        y=person_position[1],
                                        pi_id=self.pi_id
                                    )

                                    try:
                                        from pi_status_monitor import get_status_monitor
                                        status_monitor = get_status_monitor(self.pi_id)
                                        status_monitor.increment_event_count()
                                    except:
                                        pass

                            except Exception as e:
                                logger.error(f"Failed to write zone entry event to DB: {e}")

                if exits_to_count:
                    zone_data["out_count"] += len(exits_to_count)
                    for pid in exits_to_count:
                        history_entry = {"id": pid, "action": ActionType.EXIT.value, "time": timestamp_str}
                        zone_data["history"].append(history_entry)

                        topic = f"vision/{self.pi_id}/{camera_id}/history/zone/exit"
                        payload = {"zone": zone, **history_entry}
                        self._publish_mqtt_event(topic, payload)

                        if self.db_enabled and self.db_writer:
                            try:
                                dwell_time_value = None
                                if (camera_id in self.person_dwell_tracker and zone in self.person_dwell_tracker[camera_id]):
                                    tracker_data = self.person_dwell_tracker[camera_id][zone].get(pid)
                                    if tracker_data and 'entry_time' in tracker_data:
                                        dwell_time_value = (current_time - tracker_data['entry_time']).total_seconds()


                                person_position = None
                                for person_data in detected_people:
                                    if person_data[0] == pid:
                                        person_position = self._get_person_position(person_data, method="center")
                                        break

                                if not person_position:
                                    zone_data_coords = zone_data
                                    tl = zone_data_coords["top_left"]
                                    br = zone_data_coords["bottom_right"]
                                    person_position = ((tl[0] + br[0]) / 2, (tl[1] + br[1]) / 2)


                                self.db_writer.write_zone_event(
                                    camera_id=camera_id,
                                    zone_name=zone,
                                    person_id=pid,
                                    action="Exited",
                                    x=person_position[0],
                                    y=person_position[1],
                                    pi_id=self.pi_id,
                                    dwell_time=dwell_time_value
                                )

                                try:
                                    from pi_status_monitor import get_status_monitor
                                    status_monitor = get_status_monitor(self.pi_id)
                                    status_monitor.increment_event_count()
                                except:
                                    pass

                            except Exception as e:
                                logger.error(f"Failed to write zone exit event to DB: {e}")

                self.inside_zones[camera_id][zone] = current_inside
                zone_data["inside_ids"] = list(current_inside)

                zone_data["history"] = self._trim_history(zone_data["history"])

            except Exception as e:
                logger.error(f"Error processing zone {zone}: {e}")

    def _process_lines(self, camera_id: str, detected_people: Set[Tuple],
                       current_time: datetime.datetime, active_ids: Set[int]) -> None:
        for line_name, line_data in self.data[camera_id].get("lines", {}).items():
            try:
                tracker = self.line_cross_tracker[camera_id][line_name]
                cooldown_tracker = self.line_cooldown_tracker[camera_id][line_name]
                p_line_start = np.array(line_data["start"])
                p_line_end = np.array(line_data["end"])

                for person_id in list(tracker.keys()):
                    if person_id not in active_ids:
                        del tracker[person_id]

                stale_cooldowns = [pid for pid, end_time in cooldown_tracker.items()
                                   if current_time > end_time]
                for pid in stale_cooldowns:
                    del cooldown_tracker[pid]

                for person_data in detected_people:
                    person_id = person_data[0]
                    p_current = np.array(self._get_person_position(person_data, method="center"))

                    if person_id in cooldown_tracker:
                        continue

                    line_x_coords = [p_line_start[0], p_line_end[0]]
                    line_y_coords = [p_line_start[1], p_line_end[1]]
                    padding = 50

                    is_near_line = (min(line_x_coords) - padding <= p_current[0] <= max(line_x_coords) + padding) and \
                                   (min(line_y_coords) - padding <= p_current[1] <= max(line_y_coords) + padding)

                    if not is_near_line:
                        if person_id in tracker:
                            del tracker[person_id]
                        continue

                    current_side = self._get_side_of_line(p_current, p_line_start, p_line_end)
                    if current_side == 0:
                        continue

                    if person_id not in tracker:
                        tracker[person_id] = {
                            'position': p_current.copy(),
                            'side': current_side,
                            'frames_on_side': 1,
                            'stable': False
                        }
                        continue

                    person_track = tracker[person_id]
                    displacement = np.linalg.norm(p_current - person_track['position'])

                    if displacement < self.config.min_movement_threshold and person_track['stable']:
                        continue

                    if current_side != person_track['side']:
                        if person_track['stable']:
                            timestamp_str = current_time.strftime("%Y-%m-%d %H:%M:%S")
                            action = ActionType.IN.value if current_side > 0 else ActionType.OUT.value

                            if action == ActionType.IN.value:
                                line_data["in_count"] += 1
                            else:
                                line_data["out_count"] += 1

                            history_entry = {"id": person_id, "action": action, "time": timestamp_str}
                            line_data["history"].append(history_entry)
                            line_data["history"] = self._trim_history(line_data["history"])

                            topic = f"vision/{self.pi_id}/{camera_id}/history/line/cross"
                            payload = {"line": line_name, **history_entry}
                            self._publish_mqtt_event(topic, payload)

                            if self.db_enabled and self.db_writer:
                                try:
                                    self.db_writer.write_line_crossing(
                                        camera_id=camera_id,
                                        line_name=line_name,
                                        person_id=person_id,
                                        direction=action,
                                        pi_id=self.pi_id
                                    )

                                    try:
                                        from pi_status_monitor import get_status_monitor
                                        status_monitor = get_status_monitor(self.pi_id)
                                        status_monitor.increment_event_count()
                                    except:
                                        pass
                                        
                                except Exception as e:
                                    logger.error(f"Failed to write line crossing event to DB: {e}")

                            cooldown_end_time = current_time + datetime.timedelta(
                                seconds=self.config.crossing_cooldown_seconds)
                            cooldown_tracker[person_id] = cooldown_end_time

                            del tracker[person_id]
                            continue
                        else:
                            person_track.update({
                                'side': current_side,
                                'frames_on_side': 1,
                                'stable': False
                            })
                    else:
                        person_track['frames_on_side'] += 1
                        if (not person_track['stable'] and
                                person_track['frames_on_side'] >= self.config.state_confirmation_frames):
                            person_track['stable'] = True

                    person_track['position'] = p_current.copy()

            except Exception as e:
                logger.error(f"Error processing line {line_name}: {e}")

    def _perform_cleanup(self) -> None:
        try:
            current_time = datetime.datetime.now()
            cleanup_threshold = datetime.timedelta(minutes=30)

            for camera_id in self.data.keys():
                for zone, buffer in self.person_state_buffer.get(camera_id, {}).items():
                    stale_ids = [
                        pid for pid, data in buffer.items()
                        if (current_time - data['last_update']) > cleanup_threshold
                    ]
                    for pid in stale_ids:
                        del buffer[pid]

                for zone, tracker in self.person_dwell_tracker.get(camera_id, {}).items():
                    stale_ids = []
                    for pid, data in tracker.items():
                        last_active = data.get('exit_time', data.get('last_seen'))
                        if last_active and (current_time - last_active) > cleanup_threshold:
                            stale_ids.append(pid)
                    for pid in stale_ids:
                        del tracker[pid]

            logger.info("Performed periodic cleanup")

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def create_or_update_zone(self, camera_id: str, zone: str,
                              top_left: List[int], bottom_right: List[int]) -> bool:
        if not self._validate_coordinates(top_left, bottom_right):
            logger.error(f"Invalid coordinates for zone {zone}: {top_left} -> {bottom_right}")
            return False

        with self.lock:
            try:
                if camera_id not in self.data:
                    self.data[camera_id] = {"zones": {}, "lines": {}}
                    self._init_camera(camera_id)
                else:
                    if camera_id not in self.inside_zones:
                        self.inside_zones[camera_id] = defaultdict(set)
                    if camera_id not in self.person_state_buffer:
                        self.person_state_buffer[camera_id] = defaultdict(dict)

                    self.inside_zones[camera_id][zone] = set()
                    self.person_state_buffer[camera_id][zone] = {}
                    self.person_dwell_tracker[camera_id][zone] = {}
                    self.person_zone_history[camera_id][zone] = {}

                self.data[camera_id]["zones"][zone] = {
                    "top_left": top_left,
                    "bottom_right": bottom_right,
                    "in_count": 0,
                    "out_count": 0,
                    "inside_ids": [],
                    "history": []
                }
                #self._init_camera(camera_id)
                save_zone_line_config(self)
                logger.info(f"Created/updated zone '{zone}' for camera '{camera_id}'")
                return True
            except Exception as e:
                logger.error(f"Failed to create/update zone {zone}: {e}")
                return False

    def delete_zone(self, camera_id: str, zone: str) -> bool:
        with self.lock:
            try:
                if camera_id not in self.data or zone not in self.data[camera_id].get("zones", {}):
                    logger.warning(f"Zone {zone} not found in camera {camera_id}")
                    return False

                del self.data[camera_id]["zones"][zone]

                if camera_id in self.inside_zones and zone in self.inside_zones[camera_id]:
                    del self.inside_zones[camera_id][zone]
                if camera_id in self.person_state_buffer and zone in self.person_state_buffer[camera_id]:
                    del self.person_state_buffer[camera_id][zone]
                if camera_id in self.person_dwell_tracker and zone in self.person_dwell_tracker[camera_id]:
                    del self.person_dwell_tracker[camera_id][zone]
                if camera_id in self.person_zone_history and zone in self.person_zone_history[camera_id]:
                    del self.person_zone_history[camera_id][zone]

                save_zone_line_config(self)

                logger.info(f"Deleted zone '{zone}' from camera '{camera_id}'")
                return True
            except Exception as e:
                logger.error(f"Failed to delete zone {zone}: {e}")
                return False

    def reset_zone_counts(self, camera_id: str, zone: str) -> bool:
        with self.lock:
            try:
                if camera_id not in self.data or zone not in self.data[camera_id].get("zones", {}):
                    logger.warning(f"Zone {zone} not found in camera {camera_id}")
                    return False

                zone_data = self.data[camera_id]["zones"][zone]
                zone_data.update({
                    "in_count": 0,
                    "out_count": 0,
                    "history": [],
                    "inside_ids": []
                })

                if camera_id in self.inside_zones and zone in self.inside_zones[camera_id]:
                    self.inside_zones[camera_id][zone].clear()
                if camera_id in self.person_state_buffer and zone in self.person_state_buffer[camera_id]:
                    self.person_state_buffer[camera_id][zone].clear()
                if camera_id in self.person_dwell_tracker and zone in self.person_dwell_tracker[camera_id]:
                    self.person_dwell_tracker[camera_id][zone].clear()
                if camera_id in self.person_zone_history and zone in self.person_zone_history[camera_id]:
                    self.person_zone_history[camera_id][zone].clear()

                logger.info(f"Reset counts for zone '{zone}' in camera '{camera_id}'")
                return True
            except Exception as e:
                logger.error(f"Failed to reset zone {zone}: {e}")
                return False

    def get_zone_stats(self, camera_id: str, zone: str) -> Optional[Dict[str, Any]]:
        try:
            if (camera_id not in self.data or
                    zone not in self.data[camera_id].get("zones", {})):
                return None

            zone_data = self.data[camera_id]["zones"][zone]
            current_inside = self.inside_zones.get(camera_id, {}).get(zone, set())

            dwell_stats = {
                "active_people": 0,
                "avg_dwell_time": 0.0,
                "max_dwell_time": 0.0,
                "qualified_entries": 0
            }

            if (camera_id in self.person_dwell_tracker and
                    zone in self.person_dwell_tracker[camera_id]):
                now = datetime.datetime.now()
                dwell_times = []

                for pid, data in self.person_dwell_tracker[camera_id][zone].items():
                    if data['state'] == PersonState.INSIDE.value:
                        dwell_time = (now - data['entry_time']).total_seconds()
                        dwell_times.append(dwell_time)
                        dwell_stats["active_people"] += 1
                        if data['counted']:
                            dwell_stats["qualified_entries"] += 1

                if dwell_times:
                    dwell_stats["avg_dwell_time"] = sum(dwell_times) / len(dwell_times)
                    dwell_stats["max_dwell_time"] = max(dwell_times)

            return {
                "in_count": zone_data["in_count"],
                "out_count": zone_data["out_count"],
                "net_count": zone_data["in_count"] - zone_data["out_count"],
                "current_occupancy": len(current_inside),
                "inside_ids": list(current_inside),
                "dwell_stats": dwell_stats,
                "coordinates": {
                    "top_left": zone_data["top_left"],
                    "bottom_right": zone_data["bottom_right"]
                },
                "recent_history": zone_data["history"][-10:]
            }
        except Exception as e:
            logger.error(f"Failed to get stats for zone {zone}: {e}")
            return None

    def create_or_update_line(self, camera_id: str, line_name: str,
                              start: List[int], end: List[int]) -> bool:
        if not self._validate_line_coordinates(start, end):
            logger.error(f"Invalid coordinates for line {line_name}: {start} -> {end}")
            return False

        with self.lock:
            try:
                if camera_id not in self.data:
                    self.data[camera_id] = {"zones": {}, "lines": {}}

                if "lines" not in self.data[camera_id]:
                    self.data[camera_id]["lines"] = {}

                self.data[camera_id]["lines"][line_name] = {
                    "start": start,
                    "end": end,
                    "in_count": 0,
                    "out_count": 0,
                    "history": []
                }
                self._init_camera(camera_id)

                save_zone_line_config(self)

                logger.info(f"Created/updated line '{line_name}' for camera '{camera_id}'")
                return True
            except Exception as e:
                logger.error(f"Failed to create/update line {line_name}: {e}")
                return False

    def delete_line(self, camera_id: str, line_name: str) -> bool:
        with self.lock:
            try:
                if (camera_id not in self.data or
                        line_name not in self.data[camera_id].get("lines", {})):
                    logger.warning(f"Line {line_name} not found in camera {camera_id}")
                    return False

                del self.data[camera_id]["lines"][line_name]

                if (camera_id in self.line_cross_tracker and
                        line_name in self.line_cross_tracker[camera_id]):
                    del self.line_cross_tracker[camera_id][line_name]
                if (camera_id in self.line_cooldown_tracker and
                        line_name in self.line_cooldown_tracker[camera_id]):
                    del self.line_cooldown_tracker[camera_id][line_name]

                save_zone_line_config(self)

                logger.info(f"Deleted line '{line_name}' from camera '{camera_id}'")
                return True
            except Exception as e:
                logger.error(f"Failed to delete line {line_name}: {e}")
                return False

    def reset_line_counts(self, camera_id: str, line_name: str) -> bool:
        with self.lock:
            try:
                if (camera_id not in self.data or
                        line_name not in self.data[camera_id].get("lines", {})):
                    logger.warning(f"Line {line_name} not found in camera {camera_id}")
                    return False

                line_data = self.data[camera_id]["lines"][line_name]
                line_data.update({
                    "in_count": 0,
                    "out_count": 0,
                    "history": []
                })

                if (camera_id in self.line_cross_tracker and
                        line_name in self.line_cross_tracker[camera_id]):
                    self.line_cross_tracker[camera_id][line_name].clear()
                if (camera_id in self.line_cooldown_tracker and
                        line_name in self.line_cooldown_tracker[camera_id]):
                    self.line_cooldown_tracker[camera_id][line_name].clear()

                
                logger.info(f"Reset counts for line '{line_name}' in camera '{camera_id}'")
                return True
            except Exception as e:
                logger.error(f"Failed to reset line {line_name}: {e}")
                return False

    def get_line_stats(self, camera_id: str, line_name: str) -> Optional[Dict[str, Any]]:
        try:
            if (camera_id not in self.data or
                    line_name not in self.data[camera_id].get("lines", {})):
                return None

            line_data = self.data[camera_id]["lines"][line_name]

            active_tracks = 0
            if (camera_id in self.line_cross_tracker and
                    line_name in self.line_cross_tracker[camera_id]):
                active_tracks = len(self.line_cross_tracker[camera_id][line_name])

            return {
                "in_count": line_data["in_count"],
                "out_count": line_data["out_count"],
                "net_count": line_data["in_count"] - line_data["out_count"],
                "active_tracks": active_tracks,
                "coordinates": {
                    "start": line_data["start"],
                    "end": line_data["end"]
                },
                "recent_history": line_data["history"][-10:]
            }
        except Exception as e:
            logger.error(f"Failed to get stats for line {line_name}: {e}")
            return None

    def get_all_lines(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        with self.lock:
            result = {}
            for camera_id in self.data.keys():
                if "lines" in self.data[camera_id]:
                    result[camera_id] = self.data[camera_id]["lines"]
            return result

    def set_active_camera(self, camera_id: str) -> bool:
        with self.lock:
            if camera_id in self.data:
                self.active_camera = camera_id
                logger.info(f"Active camera set to: {camera_id}")
                return True
            else:
                logger.warning(f"Camera {camera_id} not found")
                return False

    def get_camera_summary(self, camera_id: str) -> Optional[Dict[str, Any]]:
        try:
            if camera_id not in self.data:
                return None

            zones_summary = {}
            for zone_name in self.data[camera_id].get("zones", {}):
                stats = self.get_zone_stats(camera_id, zone_name)
                if stats:
                    zones_summary[zone_name] = {
                        "occupancy": stats["current_occupancy"],
                        "total_entries": stats["in_count"],
                        "total_exits": stats["out_count"]
                    }

            lines_summary = {}
            for line_name in self.data[camera_id].get("lines", {}):
                stats = self.get_line_stats(camera_id, line_name)
                if stats:
                    lines_summary[line_name] = {
                        "crossings_in": stats["in_count"],
                        "crossings_out": stats["out_count"],
                        "net_flow": stats["net_count"]
                    }

            return {
                "camera_id": camera_id,
                "zones": zones_summary,
                "lines": lines_summary,
                "total_zones": len(self.data[camera_id].get("zones", {})),
                "total_lines": len(self.data[camera_id].get("lines", {}))
            }
        except Exception as e:
            logger.error(f"Failed to get camera summary for {camera_id}: {e}")
            return None

    def get_all_cameras_summary(self) -> Dict[str, Any]:
        with self.lock:
            summaries = {}
            for camera_id in self.data.keys():
                summary = self.get_camera_summary(camera_id)
                if summary:
                    summaries[camera_id] = summary

            return {
                "cameras": summaries,
                "active_camera": self.active_camera,
                "total_cameras": len(summaries)
            }

    def cleanup_stale_tracks(self, camera_id: str, active_ids: Set[int]) -> None:
        try:
            current_time = datetime.datetime.now()
            stale_threshold = datetime.timedelta(seconds=30)

            for zone, buffer in self.person_state_buffer.get(camera_id, {}).items():
                stale_ids = [
                    pid for pid, data in buffer.items()
                    if pid not in active_ids or
                    (current_time - data['last_update']) > stale_threshold
                ]
                for pid in stale_ids:
                    del buffer[pid]

            for zone, tracker in self.person_dwell_tracker.get(camera_id, {}).items():
                stale_ids = []
                for pid, data in tracker.items():
                    if pid not in active_ids:
                        if data['state'] == PersonState.INSIDE.value:
                            data.update({
                                'state': PersonState.EXITING.value,
                                'exit_time': current_time
                            })
                        elif data['state'] == PersonState.EXITING.value:
                            exit_duration = (current_time - data['exit_time']).total_seconds()
                            if exit_duration >= self.config.exit_grace_time:
                                stale_ids.append(pid)
                    else:
                        last_active = data.get('exit_time', data.get('last_seen'))
                        if last_active and (current_time - last_active) > datetime.timedelta(minutes=5):
                            stale_ids.append(pid)

                for pid in stale_ids:
                    del tracker[pid]

        except Exception as e:
            logger.error(f"Error during stale track cleanup: {e}")

    def export_data(self, camera_id: Optional[str] = None,
                      start_time: Optional[datetime.datetime] = None,
                      end_time: Optional[datetime.datetime] = None) -> Dict[str, Any]:
        try:
            with self.lock:
                export_data = {}

                cameras_to_export = [camera_id] if camera_id else list(self.data.keys())

                for cam_id in cameras_to_export:
                    if cam_id not in self.data:
                        continue

                    cam_data = self.data[cam_id].copy()

                    if start_time or end_time:
                        for zone_data in cam_data.get("zones", {}).values():
                            filtered_history = []
                            for entry in zone_data.get("history", []):
                                try:
                                    entry_time = datetime.datetime.strptime(
                                        entry["time"], "%Y-%m-%d %H:%M:%S")
                                    if start_time and entry_time < start_time:
                                        continue
                                    if end_time and entry_time > end_time:
                                        continue
                                    filtered_history.append(entry)
                                except (ValueError, KeyError):
                                    continue
                            zone_data["history"] = filtered_history

                        for line_data in cam_data.get("lines", {}).values():
                            filtered_history = []
                            for entry in line_data.get("history", []):
                                try:
                                    entry_time = datetime.datetime.strptime(
                                        entry["time"], "%Y-%m-%d %H:%M:%S")
                                    if start_time and entry_time < start_time:
                                        continue
                                    if end_time and entry_time > end_time:
                                        continue
                                    filtered_history.append(entry)
                                except (ValueError, KeyError):
                                    continue
                            line_data["history"] = filtered_history

                    export_data[cam_id] = cam_data

                return {
                    "export_timestamp": datetime.datetime.now().isoformat(),
                    "cameras": export_data,
                    "config": {
                        "frame_height": self.config.frame_height,
                        "frame_width": self.config.frame_width,
                        "zone_padding": self.config.zone_padding,
                        "min_dwell_time": self.config.min_dwell_time
                    }
                }
        except Exception as e:
            logger.error(f"Failed to export data: {e}")
            return {}

    def get_system_status(self) -> Dict[str, Any]:
        try:
            with self.lock:
                total_zones = sum(len(cam_data.get("zones", {}))
                                  for cam_data in self.data.values())
                total_lines = sum(len(cam_data.get("lines", {}))
                                  for cam_data in self.data.values())

                active_zone_tracks = 0
                active_line_tracks = 0

                for cam_id in self.data.keys():
                    for zone_tracker in self.person_dwell_tracker.get(cam_id, {}).values():
                        active_zone_tracks += len(zone_tracker)
                    for line_tracker in self.line_cross_tracker.get(cam_id, {}).values():
                        active_line_tracks += len(line_tracker)

                return {
                    "status": "operational",
                    "cameras": {
                        "total": len(self.data),
                        "active": self.active_camera,
                        "list": list(self.data.keys())
                    },
                    "zones": {
                        "total": total_zones,
                        "active_tracks": active_zone_tracks
                    },
                    "lines": {
                        "total": total_lines,
                        "active_tracks": active_line_tracks
                    },
                    "last_cleanup": self.last_cleanup.isoformat(),
                    "uptime": datetime.datetime.now().isoformat()
                }
        except Exception as e:
            logger.error(f"Failed to get system status: {e}")
            return {"status": "error", "message": str(e)}
