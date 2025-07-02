import flask
from flask import Flask, render_template, jsonify, request, redirect, url_for
import threading
import time
import sqlite3
import db_utils
import os
import logging

# Konfiguriere Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Bestimme das aktuelle Verzeichnis
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
logger.info(f"Arbeitsverzeichnis: {CURRENT_DIR}")

# Verzeichnisse für Kundendisplay
CUSTOMER_TEMPLATES_DIR = os.path.join(CURRENT_DIR, 'customer_templates')
CUSTOMER_STATIC_DIR = os.path.join(CURRENT_DIR, 'customer_static')
CUSTOMER_CSS_DIR = os.path.join(CUSTOMER_STATIC_DIR, 'css')
CUSTOMER_JS_DIR = os.path.join(CUSTOMER_STATIC_DIR, 'js')

# Erstelle die Verzeichnisse, falls sie nicht existieren
for directory in [CUSTOMER_TEMPLATES_DIR, CUSTOMER_STATIC_DIR, CUSTOMER_CSS_DIR, CUSTOMER_JS_DIR]:
    if not os.path.exists(directory):
        try:
            os.makedirs(directory)
            logger.info(f"Verzeichnis erstellt: {directory}")
        except Exception as e:
            logger.error(f"Fehler beim Erstellen des Verzeichnisses {directory}: {e}")

# Erstellt die Flask-App für den Kunden-Display
app = Flask(__name__, 
            template_folder=CUSTOMER_TEMPLATES_DIR,
            static_folder=CUSTOMER_STATIC_DIR)
app.config['SECRET_KEY'] = 'intelligent-shelf-customer-display'

# Vereinfachte Produkt-Detaildaten
product_details = {
    "cup": {
        "name": "Eco-Kaffeebecher",
        "description": "Nachhaltiger Kaffeebecher aus Bambuskomposit, 350ml.",
        "price": 8.99,
        "currency": "€",
        "material": "Bambuskomposit",
        "care": "Spülmaschinenfest"
    },
    "glass": {
        "name": "Premium Trinkglas",
        "description": "Elegantes Trinkglas aus recyceltem Glas, 250ml.",
        "price": 6.99,
        "currency": "€",
        "material": "Recyceltes Glas",
        "care": "Spülmaschinenfest"
    },
    "fork": {
        "name": "Edelstahl-Gabel",
        "description": "Hochwertige Gabel aus 18/10 Edelstahl.",
        "price": 3.99,
        "currency": "€",
        "material": "18/10 Edelstahl",
        "care": "Spülmaschinenfest"
    },
    "spoon": {
        "name": "Edelstahl-Löffel",
        "description": "Ergonomischer Löffel aus 18/10 Edelstahl.",
        "price": 3.99,
        "currency": "€",
        "material": "18/10 Edelstahl",
        "care": "Spülmaschinenfest"
    },
    "book": {
        "name": "Smart Living Handbuch",
        "description": "Das ultimative Handbuch für intelligentes Wohnen.",
        "price": 24.99,
        "currency": "€",
        "material": "Recyclingpapier, Hardcover",
        "care": "Trocken lagern"
    },
    "bottle": {
        "name": "Isolierte Wasserflasche",
        "description": "Hochwertige Edelstahl-Isolierflasche.",
        "price": 19.99,
        "currency": "€",
        "material": "BPA-freier Edelstahl",
        "care": "Handwäsche empfohlen"
    },
    "wine glass": {
        "name": "Kristall Weinglas",
        "description": "Elegantes Kristallglas für Weinliebhaber.",
        "price": 12.99,
        "currency": "€",
        "material": "Kristallglas",
        "care": "Handwäsche empfohlen"
    }
}

# Globale Variablen
current_product = None
product_display_until = 0  # Zeitpunkt, bis zu dem das Produkt angezeigt wird

