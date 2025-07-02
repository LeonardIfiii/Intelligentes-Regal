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

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Determine current directory
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
logger.info(f"Working directory: {CURRENT_DIR}")

# Ensure required directories exist
TEMPLATES_DIR = os.path.join(CURRENT_DIR, 'kassen_templates')
STATIC_DIR = os.path.join(CURRENT_DIR, 'kassen_static')
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
app.config['SECRET_KEY'] = 'smart-shelf-web-kassensystem'

# Product friendly names and prices (copied from original kassensystem.py)
product_names = {
    "cup": "Eco-Kaffeebecher",
    "book": "Smart Living Handbuch",
    "wine glass": "Kristall Weinglas",
    "bottle": "Isolierte Wasserflasche",
    "fork": "Edelstahl-Gabel",
    "spoon": "Edelstahl-Löffel",
    "glass": "Premium Trinkglas"
}

product_prices = {
    "cup": 8.99,
    "book": 24.99,
    "wine glass": 12.99,
    "bottle": 19.99,
    "fork": 3.99,
    "spoon": 3.99,
    "glass": 6.99
}

# Helper function to sanitize data for JSON serialization
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

# Helper function to get unpaid items from database
def get_unpaid_items():
    try:
        # Get all unpaid events
        events = db_utils.get_all_events()
        
        unpaid_items = []
        for event in events:
            # Check if this is a removal event that's not paid and not resolved
            # Column order in DB: id, shelf_id, product_type, event_type, event_time, resolved, resolution_time, status
            if len(event) >= 8:
                event_id, shelf_id, product_type, event_type, event_time, resolved, resolution_time, status = event[:8]
                
                # Get quantity if available (newer DB versions)
                quantity = 1
                if len(event) > 8:
                    quantity = event[8]
                
                if isinstance(product_type, bytes):
                    product_type = product_type.decode('utf-8')
                
                if isinstance(event_type, bytes):
                    event_type = event_type.decode('utf-8')
                
                if isinstance(status, bytes):
                    status = status.decode('utf-8')
                
                # Check if this is an unpaid removal event
                if event_type == "removal" and status == "not paid" and resolved == 0:
                    # Format time
                    time_str = time.strftime("%H:%M:%S", time.localtime(event_time))
                    
                    # Get price
                    price = product_prices.get(product_type, 0.0)
                    total = price * quantity
                    
                    # Get friendly product name
                    product_name = product_names.get(product_type, product_type.capitalize())
                    
                    # Add to unpaid events
                    unpaid_items.append({
                        'event_id': event_id,
                        'shelf_id': shelf_id + 1,  # +1 for display
                        'product_type': product_type,
                        'product_name': product_name,
                        'time': time_str,
                        'quantity': quantity,
                        'price': price,
                        'total': total
                    })
        
        return unpaid_items
    except Exception as e:
        logger.error(f"Error getting unpaid items: {e}")
        return []

# Process payment for items
def process_payment(event_id):
    try:
        # Get event ID
        event_id = int(event_id)
        
        # Connect to database
        conn = db_utils.sqlite3.connect(db_utils.DB_NAME)
        c = conn.cursor()
        
        # First get shelf_id and product_type using the event ID
        c.execute("SELECT shelf_id, product_type, quantity FROM events WHERE id = ?", (event_id,))
        result = c.fetchone()
        
        if result:
            shelf_id, product_type, quantity = result
            
            if isinstance(product_type, bytes):
                product_type = product_type.decode('utf-8')
            
            # Update the event: set status to 'paid' and resolved to 1
            resolution_time = int(time.time())
            c.execute('''
                UPDATE events
                SET status = 'paid', resolved = 1, resolution_time = ?
                WHERE id = ?
            ''', (resolution_time, event_id))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Item paid: {product_names.get(product_type, product_type)}")
            return True, f"Artikel bezahlt: {product_names.get(product_type, product_type)}"
        else:
            # Event not found
            conn.close()
            error_msg = f"Event mit ID {event_id} nicht gefunden."
            logger.error(error_msg)
            return False, error_msg
    except Exception as e:
        error_msg = f"Fehler bei der Bezahlung: {str(e)}"
        logger.error(error_msg)
        return False, error_msg

@app.route('/')
def index():
    """Main cash register page"""
    unpaid_items = get_unpaid_items()
    
    # Calculate total
    total_sum = sum(item['total'] for item in unpaid_items)
    
    return render_template(
        'kassensystem.html',
        unpaid_items=unpaid_items,
        total_sum=total_sum,
        last_update=datetime.now().strftime("%H:%M:%S")
    )

