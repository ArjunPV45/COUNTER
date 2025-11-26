import gi
import logging
import sys
import os
import signal
import threading
import time
import json
from dotenv import load_dotenv
import paho.mqtt.client as mqtt
 
from logging_config import setup_logging, get_logger
from health_monitor import HealthMonitor
 
load_dotenv()
 
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
Gst.init(None)
 
from config import DEBUG_MODE, load_zone_line_config
from zone_counter import MultiSourceZoneVisitorCounter
from gstreamer_pipeline import PipelineManager
from video_stream import VideoStreamManager
from command_listener import MqttCommandListener
 
from database_config import is_db_connected
from database_writer import get_database_writer
from pi_status_monitor import get_status_monitor
 
components = {}
main_loop = GLib.MainLoop()
 
logger = setup_logging()
 
def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, initiating shutdown...")
    if main_loop.is_running():
        main_loop.quit()
 
def create_mqtt_client():
    logger = logging.getLogger(__name__)
    broker_url = os.getenv("MQTT_BROKER_URL")
    broker_port = int(os.getenv("MQTT_BROKER_PORT", 8883))
    username = os.getenv("MQTT_USERNAME")
    password = os.getenv("MQTT_PASSWORD")
 
    if not all([broker_url, username, password]):
        logger.error("MQTT credentials not set. MQTT features disabled.")
        return None
 
    client = mqtt.Client()
   
    client.username_pw_set(username, password)
    client.tls_set(tls_version=mqtt.ssl.PROTOCOL_TLS)
   
    try:
        logger.info(f"Connecting to MQTT Broker at {broker_url}...")
        client.connect(broker_url, broker_port, 60)
        client.loop_start()
        logger.info("Connected to MQTT Broker.")
        return client
    except Exception as e:
        logger.error(f"Failed to connect to MQTT Broker: {e}")
        return None
 
def run_counts_pusher(user_data, mqtt_client, stop_event, interval=2.0):
    logger_pusher = logging.getLogger(f"{__name__}.counts_pusher")
    pi_id = os.getenv("PI_UNIQUE_ID", "pi-default")
 
    logger_pusher.info("Counts pusher thread started.")
 
    while not stop_event.is_set():
        try:
            with user_data.lock:
                all_data = user_data.data
            for camera_id, camera_data in all_data.items():
                counts_payload = {}
                for zone_name, zone_data in camera_data.get("zones", {}).items():
                    counts_payload[zone_name] = {"in": zone_data.get("in_count", 0), "out": zone_data.get("out_count", 0)}
                for line_name, line_data in camera_data.get("lines", {}).items():
                    counts_payload[line_name] = {"in": line_data.get("in_count", 0), "out": line_data.get("out_count", 0)}
                if counts_payload:
                    topic = f"vision/{pi_id}/{camera_id}/counts/update"
                    mqtt_client.publish(topic, json.dumps(counts_payload), qos=1)
                    logger_pusher.debug(f"Published counts for {camera_id}")
        except Exception as e:
            logger_pusher.error(f"Error in counts pusher thread: {e}")
        time.sleep(interval)
    logger_pusher.info("Counts pusher thread has stopped.")
 
def run_zone_data_pusher(user_data, mqtt_client, stop_event, interval=5.0):
    logger_pusher = logging.getLogger(f"{__name__}.zone_data_pusher")
    pi_id = os.getenv("PI_UNIQUE_ID", "pi-default")
    logger_pusher.info("Zone data pusher thread started.")
    while not stop_event.is_set():
        try:
            with user_data.lock:
                all_data = user_data.data
            for camera_id, camera_data in all_data.items():
                zone_payload = camera_data.get("zones", {})
                if zone_payload:
                    topic = f"vision/{pi_id}/{camera_id}/zones/full_data"
                    mqtt_client.publish(topic, json.dumps(zone_payload), qos=1)
                    logger_pusher.debug(f"Published zone data for {camera_id}")
        except Exception as e:
            logger_pusher.error(f"Error in zone data pusher thread: {e}")
        time.sleep(interval)
    logger_pusher.info("Zone data pusher thread has stopped.")
 
 
