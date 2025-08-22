import gi
import logging
import sys
import os
import signal
from pathlib import Path
import json

# Ensure GStreamer is properly initialized
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

# Initialize GStreamer
Gst.init(None)

from flask import Flask
from flask_socketio import SocketIO

# Import custom modules
from config import SERVER_HOST, SERVER_PORT, DEBUG_MODE, CORS_ALLOWED_ORIGINS, SOCKETIO_ASYNC_MODE, load_config, get_active_sources
from zone_counter import MultiSourceZoneVisitorCounter
from gstreamer_pipeline import PipelineManager
from video_stream import VideoStreamManager
from socketio_handlers import register_socketio_handlers
from web_routes import register_routes

logging.disable(logging.CRITICAL)  # Disable verbose logging from libraries

# Global components for cleanup
components = None
app_instance = None
socketio_instance = None

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger = logging.getLogger(__name__)
    logger.info(f"Received signal {signum}, shutting down...")
    
    if components and components.get('pipeline_manager'):
        try:
            components['pipeline_manager'].stop_pipeline()
        except Exception as e:
            logger.error(f"Error stopping pipeline: {e}")
    
    sys.exit(0)

def setup_logging():
    """Setup application logging."""
    logging.basicConfig(
        level=logging.INFO if not DEBUG_MODE else logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)


def create_app():
    """
    Create and configure Flask application with all components.
    
    Returns:
        Tuple of (Flask app, SocketIO instance, components dict)
    """
    # Create Flask app
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'your-secret-key-here'  # Change in production
    
    # Initialize SocketIO
    socketio = SocketIO(
        app, 
        cors_allowed_origins="*",  # CORS_ALLOWED_ORIGINS,
        async_mode=SOCKETIO_ASYNC_MODE
    )
    
    # Initialize core components
    user_data = MultiSourceZoneVisitorCounter()
    frame_buffers = {}  # Global frame buffer for all camera sources
    
    # Initialize managers
    pipeline_manager = PipelineManager(user_data, frame_buffers, socketio)
    video_stream_manager = VideoStreamManager(frame_buffers, user_data)
    
    try:
        config = load_config()
        active_sources = config.get("video_sources", [])
        if active_sources:
            logging.info(f"Loaded active video sources from config: {active_sources}")
            pipeline_manager.start_pipeline(active_sources)
    except Exception as e:
        logging.warning(f"Failed to load config or start pipeline: {e}")
    
    # Register SocketIO handlers
    register_socketio_handlers(socketio, user_data, pipeline_manager)
    
    # Register web routes
    register_routes(app, user_data, pipeline_manager, video_stream_manager)
    
    components = {
        'user_data': user_data,
        'frame_buffers': frame_buffers,
        'pipeline_manager': pipeline_manager,
        'video_stream_manager': video_stream_manager
        
    }
    
    return app, socketio, components


def main():
    """Main application entry point."""
    global components, app_instance, socketio_instance
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger = setup_logging()
    logger.info("Starting Multi-Source Zone Visitor Counter Application")
    
    try:
        # Create application
        app_instance, socketio_instance, components = create_app()
        
        logger.info(f"Application created successfully")
        logger.info(f"Available cameras: {list(components['user_data'].data.keys())}")
        logger.info(f"Active camera: {components['user_data'].active_camera}")

        if components['user_data'].lines:
            for cam, lines in components['user_data'].lines.items():
                logger.info(f"Camera {cam} has {len(lines)} lines: {list(lines.keys())}")
        else:
            logger.info("No lines configured for any camera")
        
        # Start the server
        logger.info(f"Starting server on {SERVER_HOST}:{SERVER_PORT}")
        socketio_instance.run(
            app_instance, 
            host=SERVER_HOST, 
            port=SERVER_PORT, 
            debug=DEBUG_MODE,
            allow_unsafe_werkzeug=True  # For development only
        )
        
    except KeyboardInterrupt:
        logger.info("Application interrupted by user")
    except Exception as e:
        logger.error(f"Application failed to start: {e}", exc_info=True)
        return 1
    finally:
        # Cleanup
        logger.info("Shutting down application")
        if components and components.get('pipeline_manager'):
            try:
                components['pipeline_manager'].stop_pipeline()
            except Exception as e:
                logger.error(f"Error during pipeline cleanup: {e}")
    
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except Exception as e:
        logging.error(f"Unhandled fatal error in main: {e}", exc_info=True)
        sys.exit(1)