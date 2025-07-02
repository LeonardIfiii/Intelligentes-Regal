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

# Konfiguriere Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Bestimme das aktuelle Verzeichnis
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
logger.info(f"Arbeitsverzeichnis: {CURRENT_DIR}")

# Stelle sicher, dass die benötigten Verzeichnisse vorhanden sind
TEMPLATES_DIR = os.path.join(CURRENT_DIR, 'templates')
STATIC_DIR = os.path.join(CURRENT_DIR, 'static')
CSS_DIR = os.path.join(STATIC_DIR, 'css')
JS_DIR = os.path.join(STATIC_DIR, 'js')

# Erstelle die Verzeichnisse, falls sie nicht existieren
for directory in [TEMPLATES_DIR, STATIC_DIR, CSS_DIR, JS_DIR]:
    if not os.path.exists(directory):
        try:
            os.makedirs(directory)
            logger.info(f"Verzeichnis erstellt: {directory}")
        except Exception as e:
            logger.error(f"Fehler beim Erstellen des Verzeichnisses {directory}: {e}")
            sys.exit(1)

# Erstellt die Flask-App
app = Flask(__name__)
app.config['SECRET_KEY'] = 'intelligent-shelf-system'

# Flag, um zu prüfen, ob der Server aktiv ist
is_active = True
start_time = time.time()

# Cache für Inventar- und Ereignisdaten - DEAKTIVIERT für sofortige Updates
data_cache = {
    'inventory': [],
    'events': [],
    'last_update': 0,
    'cache_lifetime': 0  # 0 = Cache deaktiviert für sofortige Updates
}