def run_line_data_pusher(user_data, mqtt_client, stop_event, interval=5.0):
    logger_pusher = logging.getLogger(f"{__name__}.line_data_pusher")
    pi_id = os.getenv("PI_UNIQUE_ID", "pi-default")
    logger_pusher.info("Line data pusher thread started.")
    while not stop_event.is_set():
        try:
            with user_data.lock:
                all_data = user_data.data
            for camera_id, camera_data in all_data.items():
                line_payload = camera_data.get("lines", {})
                if line_payload:
                    topic = f"vision/{pi_id}/{camera_id}/lines/full_data"
                    mqtt_client.publish(topic, json.dumps(line_payload), qos=1)
                    logger_pusher.debug(f"Published line data for {camera_id}")
        except Exception as e:
            logger_pusher.error(f"Error in line data pusher thread: {e}")
        time.sleep(interval)
    logger_pusher.info("Line data pusher thread has stopped.")
 
def run_camera_status_pusher(pipeline_manager, mqtt_client, pi_id, stop_event, interval=5.0):
    logger_pusher = logging.getLogger(f"{__name__}.camera_status_pusher")
    logger_pusher.info("Camera status pusher thread started.")
   
    camera_list_topic = f"vision/{pi_id}/cameras/active_list"
   
    while not stop_event.is_set():
        try:
            if pipeline_manager.is_running() and hasattr(pipeline_manager, 'video_sources'):
                if hasattr(pipeline_manager, 'camera_names') and pipeline_manager.camera_names:
                    expected_cameras = pipeline_manager.camera_names
                else:
                    expected_cameras = [f"camera{i+1}" for i in range(len(pipeline_manager.video_sources))]
               
                camera_info = {
                    "active_cameras": expected_cameras,
                    "active_camera_for_ui": expected_cameras[0] if expected_cameras else None,
                    "total": len(expected_cameras),
                    "timestamp": time.time(),
                    "status": "active"
                }
               
                logger_pusher.debug(f"Publishing {len(expected_cameras)} active cameras")
            else:
                camera_info = {
                    "active_cameras": [],
                    "active_camera_for_ui": None,
                    "total": 0,
                    "timestamp": time.time(),
                    "status": "pipeline_stopped"
                }
               
                logger_pusher.debug("Publishing empty camera list (pipeline stopped)")
           
           
            mqtt_client.publish(camera_list_topic, json.dumps(camera_info), qos=1, retain=True)
           
        except Exception as e:
            logger_pusher.error(f"Error in camera status pusher: {e}")
       
        time.sleep(interval)
   
    logger_pusher.info("Camera status pusher thread has stopped.")
 
 