# Thread für die Erkennung von Produktentnahmen
def detect_product_removal():
    global current_product, product_display_until
    
    # Cache für verarbeitete Event-IDs
    processed_event_ids = set()
    
    while True:
        try:
            # Prüfe, ob die Anzeigezeit für das aktuelle Produkt abgelaufen ist
            current_time = time.time()
            if current_product is not None and current_time > product_display_until:
                logger.info(f"Anzeigezeit für {current_product} abgelaufen, zurück zur Startseite")
                current_product = None
            
            # Prüfe auf neue Removal-Events nur, wenn kein Produkt angezeigt wird
            if current_product is None:
                conn = sqlite3.connect(db_utils.DB_NAME)
                c = conn.cursor()
                recent_time = int(current_time) - 5  # Letzte 5 Sekunden
                
                # Suche nach neuen Removal-Events
                c.execute('''
                    SELECT id, shelf_id, product_type, event_type, status, event_time
                    FROM events
                    WHERE event_time > ? AND event_type = 'removal' AND status = 'not paid'
                    ORDER BY event_time DESC
                    LIMIT 5
                ''', (recent_time,))
                
                removal_events = c.fetchall()
                conn.close()
                
                # Verarbeite das neueste Event, das noch nicht verarbeitet wurde
                for event_id, shelf_id, product_type, event_type, status, event_time in removal_events:
                    if event_id not in processed_event_ids:
                        processed_event_ids.add(event_id)
                        
                        # Setze das aktuelle Produkt und die Anzeigezeit
                        current_product = product_type
                        product_display_until = current_time + 15  # 15 Sekunden anzeigen
                        
                        logger.info(f"Neues Produkt erkannt: {product_type} (Event ID: {event_id}, anzeigen bis: {product_display_until})")
                        break
            
            # Begrenze die Größe des Cache
            if len(processed_event_ids) > 100:
                processed_event_ids = set(list(processed_event_ids)[-50:])
            
            # Kurze Pause, um CPU-Last zu reduzieren
            time.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Fehler bei der Erkennung von Produktentnahmen: {e}")
            time.sleep(1)  # Bei Fehler längere Pause

# Einfach-Route für Produktanzeige
@app.route('/')
def index():
    """Hauptseite des Kunden-Displays"""
    global current_product, product_display_until
    
    # Wenn kein Produkt ausgewählt ist oder die Zeit abgelaufen ist, zeige Willkommensseite
    current_time = time.time()
    if current_product is None or current_time > product_display_until:
        if current_product is not None:
            logger.info(f"Anzeigezeit für {current_product} abgelaufen, zurück zur Startseite")
            current_product = None
        return render_template('simple_welcome.html')
    
    # Wenn ein Produkt ausgewählt ist, zeige Produktinfoseite
    if current_product in product_details:
        product = product_details[current_product].copy()
        product['type'] = current_product
        
        # Hole Lagerbestand aus der Datenbank
        stock_info = get_stock_status(current_product)
        product['stock_info'] = stock_info
        
        # Berechne verbleibende Zeit
        remaining_seconds = int(max(0, product_display_until - current_time))
        product['remaining_seconds'] = remaining_seconds
        
        # Falls nicht vorrätig
        if stock_info['count'] <= 0:
            product['restock_message'] = "Artikel wird nachbestellt und ist in 3-5 Tagen wieder verfügbar."
        
        return render_template('product_info.html', product=product)
    
    # Fallback zur Willkommensseite
    return render_template('simple_welcome.html')

# Einfach-Route für manuelles Setzen des Produkts (für Tests)
@app.route('/set/<product_type>')
def set_product(product_type):
    """Route zum manuellen Setzen des aktuellen Produkts (für Tests)"""
    global current_product, product_display_until
    
    if product_type in product_details:
        current_product = product_type
        product_display_until = time.time() + 15  # 15 Sekunden anzeigen
        logger.info(f"Produkt manuell gesetzt: {product_type}")
        return redirect(url_for('index'))
    
    return redirect(url_for('index'))

