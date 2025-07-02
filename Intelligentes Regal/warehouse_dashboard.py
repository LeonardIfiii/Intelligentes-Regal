import flask
from flask import Flask, render_template, jsonify, request, redirect, url_for
import threading
import time
import sqlite3
import db_utils
import json
from datetime import datetime
import os
import sys
import logging
# Don't import yolo_monitor directly to avoid the cup count prompt


def load_stock_thresholds():
    """Lädt die Schwellwerte aus der Konfigurationsdatei"""
    config_file = "object_limits.json"
    default_thresholds = {
        "cup": {"critical": 1, "warning": 2, "target": 3},
        "glass": {"critical": 1, "warning": 2, "target": 4},
        "fork": {"critical": 2, "warning": 4, "target": 6},
        "spoon": {"critical": 2, "warning": 4, "target": 6}
    }
    default_single = {"critical": 1, "warning": 2, "target": 3}
    
    try:
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                config = json.load(f)
            
            # Hole die stock_thresholds aus der Konfiguration
            if "stock_thresholds" in config:
                thresholds = config["stock_thresholds"]
                logger.info(f"Schwellwerte aus Konfigurationsdatei geladen: {thresholds}")
                return thresholds, default_single
        
        logger.info("Konfigurationsdatei nicht gefunden, verwende Standardwerte")
        return default_thresholds, default_single
    except Exception as e:
        logger.error(f"Fehler beim Laden der Schwellwerte: {e}")
        return default_thresholds, default_single

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Determine current directory
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
logger.info(f"Working directory: {CURRENT_DIR}")

# Ensure required directories exist
TEMPLATES_DIR = os.path.join(CURRENT_DIR, 'warehouse_templates')
STATIC_DIR = os.path.join(CURRENT_DIR, 'warehouse_static')
CSS_DIR = os.path.join(STATIC_DIR, 'css')
JS_DIR = os.path.join(STATIC_DIR, 'js')

# Create directories if they don't exist
for directory in [TEMPLATES_DIR, STATIC_DIR, CSS_DIR, JS_DIR]:
    if not os.path.exists(directory):
        try:
            os.makedirs(directory)
            logger.info(f"Directory created: {directory}")
        except Exception as e:
            logger.error(f"Error creating directory {directory}: {e}")
            sys.exit(1)

# Create Flask app
app = Flask(__name__, 
            template_folder=TEMPLATES_DIR,
            static_folder=STATIC_DIR)
app.config['SECRET_KEY'] = 'intelligent-shelf-warehouse-system'

# Flag to check if server is active
is_active = True
start_time = time.time()

# Cache for inventory and event data
data_cache = {
    'warehouse_status': [],
    'last_update': 0,
    'cache_lifetime': 2  # 2 seconds cache for performance
}

# Define product-shelf mapping (basierend auf der yolo_monitor.py)
PRODUCT_SHELF_MAPPING = {
    "cup": 0,        # Cups in Shelf 1 (Index 0)
    "book": 1,       # Books in Shelf 2 (Index 1)
    "bottle": 3,     # Bottles in Shelf 4 (Index 3)
    "wine glass": 2  # Glasses in Shelf 3 (Index 2)
}

# Expected product for each shelf (invers mapping)
EXPECTED_PRODUCTS = {
    0: "cup",
    1: "book", 
    2: "wine glass",
    3: "bottle"
}

# Function to check if a product is expected to be in a shelf
def is_product_expected_in_shelf(product_type, shelf_id):
    """
    Checks if a product is expected to be in a shelf based on mapping.
    Returns True only if this product belongs in this specific shelf.
    """
    if product_type.lower() in PRODUCT_SHELF_MAPPING:
        return shelf_id == PRODUCT_SHELF_MAPPING[product_type.lower()]
    return False  # If not in mapping, not expected anywhere



# Define warehouse and sales rack mapping
# This maps warehouse rack numbers to sales rack numbers
# In a real implementation, this would come from a database or configuration file
RACK_MAPPING = {
    # Warehouse rack : Sales rack
    1: 1,
    2: 2,
    3: 3,
    4: 4
}

STOCK_THRESHOLDS, DEFAULT_THRESHOLDS = load_stock_thresholds()

# Status constants
STATUS_CRITICAL = "critical"
STATUS_WARNING = "warning"
STATUS_OK = "ok"

