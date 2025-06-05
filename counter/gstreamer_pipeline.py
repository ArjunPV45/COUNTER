import threading
import signal
import cv2
import numpy as np
import hailo
import time
from gi.repository import Gst
from hailo_apps_infra.hailo_rpi_common import get_caps_from_pad, get_numpy_from_buffer
from hailo_apps_infra.detection_pipeline import GStreamerMultiSourceDetectionApp


class SafeGStreamerMultiSourceDetectionApp(GStreamerMultiSourceDetectionApp):
    def __init__(self, *args, **kwargs):
        if threading.current_thread() == threading.main_thread():
            super().__init__(*args, **kwargs)
        else:
            import signal as signal_module
            original_signal = signal_module.signal
            signal_module.signal = lambda *a, **kw: None
            try:
                super().__init__(*args, **kwargs)
            finally:
                signal_module.signal = original_signal


def validate_rtsp_sources(sources, timeout=10):
    failed_sources = []

    for i, source in enumerate(sources):
        if source.startswith('/dev/video'):
            print(f"Skipping validation for local device: {source}")
            continue

        try:
            test_pipeline = Gst.parse_launch(f"""
                rtspsrc location={source} latency=300 protocols=tcp drop-on-latency=true ! 
                queue max-size-buffers=10 max-size-time=0 max-size-bytes=0 ! 
                rtph264depay ! 
                queue max-size-buffers=5 ! 
                avdec_h264 ! 
                queue max-size-buffers=5 ! 
                videoconvert ! 
                videoscale ! 
                video/x-raw,format=RGB,width=320,height=240 ! 
                appsink name=testsink max-buffers=1 drop=true
            """)

            if not test_pipeline:
                failed_sources.append(f"camera{i+1}: Failed to create test pipeline")
                continue

            appsink = test_pipeline.get_by_name("testsink")
            if not appsink:
                failed_sources.append(f"camera{i+1}: Failed to get appsink element")
                test_pipeline.set_state(Gst.State.NULL)
                continue

            ret = test_pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                failed_sources.append(f"camera{i+1}: Failed to start pipeline")
                test_pipeline.set_state(Gst.State.NULL)
                continue

            start_time = time.time()
            validation_success = False
            data_received = False

            def on_new_sample(appsink):
                nonlocal data_received
                data_received = True
                return Gst.FlowReturn.OK

            appsink.set_property('emit-signals', True)
            appsink.connect('new-sample', on_new_sample)

            while time.time() - start_time < timeout:
                ret, state, pending = test_pipeline.get_state(Gst.CLOCK_TIME_NONE)
                if ret == Gst.StateChangeReturn.SUCCESS and state == Gst.State.PLAYING and data_received:
                    validation_success = True
                    break
                elif ret == Gst.StateChangeReturn.FAILURE:
                    break
                time.sleep(0.2)

            test_pipeline.set_state(Gst.State.NULL)
            if not validation_success:
                failed_sources.append(f"camera{i+1}: No data received within timeout period")

        except Exception as e:
            failed_sources.append(f"camera{i+1}: {str(e)}")

    if failed_sources:
        return False, "Invalid RTSP sources detected", failed_sources
    return True, "All sources validated successfully", []


def create_visitor_counter_callback(user_data, frame_buffers, socketio):
    def visitor_counter_callback(pad, info, user_data_param):
        buffer = info.get_buffer()
        if buffer is None:
            print("Error: No buffer available")
            return Gst.PadProbeReturn.OK

        try:
            format, width, height = get_caps_from_pad(pad)
            if format is None or width is None or height is None:
                print("Error: Could not get format/dimensions from pad")
                return Gst.PadProbeReturn.OK

            np_frame = get_numpy_from_buffer(buffer, format, width, height)
            frame = cv2.cvtColor(np_frame, cv2.COLOR_RGB2BGR)
            camera_id = _extract_camera_id_from_pad(pad)
            detected_people = _extract_people_detections(buffer, width, height)
            _draw_zones_on_frame(frame, user_data, camera_id)
            frame_buffers[camera_id] = frame
            user_data.update_counts(camera_id, detected_people)
            socketio.emit("update_counts", {
                "data": user_data.data,
                "active_camera": user_data.active_camera
            })
        except Exception as e:
            print(f"Error in callback: {e}")

        return Gst.PadProbeReturn.OK

    return visitor_counter_callback