def main():
    global components
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
   
   
    try:
        logger.info("Starting Pi Vision Processing Backend (Event-Driven MQTT Architecture)")
        logger.info(f"Device ID: {os.getenv('PI_UNIQUE_ID', 'pi-default')}")
        logger.info(f" Debug Mode:{DEBUG_MODE}"
        )
        mqtt_client = create_mqtt_client()
        if not mqtt_client:
            return 1
       
        pi_id = os.getenv("PI_UNIQUE_ID", "pi-default")
       
        logger.info("Initializing core components...")
        user_data = MultiSourceZoneVisitorCounter(mqtt_client=mqtt_client, pi_id=pi_id)
       
        logger.info("Checking database connection...")
        if is_db_connected():
            try:
                db_writer = get_database_writer(batch_size=50, batch_interval=2.0)
                db_writer.start()
                user_data.db_enabled = True
                components['db_writer'] = db_writer
                logger.info("Database writer started and enabled in user data.")
 
                status_monitor = get_status_monitor(pi_id, heartbeat_interval=30.0)
                status_monitor.start()
                components['status_monitor'] = status_monitor
                logger.info("PiStatusMonitor started.")
            except Exception as e:
                logger.error(f"Failed to start database writer: {e}")
                logger.warning("Continuing without database writer.")
                user_data.db_enabled = False
        else:
            logger.warning("Database not connected. Continuing without database writer.")
            user_data.db_enabled = False
 
        frame_buffers = {}
       
        pipeline_manager = PipelineManager(user_data, frame_buffers)
        video_stream_manager = VideoStreamManager(frame_buffers, user_data, mqtt_client=mqtt_client, pi_id=pi_id)
        mqtt_listener = MqttCommandListener(pipeline_manager, user_data, video_stream_manager, mqtt_client=mqtt_client, pi_id=pi_id)
       
        health_monitor = HealthMonitor(user_data, pipeline_manager, port=8080)
        health_monitor.start()
 
        logger.info("Core components initialized")
 
        load_zone_line_config(user_data)
        logger.info("Loaded zone/line configuration from disk")
 
        components.update({
            'user_data': user_data,
            'pipeline_manager': pipeline_manager,
            'video_stream_manager': video_stream_manager,
            'mqtt_client': mqtt_client,
            'health_monitor': health_monitor
        })
       
        logger.info("Starting MQTT pusher threads...")
 
        video_stream_manager.start_snapshot_pusher(interval=0.5)
       
        counts_pusher_stop_event = threading.Event()
        counts_pusher_thread = threading.Thread(
            target=run_counts_pusher,
            args=(user_data, mqtt_client, counts_pusher_stop_event),
            daemon=True
        )
        counts_pusher_thread.start()
       
        components['counts_pusher_stop_event'] = counts_pusher_stop_event
 
       
       
        camera_status_pusher_stop_event = threading.Event()
        camera_status_pusher_thread = threading.Thread(
            target=run_camera_status_pusher,
            args=(pipeline_manager, mqtt_client, pi_id, camera_status_pusher_stop_event, 5.0),
            daemon=True
        )
        camera_status_pusher_thread.start()
        components['camera_status_pusher_stop_event'] = camera_status_pusher_stop_event
        logger.info("Started camera status pusher thread (5s interval)")
 
 
        logger.info("Core components initialized and running. Starting GStreamer MainLoop...")
        logger.info("System ready. Waiting for commands via MQTT.")
       
        main_loop.run()
   
    except KeyboardInterrupt:
        logger.info("Application interrupted by user (Ctrl+C).")
   
    except Exception as e:
        logger.critical(f"An unhandled exception occurred: {e}", exc_info=True)
        return 1
       
    finally:
        logger.info("--- Starting Graceful Shutdown ---")
 
        if 'health_monitor' in components:
            components['health_monitor'].stop()
 
        for key in ['counts_pusher_stop_event']:
            if key in components:
                components[key].set()
                logger.info(f"Stopped {key.replace('_stop_event', '')}")
 
        if 'video_stream_manager' in components:
            components['video_stream_manager'].stop_snapshot_pusher()
            logger.info("Stopped snapshot pusher")
 
        if 'db_writer' in components:
            try:
                db_writer = components['db_writer']
                stats = db_writer.get_stats()
                logger.info(f"Database writer stats: {stats}")
                db_writer.stop()
                logger.info("Database writer stopped.")
            except Exception as e:
                logger.error(f"Error stopping database writer: {e}")
 
        if 'status_monitor' in components:
            try:
                components['status_monitor'].stop()
                logger.info("PiStatusMonitor stopped.")
            except Exception as e:
                logger.error(f"Error stopping PiStatusMonitor: {e}")
 
        if 'pipeline_manager' in components:
            components['pipeline_manager'].stop_pipeline()
            logger.info("Stopped GStreamer pipeline")
 
        if 'mqtt_client' in components:
            mqtt_client = components['mqtt_client']
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
            logger.info("MQTT client disconnected.")
 
        if 'camera_status_pusher_stop_event' in components:
            components['camera_status_pusher_stop_event'].set()
            logger.info("Stopped camera_status_pusher")
 
        if main_loop.is_running():
            main_loop.quit()
           
        logger.info("--- Shutdown Complete ---")
   
    return 0
 
if __name__ == "__main__":
    sys.exit(main())
