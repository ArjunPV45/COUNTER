// Socket connection - adjust IP address if needed
const socket = io();

// State management
let selectedZone = null;
let isDrawing = false;
let startPoint = null;
let zones = {};
let currentCamera = "camera1"; // Default active camera

// Zone colors with transparency
const zoneColors = {
    zone1: 'rgba(255, 0, 0, 0.3)', // Red
    zone2: 'rgba(0, 255, 0, 0.3)', // Green
    zone3: 'rgba(0, 0, 255, 0.3)', // Blue
    zone4: 'rgba(255, 165, 0, 0.3)', // Orange
    zone5: 'rgba(165, 180, 0, 0.3)'
};

// Initialize canvas and video elements
const videoFeed = document.getElementById("video-feed");
const snapshotImage = document.getElementById("snapshot-image");
const canvasOverlay = document.getElementById("zone-overlay");
const snapshotOverlay = document.getElementById("snapshot-overlay");
const ctx = canvasOverlay.getContext("2d");
const snapshotCtx = snapshotOverlay.getContext("2d");

// Fixed original video dimensions
const ORIGINAL_VIDEO_WIDTH = 1920;
const ORIGINAL_VIDEO_HEIGHT = 1080;

// Setup canvas dimensions - using fixed size similar to script1.js
function setupCanvas() {
    // Set fixed width/height ratio but scale to fit the container
    const containerWidth = videoFeed.offsetWidth;
    const containerHeight = videoFeed.offsetHeight;
    
    // Maintain aspect ratio but fit within container
    canvasOverlay.width = containerWidth;
    canvasOverlay.height = containerHeight;
}

function setupSnapshotCanvas() {
    // For snapshot, match the image dimensions exactly
    snapshotOverlay.width = 1920; //snapshotImage.width;
    snapshotOverlay.height = 1080; //snapshotImage.height;
}

// Initialize on load
videoFeed.onload = function() {
    setupCanvas();
    //drawAllZones(ctx, canvasOverlay.width, canvasOverlay.height);
};

snapshotImage.onload = function() {
    setupSnapshotCanvas();
    drawAllZones(snapshotCtx, snapshotOverlay.width, snapshotOverlay.height);
};

// Handle window resize
window.addEventListener('resize', () => {
    setupCanvas();
    drawAllZones(ctx, canvasOverlay.width, canvasOverlay.height);
    if (!document.getElementById('snapshot-modal').classList.contains('hidden')) {
        setupSnapshotCanvas();
        drawAllZones(snapshotCtx, snapshotOverlay.width, snapshotOverlay.height);
    }
});

// Load available cameras
function loadCameras() {
    fetch('/get_cameras')
        .then(response => response.json())
        .then(data => {
            const cameraButtonsDiv = document.getElementById('camera-buttons');
            cameraButtonsDiv.innerHTML = '';
            data.cameras.forEach(camera => {
                const button = document.createElement('button');
                button.setAttribute('data-camera', camera);
                button.textContent = camera.replace('camera', 'Camera ');
                if (camera === data.active_camera) {
                    button.classList.add('active');
                    currentCamera = camera;
                }
                button.addEventListener('click', () => switchCamera(camera));
                cameraButtonsDiv.appendChild(button);
            });
        })
        .catch(error => {
            console.error('Error loading cameras:', error);
        });
}

// Switch camera feed
function switchCamera(cameraId) {
    socket.emit('set_active_camera', { camera_id: cameraId });
    
    // Update active button
    const buttons = document.querySelectorAll('#camera-selector button');
    buttons.forEach(button => {
        button.classList.remove('active');
        if (button.getAttribute('data-camera') === cameraId) {
            button.classList.add('active');
        }
    });
    
    // Update video feed source
    videoFeed.src = `/video_feed?camera_id=${cameraId}`;
    currentCamera = cameraId;
    
    // Clear and redraw zones
    loadZones();
}

// Load zones from server
function loadZones() {
    fetch('/get_zones')
        .then(response => response.json())
        .then(data => {
            zones = data.data;
            updateZoneBoxes();
            updateHistory();
            //drawAllZones(ctx, canvasOverlay.width, canvasOverlay.height);
        })
        .catch(error => {
            console.error('Error loading zones:', error);
        });
}