# Hilfsfunktion zum Konvertieren von nicht-JSON-serialisierbaren Objekten
def sanitize_data(obj):
    """Konvertiert nicht-JSON-serialisierbare Objekte in serialisierbare Form"""
    if isinstance(obj, dict):
        return {key: sanitize_data(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_data(item) for item in obj]
    elif isinstance(obj, bytes):
        return obj.decode('utf-8', errors='replace')  # Bytes in String umwandeln
    elif isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    else:
        # Andere Typen als String darstellen
        return str(obj)

# Hilfsfunktion zur Datenabfrage
def get_db_data():
    """Ruft aktuelle Daten aus der Datenbank ab und aktualisiert den Cache"""
    current_time = time.time()
    
    # Überprüfen, ob der Cache aktuell ist - bei 0 immer neue Daten holen
    if data_cache['cache_lifetime'] > 0 and current_time - data_cache['last_update'] < data_cache['cache_lifetime']:
        return data_cache['inventory'], data_cache['events']
    
    try:
        # Daten aus der Datenbank abrufen
        inventory = db_utils.get_inventory()
        events = db_utils.get_all_events()
        
        # Formatierte Daten für die Inventarübersicht erstellen
        formatted_inventory = []
        for inv in inventory:
            shelf_id, product_type, initial_count, current_count, last_update = inv
            
            # Stelle sicher, dass keine bytes-Objekte vorhanden sind
            if isinstance(product_type, bytes):
                product_type = product_type.decode('utf-8', errors='replace')
            
            # Hole Verkaufsdaten
            try:
                sold, unpaid = db_utils.get_sales_data(shelf_id, product_type)
            except Exception as e:
                logger.warning(f"Fehler beim Abrufen der Verkaufsdaten: {e}")
                sold, unpaid = 0, 0
            
            # Berechne Delta (Differenz von Startwert und aktueller Bestand plus Verkauft)
            delta = initial_count - (current_count + sold)
            
            # Status basierend auf Delta bestimmen
            if delta > 1:
                status = "Kritisch"
            elif delta == 1:
                status = "Niedrig"
            else:
                status = "OK"
            
            # Formatierte Zeit des letzten Updates
            update_time = datetime.fromtimestamp(last_update).strftime("%H:%M:%S")
            
            formatted_inventory.append({
                'shelf_id': shelf_id + 1,  # +1 für die Anzeige
                'product_type': product_type.capitalize(),
                'initial_count': initial_count,
                'current_count': current_count,
                'sold': sold,
                'unpaid': unpaid,
                'delta': delta,
                'status': status,
                'update_time': update_time
            })
        
        # Formatierte Daten für die Ereignisübersicht erstellen
        formatted_events = []
        for event in events:
            try:
                if len(event) == 10:
                    event_id, shelf_id, product_type, event_type, event_time, resolved, resolution_time, status, quantity, object_id = event
                elif len(event) == 9:
                    event_id, shelf_id, product_type, event_type, event_time, resolved, resolution_time, status, quantity = event
                    object_id = -1
                else:
                    event_id, shelf_id, product_type, event_type, event_time, resolved, resolution_time, status = event
                    quantity = 1
                    object_id = -1
                
                # Stelle sicher, dass keine bytes-Objekte vorhanden sind
                if isinstance(product_type, bytes):
                    product_type = product_type.decode('utf-8', errors='replace')
                if isinstance(event_type, bytes):
                    event_type = event_type.decode('utf-8', errors='replace')
                if isinstance(status, bytes):
                    status = status.decode('utf-8', errors='replace')
                    
                event_time_str = datetime.fromtimestamp(event_time).strftime("%Y-%m-%d %H:%M:%S")
                resolved_str = "Ja" if resolved else "Nein"
                
                if resolution_time:
                    resolution_time_str = datetime.fromtimestamp(resolution_time).strftime("%Y-%m-%d %H:%M:%S")
                else:
                    resolution_time_str = "-"
                
                formatted_events.append({
                    'event_id': event_id,
                    'shelf_id': shelf_id + 1,  # +1 für die Anzeige
                    'product_type': product_type.capitalize(),
                    'event_type': event_type.capitalize(),
                    'event_time': event_time_str,
                    'resolved': resolved_str,
                    'resolution_time': resolution_time_str,
                    'status': status.capitalize(),
                    'quantity': quantity,
                    'object_id': object_id if object_id != -1 else "N/A"
                })
            except Exception as e:
                logger.error(f"Fehler beim Formatieren eines Ereignisses: {e}")
                # Füge trotzdem ein einfaches Event hinzu, damit die Anzeige nicht komplett leer bleibt
                formatted_events.append({
                    'event_id': "Fehler",
                    'shelf_id': 0,
                    'product_type': "Fehler",
                    'event_type': "Fehler",
                    'event_time': str(datetime.now()),
                    'resolved': "Nein",
                    'resolution_time': "-",
                    'status': "Fehler",
                    'quantity': 0,
                    'object_id': "N/A"
                })
        
        # Nach Zeit sortieren (neueste zuerst)
        formatted_events.sort(key=lambda x: x['event_time'], reverse=True)
        
        # Cache aktualisieren
        data_cache['inventory'] = formatted_inventory
        data_cache['events'] = formatted_events
        data_cache['last_update'] = current_time
        
        return formatted_inventory, formatted_events
    except Exception as e:
        logger.error(f"Fehler beim Abrufen der Daten: {e}")
        # Im Fehlerfall die vorherigen Daten zurückgeben oder leere Listen
        return data_cache.get('inventory', []), data_cache.get('events', [])

# Berechnet die Produkt-Zusammenfassungen
def calculate_product_summaries(inventory_data):
    """Berechnet Zusammenfassungen pro Produkt aus den Inventardaten"""
    product_summaries = {}
    
    for item in inventory_data:
        product_type = item['product_type']
        
        if product_type not in product_summaries:
            product_summaries[product_type] = {
                'initial_count': 0,
                'current_count': 0,
                'sold': 0,
                'unpaid': 0,
                'delta': 0
            }
        
        # Werte akkumulieren
        product_summaries[product_type]['initial_count'] += item['initial_count']
        product_summaries[product_type]['current_count'] += item['current_count']
        product_summaries[product_type]['sold'] += item['sold']
        product_summaries[product_type]['unpaid'] += item['unpaid']
        product_summaries[product_type]['delta'] += item['delta']
    
    # Formatieren für die Anzeige
    formatted_summaries = []
    for product_type, data in product_summaries.items():
        # Status basierend auf Delta berechnen
        if data['delta'] > 1:
            status = "Kritisch"
        elif data['delta'] == 1:
            status = "Niedrig"
        else:
            status = "OK"
            
        formatted_summaries.append({
            'product_type': product_type,
            'initial_count': data['initial_count'],
            'current_count': data['current_count'],
            'sold': data['sold'],
            'delta': data['delta'],
            'status': status
        })
    
    # Sortiere: Kritisch zuerst, dann Niedrig, dann OK
    formatted_summaries.sort(key=lambda x: (
        0 if x['status'] == "Kritisch" else 
        1 if x['status'] == "Niedrig" else 2
    ))
    
    return formatted_summaries

@app.route('/')
def index():
    """Hauptseite des Dashboards"""
    inventory_data, event_data = get_db_data()
    product_summaries = calculate_product_summaries(inventory_data)
    
    # Laufzeit berechnen
    elapsed_time = int(time.time() - start_time)
    hours, remainder = divmod(elapsed_time, 3600)
    minutes, seconds = divmod(remainder, 60)
    runtime = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    return render_template(
        'dashboard.html',
        inventory=inventory_data,
        events=event_data,
        product_summaries=product_summaries,
        runtime=runtime,
        last_update=datetime.now().strftime("%H:%M:%S")
    )

@app.route('/product/<product_type>')
def product_detail(product_type):
    """Detailseite für ein bestimmtes Produkt"""
    # Force refresh of database cache to ensure we have the latest data
    data_cache['last_update'] = 0
    
    inventory_data, event_data = get_db_data()
    
    # Produktname konvertieren (falls nötig)
    product_type_lower = product_type.lower()
    
    # Filtere Inventardaten für dieses Produkt
    product_inventory = [item for item in inventory_data if item['product_type'].lower() == product_type_lower]
    
    # Hole die aktuell erkannten Objekte aus der Datenbank
    detected_objects = db_utils.get_detected_objects()
    
    # Füge die aktuell erkannten Objekte zu den Inventardaten hinzu
    for inv in product_inventory:
        shelf_id = inv['shelf_id'] - 1  # Convert from display format (1-based) to internal (0-based)
        
        # Suche nach dem passenden Eintrag in den erkannten Objekten
        detected_count = 0
        for det_shelf_id, det_product_type, det_count, _ in detected_objects:
            if det_shelf_id == shelf_id and det_product_type.lower() == product_type_lower:
                detected_count = det_count
                break
        
        # Füge die aktuell erkannte Anzahl zum Inventar hinzu
        inv['detected_count'] = detected_count
    
    # Alle Events chronologisch holen
    try:
        conn = sqlite3.connect(db_utils.DB_NAME)
        c = conn.cursor()
        
        # Hole alle Events für diesen Produkttyp in chronologischer Reihenfolge
        c.execute('''
            SELECT id, shelf_id, product_type, event_type, event_time, resolved, resolution_time, status, quantity, object_id
            FROM events
            WHERE product_type = ?
            ORDER BY event_time ASC
        ''', (product_type_lower,))
        
        events_data = c.fetchall()
        conn.close()
        
        # Verfolgen wir die kumulativen Delta-Werte für jedes Regal separat
        cumulative_deltas = {}  # {shelf_id: current_delta}
        initial_counts = {}     # {shelf_id: initial_count}
        
        # Sammle die Initialwerte für jedes Regal
        for inv in product_inventory:
            shelf_id = inv['shelf_id'] - 1  # 0-based
            initial_counts[shelf_id] = inv['initial_count']
            cumulative_deltas[shelf_id] = 0
        
        # Format the events with historically accurate delta values
        product_events = []
        for event in events_data:
            event_id, shelf_id, product_type, event_type, event_time, resolved, resolution_time, status, quantity, object_id = event
            
            # Convert bytes to strings if needed
            if isinstance(product_type, bytes):
                product_type = product_type.decode('utf-8', errors='replace')
            if isinstance(event_type, bytes):
                event_type = event_type.decode('utf-8', errors='replace')
            if isinstance(status, bytes):
                status = status.decode('utf-8', errors='replace')
                
            event_time_str = datetime.fromtimestamp(event_time).strftime("%Y-%m-%d %H:%M:%S")
            resolved_str = "Ja" if resolved else "Nein"
            
            if resolution_time:
                resolution_time_str = datetime.fromtimestamp(resolution_time).strftime("%Y-%m-%d %H:%M:%S")
            else:
                resolution_time_str = "-"
            
            # Erstelle das Event-Objekt
            event_obj = {
                'event_id': event_id,
                'shelf_id': shelf_id + 1,  # +1 für die Anzeige
                'product_type': product_type.capitalize(),
                'event_type': event_type.capitalize(),
                'event_time': event_time_str,
                'resolved': resolved_str,
                'resolution_time': resolution_time_str,
                'status': status.capitalize(),
                'quantity': quantity,
                'object_id': object_id if object_id != -1 else "N/A"
            }
            
            # Initialen Bestand für dieses Regal holen (oder 0, falls nicht bekannt)
            initial_count = initial_counts.get(shelf_id, 0)
            
            # NEU: Delta-Berechnung basierend auf dem Event-Typ und chronologischer Abfolge
            if event_type.lower() == "removal":
                # Für Removal-Events: Kumulatives Delta erhöhen
                if status.lower() in ["not paid", "misplaced"]:
                    # Delta erhöht sich mit jedem Removal-Event dieses Regals
                    cumulative_deltas[shelf_id] += quantity
                    
                    # Das aktuelle Delta für dieses Event ist die kumulative Summe bis zu diesem Punkt
                    current_delta = cumulative_deltas[shelf_id]
                    actual_count = initial_count - current_delta
                elif status.lower() == "returned" or status.lower() == "zurückgestellt":
                    # Für zurückgegebene Objekte: Das Delta zum Zeitpunkt der Entnahme beibehalten
                    current_delta = quantity  # Verwende die im Event gespeicherte Menge
                    actual_count = initial_count - current_delta
                else:
                    # Für andere Status (z.B. paid)
                    current_delta = quantity
                    actual_count = initial_count - current_delta
            elif event_type.lower() == "return":
                if status.lower() == "returned" or status.lower() == "zurückgestellt":
                    # Für Return-Events: Delta zurücksetzen/reduzieren
                    cumulative_deltas[shelf_id] -= quantity
                    if cumulative_deltas[shelf_id] < 0:
                        cumulative_deltas[shelf_id] = 0  # Nicht unter 0 gehen
                    
                    # Delta für return events ist 0
                    current_delta = 0
                    actual_count = initial_count
                else:
                    # Für andere Return-Status
                    current_delta = 0
                    actual_count = initial_count
            else:
                # Für andere Event-Typen
                current_delta = quantity
                actual_count = initial_count - quantity
            
            # Füge berechnete Werte zum Event hinzu
            event_obj['target_count'] = initial_count
            event_obj['actual_count'] = actual_count
            event_obj['current_delta'] = current_delta
            
            product_events.append(event_obj)
    except Exception as e:
        logger.error(f"Error accessing database directly: {e}")
        # Fall back to cached events if there's an error
        product_events = [event for event in event_data if event['product_type'].lower() == product_type_lower]
    
    # Laufzeit berechnen
    elapsed_time = int(time.time() - start_time)
    hours, remainder = divmod(elapsed_time, 3600)
    minutes, seconds = divmod(remainder, 60)
    runtime = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    return render_template(
        'product_detail.html',
        product_type=product_type,
        inventory=product_inventory,
        events=product_events,
        runtime=runtime,
        last_update=datetime.now().strftime("%H:%M:%S")
    )

@app.route('/api/data')
def get_data():
    """API-Endpunkt für aktuelle Daten (für AJAX-Updates)"""
    try:
        inventory_data, event_data = get_db_data()
        product_summaries = calculate_product_summaries(inventory_data)
        
        # Laufzeit
        elapsed_time = int(time.time() - start_time)
        hours, remainder = divmod(elapsed_time, 3600)
        minutes, seconds = divmod(remainder, 60)
        runtime = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        
        # Stelle sicher, dass alle Daten JSON-serialisierbar sind
        response_data = sanitize_data({
            'inventory': inventory_data,
            'events': event_data,
            'product_summaries': product_summaries,
            'runtime': runtime,
            'last_update': datetime.now().strftime("%H:%M:%S")
        })
        
        return jsonify(response_data)
    except Exception as e:
        logger.error(f"Fehler bei API-Datenaufruf: {str(e)}")
        # Im Fehlerfall eine leere Antwort zurückgeben
        return jsonify({
            'inventory': [],
            'events': [],
            'product_summaries': [],
            'runtime': "00:00:00",
            'last_update': datetime.now().strftime("%H:%M:%S"),
            'error': str(e)
        })

@app.route('/reset_db', methods=['POST'])
def reset_db():
    """Datenbank zurücksetzen"""
    try:
        db_utils.reset_db()
        return jsonify({'success': True, 'message': 'Datenbank wurde erfolgreich zurückgesetzt'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Fehler beim Zurücksetzen der Datenbank: {str(e)}'})

@app.route('/clear_events', methods=['POST'])
def clear_events():
    """Aktuelle Ereignisse löschen"""
    try:
        db_utils.clear_current_events()
        return jsonify({'success': True, 'message': 'Alle Ereignisse wurden erfolgreich gelöscht'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Fehler beim Löschen der Ereignisse: {str(e)}'})

@app.route('/refresh_inventory', methods=['POST'])
def refresh_inventory():
    """Signal zum Neubestimmen des Lagerbestands senden"""
    try:
        # Signal-Datei erstellen für YOLO-Monitoring
        with open('inventory_refresh.signal', 'w') as f:
            f.write('1')
        return jsonify({'success': True, 'message': 'Signal zur Neubestimmung des Lagerbestands wurde gesendet'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Fehler beim Senden des Signals: {str(e)}'})

@app.route('/filter_events', methods=['POST'])
def filter_events():
    """Ereignisse nach Status und Produkt filtern"""
    status_filter = request.form.get('status_filter', 'Alle')
    product_filter = request.form.get('product_filter', 'Alle')
    
    _, events = get_db_data()
    
    # Filtern der Ereignisse
    filtered_events = []
    for event in events:
        if status_filter != 'Alle' and event['status'].lower() != status_filter.lower():
            continue
        if product_filter != 'Alle' and event['product_type'].lower() != product_filter.lower():
            continue
        filtered_events.append(event)
    
    return jsonify({'events': sanitize_data(filtered_events)})

@app.route('/api/filter_summaries', methods=['POST'])
def filter_summaries():
    """API-Endpunkt zum Filtern der Produktzusammenfassungen nach Status"""
    try:
        status_filter = request.form.get('status_filter', 'Alle')
        
        inventory_data, _ = get_db_data()
        product_summaries = calculate_product_summaries(inventory_data)
        
        # Filtern der Zusammenfassungen
        if status_filter != 'Alle':
            product_summaries = [summary for summary in product_summaries 
                                if summary['status'].lower() == status_filter.lower()]
        
        return jsonify({'summaries': sanitize_data(product_summaries)})
    except Exception as e:
        logger.error(f"Fehler beim Filtern der Zusammenfassungen: {e}")
        return jsonify({'summaries': [], 'error': str(e)})

# Erstellt die statischen Dateien für das Web-Dashboard
def setup_static_files():
    """Erstellt die Template- und statischen Dateien für das Dashboard"""
    # Dashboard HTML Template
    dashboard_html_path = os.path.join(TEMPLATES_DIR, 'dashboard.html')
    dashboard_html = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Intelligentes Regal - Dashboard</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <div class="container-fluid">
            <a class="navbar-brand" href="#">
                <i class="bi bi-box-seam me-2"></i>
                Intelligentes Regal - Analyse Dashboard
            </a>
            <div class="navbar-text text-light" id="status-display">
                <span class="badge bg-success"><i class="bi bi-check-circle-fill me-1"></i> Verbunden</span>
                <span class="ms-2">Letzte Aktualisierung: <span id="last-update">{{ last_update }}</span></span>
                <span class="ms-2">Laufzeit: <span id="runtime">{{ runtime }}</span></span>
            </div>
        </div>
    </nav>

    <div class="container-fluid mt-3">
        <div class="row mb-3">
            <div class="col-12">
                <div class="card">
                    <div class="card-header bg-primary text-white d-flex justify-content-between align-items-center">
                        <h5 class="card-title mb-0">
                            <i class="bi bi-bar-chart-fill me-2"></i>
                            Produktzusammenfassung
                        </h5>
                        <div class="filter-buttons">
                            <button class="btn btn-sm btn-light summary-filter active" data-status="Alle">Alle</button>
                            <button class="btn btn-sm btn-danger summary-filter" data-status="Kritisch">Kritisch</button>
                            <button class="btn btn-sm btn-warning summary-filter" data-status="Niedrig">Niedrig</button>
                            <button class="btn btn-sm btn-success summary-filter" data-status="OK">OK</button>
                        </div>
                    </div>
                    <div class="card-body">
                        <div class="table-responsive">
                            <table class="table table-hover" id="product-summary-table">
                                <thead class="table-light">
                                    <tr>
                                        <th>Produkt</th>
                                        <th>Startbestand</th>
                                        <th>Aktueller Bestand</th>
                                        <th>Verkauft</th>
                                        <th>Delta</th>
                                        <th>Status</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for product in product_summaries %}
                                    <tr class="product-row" data-product-type="{{ product.product_type }}">
                                        <td class="fw-bold"><a href="{{ url_for('product_detail', product_type=product.product_type.lower()) }}">{{ product.product_type }}</a></td>
                                        <td>{{ product.initial_count }}</td>
                                        <td>{{ product.current_count }}</td>
                                        <td>{{ product.sold }}</td>
                                        <td>{{ product.delta }}</td>
                                        <td>
                                            <span class="badge {% if product.status == 'Kritisch' %}bg-danger{% elif product.status == 'Niedrig' %}bg-warning{% else %}bg-success{% endif %}">
                                                {{ product.status }}
                                            </span>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="row mb-3">
            <!-- Inventaranzeige -->
            <div class="col-md-6">
                <div class="card h-100">
                    <div class="card-header bg-success text-white">
                        <h5 class="card-title mb-0">
                            <i class="bi bi-boxes me-2"></i>
                            Aktueller Lagerbestand
                        </h5>
                    </div>
                    <div class="card-body">
                        <div class="table-responsive">
                            <table class="table table-hover" id="inventory-table">
                                <thead class="table-light">
                                    <tr>
                                        <th>Produkt</th>
                                        <th>Regal</th>
                                        <th>Startbestand</th>
                                        <th>Aktuell</th>
                                        <th>Status</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for inv in inventory %}
                                    <tr>
                                        <td><a href="{{ url_for('product_detail', product_type=inv.product_type.lower()) }}">{{ inv.product_type }}</a></td>
                                        <td>Regal {{ inv.shelf_id }}</td>
                                        <td>{{ inv.initial_count }} ({{ inv.update_time }})</td>
                                        <td>{{ inv.current_count }} ({{ inv.sold }} verkauft)</td>
                                        <td>
                                            <span class="badge {% if inv.status == 'Kritisch' %}bg-danger{% elif inv.status == 'Niedrig' %}bg-warning{% else %}bg-success{% endif %}">
                                                {{ inv.status }}
                                            </span>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                    <div class="card-footer d-flex justify-content-center">
                        <button class="btn btn-outline-primary me-2" id="refresh-inventory-btn">
                            <i class="bi bi-arrow-clockwise me-1"></i>
                            Inventar neu bestimmen
                        </button>
                    </div>
                </div>
            </div>

            <!-- Events-Anzeige -->
            <div class="col-md-6">
                <div class="card h-100">
                    <div class="card-header bg-info text-white">
                        <h5 class="card-title mb-0">
                            <i class="bi bi-activity me-2"></i>
                            Ereignisse
                        </h5>
                    </div>
                    <div class="card-body">
                        <div class="filter-bar mb-3">
                            <form id="filter-form" class="row g-3">
                                <div class="col-md-4">
                                    <label class="form-label">Status:</label>
                                    <select class="form-select" id="status-filter" name="status_filter">
                                        <option value="Alle">Alle</option>
                                        <option value="not paid">Not Paid</option>
                                        <option value="paid">Paid</option>
                                        <option value="misplaced">Misplaced</option>
                                        <option value="returned">Returned</option>
                                        <option value="replenished">Replenished</option>
                                        <option value="zurückgestellt">Zurückgestellt</option>
                                    </select>
                                </div>
                                <div class="col-md-4">
                                    <label class="form-label">Produkt:</label>
                                    <select class="form-select" id="product-filter" name="product_filter">
                                        <option value="Alle">Alle</option>
                                        <option value="glass">Glass</option>
                                        <option value="cup">Cup</option>
                                        <option value="spoon">Spoon</option>
                                        <option value="fork">Fork</option>
                                    </select>
                                </div>
                                <div class="col-md-4 d-flex align-items-end">
                                    <button type="submit" class="btn btn-primary w-100">
                                        <i class="bi bi-funnel-fill me-1"></i>
                                        Filter anwenden
                                    </button>
                                </div>
                            </form>
                        </div>
                        <div class="table-responsive">
                            <table class="table table-hover" id="events-table">
                                <thead class="table-light">
                                    <tr>
                                        <th>ID</th>
                                        <th>Regal</th>
                                        <th>Produkt</th>
                                        <th>Event-Typ</th>
                                        <th>Zeit</th>
                                        <th>Status</th>
                                        <th>Menge</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for event in events %}
                                    <tr class="
                                        {% if event.status|lower == 'not paid' %}table-danger{% endif %}
                                        {% if event.status|lower == 'paid' %}table-success{% endif %}
                                        {% if event.status|lower == 'misplaced' %}table-warning{% endif %}
                                        {% if event.status|lower == 'returned' %}table-info{% endif %}
                                    ">
                                        <td>{{ event.event_id }}</td>
                                        <td>{{ event.shelf_id }}</td>
                                        <td><a href="{{ url_for('product_detail', product_type=event.product_type.lower()) }}">{{ event.product_type }}</a></td>
                                        <td>{{ event.event_type }}</td>
                                        <td>{{ event.event_time }}</td>
                                        <td>{{ event.status }}</td>
                                        <td>{{ event.quantity }}</td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                    <div class="card-footer d-flex justify-content-center">
                        <button class="btn btn-outline-danger me-2" id="clear-events-btn">
                            <i class="bi bi-trash me-1"></i>
                            Ereignisse löschen
                        </button>
                        <button class="btn btn-outline-danger" id="reset-db-btn">
                            <i class="bi bi-database-x me-1"></i>
                            Datenbank zurücksetzen
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Modals für Warnmeldungen -->
    <div class="modal fade" id="confirmModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Bestätigung erforderlich</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <p id="confirmMessage"></p>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Abbrechen</button>
                    <button type="button" class="btn btn-danger" id="confirmButton">Bestätigen</button>
                </div>
            </div>
        </div>
    </div>

    <div class="toast-container position-fixed bottom-0 end-0 p-3">
        <div id="notificationToast" class="toast" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="toast-header">
                <strong class="me-auto" id="toastTitle">Benachrichtigung</strong>
                <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
            <div class="toast-body" id="toastMessage">
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="{{ url_for('static', filename='js/dashboard.js') }}"></script>
</body>
</html>"""

    # Produkt-Detail HTML Template
    product_detail_html_path = os.path.join(TEMPLATES_DIR, 'product_detail.html')
    product_detail_html = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ product_type }} Details - Intelligenter Regal</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <div class="container-fluid">
            <a class="navbar-brand" href="{{ url_for('index') }}">
                <i class="bi bi-box-seam me-2"></i>
                Intelligenter Regal - Analyse Dashboard
            </a>
            <div class="navbar-text text-light">
                <a href="{{ url_for('index') }}" class="btn btn-sm btn-outline-light">
                    <i class="bi bi-arrow-left me-1"></i> Zurück zum Dashboard
                </a>
                <span class="ms-2">Letzte Aktualisierung: <span id="last-update">{{ last_update }}</span></span>
                <span class="ms-2">Laufzeit: <span id="runtime">{{ runtime }}</span></span>
            </div>
        </div>
    </nav>

    <div class="container-fluid mt-3">
        <div class="row mb-3">
            <div class="col-12">
                <div class="card">
                    <div class="card-header bg-primary text-white">
                        <h5 class="card-title mb-0">
                            <i class="bi bi-info-circle me-2"></i>
                            Produktdetails: {{ product_type }}
                        </h5>
                    </div>
                    <div class="card-body">
                        {% if inventory %}
                        <div class="table-responsive mb-4">
                            <table class="table table-hover">
                                <thead class="table-light">
                                    <tr>
                                        <th>Regal</th>
                                        <th>Startbestand</th>
                                        <th>Aktueller Bestand</th>
                                        <th>Verkauft</th>
                                        <th>Delta</th>
                                        <th>Status</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for inv in inventory %}
                                    <tr>
                                        <td>Regal {{ inv.shelf_id }}</td>
                                        <td>{{ inv.initial_count }}</td>
                                        <td>{{ inv.current_count }}</td>
                                        <td>{{ inv.sold }}</td>
                                        <td class="{% if inv.delta > 0 %}delta-highlight{% endif %}">{{ inv.delta }}</td>
                                        <td>
                                            <span class="badge {% if inv.status == 'Kritisch' %}bg-danger{% elif inv.status == 'Niedrig' %}bg-warning{% else %}bg-success{% endif %}">
                                                {{ inv.status }}
                                            </span>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                        {% else %}
                        <div class="alert alert-info">
                            Keine Inventardaten für {{ product_type }} verfügbar.
                        </div>
                        {% endif %}

                        <h5 class="mb-3"><i class="bi bi-activity me-2"></i>Ereignisse für {{ product_type }}</h5>
                        {% if events %}
                        <div class="table-responsive">
                            <table class="table table-hover" id="product-events-table">
                                <thead class="table-light">
                                    <tr>
                                        <th>Event-ID</th>
                                        <th>Regal</th>
                                        <th>Event-Typ</th>
                                        <th>Zeit</th>
                                        <th>Status</th>
                                        <th>Soll</th>
                                        <th>Ist</th>
                                        <th>Delta</th>
                                        <th>Abgeschlossen</th>
                                        <th>Abschlusszeit</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for event in events %}
                                    <tr class="
                                        {% if event.status|lower == 'not paid' %}table-danger{% endif %}
                                        {% if event.status|lower == 'paid' %}table-success{% endif %}
                                        {% if event.status|lower == 'misplaced' %}table-warning{% endif %}
                                        {% if event.status|lower == 'returned' %}table-info{% endif %}
                                    ">
                                        <td>{{ event.event_id }}</td>
                                        <td>{{ event.shelf_id }}</td>
                                        <td>{{ event.event_type }}</td>
                                        <td>{{ event.event_time }}</td>
                                        <td>{{ event.status }}</td>
                                        <td>{{ event.target_count }}</td>
                                        <td>{{ event.actual_count }}</td>
                                        <td class="{% if event.current_delta > 0 %}delta-highlight{% endif %}">{{ event.current_delta }}</td>
                                        <td>{{ event.resolved }}</td>
                                        <td>{{ event.resolution_time }}</td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                        {% else %}
                        <div class="alert alert-info">
                            Keine Ereignisse für {{ product_type }} gefunden.
                        </div>
                        {% endif %}
                    </div>
                    <div class="card-footer d-flex justify-content-center">
                        <button class="btn btn-primary" id="refresh-data-btn">
                            <i class="bi bi-arrow-clockwise me-1"></i>
                            Daten aktualisieren
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div class="toast-container position-fixed bottom-0 end-0 p-3">
        <div id="notificationToast" class="toast" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="toast-header">
                <strong class="me-auto" id="toastTitle">Benachrichtigung</strong>
                <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
            <div class="toast-body" id="toastMessage">
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            // Auto-Refresh alle 5 Sekunden
            setInterval(function() {
                location.reload();
            }, 5000);
            
            // Refresh-Button
            document.getElementById('refresh-data-btn').addEventListener('click', function() {
                location.reload();
            });
        });
    </script>
</body>
</html>"""

    # CSS Datei 
    css_path = os.path.join(CSS_DIR, 'style.css')
    css_content = """/* Custom styles for the dashboard */
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

.filter-bar {
    padding: 10px;
    background-color: #f8f9fa;
    border-radius: 0.5rem;
}

/* Status badges */
.badge {
    font-size: 0.8rem;
    padding: 0.35em 0.65em;
}

/* Animation für Aktualisierungen */
@keyframes highlight {
    0% { background-color: rgba(255, 251, 0, 0.3); }
    100% { background-color: transparent; }
}

.highlight {
    animation: highlight 2s ease-out;
}

/* Responsives Design */
@media (max-width: 768px) {
    .navbar-text {
        display: none;
    }
    
    .filter-bar .form-select,
    .filter-bar .btn {
        margin-bottom: 10px;
    }
}

/* Custom color for tables */
.table-hover tbody tr:hover {
    background-color: rgba(0, 123, 255, 0.05);
}

/* Product links */
.table a {
    color: #0d6efd;
    text-decoration: none;
    font-weight: 500;
}

.table a:hover {
    text-decoration: underline;
}

/* Filter buttons */
.filter-buttons {
    display: flex;
    gap: 5px;
}

.filter-buttons .btn {
    border: 1px solid rgba(255, 255, 255, 0.2);
}

.filter-buttons .btn.active {
    font-weight: bold;
    box-shadow: 0 0 0 0.15rem rgba(255, 255, 255, 0.25);
}

/* Product row hover */
.product-row {
    cursor: pointer;
    transition: background-color 0.2s;
}

.product-row:hover {
    background-color: rgba(0, 123, 255, 0.1);
}

/* Delta highlight */
td.delta-highlight {
    font-weight: bold;
    color: #dc3545;
}"""

    # JavaScript Datei mit häufigeren Updates
    js_path = os.path.join(JS_DIR, 'dashboard.js')
    js_content = """// Dashboard functionality
document.addEventListener('DOMContentLoaded', function() {
    // Toast-Notification-Funktion
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
    
    // Funktion für Bestätigungsdialog
    function showConfirmDialog(message, confirmCallback) {
        const modal = new bootstrap.Modal(document.getElementById('confirmModal'));
        document.getElementById('confirmMessage').textContent = message;
        
        // Event-Handler für den Bestätigungsbutton setzen
        const confirmButton = document.getElementById('confirmButton');
        
        // Alten Event-Listener entfernen (falls vorhanden)
        const newConfirmButton = confirmButton.cloneNode(true);
        confirmButton.parentNode.replaceChild(newConfirmButton, confirmButton);
        
        // Neuen Event-Listener hinzufügen
        newConfirmButton.addEventListener('click', function() {
            confirmCallback();
            modal.hide();
        });
        
        modal.show();
    }
    
    // Daten aktualisieren
    function updateData() {
        fetch('/api/data')
            .then(response => response.json())
            .then(data => {
                // Inventartabelle aktualisieren
                updateTable('inventory-table', data.inventory, [
                    item => `<a href="/product/${item.product_type.toLowerCase()}">${item.product_type}</a>`, 
                    shelf => `Regal ${shelf.shelf_id}`, 
                    inv => `${inv.initial_count} (${inv.update_time})`,
                    inv => `${inv.current_count} (${inv.sold} verkauft)`,
                    inv => {
                        const statusClass = inv.status === 'Kritisch' ? 'bg-danger' : 
                                          inv.status === 'Niedrig' ? 'bg-warning' : 'bg-success';
                        return `<span class="badge ${statusClass}">${inv.status}</span>`;
                    }
                ]);
                
                // Produktzusammenfassung aktualisieren
                updateTable('product-summary-table', data.product_summaries, [
                    item => `<a href="/product/${item.product_type.toLowerCase()}">${item.product_type}</a>`,
                    'initial_count',
                    'current_count',
                    'sold',
                    'delta',
                    prod => {
                        const statusClass = prod.status === 'Kritisch' ? 'bg-danger' : 
                                          prod.status === 'Niedrig' ? 'bg-warning' : 'bg-success';
                        return `<span class="badge ${statusClass}">${prod.status}</span>`;
                    }
                ]);
                
                // Ereignistabelle aktualisieren
                updateTable('events-table', data.events, [
                    'event_id',
                    'shelf_id',
                    item => `<a href="/product/${item.product_type.toLowerCase()}">${item.product_type}</a>`,
                    'event_type',
                    'event_time',
                    'status',
                    'quantity'
                ], function(row, event) {
                    // Zeilen nach Status einfärben
                    const statusLower = event.status.toLowerCase();
                    if (statusLower === 'not paid') {
                        row.className = 'table-danger';
                    } else if (statusLower === 'paid') {
                        row.className = 'table-success';
                    } else if (statusLower === 'misplaced') {
                        row.className = 'table-warning';
                    } else if (statusLower === 'returned') {
                        row.className = 'table-info';
                    } else {
                        row.className = '';
                    }
                });
                
                // Letzte Aktualisierungszeit und Laufzeit aktualisieren
                document.getElementById('last-update').textContent = data.last_update;
                document.getElementById('runtime').textContent = data.runtime;

                // Statusanzeige aktualisieren
                document.querySelector('#status-display .badge').className = 'badge bg-success';
                document.querySelector('#status-display .badge').innerHTML = '<i class="bi bi-check-circle-fill me-1"></i> Verbunden';
            })
            .catch(error => {
                console.error('Fehler beim Abrufen der Daten:', error);
                document.querySelector('#status-display .badge').className = 'badge bg-danger';
                document.querySelector('#status-display .badge').innerHTML = '<i class="bi bi-x-circle-fill me-1"></i> Getrennt';
            });
    }
    
    // Funktion zum Aktualisieren einer Tabelle
    function updateTable(tableId, data, columns, rowCallback) {
        const table = document.getElementById(tableId);
        if (!table) return;
        
        const tbody = table.querySelector('tbody');
        
        // Alte Zeilen merken für Animation
        const oldRows = Array.from(tbody.querySelectorAll('tr'));
        const oldRowData = oldRows.map(row => {
            return Array.from(row.querySelectorAll('td')).map(td => td.textContent);
        });
        
        // Alte Zeilenzustände speichern
        const oldRowClasses = oldRows.map(row => row.className);
        
        // Tabelle leeren
        tbody.innerHTML = '';
        
        // Neue Zeilen einfügen
        data.forEach((item, index) => {
            const row = document.createElement('tr');
            
            // Für Produktzeilen: Klasse und Klick-Event hinzufügen
            if (tableId === 'product-summary-table') {
                row.className = 'product-row';
                row.dataset.productType = item.product_type;
                row.addEventListener('click', function() {
                    window.location.href = `/product/${item.product_type.toLowerCase()}`;
                });
            }
            
            // Spalten hinzufügen
            columns.forEach((column, colIndex) => {
                const cell = row.insertCell();
                if (typeof column === 'function') {
                    cell.innerHTML = column(item);
                } else if (typeof column === 'string') {
                    cell.textContent = item[column] !== undefined ? item[column] : '';
                    
                    // Hervorheben des Delta-Werts wenn > 0
                    if (column === 'delta' && item[column] > 0) {
                        cell.classList.add('delta-highlight');
                    }
                }
                
                // Wenn es die erste Spalte der Produktzusammenfassung ist, fett machen
                if (tableId === 'product-summary-table' && colIndex === 0) {
                    cell.classList.add('fw-bold');
                }
            });
            
            tbody.appendChild(row);
            
            // Optional: Callback für zusätzliche Zeilenformatierung
            if (rowCallback) {
                rowCallback(row, item);
            }
            
            // Animationen für neue oder geänderte Zeilen
            const newRowData = Array.from(row.querySelectorAll('td')).map(td => td.textContent);
            
            // Überprüfen, ob die Zeile neu ist oder sich geändert hat
            let isChanged = true;
            if (index < oldRowData.length) {
                isChanged = !arraysEqual(newRowData, oldRowData[index]) || 
                           (oldRowClasses[index] !== row.className);
            }
            
            if (isChanged) {
                row.classList.add('highlight');
            }
        });
    }
    
    // Hilfsfunktion zum Vergleichen von Arrays
    function arraysEqual(a, b) {
        if (a.length !== b.length) return false;
        for (let i = 0; i < a.length; i++) {
            if (a[i] !== b[i]) return false;
        }
        return true;
    }
    
    // Event-Handler für Formular zum Filtern von Ereignissen
    document.getElementById('filter-form').addEventListener('submit', function(e) {
        e.preventDefault();
        
        const formData = new FormData(this);
        
        fetch('/filter_events', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            updateTable('events-table', data.events, [
                'event_id',
                'shelf_id',
                item => `<a href="/product/${item.product_type.toLowerCase()}">${item.product_type}</a>`,
                'event_type',
                'event_time',
                'status',
                'quantity'
            ], function(row, event) {
                const statusLower = event.status.toLowerCase();
                if (statusLower === 'not paid') {
                    row.className = 'table-danger';
                } else if (statusLower === 'paid') {
                    row.className = 'table-success';
                } else if (statusLower === 'misplaced') {
                    row.className = 'table-warning';
                } else if (statusLower === 'returned') {
                    row.className = 'table-info';
                } else {
                    row.className = '';
                }
            });
        })
        .catch(error => {
            console.error('Fehler beim Filtern:', error);
            showNotification('Fehler', 'Ereignisse konnten nicht gefiltert werden.', false);
        });
    });
    
    // Handler für Produkt-Zusammenfassungs-Filter
    if (document.querySelectorAll('.summary-filter').length > 0) {
        document.querySelectorAll('.summary-filter').forEach(button => {
            button.addEventListener('click', function() {
                // Update active button styling
                document.querySelectorAll('.summary-filter').forEach(btn => {
                    btn.classList.remove('active');
                });
                this.classList.add('active');
                
                const statusFilter = this.dataset.status;
                
                // Form data for the request
                const formData = new FormData();
                formData.append('status_filter', statusFilter);
                
                // Fetch filtered summaries
                fetch('/api/filter_summaries', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    // Update the product summary table
                    updateTable('product-summary-table', data.summaries, [
                        item => `<a href="/product/${item.product_type.toLowerCase()}">${item.product_type}</a>`,
                        'initial_count',
                        'current_count',
                        'sold',
                        'delta',
                        prod => {
                            const statusClass = prod.status === 'Kritisch' ? 'bg-danger' : 
                                              prod.status === 'Niedrig' ? 'bg-warning' : 'bg-success';
                            return `<span class="badge ${statusClass}">${prod.status}</span>`;
                        }
                    ]);
                })
                .catch(error => {
                    console.error('Fehler beim Filtern der Zusammenfassung:', error);
                    showNotification('Fehler', 'Zusammenfassung konnte nicht gefiltert werden.', false);
                });
            });
        });
    }
    
    // Event-Handler für Buttons
    document.getElementById('refresh-inventory-btn').addEventListener('click', function() {
        showConfirmDialog('Möchtest du den Lagerbestand basierend auf der aktuellen Erkennung neu bestimmen?', function() {
            fetch('/refresh_inventory', {
                method: 'POST'
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification('Erfolg', data.message);
                } else {
                    showNotification('Fehler', data.message, false);
                }
                updateData(); // Sofortige Aktualisierung
            })
            .catch(error => {
                console.error('Fehler:', error);
                showNotification('Fehler', 'Unbekannter Fehler beim Senden des Signals.', false);
            });
        });
    });
    
    document.getElementById('clear-events-btn').addEventListener('click', function() {
        showConfirmDialog('Möchtest du alle aktuellen Events löschen? Die Inventardaten bleiben erhalten.', function() {
            fetch('/clear_events', {
                method: 'POST'
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification('Erfolg', data.message);
                    updateData(); // Daten aktualisieren
                } else {
                    showNotification('Fehler', data.message, false);
                }
            })
            .catch(error => {
                console.error('Fehler:', error);
                showNotification('Fehler', 'Unbekannter Fehler beim Löschen der Events.', false);
            });
        });
    });
    
    document.getElementById('reset-db-btn').addEventListener('click', function() {
        showConfirmDialog('Möchtest du die Datenbank wirklich vollständig zurücksetzen? Alle Daten gehen verloren!', function() {
            fetch('/reset_db', {
                method: 'POST'
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification('Erfolg', data.message);
                    updateData(); // Daten aktualisieren
                } else {
                    showNotification('Fehler', data.message, false);
                }
            })
            .catch(error => {
                console.error('Fehler:', error);
                showNotification('Fehler', 'Unbekannter Fehler beim Zurücksetzen der Datenbank.', false);
            });
        });
    });
    
    // Automatische Aktualisierung alle 1 Sekunde für häufigere Updates
    setInterval(updateData, 1000);
    
    // Initiale Datenaktualisierung
    updateData();
});"""

    # Dateien nur schreiben, wenn sie nicht existieren
    # GEÄNDERT: Prüfe, ob die Dateien bereits existieren, und überschreibe sie nicht
    files_to_create = [
        (dashboard_html_path, dashboard_html, "Dashboard-Template"),
        (product_detail_html_path, product_detail_html, "Produkt-Detail-Template"),
        (css_path, css_content, "CSS-Datei"),
        (js_path, js_content, "JavaScript-Datei")
    ]
    
    try:
        for path, content, description in files_to_create:
            if not os.path.exists(path):
                # Datei existiert nicht, erstelle sie
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(content)
                logger.info(f"{description} erstellt: {path}")
            else:
                logger.info(f"{description} existiert bereits: {path}")
                
        # Überprüfen, ob die Dateien vorhanden sind
        for path, _, description in files_to_create:
            if not os.path.exists(path):
                logger.error(f"Fehler: Datei nicht gefunden: {path}")
                return False
                
        return True
    except Exception as e:
        logger.error(f"Fehler beim Erstellen der statischen Dateien: {e}")
        return False

# Datenbank initialisieren, falls noch nicht geschehen
db_utils.init_db()

def start_dashboard(host='0.0.0.0', port=5000, debug=False):
    """Startet das Web-Dashboard auf dem angegebenen Port"""
    # Statische Dateien einrichten - VOR dem Start des Servers
    if not setup_static_files():
        logger.error("Konnte die statischen Dateien nicht erstellen. Server wird nicht gestartet.")
        sys.exit(1)
    
    logger.info(f"Web-Dashboard wird gestartet auf http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)

if __name__ == "__main__":
    # Datenbank sicherstellen
    db_utils.init_db()
    
    # Server starten
    start_dashboard(debug=True)