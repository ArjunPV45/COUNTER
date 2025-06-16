"""
Configuration module for multi-source zone visitor counter application.
Contains all configuration constants and settings.
"""

import os

# Model configurations
MODEL_PATHS = {
    "yolov6n": "/home/raspberry5/hailo-rpi5-examples/resources/yolov8s_h8l.hef",
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
CORS_ALLOWED_ORIGINS = "*"#["http://localhost:3000"]

# Socket.IO settings
SOCKETIO_ASYNC_MODE = 'threading'

# Image encoding settings
JPEG_QUALITY = 80

# Template file
TEMPLATE_FILE = "index3.html"

STABILITY_THRESHOLD = 3
DEBOUNCE_TIME = 2.0
BOUNDARY_BUFFER = 20
POSITION_HISTORY_SIZE=5
