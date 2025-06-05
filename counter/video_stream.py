"""
Video streaming module for handling video feeds and snapshots.
Manages frame generation and encoding for web streaming.
"""

import cv2
import numpy as np
from flask import Response
from config import JPEG_QUALITY


class VideoStreamManager:
    """Manager class for handling video streaming operations."""
    
    def __init__(self, frame_buffers, user_data):
        self.frame_buffers = frame_buffers
        self.user_data = user_data
    
    def generate_frames(self, camera_id):
        """
        Generate video stream frames for the specified camera.
        
        Args:
            camera_id: ID of the camera to stream from
            
        Yields:
            Video frame bytes in multipart format
        """
        while True:
            if camera_id in self.frame_buffers and self.frame_buffers[camera_id] is not None:
                frame = self.frame_buffers[camera_id]
                _, buffer = cv2.imencode(".jpg", frame)
                frame_bytes = buffer.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            else:
                # If camera feed not available, yield a blank frame
                blank_frame = self._create_blank_frame(camera_id)
                _, buffer = cv2.imencode(".jpg", blank_frame)
                frame_bytes = buffer.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
    
    def get_video_feed_response(self, camera_id):
        """
        Get Flask Response object for video streaming.
        
        Args:
            camera_id: ID of the camera to stream from
            
        Returns:
            Flask Response object with video stream
        """
        return Response(
            self.generate_frames(camera_id),
            mimetype="multipart/x-mixed-replace; boundary=frame"
        )
    
    def get_snapshot(self, camera_id):
        """
        Get a snapshot from the specified camera.
        
        Args:
            camera_id: ID of the camera to get snapshot from
            
        Returns:
            Tuple of (success: bool, data: bytes or error_message: str)
        """
        # Validate camera_id
        if camera_id not in self.user_data.data:
            return False, f"Camera {camera_id} not found"
        
        # Check if frame_buffers is properly initialized
        if not hasattr(self.frame_buffers, 'get'):
            return False, "Snapshot system not ready"
        
        frame = self.frame_buffers.get(camera_id)
        if frame is None:
            return False, "No frame available for this camera"
        
        try:
            # Compress the image
            _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            return True, buffer.tobytes()
        except Exception as e:
            return False, f"Could not process snapshot: {e}"
    
    def _create_blank_frame(self, camera_id):
        """
        Create a blank frame with error message.
        
        Args:
            camera_id: ID of the camera (for error message)
            
        Returns:
            numpy array representing blank frame
        """
        blank_frame = np.zeros((480, 640, 3), np.uint8)
        cv2.putText(
            blank_frame, 
            f"Camera {camera_id} not available", 
            (50, 240), 
            cv2.FONT_HERSHEY_SIMPLEX, 
            1, 
            (255, 255, 255), 
            2
        )
        return blank_frame
    
    def is_camera_available(self, camera_id):
        """
        Check if a camera is available and has frames.
        
        Args:
            camera_id: ID of the camera to check
            
        Returns:
            bool: True if camera is available, False otherwise
        """
        return (camera_id in self.frame_buffers and 
                self.frame_buffers[camera_id] is not None)
    
    def get_available_cameras(self):
        """
        Get list of cameras that have active video feeds.
        
        Returns:
            List of camera IDs with active feeds
        """
        return [camera_id for camera_id in self.frame_buffers.keys() 
                if self.frame_buffers[camera_id] is not None]
