import os
import json
import paho.mqtt.client as mqtt
import logging
import time
import threading
from logging_config import get_logger

logger = get_logger(__name__)

class MqttCommandListener:
    def __init__(self, pipeline_manager, user_data, video_stream_manager, mqtt_client, pi_id):
        self.logger = logging.getLogger(__name__)
        self.pipeline_manager = pipeline_manager
        self.user_data = user_data
        self.video_stream_manager = video_stream_manager

        self.client = mqtt_client
        self.pi_id = pi_id
        
        self.command_topic = f"vision/{self.pi_id}/command/request"
        self.response_topic = f"vision/{self.pi_id}/command/response"

        self.camera_list_topic = f"vision/{self.pi_id}/cameras/active_list"
        
        self.client.on_message = self.on_message
        self.client.on_connect = self.on_connect
        
    '''def subscribe_to_commands(self):
        if self.client and self.client.is_connected():
            self.client.subscribe(self.command_topic)
            self.logger.info(f"Command Listener: Subscribed to topic '{self.command_topic}'")
        else:
            self.logger.error("Command Listener: Cannot subscribe, MQTT client is not connected.")'''

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.logger.info("Command Listener: MQTT Client Connected. Subscribing to commands...")
            client.subscribe(self.command_topic)
            self.logger.info(f"Command Listener: Successfully subscribed to topic '{self.command_topic}'")
        else:
            self.logger.error(f"Command Listener: Failed to connect, return code {rc}")

    def on_message(self, client, userdata, msg):
        if msg.topic != self.command_topic:
            return

        self.logger.info(f"Command Listener: Received command on topic '{msg.topic}'")
        status = "failed"
        error_message = ""
        command = "unknown"
        success = False

        try:
            data = json.loads(msg.payload.decode())
            command = data.get("command")
            payload = data.get("payload", {})
            
            self.logger.info(f"Executing command: '{command}' with payload: {payload}")

            if command == "start_pipeline":
                sources = payload.get("sources")
                if sources: 
                    if isinstance(sources, dict):
                        custom_camera_names = list(sources.keys())
                        rtsp_urls = list(sources.values())
                        success = self.pipeline_manager.start_pipeline(rtsp_urls, custom_camera_names=custom_camera_names, on_started_callback=self.wait_and_publish_active_cameras)
                    elif isinstance(sources, list):
                        success = self.pipeline_manager.start_pipeline(sources, on_started_callback=self.wait_and_publish_active_cameras)
            
                    
            
            elif command == "stop_pipeline":
                success = self.pipeline_manager.stop_pipeline()

                if success:
                    empty_camera_info = {
                        "cameras": [],
                        "total": 0,
                        "timestamp": time.time(),
                        "status": "pipeline_stopped"
                    }
                    self.client.publish(self.camera_list_topic, json.dumps(empty_camera_info), qos=1, retain=True)
                    self.logger.info(f"Published empty camera list (pipeline stopped)")

            elif command == "request_snapshot":
                camera_id = payload.get("camera_id")
                if camera_id:
                    self.video_stream_manager.handle_snapshot_request(camera_id)
                    success = True
            elif command == "set_zone":
                if all(k in payload for k in ["camera_id", "zone", "top_left", "bottom_right"]):
                    success = self.user_data.create_or_update_zone(**payload)
            elif command == "delete_zone":
                if all(k in payload for k in ["camera_id", "zone"]):
                    success = self.user_data.delete_zone(**payload)
            elif command == "reset_zone_counts":
                 if all(k in payload for k in ["camera_id", "zone"]):
                    success = self.user_data.reset_zone_counts(**payload)
            elif command == "set_line":
                if all(k in payload for k in ["camera_id", "line_name", "start", "end"]):
                    success = self.user_data.create_or_update_line(**payload)
            elif command == "delete_line":
                if all(k in payload for k in ["camera_id", "line_name"]):
                    success = self.user_data.delete_line(**payload)
            elif command == "get_active_cameras":
                self.publish_active_cameras()
                success = True
            elif command == "reset_line_counts":
                if all(k in payload for k in ["camera_id", "line_name"]):
                    success = self.user_data.reset_line_counts(**payload)
            else:
                error_message = f"Unknown command: {command}"
                self.logger.warning(error_message)

            status = "success" if success else "failed"

        except json.JSONDecodeError:
            error_message = "Could not decode JSON from command message."
            self.logger.error(error_message)
        except Exception as e:
            error_message = f"Error processing command '{command}': {str(e)}"
            self.logger.error(error_message, exc_info=True)
        response_payload = {"command": command, "status": status, "error": error_message}
        self.client.publish(self.response_topic, json.dumps(response_payload), qos=1)
        self.logger.info(f"Published response to '{self.response_topic}': {response_payload}")

    def wait_and_publish_active_cameras(self, pipeline_manager_instance):
        self.logger.info("Pipeline has started, waiting for camera sources to initialize...")
        
        if hasattr(pipeline_manager_instance, 'camera_names'):
            expected_cameras = pipeline_manager_instance.camera_names
        else:
            expected_cameras = [f"camera{i+1}" for i in range(len(pipeline_manager_instance.video_sources))]
        expected_count = len(expected_cameras)
        
        timeout_seconds = 10 
        start_time = time.time()


        while time.time() - start_time < timeout_seconds:
            with self.user_data.lock:
                current_cameras = [cam_id for cam_id in self.user_data.data.keys() if cam_id in expected_cameras]
                current_count = len(current_cameras)
                
            if current_count >= expected_count:
                self.logger.info(f"All {current_count} camera sources are initialized. Publishing camera list.")
                self.publish_active_cameras()
                return
            time.sleep(0.5)
        
        self.logger.warning(f"Timeout reached while waiting for {expected_count} sources. Publishing current list of {current_count} cameras.")
        self.publish_active_cameras()
    
    def publish_active_cameras(self):
        if self.pipeline_manager.is_running() and hasattr(self.pipeline_manager, 'video_sources'):
            if hasattr(self.pipeline_manager, 'camera_names'):
                camera_names = self.pipeline_manager.camera_names
            else:
                camera_names = [f"camera{i+1}" for i in range(len(self.pipeline_manager.video_sources))]
            camera_info = {
                "active_cameras": camera_names,
                "active_camera_for_ui": camera_names[0] if camera_names else None,
                "total": len(camera_names),
                "timestamp": time.time(),
                "status": "active"
            }
            self.logger.info(f"Publishing {len(camera_names)} active cameras: {camera_names}")
        else:
            camera_info = {
                "active_cameras": [],
                "active_camera_for_ui": None,
                "total": 0,
                "timestamp": time.time(),
                "status": "pipeline_stopped"
            }
        self.client.publish(self.camera_list_topic, json.dumps(camera_info), qos=1, retain=True)
        self.logger.info(f"Published active camera info to '{self.camera_list_topic}': {camera_info}")

    def stop(self):
        self.logger.info("Command Listener: Stopping..")
        if self.client:
            self.client.on_message = None
    