# Function to sanitize data for JSON serialization
def sanitize_data(obj):
    """Converts non-JSON-serializable objects to serializable form"""
    if isinstance(obj, dict):
        return {key: sanitize_data(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_data(item) for item in obj]
    elif isinstance(obj, bytes):
        return obj.decode('utf-8', errors='replace')
    elif isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    else:
        return str(obj)

# Helper function to get data from database
def get_warehouse_data():
    """Fetches current data for the warehouse dashboard"""
    current_time = time.time()
    
    # Check if cache is still valid
    if current_time - data_cache['last_update'] < data_cache['cache_lifetime'] and data_cache['warehouse_status']:
        return data_cache['warehouse_status']
    
    try:
        # Get inventory data from database
        inventory = db_utils.get_inventory()
        
        # Format data for warehouse dashboard
        warehouse_status = []
        
        # Zuerst: Stelle sicher, dass alle erwarteten Produkt-Regal-Kombinationen vorhanden sind
        # Dies ist wichtig, damit wir auch leere Regale korrekt anzeigen
        for shelf_id, expected_product in EXPECTED_PRODUCTS.items():
            found = False
            for inv in inventory:
                inv_shelf_id, inv_product_type = inv[0], inv[1]
                if isinstance(inv_product_type, bytes):
                    inv_product_type = inv_product_type.decode('utf-8', errors='replace')
                
                if inv_shelf_id == shelf_id and inv_product_type.lower() == expected_product.lower():
                    found = True
                    break
            
            # Wenn dieses erwartete Produkt für dieses Regal nicht in der DB ist, 
            # erstelle einen leeren Eintrag dafür
            if not found:
                logger.info(f"Erwartetes Produkt {expected_product} für Regal {shelf_id+1} nicht in DB, erstelle leeren Eintrag")
                # Füge dieses fehlende Produkt-Regal-Paar zum Inventar hinzu
                db_utils.set_initial_inventory(shelf_id, expected_product, 0)
        
        # Jetzt, lade das Inventar erneut, um sicherzustellen, dass wir alle Einträge haben
        inventory = db_utils.get_inventory()
        
        for inv in inventory:
            shelf_id, product_type, initial_count, current_count, last_update = inv
            
            # Ensure no bytes objects
            if isinstance(product_type, bytes):
                product_type = product_type.decode('utf-8', errors='replace')
            
            # NEUE PRÜFUNG: Überspringe Einträge, wenn das Produkt nicht in diesem Regal erwartet wird
            if not is_product_expected_in_shelf(product_type, shelf_id):
                logger.info(f"Überspringe {product_type} in Regal {shelf_id+1}, da es dort nicht erwartet wird")
                continue
            
            # Get thresholds for this product type
            thresholds = STOCK_THRESHOLDS.get(product_type, DEFAULT_THRESHOLDS)
            
            # Determine refill quantity based on target and current count
            refill_quantity = max(0, thresholds['target'] - current_count)
            
            # Determine status based on current count
            if current_count <= thresholds['critical']:
                status = STATUS_CRITICAL
            elif current_count <= thresholds['warning']:
                status = STATUS_WARNING
            else:
                status = STATUS_OK
                
            # Get warehouse rack number from mapping (default to sales rack if not found)
            warehouse_rack = next((w_rack for w_rack, s_rack in RACK_MAPPING.items() 
                               if s_rack == shelf_id + 1), shelf_id + 1)
            
            # Format last update time
            update_time = datetime.fromtimestamp(last_update).strftime("%H:%M:%S")
            
            # Build the warehouse status entry
            entry = {
                'status': status,
                'warehouse_rack': warehouse_rack,
                'sales_rack': shelf_id + 1,  # +1 for display
                'product_type': product_type.capitalize(),
                'current_count': current_count,
                'refill_quantity': refill_quantity,
                'collected': False,  # Default to false
                'refilled': False,   # Default to false
                'last_update': update_time
            }
            
            warehouse_status.append(entry)
            
        # Rest der Funktion bleibt unverändert...
        
        # Sort entries: Critical first, then Warning, then OK
        warehouse_status.sort(key=lambda x: (
            0 if x['status'] == STATUS_CRITICAL else 
            1 if x['status'] == STATUS_WARNING else 2,
            x['warehouse_rack']  # Secondary sort by rack number
        ))
        
        # Update cache
        data_cache['warehouse_status'] = warehouse_status
        data_cache['last_update'] = current_time
        
        return warehouse_status
    
    except Exception as e:
        logger.error(f"Error fetching warehouse data: {e}")
        # Return previous data or empty list on error
        return data_cache.get('warehouse_status', [])

def check_refill_completion(sales_rack, product_type):
    """
    Checks if a rack has been properly refilled by triggering a new inventory scan
    and checking the results.
    
    Returns:
        tuple: (success, new_status, current_count)
    """
    try:
        # Convert sales rack to 0-based index for internal use
        shelf_id = sales_rack - 1
        
        # Get thresholds for this product
        thresholds = STOCK_THRESHOLDS.get(product_type.lower(), DEFAULT_THRESHOLDS)
        
        # Check current inventory directly from database
        current_count = db_utils.get_inventory_count(shelf_id, product_type.lower())
        
        # Determine new status based on current count
        if current_count <= thresholds['critical']:
            new_status = STATUS_CRITICAL
        elif current_count <= thresholds['warning']:
            new_status = STATUS_WARNING
        else:
            new_status = STATUS_OK
            
        return True, new_status, current_count
        
    except Exception as e:
        logger.error(f"Error checking refill completion: {e}")
        return False, "error", 0

@app.route('/')
def index():
    """Main page of the warehouse dashboard"""
    warehouse_data = get_warehouse_data()
    
    # Calculate runtime
    elapsed_time = int(time.time() - start_time)
    hours, remainder = divmod(elapsed_time, 3600)
    minutes, seconds = divmod(remainder, 60)
    runtime = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    return render_template(
        'warehouse_dashboard.html',
        warehouse_data=warehouse_data,
        runtime=runtime,
        last_update=datetime.now().strftime("%H:%M:%S")
    )

@app.route('/api/data')
def get_data():
    """API endpoint for current data (for AJAX updates)"""
    try:
        warehouse_data = get_warehouse_data()
        
        # Calculate runtime
        elapsed_time = int(time.time() - start_time)
        hours, remainder = divmod(elapsed_time, 3600)
        minutes, seconds = divmod(remainder, 60)
        runtime = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        
        # Ensure all data is JSON-serializable
        response_data = sanitize_data({
            'warehouse_data': warehouse_data,
            'runtime': runtime,
            'last_update': datetime.now().strftime("%H:%M:%S")
        })
        
        return jsonify(response_data)
    except Exception as e:
        logger.error(f"Error in API data call: {str(e)}")
        # Return empty response on error
        return jsonify({
            'warehouse_data': [],
            'runtime': "00:00:00",
            'last_update': datetime.now().strftime("%H:%M:%S"),
            'error': str(e)
        })

@app.route('/api/update_status', methods=['POST'])
def update_status():
    """API endpoint to update the collected or refilled status"""
    try:
        data = request.json
        sales_rack = int(data.get('sales_rack'))
        product_type = data.get('product_type')
        field = data.get('field')  # 'collected' or 'refilled'
        value = data.get('value', False)  # boolean
        
        # Update the cached data first
        updated = False
        for item in data_cache['warehouse_status']:
            if item['sales_rack'] == sales_rack and item['product_type'].lower() == product_type.lower():
                item[field] = value
                updated = True
                
                # If refilled is being set to true, check if refill is complete
                if field == 'refilled' and value == True:
                    success, new_status, current_count = check_refill_completion(sales_rack, product_type)
                    if success:
                        item['status'] = new_status
                        item['current_count'] = current_count
                        
                        # Recalculate refill quantity
                        thresholds = STOCK_THRESHOLDS.get(product_type.lower(), DEFAULT_THRESHOLDS)
                        item['refill_quantity'] = max(0, thresholds['target'] - current_count)
                        
                        # Force inventory refresh
                        try:
                            # Signal file for YOLO monitoring to refresh inventory
                            with open('inventory_refresh.signal', 'w') as f:
                                f.write('1')
                            logger.info(f"Sent inventory refresh signal for rack {sales_rack}")
                        except Exception as e:
                            logger.error(f"Error sending inventory refresh signal: {e}")
                
                break
        
        # If updated, re-sort the data
        if updated:
            data_cache['warehouse_status'].sort(key=lambda x: (
                0 if x['status'] == STATUS_CRITICAL else 
                1 if x['status'] == STATUS_WARNING else 2,
                x['warehouse_rack']
            ))
        
        return jsonify({
            'success': updated,
            'message': f"Successfully updated {field} status" if updated else "Item not found"
        })
    except Exception as e:
        logger.error(f"Error updating status: {e}")
        return jsonify({
            'success': False,
            'message': f"Error updating status: {str(e)}"
        })

@app.route('/api/filter', methods=['POST'])
def filter_data():
    """API endpoint to filter data by status"""
    try:
        data = request.json
        status_filter = data.get('status', 'all')
        
        warehouse_data = get_warehouse_data()
        
        # Apply filter if not 'all'
        if status_filter != 'all':
            warehouse_data = [item for item in warehouse_data if item['status'] == status_filter]
        
        return jsonify({
            'success': True,
            'data': warehouse_data
        })
    except Exception as e:
        logger.error(f"Error filtering data: {e}")
        return jsonify({
            'success': False,
            'message': f"Error filtering data: {str(e)}",
            'data': []
        })

@app.route('/api/refresh_inventory', methods=['POST'])
def refresh_inventory():
    """API endpoint to trigger a full inventory refresh"""
    try:
        # Create signal file for YOLO monitoring
        # This file is monitored by yolo_monitor.py and triggers a refresh
        # without requiring a direct import of the module
        with open('inventory_refresh.signal', 'w') as f:
            f.write('1')
            
        # Clear cache to force refresh
        data_cache['last_update'] = 0
        
        return jsonify({
            'success': True,
            'message': "Inventory refresh signal sent"
        })
    except Exception as e:
        logger.error(f"Error sending inventory refresh signal: {e}")
        return jsonify({
            'success': False,
            'message': f"Error sending inventory refresh signal: {str(e)}"
        })

# Create static files for the warehouse dashboard
def setup_static_files():
    """Creates template and static files for the warehouse dashboard"""
    
    # Dashboard HTML Template
    dashboard_html_path = os.path.join(TEMPLATES_DIR, 'warehouse_dashboard.html')
    dashboard_html = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Warehouse Dashboard - Intelligent Shelf System</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/warehouse_style.css') }}">
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <div class="container-fluid">
            <a class="navbar-brand" href="#">
                <i class="bi bi-box-seam me-2"></i>
                Warehouse Dashboard - Intelligent Shelf System
            </a>
            <div class="navbar-text text-light" id="status-display">
                <span class="badge bg-success"><i class="bi bi-check-circle-fill me-1"></i> Connected</span>
                <span class="ms-2">Last Update: <span id="last-update">{{ last_update }}</span></span>
                <span class="ms-2">Runtime: <span id="runtime">{{ runtime }}</span></span>
            </div>
        </div>
    </nav>

    <div class="container-fluid mt-3">
        <div class="row mb-3">
            <div class="col-12">
                <div class="card">
                    <div class="card-header bg-primary text-white d-flex justify-content-between align-items-center">
                        <h5 class="card-title mb-0">
                            <i class="bi bi-list-check me-2"></i>
                            Warehouse Restocking Tasks
                        </h5>
                        <div class="filter-buttons">
                            <button class="btn btn-sm btn-light status-filter active" data-status="all">All</button>
                            <button class="btn btn-sm btn-danger status-filter" data-status="critical">Critical</button>
                            <button class="btn btn-sm btn-warning status-filter" data-status="warning">Warning</button>
                            <button class="btn btn-sm btn-success status-filter" data-status="ok">OK</button>
                        </div>
                    </div>
                    <div class="card-body">
                        <div class="table-responsive">
                            <table class="table table-hover" id="warehouse-table">
                                <thead class="table-light">
                                    <tr>
                                        <th style="width: 60px">Status</th>
                                        <th>Warehouse&nbsp;Rack</th>
                                        <th>Sales&nbsp;Rack</th>
                                        <th>Product</th>
                                        <th>Current Stock</th>
                                        <th>Refill Qty</th>
                                        <th>Collected</th>
                                        <th>Refilled</th>
                                        <th>Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for item in warehouse_data %}
                                    <tr class="status-{{ item.status }}" data-sales-rack="{{ item.sales_rack }}" data-product-type="{{ item.product_type }}">
                                        <td class="text-center">
                                            <div class="status-light status-{{ item.status }}" 
                                                 title="{{ item.status|capitalize }}"></div>
                                        </td>
                                        <td>{{ item.warehouse_rack }}</td>
                                        <td>{{ item.sales_rack }}</td>
                                        <td>{{ item.product_type }}</td>
                                        <td>{{ item.current_count }}</td>
                                        <td>{{ item.refill_quantity }}</td>
                                        <td class="text-center">
                                            <input type="checkbox" class="form-check-input task-checkbox" 
                                                   data-field="collected" 
                                                   {% if item.collected %}checked{% endif %}>
                                        </td>
                                        <td class="text-center">
                                            <input type="checkbox" class="form-check-input task-checkbox" 
                                                   data-field="refilled" 
                                                   {% if item.refilled %}checked{% endif %}
                                                   {% if not item.collected %}disabled{% endif %}>
                                        </td>
                                        <td>
                                            <button class="btn btn-sm btn-outline-primary verify-btn"
                                                    {% if not item.refilled %}disabled{% endif %}>
                                                <i class="bi bi-eye"></i> Verify
                                            </button>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                    <div class="card-footer d-flex justify-content-center">
                        <button class="btn btn-primary" id="refresh-inventory-btn">
                            <i class="bi bi-arrow-clockwise me-1"></i>
                            Refresh Inventory
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Toast Notifications -->
    <div class="toast-container position-fixed bottom-0 end-0 p-3">
        <div id="notificationToast" class="toast" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="toast-header">
                <strong class="me-auto" id="toastTitle">Notification</strong>
                <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
            <div class="toast-body" id="toastMessage">
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="{{ url_for('static', filename='js/warehouse_dashboard.js') }}"></script>
</body>
</html>"""

    # CSS file for warehouse dashboard
    css_path = os.path.join(CSS_DIR, 'warehouse_style.css')
    css_content = """/* Custom styles for the warehouse dashboard */
