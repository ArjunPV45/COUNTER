import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
import os
import argparse
import multiprocessing
import numpy as np
import setproctitle
import cv2
import time
import hailo
from hailo_apps_infra1.hailo_rpi_common import (
    get_default_parser,
    detect_hailo_arch,
)
from hailo_apps_infra1.gstreamer_helper_pipelines import(
    QUEUE,
    SOURCE_PIPELINE,
    INFERENCE_PIPELINE,
    INFERENCE_PIPELINE_WRAPPER,
    TRACKER_PIPELINE,
    USER_CALLBACK_PIPELINE,
    DISPLAY_PIPELINE,
    CROP_PIPELINE,
    
)
from hailo_apps_infra1.gstreamer_app import (
    GStreamerApp,
    app_callback_class,
    dummy_callback
)



# -----------------------------------------------------------------------------------------------
# User Gstreamer Application
# -----------------------------------------------------------------------------------------------

# This class inherits from the hailo_rpi_common.GStreamerApp class
class GStreamerDetectionApp(GStreamerApp):
    def __init__(self, app_callback, user_data):
        parser = get_default_parser()
        parser.add_argument(
            "--labels-json",
            default=None,
            help="Path to costume labels JSON file",
        )
        args = parser.parse_args()
        # Call the parent class constructor
        super().__init__(args, user_data)
        # Additional initialization code can be added here
        # Set Hailo parameters these parameters should be set based on the model used
        self.batch_size = 2
        nms_score_threshold = 0.3
        nms_iou_threshold = 0.2


        # Determine the architecture if not specified
        if args.arch is None:
            detected_arch = detect_hailo_arch()
            if detected_arch is None:
                raise ValueError("Could not auto-detect Hailo architecture. Please specify --arch manually.")
            self.arch = detected_arch
            print(f"Auto-detected Hailo architecture: {self.arch}")
        else:
            self.arch = args.arch


        if args.hef_path is not None:
            self.hef_path = args.hef_path
        # Set the HEF file path based on the arch
        elif self.arch == "hailo8":
            self.hef_path = os.path.join(self.current_path, '../resources/yolov8m.hef')
        else:  # hailo8l
            self.hef_path = os.path.join(self.current_path, '../resources/yolov5s_wo_spp.hef')

        # Set the post-processing shared object file
        self.post_process_so = os.path.join(self.current_path, '../resources/libyolo_hailortpp_postprocess.so')
        self.post_function_name = "filter_letterbox"
        # User-defined label JSON file
        self.labels_json = args.labels_json

        self.app_callback = app_callback

        self.thresholds_str = (
            f"nms-score-threshold={nms_score_threshold} "
            f"nms-iou-threshold={nms_iou_threshold} "
            f"output-format-type=HAILO_FORMAT_TYPE_FLOAT32"
        )

        # Set the process title
        setproctitle.setproctitle("Hailo Detection App")

        self.create_pipeline()

    def get_pipeline_string(self):
        source_pipeline = SOURCE_PIPELINE(self.video_source, self.video_width, self.video_height)
        detection_pipeline = INFERENCE_PIPELINE(
            hef_path=self.hef_path,
            post_process_so=self.post_process_so,
            post_function_name=self.post_function_name,
            batch_size=self.batch_size,
            config_json=self.labels_json,
            additional_params=self.thresholds_str)
        detection_pipeline_wrapper = INFERENCE_PIPELINE_WRAPPER(detection_pipeline)
        tracker_pipeline = TRACKER_PIPELINE(class_id=1)
        user_callback_pipeline = USER_CALLBACK_PIPELINE()
        display_pipeline = DISPLAY_PIPELINE(video_sink=self.video_sink, sync=self.sync, show_fps=self.show_fps)

        pipeline_string = (
            f'{source_pipeline} ! '
            f'{detection_pipeline_wrapper} ! '
            f'{tracker_pipeline} ! '
            f'{user_callback_pipeline} ! '
            f'{display_pipeline}'
        )
        print(pipeline_string)
        return pipeline_string