// Draw all zones on canvas
function drawAllZones(context, width, height) {
    context.clearRect(0, 0, width, height);
    
    if (!zones[currentCamera]) return;
    
    const cameraZones = zones[currentCamera].zones;
    for (const [zoneName, zoneData] of Object.entries(cameraZones)) {
        const tl = zoneData.top_left;
        const br = zoneData.bottom_right;
        
        // Scale points to canvas dimensions
        const scaledTL = [
            (tl[0] / ORIGINAL_VIDEO_WIDTH) * width,
            (tl[1] / ORIGINAL_VIDEO_HEIGHT) * height
        ];
        
        const scaledBR = [
            (br[0] / ORIGINAL_VIDEO_WIDTH) * width,
            (br[1] / ORIGINAL_VIDEO_HEIGHT) * height
        ];
        
        drawZone(context, zoneName, scaledTL, scaledBR);
    }
}

// Draw single zone
function drawZone(context, zoneName, topLeft, bottomRight) {
    const width = bottomRight[0] - topLeft[0];
    const height = bottomRight[1] - topLeft[1];
    
    context.fillStyle = zoneColors[zoneName] || 'rgba(128, 128, 128, 0.3)';
    context.fillRect(topLeft[0], topLeft[1], width, height);
    
    context.strokeStyle = 'black';
    context.lineWidth = 2;
    context.strokeRect(topLeft[0], topLeft[1], width, height);
    
    context.fillStyle = 'black';
    context.font = '14px Arial';
    context.fillText(zoneName, topLeft[0] + 5, topLeft[1] + 20);
}

// Update zone boxes in the UI
function updateZoneBoxes() {
    // Hide all zone boxes first
    document.querySelectorAll('.zone-box').forEach(box => {
        box.classList.add('hidden');
    });
    
    if (!zones[currentCamera]) return;
    
    const cameraZones = zones[currentCamera].zones;
    for (const [zoneName, zoneData] of Object.entries(cameraZones)) {
        const zoneBox = document.getElementById(`${zoneName}-box`);
        if (zoneBox) {
            zoneBox.classList.remove('hidden');
            
            // Update counts
            document.getElementById(`${zoneName}_in`).textContent = zoneData.in_count;
            document.getElementById(`${zoneName}_out`).textContent = zoneData.out_count;
            
            // Update people inside
            const insideList = document.getElementById(`${zoneName}_inside_ids`);
            insideList.innerHTML = '';
            
            if (zoneData.inside_ids && zoneData.inside_ids.length > 0) {
                zoneBox.classList.add('occupied');
                zoneData.inside_ids.forEach(id => {
                    const li = document.createElement('li');
                    li.textContent = `Person ID: ${id}`;
                    insideList.appendChild(li);
                });
            } else {
                zoneBox.classList.remove('occupied');
                const li = document.createElement('li');
                li.textContent = 'No one inside';
                insideList.appendChild(li);
            }
        }
    }
}

// Update history table
function updateHistory() {
    const historyTable = document.getElementById('history_table').querySelector('tbody');
    historyTable.innerHTML = '';
    
    let allHistory = [];
    
    // Collect history from all cameras and zones
    for (const [cameraId, cameraData] of Object.entries(zones)) {
        for (const [zoneName, zoneData] of Object.entries(cameraData.zones)) {
            zoneData.history.forEach(entry => {
                allHistory.push({
                    camera: cameraId,
                    zone: zoneName,
                    id: entry.id,
                    action: entry.action,
                    time: entry.time
                });
            });
        }
    }
    
    // Sort by time (newest first)
    allHistory.sort((a, b) => new Date(b.time) - new Date(a.time));
    
    // Display first 50 entries
    allHistory.slice(0, 50).forEach(entry => {
        const row = document.createElement('tr');
        row.className = entry.action.toLowerCase();
        
        row.innerHTML = `
            <td>${entry.zone} (${entry.camera})</td>
            <td>${entry.id}</td>
            <td>${entry.action}</td>
            <td>${entry.time}</td>
        `;
        
        historyTable.appendChild(row);
    });
}

