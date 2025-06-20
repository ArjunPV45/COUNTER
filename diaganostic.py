#!/usr/bin/env python3

import subprocess
import sys
import json
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

class RTSPStreamAnalyzer:
    def __init__(self):
        Gst.init(None)
        
    def analyze_stream_with_ffprobe(self, rtsp_url):
        """Analyze RTSP stream using ffprobe"""
        print(f"\n=== Analyzing {rtsp_url} with FFprobe ===")
        
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams',
            '-show_format',
            '-rtsp_transport', 'tcp',  # Try TCP first
            rtsp_url
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                self.print_stream_info(data, "FFprobe (TCP)")
                return data
            else:
                print(f"FFprobe TCP failed: {result.stderr}")
                # Try with UDP
                cmd[-2] = 'udp'
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    self.print_stream_info(data, "FFprobe (UDP)")
                    return data
                else:
                    print(f"FFprobe UDP also failed: {result.stderr}")
                    return None
        except subprocess.TimeoutExpired:
            print("FFprobe timeout - stream may be unreachable")
            return None
        except Exception as e:
            print(f"FFprobe error: {e}")
            return None

    def print_stream_info(self, data, method):
        """Print detailed stream information"""
        print(f"\n--- Stream Info ({method}) ---")
        
        if 'format' in data:
            format_info = data['format']
            print(f"Format: {format_info.get('format_name', 'Unknown')}")
            print(f"Duration: {format_info.get('duration', 'Live')}")
            print(f"Bitrate: {format_info.get('bit_rate', 'Unknown')}")
        
        if 'streams' in data:
            for i, stream in enumerate(data['streams']):
                print(f"\nStream {i}:")
                print(f"  Type: {stream.get('codec_type', 'Unknown')}")
                print(f"  Codec: {stream.get('codec_name', 'Unknown')}")
                
                if stream.get('codec_type') == 'video':
                    print(f"  Resolution: {stream.get('width', '?')}x{stream.get('height', '?')}")
                    print(f"  FPS: {stream.get('r_frame_rate', 'Unknown')}")
                    print(f"  Pixel Format: {stream.get('pix_fmt', 'Unknown')}")
                    print(f"  Profile: {stream.get('profile', 'Unknown')}")
                    print(f"  Level: {stream.get('level', 'Unknown')}")
                elif stream.get('codec_type') == 'audio':
                    print(f"  Sample Rate: {stream.get('sample_rate', 'Unknown')}")
                    print(f"  Channels: {stream.get('channels', 'Unknown')}")

    def test_gstreamer_pipeline(self, rtsp_url, transport='udp'):
        """Test basic GStreamer pipeline"""
        print(f"\n=== Testing GStreamer pipeline ({transport.upper()}) ===")
        
        pipeline_str = f"""
        rtspsrc location={rtsp_url} protocols={transport} latency=200 buffer-mode=1 drop-on-latency=true 
        ! rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! fakesink sync=false
        """
        
        try:
            pipeline = Gst.parse_launch(pipeline_str)
            
            # Set up bus to catch messages
            bus = pipeline.get_bus()
            bus.add_signal_watch()
            
            def on_message(bus, message):
                t = message.type
                if t == Gst.MessageType.ERROR:
                    err, debug = message.parse_error()
                    print(f"GStreamer Error: {err}")
                    print(f"Debug info: {debug}")
                    loop.quit()
                elif t == Gst.MessageType.EOS:
                    print("End of stream")
                    loop.quit()
                elif t == Gst.MessageType.STATE_CHANGED:
                    if message.src == pipeline:
                        old_state, new_state, pending_state = message.parse_state_changed()
                        print(f"State changed: {old_state.value_name} -> {new_state.value_name}")
            
            bus.connect('message', on_message)
            
            # Start pipeline
            ret = pipeline.set_state(Gst.State.PLAYING)
            if ret == Gst.StateChangeReturn.FAILURE:
                print("Failed to start pipeline")
                return False
            
            # Run for a few seconds
            loop = GLib.MainLoop()
            GLib.timeout_add_seconds(10, lambda: loop.quit())
            
            try:
                loop.run()
            except KeyboardInterrupt:
                pass
            
            pipeline.set_state(Gst.State.NULL)
            print("GStreamer test completed")
            return True
            
        except Exception as e:
            print(f"GStreamer pipeline error: {e}")
            return False