class GStreamerMultiSourceDetectionApp(GStreamerApp):
    def __init__(self, app_callback, user_data, video_sources):
        parser = get_default_parser()
        parser.add_argument(
            "--labels-json",
            default=None,
            help="Path to costom labels JSON file",
        )
        args = parser.parse_args()
        nms_score_threshold = 0.4
        nms_iou_threshold = 0.5

        super().__init__(args, user_data)
        self.video_sources = video_sources  # Multiple RTSP sources
        self.batch_size = 2
        # Determine the architecture if not specified
        if args.arch is None:
            detected_arch = detect_hailo_arch()
            if detected_arch is None:
                raise ValueError("Could not auto-detect Hailo architecture. Please specify --arch manually.")
            self.arch = detected_arch
            print(f"Auto-detected Hailo architecture: {self.arch}")
        else:
            self.arch = args.arch


        if args.hef_path is not None:
            self.hef_path = args.hef_path
        # Set the HEF file path based on the arch
        elif self.arch == "hailo8":
            self.hef_path = os.path.join(self.current_path, '../resources/yolov8m.hef')
        else:  # hailo8l
            self.hef_path = os.path.join(self.current_path, '../resources/yolov8s_h8l.hef')

        # Set the post-processing shared object file
        self.post_process_so = os.path.join(self.current_path, '../resources/libyolo_hailortpp_postprocess.so')
        self.post_function_name = "filter_letterbox"
        # User-defined label JSON file
        self.labels_json = args.labels_json

        self.app_callback = app_callback

        self.thresholds_str = (
            f"nms-score-threshold={nms_score_threshold} "
            f"nms-iou-threshold={nms_iou_threshold} "
            f"output-format-type=HAILO_FORMAT_TYPE_FLOAT32"
        )

        # Set the process title
        setproctitle.setproctitle("Hailo Detection App")

        self.create_pipeline()

    def get_pipeline_string(self):
        source_pipelines = []
        compositor_elements = []
        cropper_so_path = os.path.join(self.current_path, '../resources/libdetection_croppers.so')
        
        num_sources = len(self.video_sources)
        screen_width = 1280 if num_sources <= 2 else 1920
        screen_height = 720 if num_sources <= 2 else 1080

        for i, video_source in enumerate(self.video_sources):
            source_pipeline = SOURCE_PIPELINE(video_source, self.video_width, self.video_height, name=f"src_{i}", source_index=i)

            detection_pipeline = INFERENCE_PIPELINE(
                hef_path=self.hef_path,
                post_process_so=self.post_process_so,
                post_function_name=self.post_function_name,
                batch_size=self.batch_size,
                config_json=self.labels_json,
                additional_params=self.thresholds_str,
                name=f"infer_{i}") # ✅ Unique name per source
            
            detection_pipeline_wrapper = INFERENCE_PIPELINE_WRAPPER(detection_pipeline, name=f"inference_wrapper_{i}")
            tracker_pipeline = TRACKER_PIPELINE(class_id=1, keep_past_metadata=True, name=f"tracker_{i}")  # ✅ Unique tracker per source
            cropper_pipeline = CROP_PIPELINE(
                so_path=cropper_so_path,
                function_name="all_detections",
                name=f"cropper_{i}",
                output_path=f"/tmp/crop_{i}_%05d.jpg"
            )
            if i == 0:
                identity_name = "identity_callback"
                display_name = "hailo_display"
            else:    
                identity_name = f"identity_callback_{i}"
                display_name = f"source_display_{i}"
                
            
            user_callback_pipeline = USER_CALLBACK_PIPELINE(name=identity_name)
            display_pipeline = DISPLAY_PIPELINE(video_sink=self.video_sink, sync=self.sync, show_fps=self.show_fps, name=display_name)
            
            enhancement_pipeline = (
                f'{QUEUE(name=f"enhance_{i}_q")} ! '
                f'videoconvert name=enhance_{i}_convert ! '
            )
            
            #user_callback_pipeline = USER_CALLBACK_PIPELINE(name=f"identity_callback_{i}")
            '''(
                f'{QUEUE(name=f"identity_callback_{i}_q")} ! '
                f'identity name=identity_callback_{i} '  #  # ✅ Unique callback per source
            )'''
            #display_pipeline = DISPLAY_PIPELINE(video_sink=self.video_sink, sync=self.sync, show_fps=self.show_fps, name=f"source_display_{i}")
            # Define compositor positions dynamically
            xpos = (i % 2) * (screen_width // 2 )
            ypos = (i // 2) * (screen_height // 2 )
            compositor_elements.append(f"sink_{i}::xpos={xpos} sink_{i}::ypos={ypos}")

            # Ensure unique queue names per pipeline
            full_pipeline = (
                f"{source_pipeline} ! "
                f"{detection_pipeline_wrapper} ! "
                f"{tracker_pipeline} ! "
                f"{user_callback_pipeline} ! {display_pipeline}"
            )

            
            
            #full_pipeline = f"{source_pipeline} ! {detection_pipeline_wrapper} ! {tracker_pipeline} ! {cropper_pipeline} ! {user_callback_pipeline} ! {display_pipeline} "#! comp.sink_{i} "
            '''full_pipeline = (
                f'{source_pipeline} ! '
                f'queue name=q_infer leaky=no max-size-buffers=3 max-size-bytes=0 max-size-time=0 ! '
                f'{detection_pipeline} ! '
                f'queue name=q_tracker leaky=no max-size-buffers=3 max-size-bytes=0 max-size-time=0 ! '
                f'{tracker_pipeline} ! '
                f'queue name=q_display leaky=no max-size-buffers=3 max-size-bytes=0 max-size-time=0 ! '
                f'{display_pipeline} ! '
                f'comp.sink_{i}'
            )'''
            source_pipelines.append(full_pipeline)

        compositor_pipeline = f"compositor name=comp { ' '.join(compositor_elements) } ! videoconvert ! autovideosink sync=false"

        pipeline_string = " ".join(source_pipelines) #+ compositor_pipeline
        print("Pipeline:\n", pipeline_string)
        return pipeline_string
        #



if __name__ == "__main__":
    video_sources = [
        "rtsp://admin:admin123@10.71.172.253:554/cam/realmonitor?channel=1&subtype=1",
        "/home/pi/hailo-rpi5-examples/venv_hailo_rpi5_examples/lib/python3.11/site-packages/resources/example.mp4"
        ]
    Gst.init(None)    

    user_data = app_callback_class()
    app_callback = dummy_callback
    app = GStreamerMultiSourceDetectionApp(app_callback, user_data, video_sources)
    app.run()
    
'''if __name__ == "__main__":
    # Create an instance of the user app callback class
    user_data = app_callback_class()
    app_callback = dummy_callback
    app = GStreamerDetectionApp(app_callback, user_data)
    app.run()'''
    
   