@app.route('/api/data')
def get_data():
    """API endpoint for current data (for AJAX updates)"""
    try:
        unpaid_items = get_unpaid_items()
        
        # Calculate total
        total_sum = sum(item['total'] for item in unpaid_items)
        
        response_data = sanitize_data({
            'unpaid_items': unpaid_items,
            'total_sum': total_sum,
            'last_update': datetime.now().strftime("%H:%M:%S")
        })
        
        return jsonify(response_data)
    except Exception as e:
        logger.error(f"Error in API data call: {str(e)}")
        return jsonify({
            'unpaid_items': [],
            'total_sum': 0,
            'last_update': datetime.now().strftime("%H:%M:%S"),
            'error': str(e)
        })

@app.route('/api/pay', methods=['POST'])
def pay_items():
    """API endpoint to pay for items"""
    try:
        data = request.json
        event_ids = data.get('event_ids', [])
        
        if not event_ids:
            return jsonify({
                'success': False,
                'message': 'Keine Artikel ausgewählt.'
            })
        
        results = []
        success_count = 0
        
        for event_id in event_ids:
            success, message = process_payment(event_id)
            results.append({
                'event_id': event_id,
                'success': success,
                'message': message
            })
            if success:
                success_count += 1
        
        overall_success = success_count > 0
        if success_count == len(event_ids):
            message = f"Alle Artikel ({success_count}) erfolgreich bezahlt."
        elif success_count > 0:
            message = f"{success_count} von {len(event_ids)} Artikeln bezahlt. Einige Zahlungen fehlgeschlagen."
        else:
            message = "Keine Artikel konnten bezahlt werden."
        
        return jsonify({
            'success': overall_success,
            'message': message,
            'results': results
        })
    except Exception as e:
        error_msg = f"Fehler bei der Bezahlung: {str(e)}"
        logger.error(error_msg)
        return jsonify({
            'success': False,
            'message': error_msg,
            'results': []
        })