def _extract_camera_id_from_pad(pad):
    element = pad.get_parent_element()
    element_name = element.get_name()
    source_index = 0
    if "identity_callback_" in element_name:
        try:
            source_index = int(element_name.split("identity_callback_")[-1])
        except ValueError:
            print(f"Couldn't parse source from {element_name}, defaulting to camera1")
    return f"camera{source_index + 1}"


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


def _draw_zones_on_frame(frame, user_data, camera_id):
    if camera_id not in user_data.data:
        return
    for zone, data in user_data.data[camera_id]["zones"].items():
        top_left = tuple(map(int, data["top_left"]))
        bottom_right = tuple(map(int, data["bottom_right"]))
        cv2.rectangle(frame, top_left, bottom_right, (0, 0, 255), 2)
        text = f"{zone} (In: {data['in_count']}, Out: {data['out_count']})"
        cv2.putText(frame, text, (top_left[0], top_left[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)


class PipelineManager:
    def __init__(self, user_data, frame_buffers, socketio):
        self.user_data = user_data
        self.frame_buffers = frame_buffers
        self.socketio = socketio
        self.app_instance = None
        self.video_sources = []

    def start_pipeline(self, video_sources):
        try:
            if self.app_instance:
                print("Stopping previous pipeline before starting a new one...")
                self.stop_pipeline()
                time.sleep(1)

            print("Validating RTSP sources...")
            if self.socketio:
                self.socketio.emit("pipeline_status", {
                    "status": "validating",
                    "message": "Validating RTSP sources..."
                })

            is_valid, message, failed_sources = validate_rtsp_sources(video_sources)

            if not is_valid:
                print(f"RTSP validation failed: {failed_sources}")
                if self.socketio:
                    self.socketio.emit("pipeline_status", {
                        "status": "error",
                        "message": message,
                        "details": failed_sources
                    })
                return False

            print("RTSP sources validated successfully, creating main pipeline...")
            if self.socketio:
                self.socketio.emit("pipeline_status", {
                    "status": "creating",
                    "message": "Creating detection pipeline..."
                })

            self.video_sources = video_sources

            camera_ids = [f"camera{i+1}" for i in range(len(video_sources))]
            self.user_data.data = {cam_id: {"zones": {}} for cam_id in camera_ids}
            self.user_data.inside_zones = {cam_id: {} for cam_id in camera_ids}
            self.user_data.person_zone_history = {cam_id: {} for cam_id in camera_ids}
            self.user_data.active_camera = camera_ids[0] if camera_ids else "camera1"
            self.user_data.save_data()

            callback = create_visitor_counter_callback(self.user_data, self.frame_buffers, self.socketio)

            self.app_instance = SafeGStreamerMultiSourceDetectionApp(callback, self.user_data, video_sources)
            self.app_instance.create_pipeline()

            for i in range(len(video_sources)):
                identity_name = f"identity_callback{'' if i == 0 else '_' + str(i)}"
                identity = self.app_instance.pipeline.get_by_name(identity_name)
                if identity:
                    src_pad = identity.get_static_pad("src")
                    if src_pad:
                        print(f"Adding pad probe to {identity_name}")
                        src_pad.add_probe(Gst.PadProbeType.BUFFER, callback, self.user_data)

            threading.Thread(target=self.app_instance.run, daemon=True).start()

            if self.socketio:
                self.socketio.emit("pipeline_status", {
                    "status": "running",
                    "message": "Pipeline started successfully"
                })

            print("Pipeline started successfully with validated sources")
            return True

        except Exception as e:
            error_msg = f"Failed to start pipeline: {str(e)}"
            print(error_msg)
            if self.socketio:
                self.socketio.emit("pipeline_status", {
                    "status": "error",
                    "message": error_msg
                })
            return False

    def stop_pipeline(self):
        if self.app_instance:
            try:
                self.app_instance.pipeline.set_state(Gst.State.NULL)
                self.app_instance = None
                self.frame_buffers.clear()
                if self.socketio:
                    self.socketio.emit("pipeline_status", {
                        "status": "stopped",
                        "message": "Pipeline stopped successfully"
                    })
                return True
            except Exception as e:
                error_msg = f"Error stopping pipeline: {e}"
                print(error_msg)
                if self.socketio:
                    self.socketio.emit("pipeline_status", {
                        "status": "error",
                        "message": error_msg
                    })
                return False
        return True

    def is_running(self):
        return self.app_instance is not None