def create_flexible_pipeline_string(rtsp_url, stream_info=None):
    """Create a flexible pipeline string based on stream analysis"""
    
    # Default pipeline components
    base_pipeline = f"""
    rtspsrc location={rtsp_url} name=src_0_0 
    latency=200 buffer-mode=1 drop-on-latency=true is-live=true 
    udp-buffer-size=524288
    """
    
    # Determine transport protocol and depayloader based on analysis
    if stream_info:
        video_streams = [s for s in stream_info.get('streams', []) if s.get('codec_type') == 'video']
        if video_streams:
            codec = video_streams[0].get('codec_name', '').lower()
            if 'h264' in codec:
                depay_parse = "rtph264depay ! h264parse"
                decoder = "avdec_h264"
            elif 'h265' in codec or 'hevc' in codec:
                depay_parse = "rtph265depay ! h265parse"
                decoder = "avdec_h265"
            else:
                # Fallback to auto-detection
                depay_parse = "rtph264depay ! h264parse"
                decoder = "avdec_h264"
        else:
            depay_parse = "rtph264depay ! h264parse"
            decoder = "avdec_h264"
    else:
        depay_parse = "rtph264depay ! h264parse"
        decoder = "avdec_h264"
    
    # Try both UDP and TCP protocols
    tcp_pipeline = f"""
    {base_pipeline} protocols=tcp 
    ! {depay_parse} ! {decoder} 
    ! queue name=src_0_0_queue leaky=downstream max-size-buffers=5 max-size-bytes=0 max-size-time=0 
    ! video/x-raw, format=I420 
    ! queue name=src_0_0_scale_q leaky=no max-size-buffers=3 max-size-bytes=0 max-size-time=0 
    ! videoscale name=src_0_0_videoscale n-threads=2 
    ! queue name=src_0_0_convert_q leaky=no max-size-buffers=3 max-size-bytes=0 max-size-time=0 
    ! videoconvert n-threads=3 name=src_0_0_convert qos=false 
    ! video/x-raw, pixel-aspect-ratio=1/1, format=RGB, width=1280, height=720
    """
    
    udp_pipeline = tcp_pipeline.replace("protocols=tcp", "protocols=udp")
    
    return {
        'tcp': tcp_pipeline,
        'udp': udp_pipeline,
        'auto': f"""
        {base_pipeline} protocols=udp+tcp 
        ! {depay_parse} ! {decoder} 
        ! queue name=src_0_0_queue leaky=downstream max-size-buffers=5 max-size-bytes=0 max-size-time=0 
        ! video/x-raw, format=I420 
        ! queue name=src_0_0_scale_q leaky=no max-size-buffers=3 max-size-bytes=0 max-size-time=0 
        ! videoscale name=src_0_0_videoscale n-threads=2 
        ! queue name=src_0_0_convert_q leaky=no max-size-buffers=3 max-size-bytes=0 max-size-time=0 
        ! videoconvert n-threads=3 name=src_0_0_convert qos=false 
        ! video/x-raw, pixel-aspect-ratio=1/1, format=RGB, width=1280, height=720
        """
    }