def get_stock_status(product_type):
    """Prüft den Lagerbestand und liefert Verfügbarkeitsinformationen"""
    try:
        conn = sqlite3.connect(db_utils.DB_NAME)
        c = conn.cursor()
        
        # Gesamtbestand für diesen Produkttyp
        c.execute('''
            SELECT SUM(current_count) FROM inventory
            WHERE product_type = ?
        ''', (product_type,))
        current_stock = c.fetchone()[0] or 0
        
        conn.close()
        
        if current_stock > 5:
            return {
                "status": "Ausreichend auf Lager", 
                "message": "Sofort verfügbar",
                "count": current_stock,
                "level": "high"
            }
        elif current_stock > 2:
            return {
                "status": "Verfügbar", 
                "message": f"{current_stock} auf Lager",
                "count": current_stock,
                "level": "medium"
            }
        elif current_stock > 0:
            return {
                "status": "Fast ausverkauft", 
                "message": f"Nur noch {current_stock} verfügbar!",
                "count": current_stock,
                "level": "low"
            }
        else:
            return {
                "status": "Nicht vorrätig", 
                "message": "Momentan nicht verfügbar",
                "count": 0,
                "level": "out"
            }
    except Exception as e:
        logger.error(f"Fehler bei der Bestandsprüfung: {e}")
        return {"status": "Status nicht verfügbar", "count": 0, "level": "unknown"}

def setup_static_files():
    """Erstellt die Templates und statischen Dateien für den Kundendisplay"""
    
    # Willkommensbildschirm HTML Template
    welcome_html_path = os.path.join(CUSTOMER_TEMPLATES_DIR, 'simple_welcome.html')
    welcome_html = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="cache-control" content="no-cache, no-store, must-revalidate">
    <title>Intelligentes Regal</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/tablet_style.css') }}">
</head>
<body class="welcome-screen">
    <div class="container-fluid d-flex flex-column justify-content-center align-items-center h-100 text-center">
        <div class="logo-container mb-4">
            <i class="bi bi-box-seam display-1"></i>
        </div>
        <h1 class="display-4 mb-4">Willkommen zum Intelligenten Regal</h1>
        <p class="lead mb-5">Nehmen Sie ein Produkt in die Hand für detaillierte Informationen</p>
        
        <div class="pulse-circle">
            <i class="bi bi-hand-index"></i>
        </div>
        
        <!-- Produkt-Buttons für Tests -->
        <div class="product-preview mt-5">
            <div class="row justify-content-center">
                <div class="col-12 text-center mb-3 text-light">
                    <p>Demo-Buttons (zum Testen):</p>
                </div>
                <div class="col-12">
                    <a href="/set/cup" class="btn btn-outline-light m-1">Becher</a>
                    <a href="/set/glass" class="btn btn-outline-light m-1">Glas</a>
                    <a href="/set/book" class="btn btn-outline-light m-1">Buch</a>
                    <a href="/set/bottle" class="btn btn-outline-light m-1">Flasche</a>
                    <a href="/set/fork" class="btn btn-outline-light m-1">Gabel</a>
                    <a href="/set/spoon" class="btn btn-outline-light m-1">Löffel</a>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Einfacher Reload, damit wir immer den aktuellen Status sehen
        setTimeout(function() {
            window.location.reload();
        }, 2000);
    </script>