body {
    background-color: #f5f5f5;
}

.navbar {
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

.card {
    box-shadow: 0 0.125rem 0.25rem rgba(0, 0, 0, 0.075);
    margin-bottom: 1.5rem;
    border-radius: 0.5rem;
    border: none;
}

.card-header {
    border-top-left-radius: 0.5rem !important;
    border-top-right-radius: 0.5rem !important;
}

.table {
    margin-bottom: 0;
}

.table th {
    border-top: none;
    font-weight: 600;
}

/* Status light styles */
.status-light {
    width: 20px;
    height: 20px;
    border-radius: 50%;
    margin: 0 auto;
    border: 1px solid rgba(0, 0, 0, 0.1);
    box-shadow: 0 0 5px rgba(0, 0, 0, 0.2);
}

.status-critical {
    background-color: #dc3545;
    box-shadow: 0 0 8px rgba(220, 53, 69, 0.7);
    animation: pulse-red 2s infinite;
}

.status-warning {
    background-color: #fd7e14;
    box-shadow: 0 0 8px rgba(253, 126, 20, 0.7);
}

.status-ok {
    background-color: #198754;
    box-shadow: 0 0 8px rgba(25, 135, 84, 0.7);
}

@keyframes pulse-red {
    0% {
        box-shadow: 0 0 0 0 rgba(220, 53, 69, 0.7);
    }
    70% {
        box-shadow: 0 0 0 10px rgba(220, 53, 69, 0);
    }
    100% {
        box-shadow: 0 0 0 0 rgba(220, 53, 69, 0);
    }
}

/* Row coloring by status */
tr.status-critical {
    background-color: rgba(220, 53, 69, 0.1);
}

tr.status-warning {
    background-color: rgba(253, 126, 20, 0.1);
}

/* Animation for rows */
@keyframes highlight {
    0% { background-color: rgba(255, 251, 0, 0.3); }
    100% { background-color: transparent; }
}

tr.highlight {
    animation: highlight 2s ease-out;
}

/* Button and filter styles */
.filter-buttons .btn {
    margin-left: 5px;
    border: 1px solid rgba(255, 255, 255, 0.5);
}

.filter-buttons .btn.active {
    font-weight: bold;
    box-shadow: 0 0 0 0.2rem rgba(255, 255, 255, 0.25);
}

.verify-btn:disabled {
    cursor: not-allowed;
    opacity: 0.5;
}

.task-checkbox {
    width: 20px;
    height: 20px;
    cursor: pointer;
}

.task-checkbox:disabled {
    cursor: not-allowed;
    opacity: 0.5;
}

/* Responsive adjustments */
@media (max-width: 768px) {
    .navbar-text {
        display: none;
    }
    
    .filter-buttons {
        margin-top: 10px;
    }
    
    .card-header {
        flex-direction: column;
        align-items: flex-start !important;
    }
    
    .filter-buttons {
        margin-top: 10px;
        width: 100%;
        display: flex;
        justify-content: space-between;
    }
    
    .filter-buttons .btn {
        flex-grow: 1;
        margin: 0 2px;
        padding: 0.25rem 0.5rem;
        font-size: 0.75rem;
    }
}"""

    # JavaScript file for warehouse dashboard
    js_path = os.path.join(JS_DIR, 'warehouse_dashboard.js')
    js_content = """// Warehouse Dashboard functionality
document.addEventListener('DOMContentLoaded', function() {
    // Toast notification function
    function showNotification(title, message, isSuccess = true) {
        const toastEl = document.getElementById('notificationToast');
        const toast = new bootstrap.Toast(toastEl);
        
        document.getElementById('toastTitle').textContent = title;
        document.getElementById('toastMessage').textContent = message;
        
        // Set color based on success/failure
        toastEl.classList.remove('bg-danger', 'bg-success', 'text-white');
        if (!isSuccess) {
            toastEl.classList.add('bg-danger', 'text-white');
        } else {
            toastEl.classList.add('bg-success', 'text-white');
        }
        
        toast.show();
    }
    
    // Update data function
    function updateData() {
        fetch('/api/data')
            .then(response => response.json())
            .then(data => {
                // Update the warehouse table
                updateWarehouseTable(data.warehouse_data);
                
                // Update last update time and runtime
                document.getElementById('last-update').textContent = data.last_update;
                document.getElementById('runtime').textContent = data.runtime;

                // Update status indicator
                document.querySelector('#status-display .badge').className = 'badge bg-success';
                document.querySelector('#status-display .badge').innerHTML = '<i class="bi bi-check-circle-fill me-1"></i> Connected';
            })
            .catch(error => {
                console.error('Error fetching data:', error);
                document.querySelector('#status-display .badge').className = 'badge bg-danger';
                document.querySelector('#status-display .badge').innerHTML = '<i class="bi bi-x-circle-fill me-1"></i> Disconnected';
            });
    }
    
    // Function to update the warehouse table
    function updateWarehouseTable(warehouseData) {
        const table = document.getElementById('warehouse-table');
        if (!table) return;
        
        const tbody = table.querySelector('tbody');
        
        // Remember current filter state
        const activeFilter = document.querySelector('.status-filter.active').dataset.status;
        
        // Old rows for animation and state preservation
        const oldRows = Array.from(tbody.querySelectorAll('tr'));
        const oldRowStates = {};
        
        // Store checkbox states from old rows
        oldRows.forEach(row => {
            const salesRack = row.dataset.salesRack;
            const productType = row.dataset.productType;
            const key = `${salesRack}-${productType}`;
            
            oldRowStates[key] = {
                collected: row.querySelector('[data-field="collected"]').checked,
                refilled: row.querySelector('[data-field="refilled"]').checked
            };
        });
        
        // Clear table
        tbody.innerHTML = '';
        
        // Filter data if necessary
        let filteredData = warehouseData;
        if (activeFilter !== 'all') {
            filteredData = warehouseData.filter(item => item.status === activeFilter);
        }
        
        // Add new rows
        filteredData.forEach(item => {
            const row = document.createElement('tr');
            row.className = `status-${item.status}`;
            row.dataset.salesRack = item.sales_rack;
            row.dataset.productType = item.product_type;
            
            // Restore checkbox states if row existed before
            const key = `${item.sales_rack}-${item.product_type}`;
            if (oldRowStates[key]) {
                item.collected = oldRowStates[key].collected;
                item.refilled = oldRowStates[key].refilled;
            }
            
            row.innerHTML = `
                <td class="text-center">
                    <div class="status-light status-${item.status}" 
                         title="${item.status.charAt(0).toUpperCase() + item.status.slice(1)}"></div>
                </td>
                <td>${item.warehouse_rack}</td>
                <td>${item.sales_rack}</td>
                <td>${item.product_type}</td>
                <td>${item.current_count}</td>
                <td>${item.refill_quantity}</td>
                <td class="text-center">
                    <input type="checkbox" class="form-check-input task-checkbox" 
                           data-field="collected" 
                           ${item.collected ? 'checked' : ''}>
                </td>
                <td class="text-center">
                    <input type="checkbox" class="form-check-input task-checkbox" 
                           data-field="refilled" 
                           ${item.refilled ? 'checked' : ''}
                           ${!item.collected ? 'disabled' : ''}>
                </td>
                <td>
                    <button class="btn btn-sm btn-outline-primary verify-btn"
                            ${!item.refilled ? 'disabled' : ''}>
                        <i class="bi bi-eye"></i> Verify
                    </button>
                </td>
            `;
            
            tbody.appendChild(row);
            
            // Add animation for new or changed rows
            const oldKeyExists = !!oldRowStates[key];
            if (!oldKeyExists) {
                row.classList.add('highlight');
            }
        });
        
        // Re-attach event listeners for checkboxes
        attachCheckboxListeners();
        
        // Re-attach event listeners for verify buttons
        attachVerifyButtonListeners();
    }
    
    // Function to attach event listeners to task checkboxes
    function attachCheckboxListeners() {
        document.querySelectorAll('.task-checkbox').forEach(checkbox => {
            checkbox.addEventListener('change', function() {
                const row = this.closest('tr');
                const salesRack = parseInt(row.dataset.salesRack);
                const productType = row.dataset.productType;
                const field = this.dataset.field;
                const value = this.checked;
                
                // Update the related UI components first
                if (field === 'collected' && !value) {
                    // If unchecking collected, also uncheck refilled
                    const refilledCheckbox = row.querySelector('[data-field="refilled"]');
                    refilledCheckbox.checked = false;
                    refilledCheckbox.disabled = true;
                    
                    // Disable verify button
                    row.querySelector('.verify-btn').disabled = true;
                } else if (field === 'collected' && value) {
                    // If checking collected, enable refilled checkbox
                    row.querySelector('[data-field="refilled"]').disabled = false;
                } else if (field === 'refilled') {
                    // Update verify button based on refilled status
                    row.querySelector('.verify-btn').disabled = !value;
                }
                
                // Send update to server
                fetch('/api/update_status', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        sales_rack: salesRack,
                        product_type: productType,
                        field: field,
                        value: value
                    })
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        // Refresh data after update
                        updateData();
                    } else {
                        showNotification('Update Error', data.message, false);
                        // Revert the checkbox
                        this.checked = !value;
                    }
                })
                .catch(error => {
                    console.error('Error updating status:', error);
                    showNotification('Error', 'Failed to update status. Please try again.', false);
                    // Revert the checkbox
                    this.checked = !value;
                });
            });
        });
    }
    
    // Function to attach event listeners to verify buttons
    function attachVerifyButtonListeners() {
        document.querySelectorAll('.verify-btn').forEach(button => {
            button.addEventListener('click', function() {
                const row = this.closest('tr');
                const salesRack = parseInt(row.dataset.salesRack);
                const productType = row.dataset.productType;
                
                // Show that verification is in progress
                this.innerHTML = '<i class="bi bi-hourglass-split"></i> Verifying...';
                this.disabled = true;
                
                // Get references to the checkboxes
                const collectedCheckbox = row.querySelector('[data-field="collected"]');
                const refilledCheckbox = row.querySelector('[data-field="refilled"]');
                
                // Trigger inventory refresh and status check
                fetch('/api/refresh_inventory', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    }
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        showNotification('Verification', 'Inventory verification started. Results will be updated shortly.');
                        
                        // Reset the checkboxes in the UI
                        collectedCheckbox.checked = false;
                        refilledCheckbox.checked = false;
                        refilledCheckbox.disabled = true;
                        
                        // Send updates to the server to reset the states
                        Promise.all([
                            // Reset collected state
                            fetch('/api/update_status', {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/json',
                                },
                                body: JSON.stringify({
                                    sales_rack: salesRack,
                                    product_type: productType,
                                    field: 'collected',
                                    value: false
                                })
                            }),
                            // Reset refilled state
                            fetch('/api/update_status', {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/json',
                                },
                                body: JSON.stringify({
                                    sales_rack: salesRack,
                                    product_type: productType,
                                    field: 'refilled',
                                    value: false
                                })
                            })
                        ])
                        .then(() => {
                            // Wait a moment for inventory to refresh, then update data
                            setTimeout(() => {
                                updateData();
                            }, 3000);
                        })
                        .catch(error => {
                            console.error('Error resetting checkbox states:', error);
                        });
                    } else {
                        showNotification('Verification Error', data.message, false);
                    }
                    
                    // Reset button after a delay
                    setTimeout(() => {
                        this.innerHTML = '<i class="bi bi-eye"></i> Verify';
                        this.disabled = false;
                    }, 3000);
                })
                .catch(error => {
                    console.error('Error during verification:', error);
                    showNotification('Error', 'Failed to verify inventory. Please try again.', false);
                    
                    // Reset button
                    this.innerHTML = '<i class="bi bi-eye"></i> Verify';
                    this.disabled = false;
                });
            });
        });
    }
    
    // Function to handle filter button clicks
    function handleFilterButtons() {
        document.querySelectorAll('.status-filter').forEach(button => {
            button.addEventListener('click', function() {
                // Update active button styling
                document.querySelectorAll('.status-filter').forEach(btn => {
                    btn.classList.remove('active');
                });
                this.classList.add('active');
                
                const statusFilter = this.dataset.status;
                
                // Get current data and apply filter
                fetch('/api/filter', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        status: statusFilter
                    })
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        updateWarehouseTable(data.data);
                    } else {
                        showNotification('Filter Error', data.message, false);
                    }
                })
                .catch(error => {
                    console.error('Error applying filter:', error);
                    showNotification('Error', 'Failed to apply filter. Please try again.', false);
                });
            });
        });
    }
    
    // Refresh inventory button handler
    document.getElementById('refresh-inventory-btn').addEventListener('click', function() {
        this.disabled = true;
        this.innerHTML = '<i class="bi bi-hourglass-split"></i> Refreshing...';
        
        fetch('/api/refresh_inventory', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showNotification('Refresh', 'Inventory refresh started. Data will update shortly.');
                
                // Wait a moment for inventory to refresh, then update data
                setTimeout(() => {
                    updateData();
                }, 3000);
            } else {
                showNotification('Refresh Error', data.message, false);
            }
            
            // Reset button after a delay
            setTimeout(() => {
                this.innerHTML = '<i class="bi bi-arrow-clockwise me-1"></i> Refresh Inventory';
                this.disabled = false;
            }, 3000);
        })
        .catch(error => {
            console.error('Error refreshing inventory:', error);
            showNotification('Error', 'Failed to refresh inventory. Please try again.', false);
            
            // Reset button
            this.innerHTML = '<i class="bi bi-arrow-clockwise me-1"></i> Refresh Inventory';
            this.disabled = false;
        });
    });
    
    // Set up filter buttons
    handleFilterButtons();
    
    // Initial checkbox and button setup
    attachCheckboxListeners();
    attachVerifyButtonListeners();
    
    // Automatic data refresh every 5 seconds
    setInterval(updateData, 5000);
    
    // Initial data load
    updateData();
});"""

    # Write files
    try:
        with open(dashboard_html_path, 'w', encoding='utf-8') as f:
            f.write(dashboard_html)
            logger.info(f"Dashboard template created: {dashboard_html_path}")
            
        with open(css_path, 'w', encoding='utf-8') as f:
            f.write(css_content)
            logger.info(f"CSS file created: {css_path}")
            
        with open(js_path, 'w', encoding='utf-8') as f:
            f.write(js_content)
            logger.info(f"JavaScript file created: {js_path}")
            
        # Check if files were created
        for path in [dashboard_html_path, css_path, js_path]:
            if not os.path.exists(path):
                logger.error(f"Error: File was not created: {path}")
                return False
                
        return True
    except Exception as e:
        logger.error(f"Error creating static files: {e}")
        return False

# Initialize database if not already done
db_utils.init_db()

def start_warehouse_dashboard(host='0.0.0.0', port=5002, debug=False):
    """Starts the warehouse dashboard on the specified port"""
    # Set up static files BEFORE starting the server    
    logger.info(f"Warehouse dashboard starting on http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)

if __name__ == "__main__":
    # Ensure database is initialized
    db_utils.init_db()
    
    # Start server
    start_warehouse_dashboard(debug=True)