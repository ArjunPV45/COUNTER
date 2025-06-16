# Multi-Source Zone Visitor Counter

A modular Python application for real-time person detection and zone-based visitor counting across multiple camera sources using Hailo AI acceleration on Raspberry Pi.

## 🏗️ Architecture Overview

The application has been refactored into separate modules for better maintainability and production deployment:

```
visitor-counter/
├── config.py                 # Configuration constants and settings
├── zone_counter.py           # Core zone counting logic
├── gstreamer_pipeline.py     # GStreamer pipeline management
├── socketio_handlers.py      # WebSocket event handlers
├── video_stream.py           # Video streaming and snapshot handling
├── web_routes.py             # Flask HTTP route handlers
├── main.py                   # Application entry point
├── requirements.txt          # Python dependencies
├── deploy.sh                 # Deployment script
├── templates/
│   └── index3.html          # Web interface template
├── static/                   # Static web assets
├── logs/                     # Application logs
└── data/                     # Data storage
```

## 📋 Module Breakdown

### Core Modules

1. **config.py**
   - Application configuration constants
   - Model paths, server settings, default values
   - Centralized configuration management

2. **zone_counter.py**
   - `MultiSourceZoneVisitorCounter` class
   - Zone management and person tracking logic
   - Data persistence and count calculations

3. **gstreamer_pipeline.py**
   - `SafeGStreamerMultiSourceDetectionApp` wrapper
   - `PipelineManager` for lifecycle management
   - Frame processing callbacks and people detection

4. **video_stream.py**
   - `VideoStreamManager` for video streaming
   - Frame generation and encoding
   - Snapshot capture functionality

5. **socketio_handlers.py**
   - WebSocket event handlers
   - Real-time communication with web interface
   - Zone updates and camera switching

6. **web_routes.py**
   - Flask HTTP route handlers
   - REST API endpoints
   - Error handling and validation

7. **main.py**
   - Application orchestration
   - Component initialization
   - Server startup and configuration

## 🚀 Quick Start

### Prerequisites

- Raspberry Pi 5 with Hailo AI accelerator
- Python 3.8+
- GStreamer 1.0
- Hailo SDK and examples installed

### Installation

1. **Make deployment script executable:**
   ```bash
   chmod +x deploy.sh
   ```

2. **Run deployment script:**
   ```bash
   sudo ./deploy.sh
   ```

3. **Start the service:**
   ```bash
   sudo systemctl start visitor-counter
   ```

4. **Access web interface:**
   ```
   http://your-pi-ip:5000
   ```

### Manual Installation

1. **Install system dependencies:**
   ```bash
   sudo apt-get update
   sudo apt-get install python3-dev python3-pip python3-venv
   sudo apt-get install gstreamer1.0-tools gstreamer1.0-plugins-good
   sudo apt-get install python3-gi python3-gi-cairo gir1.2-gtk-3.0
   ```

2. **Create virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run application:**
   ```bash
   python main.py
   ```

## 🔧 Configuration

### Camera Sources

Configure video sources in the web interface or via API:

```json
{
  "sources": [
    "/dev/video0",
    "/dev/video1",
    "rtsp://camera-ip/stream"
  ]
}
```

### Zone Configuration

Zones are configured per camera through the web interface:

```json
{
  "camera1": {
    "zones": {
      "zone1": {
        "top_left": [640, 360],
        "bottom_right": [1280, 700],
        "in_count": 0,
        "out_count": 0,
        "inside_ids": [],
        "history": []
      }
    }
  }
}
```

## 🖥️ Web Interface Features

- **Real-time video streaming** from multiple cameras
- **Interactive zone definition** with mouse/touch
- **Live visitor counting** with entry/exit tracking
- **Camera switching** and management
- **Zone statistics** and history
- **WebSocket updates** for real-time data

## 🔌 API Endpoints

### Pipeline Management
- `POST /start_pipeline` - Start video processing pipeline
- `POST /stop_pipeline` - Stop video processing pipeline
- `GET /pipeline_status` - Get pipeline status

### Camera Management
- `GET /get_cameras` - List available cameras
- `GET /video_feed?camera_id=camera1` - Video stream
- `GET /get_snapshot?camera_id=camera1` - Camera snapshot

### Zone Management
- `GET /api/camera/{camera_id}/zones` - Get camera zones
- `POST /api/camera/{camera_id}/zones` - Create/update zone
- `DELETE /api/camera/{camera_id}/zones/{zone}` - Delete zone
- `POST /api/camera/{camera_id}/zones/{zone}/reset` - Reset counts

### Data Access
- `GET /get_zones` - Get all zones data
- `GET /health` - Health check endpoint

## 🔄 WebSocket Events

### Client to Server
- `set_zone` - Create/update zone
- `reset_zone_counts` - Reset zone counters
- `set_active_camera` - Switch active camera
- `delete_zone` - Delete zone

### Server to Client
- `update_counts` - Real-time count updates
- `zone_updated` - Zone configuration changed
- `camera_changed` - Active camera switched
- `count_reset` - Zone counts reset

## 📊 Monitoring and Logging

### Service Management
```bash
# Check service status
sudo systemctl status visitor-counter

# View logs
sudo journalctl -u visitor-counter -f

# Restart service
sudo systemctl restart visitor-counter
```

### Log Files
- Application logs: `/opt/visitor-counter/logs/`
- System logs: `journalctl -u visitor-counter`

### Health Check
```bash
curl http://localhost:5000/health
```

## 🛠️ Development

### Running in Development Mode

1. **Set debug mode in config.py:**
   ```python
   DEBUG_MODE = True
   ```

2. **Run directly:**
   ```bash
   python main.py
   ```

### Adding New Features

1. **Configuration**: Add settings to `config.py`
2. **Core Logic**: Implement in appropriate module
3. **Web Interface**: Add routes to `web_routes.py`
4. **Real-time Updates**: Add handlers to `socketio_handlers.py`

## 🔒 Security Considerations

- Change default secret key in production
- Configure firewall rules (port 5000)
- Use HTTPS in production environments
- Implement authentication for production use
- Validate all input data

## 📈 Performance Optimization

- **Frame Processing**: Optimized detection pipeline
- **Memory Management**: Efficient buffer handling
- **Network**: Compressed video streaming
- **Storage**: Minimal data persistence
- **Threading**: Non-blocking I/O operations

## 🐛 Troubleshooting

### Common Issues

1. **GStreamer not found**
   ```bash
   sudo apt-get install gstreamer1.0-tools
   ```

2. **Hailo model not found**
   - Check model path in `config.py`
   - Ensure Hailo SDK is installed

3. **Permission denied**
   ```bash
   sudo chown -R $USER:$USER /opt/visitor-counter
   ```

4. **Service won't start**
   ```bash
   sudo journalctl -u visitor-counter -n 50
   ```

### Debug Mode

Enable debug logging by setting `DEBUG_MODE = True` in `config.py`.

## 📝 Contributing

1. Fork the repository
2. Create feature branch
3. Make changes in appropriate modules
4. Test thoroughly
5. Submit pull request

## 📄 License

This project is licensed under the MIT License.

## 🙏 Acknowledgments

- Hailo AI for hardware acceleration
- GStreamer community for multimedia framework
- Flask and SocketIO communities