// Initialize zone selection buttons
function initZoneButtons() {
    const zoneButtons = document.querySelectorAll('.zone-buttons button');
    zoneButtons.forEach(button => {
        button.addEventListener('click', () => {
            if (selectedZone === button.getAttribute('data-zone')) {
                // Deselect if already selected
                selectedZone = null;
                zoneButtons.forEach(btn => btn.classList.remove('active'));
            } else {
                // Select new zone
                selectedZone = button.getAttribute('data-zone');
                zoneButtons.forEach(btn => btn.classList.remove('active'));
                button.classList.add('active');
            }
        });
    });
}

// Draw zone on canvas
function initDrawing() {
    canvasOverlay.addEventListener('mousedown', handleMouseDown);
    canvasOverlay.addEventListener('mousemove', handleMouseMove);
    canvasOverlay.addEventListener('mouseup', handleMouseUp);
    canvasOverlay.addEventListener('mouseleave', handleMouseLeave);
    
    snapshotOverlay.addEventListener('mousedown', handleSnapshotMouseDown);
    snapshotOverlay.addEventListener('mousemove', handleSnapshotMouseMove);
    snapshotOverlay.addEventListener('mouseup', handleSnapshotMouseUp);
    snapshotOverlay.addEventListener('mouseleave', handleSnapshotMouseUp);
}

function handleMouseDown(e) {
    if (!selectedZone) return;
    
    isDrawing = true;
    const rect = canvasOverlay.getBoundingClientRect();
    startPoint = [
        e.clientX - rect.left,
        e.clientY - rect.top
    ];
}

function handleMouseMove(e) {
    if (!isDrawing || !selectedZone) return;
    
    const rect = canvasOverlay.getBoundingClientRect();
    const currentPoint = [
        e.clientX - rect.left,
        e.clientY - rect.top
    ];
    
    // Redraw all zones
    //drawAllZones(ctx, canvasOverlay.width, canvasOverlay.height);
    ctx.clearRect(0, 0, canvasOverlay.width, canvasOverlay.height);
    
    // Draw current selection
    const width = currentPoint[0] - startPoint[0];
    const height = currentPoint[1] - startPoint[1];
    
    ctx.fillStyle = zoneColors[selectedZone] || 'rgba(128, 128, 128, 0.3)';
    ctx.fillRect(startPoint[0], startPoint[1], width, height);
    
    ctx.strokeStyle = 'red';
    ctx.lineWidth = 2;
    ctx.strokeRect(startPoint[0], startPoint[1], width, height);
}

function handleMouseUp(e) {
    if (!isDrawing || !selectedZone) return;
    
    const rect = canvasOverlay.getBoundingClientRect();
    const endPoint = [
        e.clientX - rect.left,
        e.clientY - rect.top
    ];
    
    saveZone(startPoint, endPoint);
    isDrawing = false;
    
    drawAllZones(ctx, canvasOverlay.width, canvasOverlay.height);
}

function handleMouseLeave() {
    if (isDrawing) {
        isDrawing = false;
        drawAllZones(ctx, canvasOverlay.width, canvasOverlay.height);
    }
}

function handleSnapshotMouseDown(e) {
    if (!selectedZone) return;
    
    isDrawing = true;
    const rect = snapshotOverlay.getBoundingClientRect();
    startPoint = [
        e.clientX - rect.left,
        e.clientY - rect.top
    ];
}

function handleSnapshotMouseMove(e) {
    if (!isDrawing || !selectedZone) return;
    
    const rect = snapshotOverlay.getBoundingClientRect();
    const currentPoint = [
        e.clientX - rect.left,
        e.clientY - rect.top
    ];
    
    // Redraw all zones
    drawAllZones(snapshotCtx, snapshotOverlay.width, snapshotOverlay.height);
    snapshotCtx.clearRect(0, 0, snapshotOverlay.width, snapshotOverlay.height);
    
    // Draw current selection
    const width = currentPoint[0] - startPoint[0];
    const height = currentPoint[1] - startPoint[1];
    
    snapshotCtx.fillStyle = zoneColors[selectedZone] || 'rgba(128, 128, 128, 0.3)';
    snapshotCtx.fillRect(startPoint[0], startPoint[1], width, height);
    
    snapshotCtx.strokeStyle = 'red';
    snapshotCtx.lineWidth = 2;
    snapshotCtx.strokeRect(startPoint[0], startPoint[1], width, height);
}

