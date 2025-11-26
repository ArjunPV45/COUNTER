import threading
import signal
import cv2
import numpy as np
import hailo
import time
import subprocess
import logging
from gi.repository import Gst, GLib
from logging_config import get_logger
import os

logger = get_logger(__name__)

PIPELINE_PARAMS = {
    "input_width": 640,
    "input_height": 640,

    "input_framerate": 8,
    "hailo_objects": ["hailonet"]
}

from hailo_apps_infra1.hailo_rpi_common import get_caps_from_pad, get_numpy_from_buffer
from hailo_apps_infra1.detection_pipeline import GStreamerMultiSourceDetectionApp

class SafeGStreamerMultiSourceDetectionApp(GStreamerMultiSourceDetectionApp):
    def __init__(self, *args, **kwargs):
        if threading.current_thread() == threading.main_thread():
            super().__init__(*args, **kwargs)
        else:
            original_signal = signal.signal
            signal.signal = lambda *a, **kw: None
            try:
                super().__init__(*args, **kwargs)
            finally:
                signal.signal = original_signal

def diagnose_rtsp_stream(rtsp_url):
    print(f"Diagnosing RTSP stream: {rtsp_url}")
    
    try:
        cmd = [
            'gst-discoverer-1.0', 
            '-v', 
            rtsp_url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        print("=== GST-DISCOVERER OUTPUT ===")
        print(result.stdout)
        if result.stderr:
            print("=== GST-DISCOVERER ERRORS ===")
            print(result.stderr)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("gst-discoverer timed out")
        return False
    except Exception as e:
        print(f"gst-discoverer failed: {e}")
        return False

def validate_rtsp_sources(sources, timeout=20):
    failed_sources = []

    for i, source in enumerate(sources):
        if source.startswith('/dev/video'):
            print(f"Skipping validation for local device: {source}")
            continue

        print(f"\n=== Validating camera{i+1}: {source} ===")
        
        diagnose_rtsp_stream(source)
        
        validation_success = False
        
        try:
            validation_success = _validate_with_ffmpeg_pipeline(source, i, timeout, failed_sources)
            if validation_success:
                print(f"✓ FFmpeg pipeline validation successful for camera{i+1}")
        except Exception as e:
            print(f"FFmpeg validation failed for camera{i+1}: {e}")
        
        if not validation_success:
            try:
                validation_success = _validate_with_udp_pipeline(source, i, timeout, failed_sources)
                if validation_success:
                    print(f"✓ UDP pipeline validation successful for camera{i+1}")
            except Exception as e:
                print(f"UDP validation failed for camera{i+1}: {e}")
        
        if not validation_success:
            try:
                validation_success = _validate_with_raw_pipeline(source, i, timeout, failed_sources)
                if validation_success:
                    print(f"✓ Raw pipeline validation successful for camera{i+1}")
            except Exception as e:
                print(f"Raw validation failed for camera{i+1}: {e}")
        
        if not validation_success:
            try:
                validation_success = _validate_with_baseline_pipeline(source, i, timeout, failed_sources)
                if validation_success:
                    print(f"✓ Baseline H.264 pipeline validation successful for camera{i+1}")
            except Exception as e:
                print(f"Baseline validation failed for camera{i+1}: {e}")

        if not validation_success:
            failed_sources.append(f"camera{i+1}: All validation methods failed - stream may be incompatible with GStreamer")

    if failed_sources:
        return False, "Some RTSP sources failed validation", failed_sources
    logger.info("RTSP validation logic is running (assuming success for this example).")
    return True, "Sources assumed valid", []

def _validate_with_ffmpeg_pipeline(source, camera_index, timeout, failed_sources):
    try:
        print(f"Trying FFmpeg-based validation for camera{camera_index+1}...")
        
        test_pipeline = Gst.parse_launch(f"""
            uridecodebin uri={source} ! 
            queue max-size-buffers=10 leaky=downstream ! 
            videoconvert ! 
            videoscale ! 
            video/x-raw,format=RGB,width=320,height=240 ! 
            appsink name=testsink max-buffers=1 drop=true sync=false
        """)

        return _run_validation_pipeline(test_pipeline, camera_index, timeout, "FFmpeg")

    except Exception as e:
        failed_sources.append(f"camera{camera_index+1}: FFmpeg validation exception: {str(e)}")
        return False

def _validate_with_udp_pipeline(source, camera_index, timeout, failed_sources):
    try:
        print(f"Trying UDP validation for camera{camera_index+1}...")
        
        test_pipeline = Gst.parse_launch(f"""
            rtspsrc location={source} 
                   latency=2000 
                   protocols=udp 
                   timeout=20000000
                   retry=3 ! 
            queue max-size-buffers=20 leaky=downstream ! 
            rtph264depay ! 
            queue max-size-buffers=10 leaky=downstream ! 
            avdec_h264 ! 
            queue max-size-buffers=5 leaky=downstream ! 
            videoconvert ! 
            videoscale ! 
            video/x-raw,format=RGB,width=320,height=240 ! 
            appsink name=testsink max-buffers=1 drop=true sync=false
        """)

        return _run_validation_pipeline(test_pipeline, camera_index, timeout, "UDP")

    except Exception as e:
        failed_sources.append(f"camera{camera_index+1}: UDP validation exception: {str(e)}")
        return False

def _validate_with_raw_pipeline(source, camera_index, timeout, failed_sources):
    try:
        print(f"Trying raw validation for camera{camera_index+1}...")
        
        test_pipeline = Gst.parse_launch(f"""
            rtspsrc location={source} 
                   protocols=tcp+udp+http
                   latency=3000 
                   timeout=30000000
                   do-retransmission=false ! 
            queue ! 
            rtph264depay ! 
            h264parse ! 
            avdec_h264 skip-frame=0 ! 
            videoconvert ! 
            video/x-raw,format=RGB ! 
            videoscale ! 
            video/x-raw,width=320,height=240 ! 
            appsink name=testsink max-buffers=2 drop=true sync=false async=false
        """)

        return _run_validation_pipeline(test_pipeline, camera_index, timeout, "Raw")

    except Exception as e:
        failed_sources.append(f"camera{camera_index+1}: Raw validation exception: {str(e)}")
        return False

def _validate_with_baseline_pipeline(source, camera_index, timeout, failed_sources):
    try:
        print(f"Trying baseline H.264 validation for camera{camera_index+1}...")
        
        test_pipeline = Gst.parse_launch(f"""
            rtspsrc location={source} 
                   protocols=tcp
                   latency=5000 
                   timeout=30000000 ! 
            queue max-size-buffers=30 ! 
            rtph264depay ! 
            h264parse ! 
            video/x-h264,stream-format=avc,profile=baseline ! 
            avdec_h264 ! 
            videoconvert ! 
            videoscale method=bilinear ! 
            video/x-raw,format=RGB,width=320,height=240,framerate=10/1 ! 
            appsink name=testsink max-buffers=1 drop=true sync=false
        """)

        return _run_validation_pipeline(test_pipeline, camera_index, timeout, "Baseline")

    except Exception as e:
        failed_sources.append(f"camera{camera_index+1}: Baseline validation exception: {str(e)}")
        return False

def _run_validation_pipeline(test_pipeline, camera_index, timeout, method_name):
    if not test_pipeline:
        print(f"{method_name} pipeline creation failed for camera{camera_index+1}")
        return False

    appsink = test_pipeline.get_by_name("testsink")
    if not appsink:
        test_pipeline.set_state(Gst.State.NULL)
        return False

    bus = test_pipeline.get_bus()
    bus.add_signal_watch()
    
    error_occurred = False
    warning_occurred = False
    
    def on_bus_message(bus, message):
        nonlocal error_occurred, warning_occurred
        if message.type == Gst.MessageType.ERROR:
            error_occurred = True
            err, debug_info = message.parse_error()
            print(f"Pipeline error for camera{camera_index+1} ({method_name}): {err}")
            if debug_info:
                print(f"Debug info: {debug_info}")
        elif message.type == Gst.MessageType.WARNING:
            warning_occurred = True
            warn, debug_info = message.parse_warning()
            print(f"Pipeline warning for camera{camera_index+1} ({method_name}): {warn}")
        elif message.type == Gst.MessageType.STATE_CHANGED:
            old_state, new_state, pending_state = message.parse_state_changed()
            if message.src == test_pipeline:
                print(f"Pipeline state changed: {old_state.value_nick} -> {new_state.value_nick}")
                
    bus.connect("message", on_bus_message)

    ret = test_pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        test_pipeline.set_state(Gst.State.NULL)
        bus.remove_signal_watch()
        return False

    start_time = time.time()
    frames_received = 0
    data_received = False

    def on_new_sample(appsink):
        nonlocal frames_received, data_received
        sample = appsink.emit("pull-sample")
        if sample:
            frames_received += 1
            data_received = True
            print(f"{method_name}: Frame {frames_received} received for camera{camera_index+1}")
        return Gst.FlowReturn.OK

    appsink.set_property('emit-signals', True)
    appsink.connect('new-sample', on_new_sample)

    success = False
    while time.time() - start_time < timeout:
        if error_occurred:
            print(f"{method_name} validation failed due to error")
            break
            
        ret, state, pending = test_pipeline.get_state(Gst.SECOND)
        
        if ret == Gst.StateChangeReturn.SUCCESS and state == Gst.State.PLAYING:
            if frames_received >= 1: 
                success = True
                break
        elif ret == Gst.StateChangeReturn.FAILURE:
            print(f"{method_name} pipeline state change failed")
            break
            
        time.sleep(0.5)

    test_pipeline.set_state(Gst.State.NULL)
    bus.remove_signal_watch()
    
    if success:
        print(f"{method_name} validation successful: {frames_received} frames received")
    else:
        print(f"{method_name} validation failed: {frames_received} frames received, error: {error_occurred}")
    
    return success

def create_visitor_counter_callback(user_data, frame_buffers, pipeline_manager=None):
    def visitor_counter_callback(pad, info, user_data_param):
        buffer = info.get_buffer()
        if not buffer:
            print("No buffer received in pad probe.")
            return Gst.PadProbeReturn.OK

        try:
            format, width, height = get_caps_from_pad(pad)
            camera_id = pipeline_manager._extract_camera_id_from_pad(pad)
            logger.debug(f"Processing frame from {camera_id} - Format: {format}, Size: {width}x{height}")
            detected_people = _extract_people_detections(buffer, width, height)
            
            user_data.update_counts(camera_id, detected_people)
            
            np_frame = get_numpy_from_buffer(buffer, format, width, height)
            frame_bgr = cv2.cvtColor(np_frame, cv2.COLOR_RGB2BGR)
            _draw_visuals_on_frame(frame_bgr, user_data, camera_id)
            
            frame_buffers[camera_id] = frame_bgr

            if pipeline_manager and hasattr(pipeline_manager, 'health_monitor'):
                health_monitor = pipeline_manager.health_monitor
                if health_monitor:
                    health_monitor.update_frame_timestamp(camera_id)

            try:
                from pi_status_monitor import get_status_monitor
                status_monitor = get_status_monitor(os.getenv("PI_UNIQUE_ID", "pi-default"))
                status_monitor.update_frame_time()
            except:
                pass
                

        except Exception as e:
            logger.error(f"Error in visitor_counter_callback: {e}", exc_info=False)

        return Gst.PadProbeReturn.OK
    return visitor_counter_callback



def _extract_people_detections(buffer, width, height):
    detected_people = set()
    roi = hailo.get_roi_from_buffer(buffer)
    if roi is None:
        print("Error: Could not get ROI from buffer")
        return detected_people
    for d in roi.get_objects_typed(hailo.HAILO_DETECTION):
        if d.get_label() == "person":
            bbox = d.get_bbox()
            x1 = bbox.xmin() * width
            y1 = bbox.ymin() * height
            x2 = bbox.xmax() * width
            y2 = bbox.ymax() * height
            center_x = (x1 + x2) / 2
            center_y = (y1 + y2) / 2
            unique_ids = d.get_objects_typed(hailo.HAILO_UNIQUE_ID)
            person_id = unique_ids[0].get_id() if unique_ids else -1
            detected_people.add((person_id, center_x, center_y))
    return detected_people

def _draw_visuals_on_frame(frame, user_data, camera_id):
    if camera_id not in user_data.data: return
    with user_data.lock: 
        for zone, data in user_data.data[camera_id].get("zones", {}).items():
            top_left = tuple(map(int, data["top_left"]))
            bottom_right = tuple(map(int, data["bottom_right"]))
            cv2.rectangle(frame, top_left, bottom_right, (0, 0, 255), 2)
    
        for line_name, data in user_data.data[camera_id].get("lines", {}).items():
            start_point = tuple(map(int, data["start"]))
            end_point = tuple(map(int, data["end"]))
            cv2.line(frame, start_point, end_point, (0, 255, 0), 2)
            cv2.putText(frame, line_name, (start_point[0], start_point[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

class PipelineManager:
    def __init__(self, user_data, frame_buffers):
        self.user_data = user_data
        self.frame_buffers = frame_buffers
        self.app_instance = None
        self.video_sources = []
        self.is_running_flag = False
        self.bus = None
        self.bus_watch_id = None
        self.health_monitor = None
        self.camera_names = []
        logger.info("PipelineManager initialized.")

    def _on_bus_message(self, bus, message):
        msg_type = message.type
        if msg_type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error(f"Gstreamer pipeline error: {err}, {debug}")
            logger.warning("Critical pipeline erro detected. Attempting to restart the pipeline..")
            self.trigger_restart()

        elif msg_type == Gst.MessageType.EOS:
            logger.info("End-of-Stream reached. Pipeline is stopping.")
            logger.warning("Attempting to automatically restart the pipeline")
            self.trigger_restart()
        return True

    def trigger_restart(self):
        if hasattr(self, 'restart_thread') and self.restart_thread.is_alive():
            logger.info("Restart already in progress. Skipping new restart request.")
            return

        self.restart_thread = threading.Thread(target=self.restart_pipeline, daemon=True)
        self.restart_thread.start()

    def restart_pipeline(self):
        if self.is_running():
            logger.info("Restarting pipeline...")
            source_to_restart = self.video_sources.copy()

            health_monitor_backup = self.health_monitor

            self.stop_pipeline()
            time.sleep(2)

            self.health_monitor = health_monitor_backup

            if source_to_restart:
                self.start_pipeline(source_to_restart)
            else:
                logger.error("No video sources available to restart the pipeline.")

    
    def start_pipeline(self, video_sources, custom_camera_names=None, on_started_callback=None):
        if custom_camera_names is None:
            self.camera_names = [f"camera{i+1}" for i in range(len(video_sources))]
        else:
            if len(custom_camera_names) != len(video_sources):
                raise ValueError("Length of custom_camera_names must equal length of video_sources.")
            self.camera_names = custom_camera_names
        
        if self.is_running():
            logger.info("Pipeline is already running. Stopping first.")
            self.stop_pipeline()
            time.sleep(1)

        try:
            logger.info("Initializing fresh state for new pipeline...")
            
            if hasattr(self.user_data, 'initialize_sources'):
                 self.user_data.initialize_sources(self.camera_names)
            else:
                 logger.warning("user_data object is missing the initialize_sources method.")

            logger.info("Validating RTSP sources...")
            is_valid, message, failed_sources = validate_rtsp_sources(video_sources)
            if not is_valid:
                logger.error(f"RTSP validation failed: {message}, Details: {failed_sources}")
                return False

            logger.info("RTSP sources validated. Creating GStreamer pipeline...")
            self.video_sources = video_sources

            callback = create_visitor_counter_callback(self.user_data, self.frame_buffers, self)

            self.app_instance = SafeGStreamerMultiSourceDetectionApp(
                callback, self.user_data, video_sources, self.user_data.data
            )
            self.app_instance.create_pipeline()

            self.bus = self.app_instance.pipeline.get_bus()
            self.bus_watch_id = self.bus.add_watch(GLib.PRIORITY_DEFAULT, self._on_bus_message)

            for i in range(len(video_sources)):
                identity_name = f"identity_callback_{i}" if i > 0 else "identity_callback"
                identity = self.app_instance.pipeline.get_by_name(identity_name)
                if identity:
                    src_pad = identity.get_static_pad("src")
                    if src_pad:
                        logger.info(f"Adding pad probe to '{identity_name}'")
                        src_pad.add_probe(Gst.PadProbeType.BUFFER, callback, self.user_data)
                else:
                    logger.error(f"Could not find element '{identity_name}' to add a probe.")

            threading.Thread(target=self.app_instance.run, daemon=True).start()
            self.is_running_flag = True

            if self.health_monitor:
                logger.info(f"Health monitor linked to pipeline")
            else:
                logger.warning("Health monitor not set in PipelineManager")
            
            if on_started_callback:
                threading.Timer(2.0, on_started_callback, args=[self]).start()

            try:
                from pi_status_monitor import get_status_monitor
                status_monitor = get_status_monitor(os.getenv("PI_UNIQUE_ID", "pi-default"))
                if self.camera_names:
                    cameras = self.camera_names
                else:
                    cameras = [f"camera{i+1}" for i in range(len(video_sources))]
                
                status_monitor.set_pipeline_status(True, cameras=cameras)
            except Exception as e:
                logger.warning(f"Could not update status monitor: {e}")
                
            logger.info("Pipeline started successfully")
            return True


        except Exception as e:
            logger.error(f"Failed to start pipeline: {e}", exc_info=True)
            self.is_running_flag = False
            return False


    def _extract_camera_id_from_pad(self, pad):
        element = pad.get_parent_element()
        element_name = element.get_name()
        source_index = 0
        if "identity_callback_" in element_name:
            try:
                source_index = int(element_name.split("identity_callback_")[-1])
            except ValueError:
                print(f"Couldn't parse source from {element_name}, defaulting to 0")
        if 0 <= source_index < len(self.camera_names):
            return self.camera_names[source_index]
        else:
            logger.warning(f"Source index {source_index} out of range, defaulting camera name to 'unknown_camera'")
            return "unknown_camera"

    def stop_pipeline(self):
        if not self.is_running():
            logger.info("Stop command received, but no pipeline was running.")
            return True
        logger.info("Stopping pipeline...")
        try:
            if self.bus_watch_id:
                GLib.source_remove(self.bus_watch_id)
                self.bus_watch_id = None

            if self.app_instance and hasattr(self.app_instance, 'pipeline'):
                self.app_instance.pipeline.send_event(Gst.Event.new_eos())
                time.sleep(0.5)
                ret = self.app_instance.pipeline.set_state(Gst.State.NULL)

                if ret != Gst.StateChangeReturn.SUCCESS:
                    ret, state, pending = self.app_instance.pipeline.get_state(3*Gst.SECOND)
                    if ret == Gst.StateChangeReturn.SUCCESS:
                        logger.info("Pipeline stopped successfully after EOS.")
                    else:
                        logger.warning("Pipeline did not stop cleanly after EOS.")
                time.sleep(0.5)
                 
            self.app_instance = None
            self.frame_buffers.clear()
            self.video_sources = []
            self.is_running_flag = False

            try:
                from pi_status_monitor import get_status_monitor
                status_monitor = get_status_monitor(os.getenv("PI_UNIQUE_ID", "pi-default"))
                status_monitor.set_pipeline_status(False)
            except Exception as e:
                logger.warning(f"Could not update status monitor: {e}")

            logger.info("Pipeline stopped successfully.")
            return True

        except Exception as e:
            logger.error(f"Error during pipeline shutdown: {e}", exc_info=True)
            self.app_instance = None
            self.frame_buffers.clear()
            self.video_sources = []
            self.is_running_flag = False
            return False

    def is_running(self):
        return self.app_instance is not None and self.is_running_flag