# Create static files for the web interface
def setup_static_files():
    """Creates template and static files for the web kassensystem"""
    
    # HTML Template
    html_path = os.path.join(TEMPLATES_DIR, 'kassensystem.html')
    html_content = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Intelligentes Regal - Kassensystem</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/kassen_style.css') }}">
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <div class="container-fluid">
            <a class="navbar-brand" href="#">
                <i class="bi bi-cash-register me-2"></i>
                Intelligentes Regal - Kassensystem
            </a>
            <div class="navbar-text text-light" id="status-display">
                <span class="badge bg-success"><i class="bi bi-check-circle-fill me-1"></i> Verbunden</span>
                <span class="ms-2">Letzte Aktualisierung: <span id="last-update">{{ last_update }}</span></span>
            </div>
        </div>
    </nav>

    <div class="container-fluid mt-3">
        <div class="row mb-3">
            <div class="col-12">
                <div class="card">
                    <div class="card-header bg-primary text-white d-flex justify-content-between align-items-center">
                        <h5 class="card-title mb-0">
                            <i class="bi bi-basket me-2"></i>
                            Unbezahlte Artikel
                        </h5>
                        <div class="header-actions">
                            <span class="total-display me-3">Gesamtsumme: <span id="total-sum">{{ "%.2f"|format(total_sum) }}</span> €</span>
                            <button class="btn btn-sm btn-light" id="refresh-btn">
                                <i class="bi bi-arrow-clockwise me-1"></i>
                                Aktualisieren
                            </button>
                        </div>
                    </div>
                    <div class="card-body">
                        <div class="table-responsive">
                            <table class="table table-hover" id="unpaid-items-table">
                                <thead class="table-light">
                                    <tr>
                                        <th style="width: 40px;">
                                            <div class="form-check">
                                                <input class="form-check-input" type="checkbox" id="select-all">
                                            </div>
                                        </th>
                                        <th>Regal</th>
                                        <th>Produkt</th>
                                        <th>Zeit</th>
                                        <th>Menge</th>
                                        <th>Preis</th>
                                        <th>Gesamt</th>
                                        <th>Aktionen</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for item in unpaid_items %}
                                    <tr data-event-id="{{ item.event_id }}">
                                        <td>
                                            <div class="form-check">
                                                <input class="form-check-input item-checkbox" type="checkbox" value="{{ item.event_id }}">
                                            </div>
                                        </td>
                                        <td>Regal {{ item.shelf_id }}</td>
                                        <td>{{ item.product_name }}</td>
                                        <td>{{ item.time }}</td>
                                        <td>{{ item.quantity }}</td>
                                        <td>{{ "%.2f"|format(item.price) }} €</td>
                                        <td>{{ "%.2f"|format(item.total) }} €</td>
                                        <td>
                                            <button class="btn btn-sm btn-success pay-single-btn" data-event-id="{{ item.event_id }}">
                                                <i class="bi bi-cash me-1"></i>
                                                Bezahlen
                                            </button>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                    {% if not unpaid_items %}
                                    <tr>
                                        <td colspan="8" class="text-center py-4">
                                            <i class="bi bi-info-circle me-2"></i>
                                            Keine unbezahlten Artikel vorhanden
                                        </td>
                                    </tr>
                                    {% endif %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                    <div class="card-footer d-flex justify-content-between align-items-center">
                        <span class="selected-items-count">0 Artikel ausgewählt</span>
                        <div>
                            <button class="btn btn-primary" id="pay-selected-btn" disabled>
                                <i class="bi bi-cash-coin me-1"></i>
                                Ausgewählte bezahlen
                            </button>
                            <button class="btn btn-success" id="pay-all-btn" {% if not unpaid_items %}disabled{% endif %}>
                                <i class="bi bi-cash-stack me-1"></i>
                                Alle bezahlen
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Toast Notification -->
    <div class="toast-container position-fixed bottom-0 end-0 p-3">
        <div id="notification-toast" class="toast" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="toast-header">
                <strong class="me-auto" id="toast-title">Benachrichtigung</strong>
                <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
            <div class="toast-body" id="toast-message">
            </div>
        </div>
    </div>

    <!-- Confirmation Modal -->
    <div class="modal fade" id="confirm-modal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Bestätigung</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <p id="confirm-message"></p>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Abbrechen</button>
                    <button type="button" class="btn btn-primary" id="confirm-button">Bestätigen</button>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="{{ url_for('static', filename='js/kassensystem.js') }}"></script>
</body>
</html>"""

    # CSS file
    css_path = os.path.join(CSS_DIR, 'kassen_style.css')
    css_content = """/* Custom styles for the kassensystem */
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

.total-display {
    font-weight: bold;
    font-size: 1.1rem;
    color: white;
}

#total-sum {
    min-width: 60px;
    display: inline-block;
}

.form-check-input[type="checkbox"] {
    width: 20px;
    height: 20px;
    cursor: pointer;
}

.selected-items-count {
    font-weight: 500;
    color: #6c757d;
}

/* Hover effect for rows */
.table-hover tbody tr:hover {
    background-color: rgba(0, 123, 255, 0.05);
}

/* Animation for new or changed rows */
@keyframes highlight {
    0% { background-color: rgba(255, 251, 0, 0.3); }
    100% { background-color: transparent; }
}

.highlight {
    animation: highlight 2s ease-out;
}

/* Responsive adjustments */
@media (max-width: 768px) {
    .navbar-text {
        display: none;
    }
    
    .header-actions {
        flex-direction: column;
        align-items: flex-start;
    }
    
    .total-display {
        margin-bottom: 0.5rem;
    }
    
    .card-footer {
        flex-direction: column;
        gap: 1rem;
    }
    
    .card-footer > div {
        display: flex;
        flex-direction: column;
        width: 100%;
        gap: 0.5rem;
    }
    
    .btn {
        width: 100%;
    }
}

/* Toast styles */
.toast {
    opacity: 1 !important;
}

.toast.bg-success .toast-header {
    background-color: rgba(25, 135, 84, 0.2);
    color: #198754;
}

.toast.bg-danger .toast-header {
    background-color: rgba(220, 53, 69, 0.2);
    color: #dc3545;
}"""

    # JavaScript file
    js_path = os.path.join(JS_DIR, 'kassensystem.js')
    js_content = """// Kassensystem functionality
