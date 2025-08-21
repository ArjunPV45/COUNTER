from flask import Flask, render_template, jsonify, request, Response
from video_stream import VideoStreamManager
from config import TEMPLATE_FILE, load_config, save_active_sources



def register_routes(app: Flask, user_data, pipeline_manager, video_stream_manager):
    """
    Register all Flask routes.

    Args:
        app: Flask application instance
        user_data: MultiSourceZoneVisitorCounter instance
        pipeline_manager: PipelineManager instance
        video_stream_manager: VideoStreamManager instance
    """

    @app.route("/")
    def index():
        """Serve the main application page."""
        return render_template(TEMPLATE_FILE)

    @app.route("/start_pipeline", methods=["POST"])
    def start_pipeline():
        """Start the GStreamer pipeline with video sources."""
        data = request.json
        if not data or "sources" not in data:
            return jsonify({"success": False, "message": "Missing 'sources' list"}), 400

        video_sources = data["sources"]
        if not isinstance(video_sources, list) or len(video_sources) == 0:
            return jsonify({"success": False, "message": "Sources must be a non-empty list"}), 400

        try:
            success = pipeline_manager.start_pipeline(video_sources)
            if success:
                config = load_config()
                config["video_sources"] = video_sources
                save_active_sources(video_sources)
                return jsonify({"success": True, "message": "Pipeline started with validated sources"}), 200
            else:
                return jsonify({"success": False, "message": "Failed to start pipeline - check RTSP sources"}), 400
        except Exception as e:
            return jsonify({"success": False, "message": f"Failed to start pipeline: {e}"}), 500

    @app.route("/stop_pipeline", methods=["POST"])
    def stop_pipeline():
        """Stop the GStreamer pipeline."""
        try:
            success = pipeline_manager.stop_pipeline()
            if success:
                return jsonify({"success": True, "message": "Pipeline stopped successfully"}), 200
            else:
                return jsonify({"success": False, "message": "Failed to stop pipeline"}), 500
        except Exception as e:
            return jsonify({"success": False, "message": f"Error stopping pipeline: {e}"}), 500

    @app.route("/pipeline_status")
    def pipeline_status():
        """Get the current pipeline status."""
        return jsonify({
            "running": pipeline_manager.is_running(),
            "sources": pipeline_manager.video_sources if pipeline_manager.is_running() else []
        })

    @app.route("/video_feed")
    def video_feed():
        """Stream video feed from specified camera."""
        camera_id = request.args.get("camera_id", user_data.active_camera)
        return video_stream_manager.get_video_feed_response(camera_id)

    @app.route("/get_snapshot")
    def get_snapshot():
        """Get a snapshot from the specified camera."""
        camera_id = request.args.get("camera_id", user_data.active_camera)
        success, data = video_stream_manager.get_snapshot(camera_id)

        if success:
            return Response(data, mimetype='image/jpeg')
        else:
            return jsonify({"error": data}), 404 if "not found" in data else 500

    @app.route("/get_cameras")
    def get_cameras():
        """Return list of available cameras."""
        return jsonify({
            "cameras": list(user_data.data.keys()),
            "active_camera": user_data.active_camera,
            "available_feeds": video_stream_manager.get_available_cameras(),
            "processing_count": len(pipeline_manager.video_sources)
        })

    @app.route("/get_zones")
    def get_zones():
        """Return zones for all cameras."""
        return jsonify({"data": user_data.data})

    @app.route("/api/camera/<camera_id>/zones", methods=["GET"])
    def get_camera_zones(camera_id):
        """Get zones for a specific camera."""
        if camera_id not in user_data.data:
            return jsonify({"error": f"Camera {camera_id} not found"}), 404

        return jsonify({
            "camera_id": camera_id,
            "zones": user_data.data[camera_id]["zones"]
        })

        success = user_data.create_or_update_zone(camera_id, zone, top_left, bottom_right)

        if success:
            return jsonify({
                "success": True,
                "message": f"Zone '{zone}' created/updated for camera {camera_id}",
                "zone_data": user_data.data[camera_id]["zones"][zone]
            }), 201
        else:
            return jsonify({"error": "Invalid zone coordinates"}), 400

    @app.route("/api/camera/<camera_id>/zones", methods=["POST"])
    def create_camera_zone(camera_id):
        """Create or update a zone for a specific camera."""
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400

        required_fields = ["zone", "top_left", "bottom_right"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        zone = data["zone"]
        top_left = data["top_left"]
        bottom_right = data["bottom_right"]

        success = user_data.create_or_update_zone(camera_id, zone, top_left, bottom_right)

        if success:
            return jsonify({
                "success": True,
                "message": f"Zone '{zone}' created/updated for camera {camera_id}",
                "zone_data": user_data.data[camera_id]["zones"][zone]
            }), 201
        else:
            return jsonify({"error": "Invalid zone coordinates"}), 400

    @app.route("/api/camera/<camera_id>/zones/<zone>", methods=["DELETE"])
    def delete_camera_zone(camera_id, zone):
        """Delete a specific zone from a camera."""
        success = user_data.delete_zone(camera_id, zone)

        if success:
            return jsonify({
                "success": True,
                "message": f"Zone '{zone}' deleted from camera {camera_id}"
            })
        else:
            return jsonify({"error": f"Zone {zone} not found in camera {camera_id}"}), 404

    @app.route("/api/camera/<camera_id>/zones/<zone>/reset", methods=["POST"])
    def reset_camera_zone_counts(camera_id, zone):
        """Reset counts for a specific zone."""
        success = user_data.reset_zone_counts(camera_id, zone)

        if success:
            return jsonify({
                "success": True,
                "message": f"Counts reset for zone '{zone}' in camera {camera_id}",
                "zone_data": user_data.data[camera_id]["zones"][zone]
            })
        else:
            return jsonify({"error": f"Zone {zone} not found in camera {camera_id}"}), 404

    @app.route("/get_counts", methods=["GET"])
    def get_counts():
        """
        Return only live in/out count data for each zone.
        Optional query param: ?camera_id=camera1
        """

        def extract_counts(zones):
            """Helper to return only in/out counts from zones dict"""
            return {
                zone_name: {
                    "in_count": zone_data.get("in_count", 0),
                    "out_count": zone_data.get("out_count", 0),
                    "history": zone_data.get("history", [])
                }
                for zone_name, zone_data in zones.items()
            }

        camera_id = request.args.get("camera_id")

        if camera_id:
            if camera_id not in user_data.data:
                return jsonify({"error": f"Camera {camera_id} not found"}), 404

            zone_counts = extract_counts(user_data.data[camera_id]["zones"])
            return jsonify({
                "camera_id": camera_id,
                "counts": zone_counts
            })

        all_counts = {
            cam_id: extract_counts(cam_data["zones"])
            for cam_id, cam_data in user_data.data.items()
        }

        return jsonify({
            "counts": all_counts
        })

    @app.route("/get_all_data", methods=["GET"])
    def get_all_data():
        """
        Return the complete data structure for all cameras or a specific one.
        Optional query param: ?camera_id=camera1
        """
        camera_id = request.args.get("camera_id")

        if camera_id:
            if camera_id not in user_data.data:
                return jsonify({"error": f"Camera {camera_id} not found"}), 404
            return jsonify({
                "camera_id": camera_id,
                "data": user_data.data[camera_id]
            })

        return jsonify({
            "data": user_data.data
        })

    @app.route("/api/camera/<camera_id>/lines", methods=["POST"])
    def create_camera_line(camera_id):
        """Create or update a line for a specific camera."""
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400

        required_fields = ["line", "start", "end"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400

        line = data["line"]
        start = data["start"]
        end = data["end"]

        success = user_data.create_or_update_line(camera_id, line, start, end)

        if success:
            return jsonify({
                "success": True,
                "message": f"Line '{line}' created/updated for camera {camera_id}",
                "line_data": user_data.lines[camera_id][line]
            }), 201
        else:
            return jsonify({"error": "Invalid line coordinates"}), 400

    
    @app.route("/api/camera/<camera_id>/lines", methods=["GET"])
    def get_camera_lines(camera_id):
        """Get all lines configured for a specific camera."""
        if camera_id not in user_data.lines:
            return jsonify({
                "camera_id": camera_id,
                "lines": {}
            })
        return jsonify({
            "camera_id": camera_id,
            "lines": user_data.lines[camera_id]
        })

    @app.route("/api/camera/<camera_id>/lines/<line>", methods=["DELETE"])
    def delete_camera_line(camera_id, line):
        """Delete a specific line from a camera."""
        success = user_data.delete_line(camera_id, line)

        if success:
            return jsonify({
                "success": True,
                "message": f"Line '{line}' deleted from camera {camera_id}"
            })
        else:
            return jsonify({"error": f"Line {line} not found in camera {camera_id}"}), 404

    @app.route("/get_line_counts", methods=["GET"])
    def get_line_counts():
        """
        Return live in/out counts for each line.
        Optional query param: ?camera_id=camera1
        """
        def extract_line_counts(lines):
            return {
                line_name: {
                    "in_count": line_data.get("in_count", 0),
                    "out_count": line_data.get("out_count", 0),
                    "history": line_data.get("history", [])
                }
                for line_name, line_data in lines.items()
            }

        camera_id = request.args.get("camera_id")

        if camera_id:
            if camera_id not in user_data.lines:
                return jsonify({
                    "camera_id": camera_id,
                    "line_counts": {camera_id: {}}
                 }) #"error": f"No lines found for camera {camera_id}"}), 404

            return jsonify({
                "camera_id": camera_id,
                "line_counts": {camera_id: extract_line_counts(user_data.lines[camera_id])}
            })

        all_line_counts = {}
        for cam_id, lines in user_data.lines.items():
            all_line_counts[cam_id] = extract_line_counts(lines)

            #cam_id: extract_line_counts(lines)
            #for cam_id, lines in user_data.lines.items()
        #}

        return jsonify({
            "line_counts": all_line_counts
        })


    @app.route("/health")
    def health_check():
        """Health check endpoint."""
        return jsonify({
            "status": "healthy",
            "pipeline_running": pipeline_manager.is_running(),
            "cameras_count": len(user_data.data),
            "active_camera": user_data.active_camera
        })

    @app.errorhandler(404)
    def not_found(error):
        """Handle 404 errors."""
        return jsonify({"error": "Resource not found"}), 404

    @app.errorhandler(500)
    def internal_error(error):
        """Handle 500 errors."""
        return jsonify({"error": "Internal server error"}), 500

    return app