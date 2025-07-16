"""
Socket.IO event handlers module.
Contains all WebSocket event handlers for real-time communication.
"""

from flask_socketio import SocketIO, emit

def register_socketio_handlers(socketio: SocketIO, user_data):
    """
    Register all Socket.IO event handlers.

    Args:
        socketio: Flask-SocketIO instance
        user_data: MultiSourceZoneVisitorCounter instance
    """

    @socketio.on('request_pipeline_status')
    def handle_pipeline_status_request():
        status = {
            "running": pipeline_manager.is_running(),
            "sources": pipeline_manager.video_sources if pipeline_manager.is_running() else []
        }
        emit('pipeline_status_update', status)

    @socketio.on("set_zone")
    def handle_set_zone(data):
        print(f"[Socket.IO] received data: {data}")
        """Handle zone updates from the UI with improved validation."""
        required_keys = ["camera_id", "zone", "top_left", "bottom_right"]
        for key in required_keys:
            if key not in data:
                emit("error", {"message": f"Missing required key: {key}"})
                return

        camera_id = data["camera_id"]
        zone = data["zone"]
        top_left = data["top_left"]
        bottom_right = data["bottom_right"]

        success = user_data.create_or_update_zone(camera_id, zone, top_left, bottom_right)
        
        if success:
            zone_data = user_data.data[camera_id]["zones"][zone]
            print(f"[Socket.IO] Zone updated - Camera: {camera_id}, Zone: {zone}")
            print(f"[Socket.IO] Zone data: {zone_data}")
            
            emit("zone_updated", {
                "data": user_data.data,
                "camera": camera_id,
                "zone": zone,
                "message": "Zone successfully updated"
            })
        else:
            print(f"[Socket.IO] Failed to update zone {zone} for camera {camera_id}")
            emit("error", {"message": "Invalid zone coordinates"})

    @socketio.on("reset_zone_counts")
    def handle_reset_zone_counts(data):
        """Handle zone count reset from UI."""
        camera_id = data.get("camera_id")
        zone = data.get("zone")

        if not camera_id or not zone:
            emit("error", {"message": "Missing camera_id or zone"})
            return

        success = user_data.reset_zone_counts(camera_id, zone)
        if success:
            emit("count_reset", {
                "data": user_data.data,
                "camera": camera_id,
                "zone": zone
            })
        else:
            emit("error", {"message": f"Zone {zone} in camera {camera_id} not found"})

    @socketio.on("set_active_camera")
    def handle_set_active_camera(data):
        """Handle camera switch from UI."""
        camera_id = data.get("camera_id")

        if not camera_id:
            emit("error", {"message": "Missing camera_id"})
            return

        success = user_data.set_active_camera(camera_id)
        if success:
            emit("camera_changed", {
                "active_camera": user_data.active_camera,
                "data": user_data.data,
                "cameras": list(user_data.data.keys())
            })
        else:
            emit("error", {"message": f"Camera {camera_id} not found"})

    @socketio.on("delete_zone")
    def handle_delete_zone(data):
        """Handle zone deletion from UI."""
        camera_id = data.get("camera_id")
        zone = data.get("zone")

        if not camera_id or not zone:
            emit("error", {"message": "Missing camera_id or zone"})
            return

        success = user_data.delete_zone(camera_id, zone)
        if success:
            emit("zone_deleted", {
                "data": user_data.data,
                "camera": camera_id,
                "zone": zone
            })
        else:
            emit("error", {"message": f"Zone {zone} in camera {camera_id} not found"})

    @socketio.on("connect")
    def handle_connect():
        """Handle client connection."""
        print("Client connected")
        emit("initial_data", {
            "data": user_data.data,
            "active_camera": user_data.active_camera,
            "cameras": list(user_data.data.keys())
        })

    @socketio.on("disconnect")
    def handle_disconnect():
        """Handle client disconnection."""
        print("Client disconnected")

    @socketio.on("get_current_data")
    def handle_get_current_data():
        """Send current data to requesting client."""
        emit("current_data", {
            "data": user_data.data,
            "active_camera": user_data.active_camera,
            "cameras": list(user_data.data.keys())
        })

    return socketio