function handleSnapshotMouseUp(e) {
    if (!isDrawing || !selectedZone) return;
    
    const rect = snapshotOverlay.getBoundingClientRect();
    const endPoint = [
        e.clientX - rect.left,
        e.clientY - rect.top
    ];
    
    saveZoneFromSnapshot(startPoint, endPoint);
    isDrawing = false;
    
    
    // Reset zone selection
    selectedZone = null;
    document.querySelectorAll('.zone-buttons button').forEach(btn => btn.classList.remove('active'));
    
    drawAllZones(snapshotctx, snapshotOverlay.width, snapshotOverlay.height);


}

function saveZone(start, end) {
    if (!selectedZone || !currentCamera) return;
    
    // Calculate scaling factors based on fixed original dimensions
    const scaleX = ORIGINAL_VIDEO_WIDTH / canvasOverlay.width;
    const scaleY = ORIGINAL_VIDEO_HEIGHT / canvasOverlay.height;
    
    // Ensure start is top-left and end is bottom-right
    const topLeft = [
        Math.min(start[0], end[0]) * scaleX,
        Math.min(start[1], end[1]) * scaleY
    ];
    
    const bottomRight = [
        Math.max(start[0], end[0]) * scaleX,
        Math.max(start[1], end[1]) * scaleY
    ];
    
    // Send to server
    socket.emit('set_zone', {
        camera_id: currentCamera,
        zone: selectedZone,
        top_left: topLeft.map(Math.round),
        bottom_right: bottomRight.map(Math.round)
    });
    
    // Reset selection
    selectedZone = null;
    document.querySelectorAll('.zone-buttons button').forEach(btn => btn.classList.remove('active'));
    
    showToast(`Zone ${selectedZone} set successfully`);
}

function saveZoneFromSnapshot(start, end) {
    if (!selectedZone || !currentCamera) return;
    
    // Calculate scaling factors - snapshot should directly map to original dimensions
    const scaleX = ORIGINAL_VIDEO_WIDTH / snapshotOverlay.width;
    const scaleY = ORIGINAL_VIDEO_HEIGHT / snapshotOverlay.height;
    
    // Ensure start is top-left and end is bottom-right
    const topLeft = [
        Math.min(start[0], end[0]) * scaleX,
        Math.min(start[1], end[1]) * scaleY
    ];
    
    const bottomRight = [
        Math.max(start[0], end[0]) * scaleX,
        Math.max(start[1], end[1]) * scaleY
    ];
    
    // Send to server
    socket.emit('set_zone', {
        camera_id: currentCamera,
        zone: selectedZone,
        top_left: topLeft.map(Math.round),
        bottom_right: bottomRight.map(Math.round)
    });
    
    showToast(`Zone ${selectedZone} set successfully`);
}

// Snapshot functionality
function initSnapshot() {
    const snapshotBtn = document.getElementById('snapshot-btn');
    const snapshotModal = document.getElementById('snapshot-modal');
    const closeSnapshot = document.getElementById('close-snapshot');
    
    snapshotBtn.addEventListener('click', () => {
        // Take snapshot of current camera
        fetch(`/get_snapshot?camera_id=${currentCamera}`)
            .then(response => {
                if (!response.ok) throw new Error('Snapshot not available');
                return response.blob();
            })
            .then(blob => {
                const url = URL.createObjectURL(blob);
                snapshotImage.src = url;
                snapshotModal.classList.remove('hidden');
            })
            .catch(error => {
                console.error('Error taking snapshot:', error);
                showToast('Error taking snapshot');
            });
    });
    
    closeSnapshot.addEventListener('click', () => {
        snapshotModal.classList.add('hidden');
        URL.revokeObjectURL(snapshotImage.src);
    });
}

// Reset zone counts
function initResetButtons() {
    document.querySelectorAll('.reset-button').forEach(button => {
        button.addEventListener('click', () => {
            const zoneId = button.id.replace('reset-', '');
            
            socket.emit('reset_zone_counts', {
                camera_id: currentCamera,
                zone: zoneId
            });
            
            showToast(`Reset counts for ${zoneId}`);
        });
    });
}