</body>
</html>"""

    # Vereinfachte Produktinformationen HTML Template
    info_html_path = os.path.join(CUSTOMER_TEMPLATES_DIR, 'product_info.html')
    info_html = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="cache-control" content="no-cache, no-store, must-revalidate">
    <title>{{ product.name }} | Intelligentes Regal</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/tablet_style.css') }}">
</head>
<body class="product-screen">
    <div class="container-fluid h-100 p-4">
        <div class="screen-header d-flex justify-content-between align-items-center mb-4">
            <div class="screen-title">
                <h1>Produktinformationen</h1>
            </div>
        </div>

        <div class="main-content pt-3">
            <div class="product-info-card">
                <h2 class="product-name">{{ product.name }}</h2>
                <div class="product-price mb-3">{{ product.price }}{{ product.currency }}</div>
                
                <div class="product-description mb-4">
                    {{ product.description }}
                </div>
                
                <!-- Produktspezifikationen -->
                <div class="specs-container mb-4">
                    <div class="spec-row">
                        <div class="spec-label">Material:</div>
                        <div class="spec-value">{{ product.material }}</div>
                    </div>
                    <div class="spec-row">
                        <div class="spec-label">Pflege:</div>
                        <div class="spec-value">{{ product.care }}</div>
                    </div>
                </div>
                
                <!-- Bestandsinformation -->
                <div class="stock-info-container {% if product.stock_info.count <= 0 %}out-of-stock{% endif %}">
                    <div class="stock-header">
                        <i class="bi {% if product.stock_info.count > 0 %}bi-check-circle-fill{% else %}bi-exclamation-circle-fill{% endif %}"></i>
                        <h3>Verfügbarkeit</h3>
                    </div>
                    
                    {% if product.stock_info.count > 0 %}
                    <div class="stock-details">
                        <div class="stock-status stock-{{ product.stock_info.level }}">{{ product.stock_info.status }}</div>
                        <div class="stock-message">{{ product.stock_info.message }}</div>
                    </div>
                    {% else %}
                    <div class="restock-info">
                        <div class="stock-status stock-out">{{ product.stock_info.status }}</div>
                        <div class="restock-message">{{ product.restock_message }}</div>
                    </div>
                    {% endif %}
                </div>
            </div>
            
            <!-- Timer-Anzeige -->
            <div class="timer-container mt-4">
                <div class="timer-progress" id="timer-bar" style="width: {{ (product.remaining_seconds / 15) * 100 }}%"></div>
                <div class="timer-text">Ansicht wird in <span id="timer-seconds">{{ product.remaining_seconds }}</span> Sekunden zurückgesetzt</div>
            </div>
            
            <!-- Test-Link zurück zur Startseite -->
            <div class="text-center mt-3">
                <a href="/" class="btn btn-sm btn-outline-secondary">Zurück zur Startseite</a>
            </div>
        </div>
    </div>

    <script>
        // Einfacher Countdown-Timer
        let seconds = {{ product.remaining_seconds }};
        const timerElement = document.getElementById('timer-seconds');
        const timerBar = document.getElementById('timer-bar');
        
        const interval = setInterval(function() {
            seconds--;
            if (seconds <= 0) {
                clearInterval(interval);
                window.location.href = '/';
            } else {
                timerElement.textContent = seconds;
                timerBar.style.width = ((seconds / 15) * 100) + '%';
            }
        }, 1000);
        
        // Sicherstellen, dass wir nach 15 Sekunden definitiv zurück zur Startseite gehen
        setTimeout(function() {
            window.location.href = '/';
        }, {{ product.remaining_seconds }} * 1000);
    </script>
</body>
</html>"""

    # CSS Datei für das Tablet-Display
    css_path = os.path.join(CUSTOMER_CSS_DIR, 'tablet_style.css')
    css_content = """/* Tablet-optimiertes Design für das Kunden-Display */
html, body {
    height: 100%;
    margin: 0;
    padding: 0;
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    overflow: hidden;
}

body {
    background-color: #f8f9fa;
}

/* Willkommensbildschirm */
.welcome-screen {
    background: linear-gradient(135deg, #3498db, #2c3e50);
    color: white;
}

.logo-container {
    height: 120px;
    width: 120px;
    border-radius: 50%;
    background-color: rgba(255, 255, 255, 0.2);
    display: flex;
    align-items: center;
    justify-content: center;
    margin-bottom: 30px;
}

.pulse-circle {
    width: 80px;
    height: 80px;
    border-radius: 50%;
    background-color: rgba(255, 255, 255, 0.3);
    display: flex;
    align-items: center;
    justify-content: center;
    animation: pulse 2s infinite;
    font-size: 30px;
}

@keyframes pulse {
    0% {
        transform: scale(0.95);
        box-shadow: 0 0 0 0 rgba(255, 255, 255, 0.7);
    }
    
    70% {
        transform: scale(1);
        box-shadow: 0 0 0 15px rgba(255, 255, 255, 0);
    }
    
    100% {
        transform: scale(0.95);
        box-shadow: 0 0 0 0 rgba(255, 255, 255, 0);
    }
}

/* Gemeinsame Elemente für alle Produktscreens */
.product-screen {
    background-color: white;
}

.screen-header {
    border-bottom: 1px solid #eaeaea;
    padding-bottom: 10px;
    margin-bottom: 20px;
}

.screen-title h1 {
    margin: 0;
    font-weight: 600;
    font-size: 28px;
    color: #2c3e50;
}

/* Timer Anzeige */
.timer-container {
    margin-top: 20px;
    padding: 10px;
    background-color: #f8f9fa;
    border-radius: 8px;
    position: relative;
}

.timer-progress {
    height: 4px;
    background-color: #3498db;
    width: 100%;
    border-radius: 2px;
    transition: width 1s linear;
}

.timer-text {
    text-align: center;
    font-size: 14px;
    color: #7f8c8d;
    margin-top: 5px;
}

/* Vereinfachte Produktinformationen */
.main-content {
    max-width: 800px;
    margin: 0 auto;
}

.product-info-card {
    background-color: #fff;
    border-radius: 12px;
    box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
    padding: 25px;
}

.product-name {
    font-size: 32px;
    font-weight: 700;
    margin-bottom: 10px;
    color: #2c3e50;
}

.product-price {
    font-size: 28px;
    font-weight: 700;
    color: #2980b9;
}

.product-description {
    font-size: 18px;
    line-height: 1.6;
    margin-bottom: 20px;
    color: #34495e;
}

/* Spezifikationen */
.specs-container {
    border-top: 1px solid #eee;
    border-bottom: 1px solid #eee;
    padding: 15px 0;
}

.spec-row {
    display: flex;
    margin-bottom: 10px;
}

.spec-label {
    font-weight: 600;
    color: #7f8c8d;
    width: 120px;
}

.spec-value {
    color: #2c3e50;
}

/* Bestandsinformation */
.stock-info-container {
    background-color: #f9f9f9;
    border-radius: 8px;
    padding: 20px;
    margin-top: 20px;
}

.out-of-stock {
    background-color: #fff2f0;
    border-left: 4px solid #e74c3c;
}

.stock-header {
    display: flex;
    align-items: center;
    margin-bottom: 15px;
}

.stock-header i {
    font-size: 24px;
    margin-right: 10px;
    color: #2ecc71;
}

.out-of-stock .stock-header i {
    color: #e74c3c;
}

.stock-header h3 {
    margin: 0;
    font-size: 20px;
    font-weight: 600;
    color: #2c3e50;
}

.stock-status {
    font-weight: 700;
    font-size: 18px;
    margin-bottom: 5px;
}

.stock-message, .restock-message {
    color: #7f8c8d;
}

.restock-message {
    font-style: italic;
    padding: 10px 0;
}

.stock-high {
    color: #27ae60;
}

.stock-medium {
    color: #f39c12;
}

.stock-low {
    color: #e67e22; 
}

.stock-out {
    color: #e74c3c;
}

/* Responsive Anpassungen */
@media (max-width: 768px) {
    .product-name {
        font-size: 28px;
    }
    
    .product-price {
        font-size: 24px;
    }
    
    .product-description {
        font-size: 16px;
    }
    
    .screen-title h1 {
        font-size: 24px;
    }
}"""

    # Dateien schreiben
    try:
        with open(welcome_html_path, 'w', encoding='utf-8') as f:
            f.write(welcome_html)
            logger.info(f"Willkommens-Template erstellt: {welcome_html_path}")
            
        with open(info_html_path, 'w', encoding='utf-8') as f:
            f.write(info_html)
            logger.info(f"Produktinfo-Template erstellt: {info_html_path}")
            
        with open(css_path, 'w', encoding='utf-8') as f:
            f.write(css_content)
            logger.info(f"CSS-Datei erstellt: {css_path}")
            
        # Überprüfen, ob die Dateien erstellt wurden
        for path in [welcome_html_path, info_html_path, css_path]:
            if not os.path.exists(path):
                logger.error(f"Fehler: Datei wurde nicht erstellt: {path}")
                return False
                
        return True
    except Exception as e:
        logger.error(f"Fehler beim Erstellen der statischen Dateien: {e}")
        return False

def start_customer_display(host='0.0.0.0', port=5001, debug=False):
    """Startet den Kunden-Display auf dem angegebenen Port"""
    # Statische Dateien einrichten - VOR dem Start des Servers
    if not setup_static_files():
        logger.error("Konnte die statischen Dateien nicht erstellen. Server wird nicht gestartet.")
        return False
    
    # Starte den Thread für die Erkennung von Produktentnahmen
    detection_thread = threading.Thread(target=detect_product_removal)
    detection_thread.daemon = True
    detection_thread.start()
    
    logger.info(f"Kunden-Display wird gestartet auf http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)

if __name__ == "__main__":
    # Datenbank sicherstellen
    db_utils.init_db()
    
    # Server starten
    start_customer_display(debug=True)