def enhanced_rtsp_validation(rtsp_url):
    """Enhanced RTSP validation with multiple methods"""
    print(f"\n{'='*60}")
    print(f"ENHANCED RTSP VALIDATION: {rtsp_url}")
    print(f"{'='*60}")
    
    analyzer = RTSPStreamAnalyzer()
    
    # Method 1: FFprobe analysis
    stream_info = analyzer.analyze_stream_with_ffprobe(rtsp_url)
    
    # Method 2: GStreamer pipeline test
    print(f"\n=== Testing GStreamer Connectivity ===")
    udp_success = analyzer.test_gstreamer_pipeline(rtsp_url, 'udp')
    if not udp_success:
        print("UDP failed, trying TCP...")
        tcp_success = analyzer.test_gstreamer_pipeline(rtsp_url, 'tcp')
    else:
        tcp_success = True
    
    # Method 3: Simple connectivity test
    print(f"\n=== Basic Connectivity Test ===")
    ffmpeg_cmd = [
        'ffmpeg', '-rtsp_transport', 'tcp', '-i', rtsp_url, 
        '-frames:v', '1', '-f', 'null', '-', '-v', 'error'
    ]
    
    try:
        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print("✓ Basic connectivity successful")
        else:
            print(f"✗ Basic connectivity failed: {result.stderr}")
    except Exception as e:
        print(f"✗ Basic connectivity test error: {e}")
    
    # Generate recommended pipeline
    pipelines = create_flexible_pipeline_string(rtsp_url, stream_info)
    
    print(f"\n=== RECOMMENDED PIPELINE CONFIGURATIONS ===")
    print("\n1. AUTO-DETECT (Try this first):")
    print(pipelines['auto'])
    
    print("\n2. TCP (If auto-detect fails):")
    print(pipelines['tcp'])
    
    print("\n3. UDP (If TCP fails):")
    print(pipelines['udp'])
    
    return stream_info, pipelines

if __name__ == "__main__":
    # Test both URLs
    working_url = "rtsp://centelon_tvm:Cent%409876@10.71.172.50:554/Streaming/Channels/402"
    problem_url = "rtsp://centelon_tvm:Cent%409876@10.71.172.50:554/Streaming/Channels/502"
    
    print("COMPARING WORKING VS PROBLEM STREAMS")
    print("="*80)
    
    print("\n🟢 ANALYZING WORKING STREAM (Channel 402)")
    working_info, working_pipelines = enhanced_rtsp_validation(working_url)
    
    print("\n🔴 ANALYZING PROBLEM STREAM (Channel 502)")
    problem_info, problem_pipelines = enhanced_rtsp_validation(problem_url)
    
    print("\n" + "="*80)
    print("ANALYSIS COMPLETE")
    print("="*80)
    
    if working_info and problem_info:
        print("\n=== COMPARISON SUMMARY ===")
        
        # Compare video streams
        working_video = [s for s in working_info.get('streams', []) if s.get('codec_type') == 'video']
        problem_video = [s for s in problem_info.get('streams', []) if s.get('codec_type') == 'video']
        
        if working_video and problem_video:
            w_stream, p_stream = working_video[0], problem_video[0]
            
            print(f"Codec: {w_stream.get('codec_name')} vs {p_stream.get('codec_name')}")
            print(f"Resolution: {w_stream.get('width')}x{w_stream.get('height')} vs {p_stream.get('width')}x{p_stream.get('height')}")
            print(f"Profile: {w_stream.get('profile')} vs {p_stream.get('profile')}")
            print(f"Pixel Format: {w_stream.get('pix_fmt')} vs {p_stream.get('pix_fmt')}")
            
            # Identify potential issues
            issues = []
            if w_stream.get('codec_name') != p_stream.get('codec_name'):
                issues.append("Different codecs")
            if w_stream.get('profile') != p_stream.get('profile'):
                issues.append("Different H.264 profiles")
            if w_stream.get('pix_fmt') != p_stream.get('pix_fmt'):
                issues.append("Different pixel formats")
                
            if issues:
                print(f"\n⚠️  POTENTIAL ISSUES: {', '.join(issues)}")
            else:
                print("\n✓ No obvious differences detected")