// History filtering
function initFilters() {
    const applyFiltersBtn = document.getElementById('apply-filters');
    const resetFiltersBtn = document.getElementById('reset-filters');
    const exportCsvBtn = document.getElementById('export-csv');
    
    applyFiltersBtn.addEventListener('click', applyFilters);
    resetFiltersBtn.addEventListener('click', resetFilters);
    exportCsvBtn.addEventListener('click', exportToCsv);
}

function applyFilters() {
    const zoneFilter = document.getElementById('zone-filter').value;
    const actionFilter = document.getElementById('action-filter').value;
    const dateFilter = document.getElementById('date-filter').value;
    const timeFrom = document.getElementById('time-from').value;
    const timeTo = document.getElementById('time-to').value;
    
    let allHistory = [];
    
    // Collect history from all cameras and zones
    for (const [cameraId, cameraData] of Object.entries(zones)) {
        for (const [zoneName, zoneData] of Object.entries(cameraData.zones)) {
            zoneData.history.forEach(entry => {
                allHistory.push({
                    camera: cameraId,
                    zone: zoneName,
                    id: entry.id,
                    action: entry.action,
                    time: entry.time
                });
            });
        }
    }
    
    // Apply filters
    let filteredHistory = allHistory;
    
    if (zoneFilter !== 'all') {
        filteredHistory = filteredHistory.filter(entry => entry.zone === zoneFilter);
    }
    
    if (actionFilter !== 'all') {
        filteredHistory = filteredHistory.filter(entry => entry.action === actionFilter);
    }
    
    if (dateFilter) {
        const filterDate = new Date(dateFilter).setHours(0, 0, 0, 0);
        filteredHistory = filteredHistory.filter(entry => {
            const entryDate = new Date(entry.time).setHours(0, 0, 0, 0);
            return entryDate === filterDate;
        });
    }
    
    if (timeFrom) {
        const [fromHours, fromMinutes] = timeFrom.split(':').map(Number);
        filteredHistory = filteredHistory.filter(entry => {
            const entryTime = new Date(entry.time);
            const entryHours = entryTime.getHours();
            const entryMinutes = entryTime.getMinutes();
            
            return (entryHours > fromHours || 
                   (entryHours === fromHours && entryMinutes >= fromMinutes));
        });
    }
    
    if (timeTo) {
        const [toHours, toMinutes] = timeTo.split(':').map(Number);
        filteredHistory = filteredHistory.filter(entry => {
            const entryTime = new Date(entry.time);
            const entryHours = entryTime.getHours();
            const entryMinutes = entryTime.getMinutes();
            
            return (entryHours < toHours || 
                   (entryHours === toHours && entryMinutes <= toMinutes));
        });
    }
    
    // Sort by time (newest first)
    filteredHistory.sort((a, b) => new Date(b.time) - new Date(a.time));
    
    // Update filtered table
    updateFilteredTable(filteredHistory);
}

function updateFilteredTable(filteredHistory) {
    const filteredTable = document.getElementById('filtered_history_table').querySelector('tbody');
    filteredTable.innerHTML = '';
    
    if (filteredHistory.length === 0) {
        const row = document.createElement('tr');
        row.innerHTML = '<td colspan="4">No results found</td>';
        filteredTable.appendChild(row);
        return;
    }
    
    filteredHistory.forEach(entry => {
        const row = document.createElement('tr');
        row.className = entry.action.toLowerCase();
        
        row.innerHTML = `
            <td>${entry.zone} (${entry.camera})</td>
            <td>${entry.id}</td>
            <td>${entry.action}</td>
            <td>${entry.time}</td>
        `;
        
        filteredTable.appendChild(row);
    });
}

function resetFilters() {
    document.getElementById('zone-filter').value = 'all';
    document.getElementById('action-filter').value = 'all';
    document.getElementById('date-filter').value = '';
    document.getElementById('time-from').value = '';
    document.getElementById('time-to').value = '';
    
    document.getElementById('filtered_history_table').querySelector('tbody').innerHTML = '';
}

