"""
Configuration module for multi-source zone visitor counter application.
Contains all configuration constants and settings.
"""

import json
import os

CONFIG_FILE = "cameras_zones.json"


def load_config(filename=CONFIG_FILE):
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                content = f.read().strip()
                if not content:  # Empty file
                    return {}
                return json.loads(content)
        except Exception as e:
            print(f"Error loading config from {filename}: {e}")
            return {}
    return {}

def save_user_data(user_data, filename=CONFIG_FILE):
    """
    Save user_data.data and retain active_sources if present.
    """
    config = load_config(filename)
    config["camera_data"] = user_data.data  # main zone data
    with open(filename, "w") as f:
        json.dump(config, f, indent=4)

def load_user_data(user_data, file_name=CONFIG_FILE):
    config = load_config(file_name)
    config_data = config.get("camera_data", {})

def save_active_sources(active_sources, filename=CONFIG_FILE):
    """
    Save RTSP camera sources to config file.
    """
    config = load_config(filename) or {}
    config["video_sources"] = active_sources
    with open(filename, "w") as f:
        json.dump(config, f, indent=4)

def get_active_sources(filename=CONFIG_FILE):
    """
    Load and return list of previously saved RTSP sources.
    """
    config = load_config(filename)
    return config.get("video_sources", [])


# Model configurations
MODEL_PATHS = {
    "yolov8s": "../resources/yolov8s_h8l.hef",
}

# File paths
HISTORY_FILE = "multisource1.json"

# Default frame dimensions
DEFAULT_FRAME_HEIGHT = 1080
DEFAULT_FRAME_WIDTH = 1920

# Default zone configuration
DEFAULT_ZONE_CONFIG = {
    "zone1": {
        "top_left": [640, 360], 
        "bottom_right": [1280, 700], 
        "in_count": 0, 
        "out_count": 0, 
        "inside_ids": [], 
        "history": []
    }
}

# Server configuration
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 5000
DEBUG_MODE = False

# CORS settings
CORS_ALLOWED_ORIGINS = "*" #["http://localhost:3000"]

# Socket.IO settings
SOCKETIO_ASYNC_MODE = 'threading'

# Image encoding settings
JPEG_QUALITY = 100

# Template file
TEMPLATE_FILE = "index3.html"

STABILITY_THRESHOLD = 3
DEBOUNCE_TIME = 2.0
BOUNDARY_BUFFER = 20
POSITION_HISTORY_SIZE=5


