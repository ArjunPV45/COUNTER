import multiprocessing
import setproctitle
import signal
import os
import gi
import threading
import sys
import cv2
import numpy as np
import time
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib, GObject
from hailo_apps_infra1.gstreamer_helper_pipelines import get_source_type


# -----------------------------------------------------------------------------------------------
# User-defined class to be used in the callback function
# -----------------------------------------------------------------------------------------------
class app_callback_class:
    def __init__(self):
        self.frame_count = 0
        self.use_frame = False
        self.frame_queue = multiprocessing.Queue(maxsize=3)
        self.running = True

    def increment(self):
        self.frame_count += 1

    def get_count(self):
        return self.frame_count

    def set_frame(self, frame):
        if not self.frame_queue.full():
            self.frame_queue.put(frame)

    def get_frame(self):
        if not self.frame_queue.empty():
            return self.frame_queue.get()
        else:
            return None

def dummy_callback(pad, info, user_data):
    """
    A minimal dummy callback function that returns immediately.
    """
    return Gst.PadProbeReturn.OK

# -----------------------------------------------------------------------------------------------
# GStreamerApp class
# -----------------------------------------------------------------------------------------------
class GStreamerApp:
    def __init__(self, args, user_data: app_callback_class):
        # Set the process title
        setproctitle.setproctitle("Hailo Python App")

        # Create options menu
        self.options_menu = args

        # Set up signal handler for SIGINT (Ctrl-C)
        import threading
        if threading.current_thread() == threading.main_thread():
            signal.signal(signal.SIGINT, self.shutdown)
        else:
            print("[INFO] Skipping signal setup (not in main thread)")

        # Initialize variables
        tappas_post_process_dir = os.environ.get('TAPPAS_POST_PROC_DIR', '')
        if tappas_post_process_dir == '':
            print("TAPPAS_POST_PROC_DIR environment variable is not set. Please set it to by sourcing setup_env.sh")
            exit(1)
        self.current_path = os.path.dirname(os.path.abspath(__file__))
        self.postprocess_dir = tappas_post_process_dir
        self.video_source = self.options_menu.input
        self.source_type = get_source_type(self.video_source)
        self.user_data = user_data
        self.video_sink = "autovideosink"
        self.pipeline = None
        self.loop = None
        self.threads = []
        self.error_occurred = False
        self.pipeline_latency = 300  # milliseconds
        self.display_process = None
        self.should_exit = False  # Add exit flag

        # Set Hailo parameters
        self.batch_size = 1
        self.video_width = 1280
        self.video_height = 720
        self.video_format = "RGB"
        self.hef_path = None
        self.app_callback = None

        # Set user data parameters
        user_data.use_frame = self.options_menu.use_frame

        self.sync = "false" if (self.options_menu.disable_sync or self.source_type != "file") else "true"
        self.show_fps = self.options_menu.show_fps

        if self.options_menu.dump_dot:
            os.environ["GST_DEBUG_DUMP_DOT_DIR"] = os.getcwd()

    def on_fps_measurement(self, sink, fps, droprate, avgfps):
        print(f"FPS: {fps:.2f}, Droprate: {droprate:.2f}, Avg FPS: {avgfps:.2f}")
        return True

    def create_pipeline(self):
        # Initialize GStreamer
        Gst.init(None)

        pipeline_string = self.get_pipeline_string()
        try:
            self.pipeline = Gst.parse_launch(pipeline_string)
        except Exception as e:
            print(f"Error creating pipeline: {e}", file=sys.stderr)
            sys.exit(1)

        # Connect to hailo_display fps-measurements
        if self.show_fps:
            print("Showing FPS")
            self.pipeline.get_by_name("hailo_display").connect("fps-measurements", self.on_fps_measurement)

        # Create a GLib Main Loop
        self.loop = GLib.MainLoop()

    def bus_call(self, bus, message, loop):
        t = message.type
        if t == Gst.MessageType.EOS:
            print("End-of-stream")
            self.on_eos()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"Error: {err}, {debug}", file=sys.stderr)
            self.error_occurred = True
            self.should_exit = True
            # Force quit the main loop immediately
            if self.loop and self.loop.is_running():
                self.loop.quit()
            return False
        # QOS
        elif t == Gst.MessageType.QOS:
            # Handle QoS message here
            qos_element = message.src.get_name()
            print(f"QoS message received from {qos_element}")
        return True

    def on_eos(self):
        if self.source_type == "file":
             # Seek to the start (position 0) in nanoseconds
            success = self.pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            if success:
                print("Video rewound successfully. Restarting playback...")
            else:
                print("Error rewinding the video.", file=sys.stderr)
                self.error_occurred = True
                self.should_exit = True
                if self.loop and self.loop.is_running():
                    self.loop.quit()
        else:
            self.error_occurred = True
            self.should_exit = True
            if self.loop and self.loop.is_running():
                self.loop.quit()

    def shutdown(self, signum=None, frame=None):
        print("Shutting down...")
        self.should_exit = True

        try:
            if self.pipeline:
                print("Stopping pipeline...")
                self.pipeline.set_state(Gst.State.PAUSED)
                GLib.usleep(100000)

                self.pipeline.set_state(Gst.State.READY)
                GLib.usleep(100000)

                self.pipeline.set_state(Gst.State.NULL)
                print("Pipeline set to NULL.")
        except Exception as e:
            print(f"Pipeline shutdown error: {e}", file=sys.stderr)

        try:
            if self.display_process:
                print("Terminating display process...")
                self.display_process.terminate()
                self.display_process.join(timeout=5)  # Add timeout
                if self.display_process.is_alive():
                    self.display_process.kill()
        except Exception as e:
            print(f"Display process termination error: {e}", file=sys.stderr)

        try:
            if self.loop and self.loop.is_running():
                print("Quitting main loop...")
                self.loop.quit()
        except Exception as e:
            print(f"Loop shutdown error: {e}", file=sys.stderr)

        print("Shutdown complete.")

    def get_pipeline_string(self):
        # This is a placeholder function that should be overridden by the child class
        return ""

    def dump_dot_file(self):
        print("Dumping dot file...")
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, "pipeline")
        return False

    def run(self):
        # Add a watch for messages on the pipeline's bus
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.bus_call, self.loop)

        # Connect pad probe to the identity element
        if not self.options_menu.disable_callback:
            identity = self.pipeline.get_by_name("identity_callback")
            if identity is None:
                print("Warning: identity_callback element not found, add <identity name=identity_callback> in your pipeline where you want the callback to be called.")
            else:
                identity_pad = identity.get_static_pad("src")
                identity_pad.add_probe(Gst.PadProbeType.BUFFER, self.app_callback, self.user_data)

        hailo_display = self.pipeline.get_by_name("hailo_display")
        if hailo_display is None:
            print("Warning: hailo_display element not found, add <fpsdisplaysink name=hailo_display> to your pipeline to support fps display.")

        # Disable QoS to prevent frame drops
        disable_qos(self.pipeline)

        # Start a subprocess to run the display_user_data_frame function
        if self.options_menu.use_frame:
            self.display_process = multiprocessing.Process(target=display_user_data_frame, args=(self.user_data,))
            self.display_process.start()

        # Set the pipeline to PAUSED to ensure elements are initialized
        self.pipeline.set_state(Gst.State.PAUSED)

        # Set pipeline latency
        new_latency = self.pipeline_latency * Gst.MSECOND
        self.pipeline.set_latency(new_latency)

        # Set pipeline to PLAYING state
        self.pipeline.set_state(Gst.State.PLAYING)

        # Dump dot file
        if self.options_menu.dump_dot:
            GLib.timeout_add_seconds(3, self.dump_dot_file)

        # Add a timeout to check for exit condition
        def check_exit():
            if self.should_exit:
                if self.loop and self.loop.is_running():
                    self.loop.quit()
                return False  # Remove this timeout
            return True  # Continue checking

        GLib.timeout_add(100, check_exit)  # Check every 100ms

        # Run the GLib event loop
        try:
            self.loop.run()
        except KeyboardInterrupt:
            print("Interrupted by user")
            self.should_exit = True
        except Exception as e:
            print(f"Error in main loop: {e}", file=sys.stderr)
            self.should_exit = True

        # Clean up
        try:
            self.user_data.running = False
            if self.pipeline:
                self.pipeline.set_state(Gst.State.NULL)

            if self.display_process and self.display_process.is_alive():
                print("Terminating display process...")
                self.display_process.terminate()
                self.display_process.join(timeout=5)
                if self.display_process.is_alive():
                    self.display_process.kill()
                    
            for t in self.threads:
                t.join()
        except Exception as e:
            print(f"Error during cleanup: {e}", file=sys.stderr)
        finally:
            print("Exiting process.")
            if self.error_occurred:
                print("Exiting with error...", file=sys.stderr)
                sys.exit(1)
            else:
                print("Exiting normally...")
                sys.exit(0)


def disable_qos(pipeline):
    """
    Iterate through all elements in the given GStreamer pipeline and set the qos property to False
    where applicable.
    """
    if not isinstance(pipeline, Gst.Pipeline):
        print("The provided object is not a GStreamer Pipeline")
        return

    it = pipeline.iterate_elements()
    while True:
        result, element = it.next()
        if result != Gst.IteratorResult.OK:
            break

        if 'qos' in GObject.list_properties(element):
            element.set_property('qos', False)
            print(f"Set qos to False for {element.get_name()}")

def display_user_data_frame(user_data: app_callback_class):
    while user_data.running:
        frame = user_data.get_frame()
        if frame is not None:
            cv2.imshow("User Frame", frame)
        cv2.waitKey(1)
    cv2.destroyAllWindows()