function exportToCsv() {
    const table = document.getElementById('filtered_history_table');
    const rows = table.querySelectorAll('tr');
    
    if (rows.length <= 1) {
        showToast('No data to export');
        return;
    }
    
    let csvContent = 'Zone,ID,Action,Time\n';
    
    // Skip header row (index 0)
    for (let i = 1; i < rows.length; i++) {
        const row = rows[i];
        const cells = row.querySelectorAll('td');
        
        if (cells.length === 4) {
            const rowData = Array.from(cells).map(cell => {
                // Escape quotes and wrap with quotes
                return `"${cell.textContent.replace(/"/g, '""')}"`;
            });
            
            csvContent += rowData.join(',') + '\n';
        }
    }
    
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.setAttribute('href', url);
    link.setAttribute('download', `visitor_history_${new Date().toISOString().slice(0, 10)}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

// Show toast message
function showToast(message) {
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    document.body.appendChild(toast);
    
    // Force reflow
    toast.offsetHeight;
    
    // Show toast
    toast.classList.add('show');
    
    // Auto-hide after 3 seconds
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => {
            document.body.removeChild(toast);
        }, 300);
    }, 3000);
}

// Socket events
socket.on('connect', () => {
    console.log('Connected to server');
    loadCameras();
    loadZones();
});

socket.on('update_counts', (data) => {
    zones = data.data;
    currentCamera = data.active_camera;
    updateZoneBoxes();
    updateHistory();
});

socket.on('count_reset', (data) => {
    zones = data.data;
    updateZoneBoxes();
    updateHistory();
});

socket.on('zone_updated', (data) => {
    zones = data.data;
    drawAllZones(ctx, canvasOverlay.width, canvasOverlay.height);
});

socket.on('camera_changed', (data) => {
    zones = data.data;
    currentCamera = data.active_camera;
    
    // Update video feed
    videoFeed.src = `/video_feed?camera_id=${currentCamera}`;
    
    // Update active button
    const buttons = document.querySelectorAll('#camera-selector button');
    buttons.forEach(button => {
        button.classList.remove('active');
        if (button.getAttribute('data-camera') === currentCamera) {
            button.classList.add('active');
        }
    });
    
    updateZoneBoxes();
    drawAllZones(ctx, canvasOverlay.width, canvasOverlay.height);
});

socket.on('error', (data) => {
    showToast(data.message);
});

function initializeSources() {
    const form = document.getElementById("start-pipeline-form");
    const textarea = document.getElementById("source-urls");
    const cameraButtonsDiv = document.getElementById("camera-buttons");

    if (!form || !textarea || !cameraButtonsDiv) {
        console.error("Required elements for source initialization not found");
        return;
    }

    form.addEventListener("submit", async function (e) {
        e.preventDefault();
        const sources = textarea.value
            .split("\n")
            .map(url => url.trim())
            .filter(url => url.length > 0);

        if (sources.length === 0) {
            showToast("Please enter at least one video source.");
            return;
        }

        try {
            const res = await fetch("/start_pipeline", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sources })
            });
            const result = await res.json();

            if (result.success) {
                // Clear existing camera buttons
                cameraButtonsDiv.innerHTML = '';

                // Create camera buttons dynamically
                sources.forEach((src, index) => {
                    const camId = `camera${index + 1}`;
                    const btn = document.createElement("button");
                    btn.textContent = `Camera ${index + 1}`;
                    btn.setAttribute('data-camera', camId);

                    btn.addEventListener('click', () => {
                        // Use existing switchCamera function
                        switchCamera(camId);
                    });

                    cameraButtonsDiv.appendChild(btn);
                });

                // Automatically switch to first camera
                if (sources.length > 0) {
                    switchCamera('camera1');
                }

                // Show success toast
                showToast(`Pipeline started with ${sources.length} camera(s)`);
            } else {
                showToast(result.message || "Failed to start pipeline");
            }
        } catch (err) {
            console.error("Failed to start pipeline:", err);
            showToast("Error starting pipeline");
        }
    });
}

// Initialize application
document.addEventListener('DOMContentLoaded', () => {
    loadCameras();
    loadZones();
    initZoneButtons();
    initDrawing();
    initSnapshot();
    initResetButtons();
    initFilters();
    initializeSources();
    
    setupCanvas();
    drawAllZones(ctx, canvasOverlay.width, canvasOverlay.height);
});