document.addEventListener('DOMContentLoaded', function() {
    // Toast notification function
    function showNotification(title, message, isSuccess = true) {
        const toastEl = document.getElementById('notification-toast');
        const toast = new bootstrap.Toast(toastEl);
        
        document.getElementById('toast-title').textContent = title;
        document.getElementById('toast-message').textContent = message;
        
        // Set color based on success/failure
        toastEl.classList.remove('bg-danger', 'bg-success', 'text-white');
        if (!isSuccess) {
            toastEl.classList.add('bg-danger', 'text-white');
        } else {
            toastEl.classList.add('bg-success', 'text-white');
        }
        
        toast.show();
    }

    // Confirmation dialog function
    function showConfirmDialog(message, callback) {
        const modal = new bootstrap.Modal(document.getElementById('confirm-modal'));
        document.getElementById('confirm-message').textContent = message;
        
        // Set confirm button action
        const confirmButton = document.getElementById('confirm-button');
        confirmButton.onclick = function() {
            modal.hide();
            callback();
        };
        
        modal.show();
    }
    
    // Update data function
    function updateData() {
        fetch('/api/data')
            .then(response => response.json())
            .then(data => {
                updateUnpaidItemsTable(data.unpaid_items);
                
                // Update total sum
                document.getElementById('total-sum').textContent = data.total_sum.toFixed(2);
                
                // Enable/disable pay all button
                const payAllBtn = document.getElementById('pay-all-btn');
                payAllBtn.disabled = data.unpaid_items.length === 0;
                
                // Update last update time
                document.getElementById('last-update').textContent = data.last_update;
                
                // Update status indicator
                document.querySelector('#status-display .badge').className = 'badge bg-success';
                document.querySelector('#status-display .badge').innerHTML = '<i class="bi bi-check-circle-fill me-1"></i> Verbunden';
            })
            .catch(error => {
                console.error('Error fetching data:', error);
                document.querySelector('#status-display .badge').className = 'badge bg-danger';
                document.querySelector('#status-display .badge').innerHTML = '<i class="bi bi-x-circle-fill me-1"></i> Getrennt';
            });
    }
    
    // Function to update the unpaid items table
    function updateUnpaidItemsTable(unpaidItems) {
        const table = document.getElementById('unpaid-items-table');
        if (!table) return;
        
        const tbody = table.querySelector('tbody');
        
        // Remember selections
        const selectedItems = Array.from(document.querySelectorAll('.item-checkbox:checked')).map(cb => cb.value);
        
        // Clear table
        tbody.innerHTML = '';
        
        if (unpaidItems.length === 0) {
            // No items
            const row = document.createElement('tr');
            row.innerHTML = `
                <td colspan="8" class="text-center py-4">
                    <i class="bi bi-info-circle me-2"></i>
                    Keine unbezahlten Artikel vorhanden
                </td>
            `;
            tbody.appendChild(row);
        } else {
            // Add items
            unpaidItems.forEach(item => {
                const row = document.createElement('tr');
                row.dataset.eventId = item.event_id;
                
                row.innerHTML = `
                    <td>
                        <div class="form-check">
                            <input class="form-check-input item-checkbox" type="checkbox" value="${item.event_id}" ${selectedItems.includes(item.event_id.toString()) ? 'checked' : ''}>
                        </div>
                    </td>
                    <td>Regal ${item.shelf_id}</td>
                    <td>${item.product_name}</td>
                    <td>${item.time}</td>
                    <td>${item.quantity}</td>
                    <td>${item.price.toFixed(2)} €</td>
                    <td>${item.total.toFixed(2)} €</td>
                    <td>
                        <button class="btn btn-sm btn-success pay-single-btn" data-event-id="${item.event_id}">
                            <i class="bi bi-cash me-1"></i>
                            Bezahlen
                        </button>
                    </td>
                `;
                
                tbody.appendChild(row);
            });
            
            // Re-attach event listeners for pay buttons
            attachPayButtonListeners();
            
            // Re-attach event listeners for checkboxes
            attachCheckboxListeners();
            
            // Update selection count
            updateSelectionCount();
        }
    }
    
    // Function to attach event listeners to pay buttons
    function attachPayButtonListeners() {
        document.querySelectorAll('.pay-single-btn').forEach(button => {
            button.addEventListener('click', function() {
                const eventId = this.getAttribute('data-event-id');
                
                showConfirmDialog('Möchten Sie diesen Artikel bezahlen?', function() {
                    payItems([eventId]);
                });
            });
        });
    }
    
    // Function to attach event listeners to checkboxes
    function attachCheckboxListeners() {
        // Select all checkbox
        const selectAll = document.getElementById('select-all');
        if (selectAll) {
            selectAll.addEventListener('change', function() {
                document.querySelectorAll('.item-checkbox').forEach(checkbox => {
                    checkbox.checked = this.checked;
                });
                updateSelectionCount();
            });
        }
        
        // Individual checkboxes
        document.querySelectorAll('.item-checkbox').forEach(checkbox => {
            checkbox.addEventListener('change', function() {
                updateSelectionCount();
                
                // Update select all checkbox
                if (!this.checked) {
                    document.getElementById('select-all').checked = false;
                } else {
                    const allChecked = Array.from(document.querySelectorAll('.item-checkbox')).every(cb => cb.checked);
                    document.getElementById('select-all').checked = allChecked;
                }
            });
        });
    }
    
    // Function to update selection count
    function updateSelectionCount() {
        const selectedCount = document.querySelectorAll('.item-checkbox:checked').length;
        document.querySelector('.selected-items-count').textContent = `${selectedCount} Artikel ausgewählt`;
        
        // Enable/disable pay selected button
        document.getElementById('pay-selected-btn').disabled = selectedCount === 0;
    }
    
    // Function to pay for items
    function payItems(eventIds) {
        fetch('/api/pay', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                event_ids: eventIds
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showNotification('Bezahlung erfolgreich', data.message, true);
                
                // Refresh data
                updateData();
            } else {
                showNotification('Fehler', data.message, false);
            }
        })
        .catch(error => {
            console.error('Error paying items:', error);
            showNotification('Fehler', 'Ein unerwarteter Fehler ist aufgetreten.', false);
        });
    }
    
    // Pay all items
    document.getElementById('pay-all-btn').addEventListener('click', function() {
        const items = Array.from(document.querySelectorAll('.item-checkbox')).map(cb => cb.value);
        
        if (items.length === 0) {
            showNotification('Information', 'Keine Artikel zum Bezahlen vorhanden.', false);
            return;
        }
        
        const totalSum = document.getElementById('total-sum').textContent;
        showConfirmDialog(`Möchten Sie alle Artikel im Wert von ${totalSum} € bezahlen?`, function() {
            payItems(items);
        });
    });
    
    // Pay selected items
    document.getElementById('pay-selected-btn').addEventListener('click', function() {
        const selectedItems = Array.from(document.querySelectorAll('.item-checkbox:checked')).map(cb => cb.value);
        
        if (selectedItems.length === 0) {
            showNotification('Information', 'Keine Artikel ausgewählt.', false);
            return;
        }
        
        showConfirmDialog(`Möchten Sie die ausgewählten Artikel (${selectedItems.length} Stück) bezahlen?`, function() {
            payItems(selectedItems);
        });
    });
    
    // Refresh button
    document.getElementById('refresh-btn').addEventListener('click', function() {
        this.disabled = true;
        this.innerHTML = '<i class="bi bi-hourglass-split"></i> Aktualisiere...';
        
        updateData().finally(() => {
            setTimeout(() => {
                this.disabled = false;
                this.innerHTML = '<i class="bi bi-arrow-clockwise me-1"></i> Aktualisieren';
            }, 500);
        });
    });
    
    // Initial setup
    attachPayButtonListeners();
    attachCheckboxListeners();
    updateSelectionCount();
    
    // Auto-refresh every 3 seconds
    setInterval(updateData, 3000);
});"""

    # Write files
    try:
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
            logger.info(f"HTML template created: {html_path}")
            
        with open(css_path, 'w', encoding='utf-8') as f:
            f.write(css_content)
            logger.info(f"CSS file created: {css_path}")
            
        with open(js_path, 'w', encoding='utf-8') as f:
            f.write(js_content)
            logger.info(f"JavaScript file created: {js_path}")
            
        # Check if files were created
        for path in [html_path, css_path, js_path]:
            if not os.path.exists(path):
                logger.error(f"Error: File was not created: {path}")
                return False
                
        return True
    except Exception as e:
        logger.error(f"Error creating static files: {e}")
        return False

# Initialize database if not already done
db_utils.init_db()

def start_web_kassensystem(host='0.0.0.0', port=5003, debug=False):
    """Starts the web kassensystem on the specified port"""
    # Set up static files BEFORE starting the server
    if not setup_static_files():
        logger.error("Could not create static files. Server will not start.")
        sys.exit(1)
        
    logger.info(f"Web kassensystem starting on http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)

if __name__ == "__main__":
    # Start server
    start_web_kassensystem(debug=True)