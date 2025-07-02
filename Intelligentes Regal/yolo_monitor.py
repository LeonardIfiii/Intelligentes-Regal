import os.path
import cv2
import numpy as np
import time
from ultralytics import YOLO
import db_utils
from debug_utils import log_debug
import torch
from sort import Sort
from collections import Counter, deque
import threading
from scipy.spatial.distance import cosine
import sqlite3
import json

###############################################
# Initialisierung & Konfiguration
###############################################

# Konfigurationsdatei für ROIs und Linien
CONFIG_FILE = "regal_config.json"

if torch.cuda.is_available():
    device = "cuda:0"
    print('cuda in usage')
else:
    print("Keine GPU verfügbar. Nutze CPU.")
    device = "cpu"

# Datenbank initialisieren (Tabellen für Events und Inventar)
db_utils.init_db()



def update_detected_objects_in_db(rois, enhanced_tracker):
    """Aktualisiert die Anzahl der erkannten Objekte in der Datenbank."""
    # Sammle alle erkannten Objekte nach Regal und Typ
    detected_objects = {}
    
    # Initialisiere alle Regale mit 0 für Cups
    for shelf_id in rois.keys():
        detected_objects[(shelf_id, "cup")] = 0
    
    # Zähle die tatsächlich erkannten Objekte
    for trk_id, obj in enhanced_tracker.get_all_active_objects().items():
        shelf_id = obj.current_shelf
        product_type = obj.product_type
        
        # Ignoriere Objekte im REMOVED-Zustand
        if obj.state == ObjectState.REMOVED:
            continue
        
        # Erhöhe den Zähler
        key = (shelf_id, product_type)
        detected_objects[key] = detected_objects.get(key, 0) + 1
    
    # Aktualisiere die Datenbank für alle Regale
    for (shelf_id, product_type), count in detected_objects.items():
        # Stelle sicher, dass die Verbindung zur Datenbank existiert
        try:
            conn = sqlite3.connect(db_utils.DB_NAME)
            c = conn.cursor()
            now = int(time.time())
            
            # Einfügen oder Aktualisieren in einem einzigen Statement
            c.execute('''
                INSERT OR REPLACE INTO detected_objects (shelf_id, product_type, count, last_update)
                VALUES (?, ?, ?, ?)
            ''', (shelf_id, product_type, count, now))
            
            conn.commit()
            conn.close()
            
            log_debug(f"YOLO Erkenntnisse gespeichert - Regal {shelf_id+1} {product_type}: {count} Objekte")
        except Exception as e:
            log_debug(f"FEHLER beim Speichern der YOLO-Erkennungen: {e}")
    
    return detected_objects


ALLOWED_CLASSES = ["cup", "book", "bottle", "wine glass"]
# Limits für verschiedene Objekttypen definieren
OBJECT_LIMITS = {
    "cup": 3,
    "book": 3,
    "bottle": 3,
    "wine glass": 3
}


# Benutzer nach Limits fragen oder Standard verwenden
def ask_for_limits():
    print("\nObjekt-Limits konfigurieren:")
    for obj_type in ALLOWED_CLASSES:
        try:
            count = input(f"Maximale Anzahl von {obj_type}s (oder Enter für Standard={OBJECT_LIMITS[obj_type]}): ")
            if count.strip():
                OBJECT_LIMITS[obj_type] = int(count.strip())
        except ValueError:
            print(f"Ungültige Eingabe. Verwende Standard: {OBJECT_LIMITS[obj_type]}")
    
    # Zeige finale Konfiguration
    print("\nTracking konfiguriert mit folgenden Limits:")
    for obj_type, limit in OBJECT_LIMITS.items():
        print(f"  {obj_type}: {limit}")
    
    # Setze globales Limit für Abwärtskompatibilität wenn nötig
    if hasattr(db_utils, 'GLOBAL_CUPS_LIMIT'):
        db_utils.GLOBAL_CUPS_LIMIT = OBJECT_LIMITS["cup"]
    
    # NEU: Speichere die Limits in einer Konfigurationsdatei
    save_limits_to_config()

def save_limits_to_config():
    """Speichert die aktuellen Objektlimits in einer Konfigurationsdatei für andere Programme"""
    config_file = "object_limits.json"
    try:
        # Erstelle ein Dictionary mit Schwellwerten für das Warehouse Dashboard
        stock_thresholds = {}
        for obj_type, limit in OBJECT_LIMITS.items():
            # Bestimme die Schwellwerte basierend auf dem Limit
            critical = max(1, int(limit * 0.3))  # 30% des Limits als kritisch
            warning = max(critical + 1, int(limit * 0.6))  # 60% des Limits als Warnung
            target = limit  # Zielmenge ist das volle Limit
            
            # Abhängig vom Objekttyp anpassen (z.B. "wine glass" zu "glass" für Warehouse)
            dashboard_type = obj_type
            if obj_type == "wine glass":
                dashboard_type = "glass"
            
            stock_thresholds[dashboard_type] = {
                "critical": critical,
                "warning": warning,
                "target": target
            }
        
        # Gesamtes Konfigurationsobjekt erstellen
        config = {
            "object_limits": OBJECT_LIMITS,
            "stock_thresholds": stock_thresholds
        }
        
        # In JSON-Datei speichern
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)
        
        log_debug(f"Objektlimits in {config_file} gespeichert")
    except Exception as e:
        log_debug(f"Fehler beim Speichern der Objektlimits: {e}")
# Frage nach Limits oder verwende Standardwerte
ask_for_limits()

expected_products = {
    0: "cup",
    1: "book",
    2: "bottle",
    3: "wine glass"
}



PRODUCT_SHELF_MAPPING = {
    "cup": 0,        # Tassen in Regal 1 (Index 0)
    "book": 1,       # Bücher in Regal 2 (Index 1)
    "bottle": 3,     # Flaschen in Regal 4 (Index 3) - geändert von 2 auf 3
    "wine glass": 2  # Gläser in Regal 3 (Index 2)
}
# Function to check if product is in its designated shelf
def is_product_in_correct_shelf(product_type, shelf_id):
    """
    Checks if a product is in its designated shelf.
    If no mapping exists for the product, allows it to be in any shelf.
    """
    if product_type in PRODUCT_SHELF_MAPPING:
        return shelf_id == PRODUCT_SHELF_MAPPING[product_type]
    # If product has no designated shelf mapping, allow it in any shelf
    return True

class ObjectSignature:
    """Speichert eine eindeutige Signatur eines Objekts für Re-Identifikation"""
    def __init__(self, color_hist=None, dimensions=None, last_seen_time=None):
        self.color_hist = color_hist  # Farb-Histogramm
        self.dimensions = dimensions  # (Breite, Höhe) des Objekts
        self.last_seen_time = last_seen_time or time.time()
        
    def update(self, color_hist=None, dimensions=None):
        if color_hist is not None:
            # Wenn wir bereits ein Histogramm haben, aktualisieren wir es mit einem gleitenden Mittelwert
            if self.color_hist is not None:
                self.color_hist = 0.7 * self.color_hist + 0.3 * color_hist
            else:
                self.color_hist = color_hist
                
        if dimensions is not None:
            # Aktualisiere die Dimensionen mit einem gleitenden Mittelwert
            if self.dimensions is not None:
                self.dimensions = (
                    0.7 * self.dimensions[0] + 0.3 * dimensions[0],
                    0.7 * self.dimensions[1] + 0.3 * dimensions[1]
                )
            else:
                self.dimensions = dimensions
                
        self.last_seen_time = time.time()


# Lade ROIs und virtuelle Linien aus der Konfigurationsdatei
def load_config():
    """Lädt die ROIs und Linien aus der Konfigurationsdatei"""
    if not os.path.exists(CONFIG_FILE):
        print(f"Konfigurationsdatei nicht gefunden: {CONFIG_FILE}")
        print("Bitte führen Sie zuerst regal_setup.py aus, um die Regale zu definieren.")
        return {}, {}
    
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            rois = {int(k): tuple(v) for k, v in config.get('rois', {}).items()}
            lines = {}
            
            # Konvertiere die Linien-Konfiguration in das richtige Format
            for shelf_id, y_rel in {int(k): v for k, v in config.get('lines', {}).items()}.items():
                if shelf_id in rois:
                    lines[shelf_id] = y_rel
            
            print(f"Konfiguration geladen: {len(rois)} Regale, {len(lines)} Linien")
            return rois, lines
    except Exception as e:
        print(f"Fehler beim Laden der Konfiguration: {e}")
        return {}, {}

# Lade die Konfiguration
loaded_rois, virtual_line_offsets = load_config()

# Fallback zu hartkodierten Werten, falls keine Konfiguration vorhanden ist
if not loaded_rois:
    print("Verwende Standard-ROIs, da keine Konfiguration gefunden wurde.")
    # Definiere ROIs (globale Koordinaten) für ein 2x2-Regal.
    loaded_rois = {
        0: (338, 32, 302, 197),
        1: (337, 237, 302, 159),
        2: (2, 34, 302, 204),
        3: (0, 248, 311, 152)
    }
    
    # Berechne virtuelle Linien (z. B. 80% der ROI-Höhe minus einem Puffer)
    virtual_line_offsets = {}
    buffer_zone = 20  # zusätzlicher Puffer in Pixeln
    for shelf, (rx, ry, rw, rh) in loaded_rois.items():
        virtual_line_offsets[shelf] = int(0.8 * rh) - buffer_zone

# Setze die endgültigen ROIs und virtuellen Linien
rois = loaded_rois

# Berechne virtuelle Linien basierend auf den Offsets
virtual_lines = {}
for shelf, (rx, ry, rw, rh) in rois.items():
    if shelf in virtual_line_offsets:
        virtual_lines[shelf] = virtual_line_offsets[shelf]
    else:
        # Fallback: 80% der Regalhöhe
        virtual_lines[shelf] = int(0.8 * rh)

# Überprüfe, ob die Konfiguration vollständig ist
if len(rois) < 1:
    print("Fehler: Keine gültigen Regale definiert!")
    print("Bitte führen Sie zunächst regal_setup.py aus, um die Regale zu definieren.")
    exit(1)

###############################################
# Strikte Inventarverwaltung
###############################################

class StrictInventory:
    """Strikte Inventarverwaltung, die verhindert, dass mehr Objekte verfolgt werden als physisch möglich."""
    
    def __init__(self, max_objects_per_product):
        # Dictionary für die Zählung der aktiven Objekte: {product_type: count}
        self.active_objects = {product_type: 0 for product_type in ALLOWED_CLASSES}
        self.max_objects = max_objects_per_product
        self.last_warning_time = 0
        
        # Aktive Objekt-IDs für besseres Tracking
        self.active_object_ids = {product_type: set() for product_type in ALLOWED_CLASSES}
        
        log_debug(f"StrictInventory initialisiert mit Objektlimits: {max_objects_per_product}")
    
    def can_add_object(self, product_type, object_id=None):
        """Prüft, ob ein neues Objekt hinzugefügt werden darf."""
        # NEU: Prüfe, ob die Objekt-ID bereits bekannt ist
        if object_id is not None and object_id in self.active_object_ids.get(product_type, set()):
            log_debug(f"Objekt-ID {object_id} bereits bekannt, darf hinzugefügt werden")
            return True
                
        count = self.active_objects.get(product_type, 0)
        # Berechne das Limit für diesen Produkttyp
        max_limit = self.max_objects.get(product_type, 3) if isinstance(self.max_objects, dict) else self.max_objects
        
        # WICHTIG: Genau max_objects erlauben (nicht kleiner als)
        result = count < max_limit
        
        # Immer den aktuellen Status loggen für bessere Diagnose
        log_debug(f"Prüfe Objekt-Hinzufügung: {product_type} (ID: {object_id}), aktuell {count}/{max_limit}")
        
        current_time = time.time()
        if not result and current_time - self.last_warning_time > 5:
            log_debug(f"⚠️ Maximale Anzahl von {product_type} überschritten: {count}/{max_limit}")
            self.last_warning_time = current_time
                
        return result
    
    def add_object(self, product_type, object_id=None):
        """Fügt ein Objekt zum Inventar hinzu."""
        # DEBUG: Zeige detaillierte Informationen zum aktuellen Zustand
        log_debug(f"Versuche Objekt hinzuzufügen: {product_type} (ID: {object_id})")
        log_debug(f"Aktueller Bestand: {self.active_objects.get(product_type, 0)}/{self.max_objects}")
        
        # NEU: Wenn Objekt bereits bekannt, nicht erneut zählen
        if object_id is not None and object_id in self.active_object_ids.get(product_type, set()):
            log_debug(f"Objekt-ID {object_id} bereits im Inventar, keine Zählung erhöht")
            return True
        
        # Berechne das Limit für diesen Produkttyp
        max_limit = self.max_objects.get(product_type, 3) if isinstance(self.max_objects, dict) else self.max_objects
        
        # WICHTIG: Erzwinge bis zu max_limit, wenn wir weniger haben
        current_count = self.active_objects.get(product_type, 0)
        if current_count < max_limit:
            self.active_objects[product_type] = current_count + 1
            
            # NEU: Objekt-ID speichern, wenn vorhanden
            if object_id is not None:
                if product_type not in self.active_object_ids:
                    self.active_object_ids[product_type] = set()
                self.active_object_ids[product_type].add(object_id)
                
            log_debug(f"Objekt erfolgreich hinzugefügt: {product_type} (ID: {object_id}), neue Zählung: {self.active_objects[product_type]}")
            return True
        else:
            # WICHTIG: Bei Cups versuchen wir trotzdem, sie hinzuzufügen, wenn die ID neu ist
            # Dies hilft beim Wiedererkennen nach dem Entfernen
            if product_type == "cup" and object_id is not None:
                if product_type not in self.active_object_ids:
                    self.active_object_ids[product_type] = set()
                
                # Prüfen, ob die ID neu ist
                if object_id not in self.active_object_ids[product_type]:
                    # Wir fügen die ID hinzu, auch wenn wir den Zähler nicht erhöhen
                    self.active_object_ids[product_type].add(object_id)
                    log_debug(f"Objekt-ID {object_id} für Cup wurde in ID-Liste aufgenommen, auch wenn Maximum erreicht")
                    # Wir geben True zurück, damit die Verarbeitung fortgesetzt wird
                    return True
            
            log_debug(f"⚠️ Kann {product_type} (ID: {object_id}) nicht hinzufügen: Maximum von {max_limit} erreicht")
            return False
    def remove_object(self, product_type, object_id=None):
        """Entfernt ein Objekt aus dem Inventar."""
        # NEU: Wenn Objekt-ID bekannt ist, aus der ID-Liste entfernen
        if object_id is not None and product_type in self.active_object_ids:
            if object_id in self.active_object_ids[product_type]:
                self.active_object_ids[product_type].remove(object_id)
                log_debug(f"Objekt-ID {object_id} aus Inventar-Tracking entfernt")
            else:
                log_debug(f"Objekt-ID {object_id} nicht im Inventar gefunden")
                # WICHTIG: Auch wenn die ID nicht gefunden wird, reduzieren wir trotzdem den Zähler
                # Dies verhindert, dass Cups "stecken bleiben" wenn ID-Tracking fehlschlägt
        
        if product_type in self.active_objects and self.active_objects[product_type] > 0:
            self.active_objects[product_type] -= 1
            log_debug(f"Objekt entfernt: {product_type}, neue Zählung: {self.active_objects[product_type]}")
            return True
        return False
    
    def get_count(self, product_type):
        """Gibt die aktuelle Anzahl von Objekten eines Typs zurück."""
        return self.active_objects.get(product_type, 0)
    
    def set_count(self, product_type, count):
        """Setzt die Anzahl für einen Produkttyp direkt, aber begrenzt auf das Maximum."""
        # Stelle sicher, dass count nie den maximal erlaubten Wert übersteigt
        max_limit = self.max_objects.get(product_type, 3) if isinstance(self.max_objects, dict) else self.max_objects
        self.active_objects[product_type] = min(max(0, count), max_limit)
        log_debug(f"Inventory-Zählung gesetzt: {product_type} = {self.active_objects[product_type]} (begrenzt auf max {max_limit})")

    def print_status(self):
        """Gibt den aktuellen Status des Inventars aus."""
        log_debug(f"Inventar-Status: {self.active_objects}")
        log_debug(f"Aktive Objekt-IDs: {self.active_object_ids}")

###############################################
# Automatische Lagerbestandsermittlung
###############################################

class InventoryInitializer:
    def __init__(self, rois, expected_products, allowed_classes, confidence_threshold=0.5, duration=10, max_objects=None):
        """
        Initialisiert die automatische Lagerbestandsermittlung für die ersten 10 Sekunden.
        """
        self.rois = rois
        self.expected_products = expected_products
        self.allowed_classes = allowed_classes
        self.confidence_threshold = confidence_threshold
        self.duration = duration
        self.max_objects = max_objects or OBJECT_LIMITS  # Verwende Objekt-spezifische Limits
        
        # Liste von Counter-Objekten für jeden Frame
        self.frame_detections = {shelf_id: [] for shelf_id in rois.keys()}
        
        # Zeitpunkte
        self.start_time = None
        self.end_time = None
        
        # Status
        self.is_initializing = False
        self.is_initialized = False
        
        # Thread für asynchrone Initialisierung
        self.init_thread = None
        
        log_debug(f"InventoryInitializer bereit mit Objektlimits: {self.max_objects}")
        
    def start_initialization(self):
        """Startet die Initialisierungsphase"""
        if self.is_initializing or self.is_initialized:
            log_debug("Lagerbestandsermittlung bereits aktiv oder abgeschlossen.")
            return False
            
        self.start_time = time.time()
        self.end_time = self.start_time + self.duration
        self.is_initializing = True
        self.is_initialized = False
        
        # Zurücksetzen der Erkennungslisten
        self.frame_detections = {shelf_id: [] for shelf_id in self.rois.keys()}
        
        log_debug(f"Lagerbestandsermittlung gestartet. Dauer: {self.duration} Sekunden.")
        
        # Timer zur automatischen Finalisierung
        self.init_thread = threading.Timer(self.duration, self.finalize_initialization)
        self.init_thread.daemon = True
        self.init_thread.start()
        
        return True
        
    def process_detections(self, frame, results):
        """
        Verarbeitet Erkennungen für die Lagerbestandsermittlung.
        """
        if not self.is_initializing or self.is_initialized:
            return
            
        current_time = time.time()
        
        # Verbleibende Zeit anzeigen
        remaining = max(0, self.end_time - current_time)
        percent = int((self.duration - remaining) / self.duration * 100)
        
        # Statustext auf Frame anzeigen
        status_text = f"Lagerbestand wird ermittelt: {percent}% ({int(remaining)}s übrig)"
        cv2.putText(frame, status_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        
        # Für jedes Regal: Objekte pro Frame zählen
        frame_counts = {shelf_id: Counter() for shelf_id in self.rois.keys()}
        
        # Prüfe alle Erkennungen
        detections = results[0].boxes if results and results[0].boxes is not None else []
        
        for shelf_id, (rx, ry, rw, rh) in self.rois.items():
            # Visualisiere aktives Regal mit grünem Rahmen
            cv2.rectangle(frame, (rx, ry), (rx+rw, ry+rh), (0, 255, 0), 3)
            
            # Zähle für dieses Regal die Objekte in diesem Frame
            shelf_objects = 0
            
            for det in detections:
                conf = float(det.conf[0]) if hasattr(det.conf, '__getitem__') else float(det.conf)
                if conf < self.confidence_threshold:
                    # DEBUG: Auch niedrig-konfidente Objekte anzeigen, aber in anderer Farbe
                    cls_idx = int(det.cls[0]) if hasattr(det.cls, '__getitem__') else int(det.cls)
                    label = results[0].names.get(cls_idx, "unknown").lower()
                    if label in self.allowed_classes:
                        box = det.xyxy[0].cpu().numpy()
                        x1, y1, x2, y2 = box.astype(int)
                        # Objekt mit niedriger Konfidenz in Rot anzeigen
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 1)
                        cv2.putText(frame, f"Low-conf {label} {int(conf*100)}%", (x1, y1-10),
                                  cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
                    continue
                    
                cls_idx = int(det.cls[0]) if hasattr(det.cls, '__getitem__') else int(det.cls)
                label = results[0].names.get(cls_idx, "unknown").lower()
                
                if label not in self.allowed_classes:
                    continue
                    
                box = det.xyxy[0].cpu().numpy()
                x1, y1, x2, y2 = box.astype(int)
                
                # Mittelpunkt der Box berechnen
                center_x = (x1 + x2) // 2
                center_y = (y1 + y2) // 2
                
                # Prüfen, ob Mittelpunkt im aktuellen Regal liegt
                if rx <= center_x <= rx+rw and ry <= center_y <= ry+rh:
                    shelf_objects += 1
                    
                    # Erkanntes Objekt im Regal markieren
                    cv2.putText(frame, f"{label.capitalize()} #{shelf_objects}", (x1, y1-10),
                              cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    # Zeichne den Mittelpunkt klar sichtbar
                    cv2.circle(frame, (center_x, center_y), 5, (0, 255, 0), -1)
            
            # Speichere die Anzahl der Objekte für dieses Regal in diesem Frame
            self.frame_detections[shelf_id].append(shelf_objects)
            
            # Berechne die aktuelle Häufigkeitsverteilung der erkannten Anzahlen
            counter = Counter(self.frame_detections[shelf_id])
            
            # Zeige die häufigste Anzahl im Frame an
            shelf_text = f"Regal {shelf_id+1}: "
            if counter:
                most_common, count = counter.most_common(1)[0]
                percentage = count / len(self.frame_detections[shelf_id]) * 100
                shelf_text += f"{most_common} Objekte ({percentage:.1f}% der Frames)"
            else:
                shelf_text += "Keine Erkennung"
                
            cv2.putText(frame, shelf_text, (rx, ry-5),
                      cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        
        # Prüfe, ob die Initialisierungsphase abgelaufen ist
        if current_time >= self.end_time and self.is_initializing:
            self.finalize_initialization()
            
    def reset(self):
        """Setzt die Initialisierung zurück"""
        if self.init_thread and self.init_thread.is_alive():
            self.init_thread.cancel()
            
        self.is_initializing = False
        self.is_initialized = False
        self.frame_detections = {shelf_id: [] for shelf_id in self.rois.keys()}
        log_debug("Lagerbestandsermittlung zurückgesetzt.")
        
    def finalize_initialization(self):
        """
        Schließt die Initialisierungsphase ab und setzt den Lagerbestand auf die eingegebenen Limits.
        """
        if not self.is_initializing or self.is_initialized:
            return
            
        self.is_initializing = False
        self.is_initialized = True
        
        log_debug("Lagerbestandsermittlung wird übersprungen. Setze initialen Lagerbestand auf eingegebene Limits...")
        
        # Verwende die eingegebenen Objektlimits als initialen Bestand
        object_limits = self.max_objects if isinstance(self.max_objects, dict) else {obj_type: self.max_objects for obj_type in ALLOWED_CLASSES}
        
        # Globalen Bestand für Tracking initialisieren
        global_inventory = {product_type: 0 for product_type in ALLOWED_CLASSES}
        
        # Für jedes Regal: Setze den korrekten Produkttyp basierend auf PRODUCT_SHELF_MAPPING
        for shelf_id in self.rois.keys():
            # Finde das Produkt, das laut Konfiguration in dieses Regal gehört
            matching_product = None
            for product, mapped_shelf in PRODUCT_SHELF_MAPPING.items():
                if mapped_shelf == shelf_id:
                    matching_product = product
                    break
            
            if matching_product is None:
                log_debug(f"Regal {shelf_id+1}: Kein passendes Produkt in PRODUCT_SHELF_MAPPING gefunden. Verwende expected_products.")
                matching_product = self.expected_products.get(shelf_id)
                
                if matching_product is None:
                    log_debug(f"Regal {shelf_id+1}: Auch kein Produkt in expected_products gefunden. Überspringe.")
                    continue
            
            # Hole den eingegebenen Limit-Wert für dieses Produkt
            target_count = object_limits.get(matching_product, 0)
            
            # Setze den initialen Lagerbestand auf den Zielwert
            actual_count = db_utils.set_initial_inventory(shelf_id, matching_product, target_count)
            global_inventory[matching_product] = global_inventory.get(matching_product, 0) + actual_count
            
            log_debug(f"Regal {shelf_id+1}: Setze {matching_product} auf {actual_count} (Zielwert: {target_count}).")
            
            # Setze alle anderen Produkttypen in diesem Regal auf 0
            for other_product in ALLOWED_CLASSES:
                if other_product != matching_product:
                    db_utils.set_initial_inventory(shelf_id, other_product, 0)
                    log_debug(f"Regal {shelf_id+1}: Lösche {other_product} (falsches Produkt für dieses Regal).")
        
        log_debug("Initialer Lagerbestand basierend auf eingegebenen Limits gesetzt.")
        
        # Aktualisiere die Datenbank
        db_utils.clear_current_events()
        
        # Synchronisiere das strikte Inventory-System mit dem tatsächlichen Bestand
        for product_type in ALLOWED_CLASSES:
            # Setze die Anzahl direkt aus unserem Tracking
            count = global_inventory.get(product_type, 0)
            strict_inventory.set_count(product_type, count)
            log_debug(f"Strikte Inventarverwaltung initialisiert: {count} {product_type}(s)")
        
    def check_signal_file(self):
        """Prüft, ob eine Signaldatei zur Neuinitialisierung existiert"""
        signal_file = 'inventory_refresh.signal'
        if os.path.exists(signal_file):
            try:
                os.remove(signal_file)
                log_debug("Signaldatei zur Neuinitialisierung gefunden. Starte Neuinitialisierung...")
                self.reset()
                return self.start_initialization()
            except Exception as e:
                log_debug(f"Fehler beim Verarbeiten der Signaldatei: {str(e)}")
        return False

###############################################
# Erweitertes Objekt-Tracking mit Re-Identifikation
###############################################

def check_for_missing_objects(rois, enhanced_tracker):
    """
    Überprüft, ob Objekte im Inventory fehlen ohne entsprechendes Removal-Event,
    oder ob Objekte zurückgekehrt sind ohne entsprechendes Return-Event.
    
    VERBESSERT: Erkennt auch Objekte, die in falschen Regalen platziert wurden.
    """
    log_debug("Überprüfe auf fehlende und zurückgekehrte Objekte...")
    
    # Aktuelle Zeit für Zeitstempel und Vergleiche
    current_time = time.time()
    
    # Hole den aktuellen Inventarbestand aus der Datenbank
    db_inventory = db_utils.get_inventory()
    
    # Sammle alle aktuell sichtbaren Objekte nach Regal und Typ
    visible_objects = {}
    misplaced_objects = {}  # New: Track objects placed in wrong shelves
    
    for trk_id, obj in enhanced_tracker.get_all_active_objects().items():
        shelf_id = obj.current_shelf
        product_type = obj.product_type
        
        # Ignoriere Objekte im REMOVED-Zustand, da diese bereits als entnommen gelten
        if obj.state == ObjectState.REMOVED:
            continue
        
        # Check if the object is in its correct designated shelf
        # If not, count it as misplaced
        if not is_product_in_correct_shelf(product_type, shelf_id):
            if product_type not in misplaced_objects:
                misplaced_objects[product_type] = []
            misplaced_objects[product_type].append((shelf_id, obj))
            log_debug(f"Falsch platziertes Objekt gefunden: {product_type} in Regal {shelf_id+1}")
            
            # If the object doesn't have an active removal event, create a misplaced event
            if not obj.removal_event_active:
                # Find the designated shelf for this product (or use 0 as default)
                designated_shelf = PRODUCT_SHELF_MAPPING.get(product_type, 0)
                log_debug(f"Erstelle neues misplaced Event für {product_type} in Regal {shelf_id+1} (sollte in {designated_shelf+1} sein)")
                
                # Create a misplaced event with the designated shelf as the original shelf
                db_utils.upsert_event(designated_shelf, product_type, "removal", "misplaced", object_id=obj.trk_id)
                
                # Mark the object as having an active removal event
                obj.removal_event_active = True
                obj.misplaced_updated = True
                obj.original_shelf = designated_shelf
            
            continue  # Skip counting this object in the normal inventory count
            
        # Initialisiere den Zähler für dieses Regal und Produkttyp, falls nicht vorhanden
        if (shelf_id, product_type) not in visible_objects:
            visible_objects[(shelf_id, product_type)] = 0
            
        # Erhöhe den Zähler
        visible_objects[(shelf_id, product_type)] += 1
    
    # Gesamtzahl aller sichtbaren Objekte pro Produkttyp
    total_visible_by_type = {}
    for (shelf_id, product_type), count in visible_objects.items():
        if product_type not in total_visible_by_type:
            total_visible_by_type[product_type] = 0
        total_visible_by_type[product_type] += count
    
    # Gesamtzahl aller offenen Removal-Events pro Produkttyp
    total_unresolved_by_type = {}
    
    # Vergleiche für jedes Regal und jeden Produkttyp, ob Objekte fehlen oder zurückgekehrt sind
    for inv_entry in db_inventory:
        shelf_id, product_type, initial_count, current_count, last_update = inv_entry
        
        # Skip if this product is not expected to be in this shelf according to our mapping
        if not is_product_in_correct_shelf(product_type, shelf_id):
            log_debug(f"Überspringe Inventarcheck für {product_type} in Regal {shelf_id+1}, da es nicht dort hingehört")
            continue
        
        # Debug-Ausgaben für dieses Regal
        log_debug(f"=== Check für Regal {shelf_id+1} ===")
        
        # Wie viele Objekte sind aktuell sichtbar in diesem Regal?
        visible_count = visible_objects.get((shelf_id, product_type), 0)
        
        # Wie viele offene Removal-Events gibt es bereits für dieses Regal?
        unresolved_count = db_utils.get_unresolved_count(shelf_id, product_type, "removal")
        
        # Aktualisiere die Gesamtzahl der offenen Events
        if product_type not in total_unresolved_by_type:
            total_unresolved_by_type[product_type] = 0
        total_unresolved_by_type[product_type] += unresolved_count
        
        # Berechne, wie viele Objekte fehlen sollten (laut DB)
        expected_missing = initial_count - current_count
        
        # Berechne, wie viele Objekte tatsächlich fehlen (visuell)
        actual_missing = initial_count - visible_count
        
        # Debug-Ausgaben
        log_debug(f"DB Inventory - Initial: {initial_count}, Current: {current_count}")
        log_debug(f"Visible count: {visible_count}, Unresolved count: {unresolved_count}")
        log_debug(f"Actual missing: {actual_missing}, Missing without event: {actual_missing - unresolved_count}")
        
        # FALL 1: Mehr Objekte fehlen als durch Events erfasst -> Erstelle neue "not paid" Events
        missing_without_event = actual_missing - unresolved_count
        
        if missing_without_event > 0:
            log_debug(f"Regal {shelf_id+1}, {product_type}: {missing_without_event} Objekt(e) fehlen ohne Event!")
            log_debug(f"  Initial: {initial_count}, Current: {current_count}, Visible: {visible_count}, Unresolved: {unresolved_count}")
            
            # Prüfe, ob wir ein neues Event erstellen dürfen
            if can_create_new_event(shelf_id, product_type, "removal"):
                # Erstelle ein neues "not paid" Event
                db_utils.upsert_event(shelf_id, product_type, "removal", "not paid", quantity_increment=missing_without_event)
                # Aktualisiere den Inventarbestand
                db_utils.update_inventory(shelf_id, product_type, visible_count)
                log_debug(f"  → Automatisches 'not paid' Event für {missing_without_event} fehlende(s) Objekt(e) erstellt")
            else:
                log_debug(f"  ⚠️ Konnte kein neues Event erstellen, maximale Anzahl bereits erreicht")
    
    # Special handling for misplaced objects after all regular inventory is checked
    for product_type, misplaced_list in misplaced_objects.items():
        log_debug(f"=== Handling {len(misplaced_list)} misplaced {product_type}(s) ===")
        
        # Get the designated shelf for this product
        if product_type in PRODUCT_SHELF_MAPPING:
            designated_shelf = PRODUCT_SHELF_MAPPING[product_type]
            
            # Update inventory to reflect that these objects exist but are in wrong places
            # This helps prevent false "not paid" events
            for wrong_shelf, obj in misplaced_list:
                # Log the incorrect placement
                log_debug(f"{product_type} gefunden in Regal {wrong_shelf+1}, sollte in Regal {designated_shelf+1} sein")
    
    # VERBESSERT: Nach der Bestandsaufnahme aller Regale, prüfe auf misplaced Objekte
    # Wir müssen die Gesamtbilanz betrachten, nicht nur pro Regal
    for product_type, total_visible in total_visible_by_type.items():
        # Gesamtzahl offener (not paid) Events für diesen Produkttyp
        total_unresolved = total_unresolved_by_type.get(product_type, 0)
        
        # Gesamtzahl des Startbestands dieses Produkttyps
        total_initial = sum(inv[2] for inv in db_inventory if inv[1] == product_type)
        
        # Theoretischer Gesamtbestand ohne Events
        theoretical_total = total_initial
        
        log_debug(f"=== Gesamtbilanz für {product_type} ===")
        log_debug(f"  Startbestand gesamt: {total_initial}")
        log_debug(f"  Sichtbare Objekte gesamt: {total_visible}")
        log_debug(f"  Offene Events gesamt: {total_unresolved}")
        
        # FALL: Objekte wurden zurückgebracht, aber nicht ins richtige Regal
        # Erkennbar wenn: total_visible + total_unresolved > total_initial
        excess_objects = (total_visible + total_unresolved) - total_initial
        
        if excess_objects > 0:
            # GEÄNDERT: Überprüfe, ob alle Objekte im richtigen Regal sind
            all_in_correct_shelf = True
            for (shelf_id, p_type), count in visible_objects.items():
                if p_type == product_type and not is_product_in_correct_shelf(p_type, shelf_id):
                    all_in_correct_shelf = False
                    break
            
            # Wenn alle Objekte im richtigen Regal sind, sollten wir keine Events als misplaced markieren
            if all_in_correct_shelf:
                log_debug(f"  HINWEIS: Bilanzüberschuss von {excess_objects} {product_type}(s) erkannt, aber alle sind im richtigen Regal")
                log_debug(f"  → Schließe nicht bezahlte Events als 'returned', da alle Objekte im richtigen Regal sind")
                
                # Suche nach offenen "not paid" Events, die wir als "returned" markieren können
                conn = sqlite3.connect(db_utils.DB_NAME)
                c = conn.cursor()
                
                # Hole alle offenen "not paid" Events für diesen Produkttyp
                c.execute('''
                    SELECT id, shelf_id FROM events
                    WHERE product_type = ? AND event_type = "removal" AND status = "not paid" AND resolved = 0
                    ORDER BY event_time ASC
                    LIMIT ?
                ''', (product_type, excess_objects))
                
                not_paid_events = c.fetchall()
                
                # Markiere die ältesten "not paid" Events als "returned"
                for event_id, original_shelf_id in not_paid_events:
                    log_debug(f"  → Markiere Event ID {event_id} (Regal {original_shelf_id+1}) als 'returned'")
                    
                    # Aktualisiere direkt in der Datenbank
                    resolution_time = int(time.time())
                    c.execute('''
                        UPDATE events
                        SET status = "returned", resolved = 1, resolution_time = ?
                        WHERE id = ?
                    ''', (resolution_time, event_id))
                
                conn.commit()
                conn.close()
            else:
                log_debug(f"  MISPLACED ERKANNT: {excess_objects} {product_type}(s) sind in falschen Regalen!")
                
                # Suche nach offenen "not paid" Events, die wir als "misplaced" markieren können
                conn = sqlite3.connect(db_utils.DB_NAME)
                c = conn.cursor()
                
                # Hole alle offenen "not paid" Events für diesen Produkttyp
                c.execute('''
                    SELECT id, shelf_id FROM events
                    WHERE product_type = ? AND event_type = "removal" AND status = "not paid" AND resolved = 0
                    ORDER BY event_time ASC
                    LIMIT ?
                ''', (product_type, excess_objects))
                
                not_paid_events = c.fetchall()
                conn.close()
                
                # Markiere die ältesten "not paid" Events als "misplaced"
                for event_id, original_shelf_id in not_paid_events:
                    log_debug(f"  → Markiere Event ID {event_id} (Regal {original_shelf_id+1}) als 'misplaced'")
                    
                    # Aktualisiere direkt in der Datenbank
                    conn = sqlite3.connect(db_utils.DB_NAME)
                    c = conn.cursor()
                    c.execute('''
                        UPDATE events
                        SET status = "misplaced"
                        WHERE id = ?
                    ''', (event_id,))
                    conn.commit()
                    conn.close()
                    
                    # Reduziere den Zähler verbleibender zu markierender Events
                    excess_objects -= 1
                    if excess_objects <= 0:
                        break
        
        # FALL: Es wurden zu viele "returned" Events generiert, bereinige die Datenbank
        # (Dieser Fall tritt auf, wenn die bisherige Funktion zu viele "returned" Events erstellt hat)
        elif excess_objects < 0:
            log_debug(f"  ÜBERFLÜSSIGE RETURNED EVENTS: {-excess_objects} zu viele Events wurden generiert!")
            
            # Hier könnten wir überflüssige "returned" Events löschen, falls nötig
            # Dies ist aber gefährlich und sollte mit Vorsicht implementiert werden
            
            # Alternativ: Eine Warnung ausgeben
            log_debug(f"  ⚠️ Die Datenbank enthält möglicherweise inkonsistente Events. Überprüfung empfohlen.")

class EnhancedObjectTracker:
    """Verbesserte Objektverfolgung mit Re-Identifikation für verlorene Objekte"""
    def __init__(self, max_memory_time=30, similarity_threshold=0.7, global_inventory=None, max_objects_per_product=None):
        # Speichert aktive Objekte: {id: TrackedObject}
        self.active_objects = {}
        
        # Speichert aus dem Bild verschwundene Objekte, die noch verfolgt werden müssen
        self.memory_objects = deque()
        
        # Konfiguration
        self.max_memory_time = max_memory_time
        self.similarity_threshold = similarity_threshold
        
        # Globales Inventar zur Konsistenzprüfung
        self.global_inventory = global_inventory or {obj_type: 0 for obj_type in ALLOWED_CLASSES}
        self.max_objects_per_product = max_objects_per_product or OBJECT_LIMITS
        
        # Cooldown-System für Event-Erstellung
        self.last_event_time = {}
        self.event_cooldown = 2.0
        
        # Historie der aktiven Objekte, um Dopplungen zu vermeiden
        self.tracked_object_history = set()
        
        # Erzwinge Unterstützung für mindestens die konfigurierten Limits
        self.force_minimum_objects = True
        self.forced_object_count = max_objects_per_product
        
        # Liste unterschiedlicher Objekt-Signaturen zur Differenzierung
        self.unique_signatures = {obj_type: [] for obj_type in ALLOWED_CLASSES}
        
        log_debug(f"EnhancedObjectTracker initialisiert mit Objektlimits: {self.max_objects_per_product}")
    
    def update_inventory_count(self, product_type, delta):
        """Aktualisiert den globalen Bestand für einen Produkttyp"""
        if product_type not in self.global_inventory:
            self.global_inventory[product_type] = 0
        
        # Prüfe, ob ein Hinzufügen das globale Maximum überschreiten würde
        product_limit = self.max_objects_per_product.get(product_type, 3) if isinstance(self.max_objects_per_product, dict) else self.max_objects_per_product
        if delta > 0 and self.global_inventory[product_type] + delta > product_limit:
            log_debug(f"⚠️ Globale Inventarbegrenzung: Kann {delta} {product_type} nicht hinzufügen, aktuell {self.global_inventory[product_type]}/{product_limit}")
            return False
        
        self.global_inventory[product_type] += delta
        # Stelle sicher, dass wir keine negativen Werte haben
        self.global_inventory[product_type] = max(0, self.global_inventory[product_type])
        
        # Synchronisiere mit striktem Inventory
        strict_inventory.set_count(product_type, self.global_inventory[product_type])
            
        log_debug(f"Globales Inventar für {product_type}: {self.global_inventory[product_type]}")
        return True
    
    def can_create_event(self, product_type):
        """Prüft, ob ein neues Event für diesen Produkttyp erstellt werden darf (Cooldown)"""
        current_time = time.time()
        last_time = self.last_event_time.get(product_type, 0)
        
        # Wenn das Cooldown für diesen Produkttyp noch nicht abgelaufen ist
        if current_time - last_time < self.event_cooldown:
            log_debug(f"Event-Cooldown für {product_type} noch aktiv: {self.event_cooldown - (current_time - last_time):.1f}s übrig")
            return False
            
        return True
    
    def register_event(self, product_type):
        """Registriert ein neues Event für diesen Produkttyp (setzt den Cooldown)"""
        self.last_event_time[product_type] = time.time()
    
    def add_object(self, tracker_id, shelf, product_type, signature, image_frame):
        """Fügt ein neues Objekt zum Tracker hinzu oder identifiziert ein bestehendes neu"""
        current_time = time.time()
        
        # VERBESSERT: Debug-Info zur besseren Diagnose
        log_debug(f"add_object: Verarbeite ID {tracker_id}, Typ {product_type}, Regal {shelf+1}")
        log_debug(f"Aktive Objekte: {len(self.active_objects)}, Limit: {self.max_objects_per_product}")
        
        # NEU: Prüfe, ob das Objekt bereits bekannt ist
        if tracker_id in self.tracked_object_history:
            log_debug(f"Tracker-ID {tracker_id} bereits bekannt")
            
        # Entferne zu alte Objekte aus dem Gedächtnis
        self.clean_memory(current_time)
        
        # Prüfe, ob es ein bereits bekanntes Objekt ist, das wiedererkannt wird
        best_match_idx = None
        best_match_similarity = 0
        
        for i, (mem_sig, mem_id, mem_shelf, mem_type, mem_event_active, mem_original_shelf) in enumerate(self.memory_objects):
            # Berechne Ähnlichkeit basierend auf visuellen Merkmalen
            similarity = self.calculate_similarity(signature, mem_sig)
            
            # Wenn es eine gute Übereinstimmung gibt
            if similarity > self.similarity_threshold and similarity > best_match_similarity:
                best_match_similarity = similarity
                best_match_idx = i
        
        # Wenn wir eine Übereinstimmung gefunden haben, identifizieren wir das Objekt neu
        if best_match_idx is not None:
            mem_sig, mem_id, mem_shelf, mem_type, mem_event_active, mem_original_shelf = self.memory_objects[best_match_idx]
            
            # Lösche das Gedächtnisobjekt, da es jetzt wieder aktiv ist
            del self.memory_objects[best_match_idx]
            
            # Erstelle ein neues TrackedObject mit der Identität des gefundenen Objekts
            tracked_obj = TrackedObject(mem_id, shelf, mem_type)
            tracked_obj.state = ObjectState.REMOVED  # Es wurde bereits als entfernt markiert
            tracked_obj.removal_event_active = mem_event_active
            tracked_obj.original_shelf = mem_original_shelf
            
            self.active_objects[tracker_id] = tracked_obj
            
            # NEU: In Historie aufnehmen
            self.tracked_object_history.add(tracker_id)
            
            log_debug(f"Objekt wiedererkannt! Alter ID: {mem_id}, Neue ID: {tracker_id}, Ähnlichkeit: {best_match_similarity:.2f}")
            
            return tracked_obj, True  # True bedeutet, dass es ein wiedererkanntes Objekt ist
        
        # NEU: Wenn das Objekt bereits in der Historie ist, erlauben wir es mit höherer Wahrscheinlichkeit
        already_known = tracker_id in self.tracked_object_history
        
        # NEU: Speichere die Signatur in der Liste der eindeutigen Signaturen
        if product_type not in self.unique_signatures:
            self.unique_signatures[product_type] = []
        
        # WICHTIG: Force-Mode für 2 Cups - immer aktivieren wenn wir noch unter dem Limit sind
        product_count = self.global_inventory.get(product_type, 0)
        actual_active_count = len([obj for obj in self.active_objects.values() if obj.product_type == product_type])
        product_limit = self.max_objects_per_product.get(product_type, 3) if isinstance(self.max_objects_per_product, dict) else self.max_objects_per_product
        should_force = self.force_minimum_objects and actual_active_count < product_limit
        
        # Konsistenzprüfung, aber mit Force-Option
        if not already_known and actual_active_count >= product_limit and not should_force:
            log_debug(f"⚠️ Maximale Anzahl von {product_type} überschritten ({product_count}/{self.max_objects_per_product}). Erlaubnis verweigert.")
            return None, False
        
        # Force-Modus: Wenn wir noch nicht genug Cups haben, akzeptieren wir es unabhängig von strict_inventory
        if should_force:
            log_debug(f"FORCE-MODUS: Akzeptiere {product_type} (ID: {tracker_id}) auch bei strengen Regeln, da wir nur {product_count}/{self.max_objects_per_product} haben.")
        elif not already_known and not strict_inventory.can_add_object(product_type, tracker_id):
            log_debug(f"⚠️ Strenge Inventarprüfung: Kein neues {product_type} erlaubt.")
            return None, False
        
        # Wenn es ein neues Objekt ist, erstelle ein neues TrackedObject
        tracked_obj = TrackedObject(tracker_id, shelf, product_type)
        tracked_obj.is_inside_roi = True  # Neue Eigenschaft, um zu verfolgen, ob Objekt in einer ROI ist
        self.active_objects[tracker_id] = tracked_obj
        
        # NEU: In Historie aufnehmen
        self.tracked_object_history.add(tracker_id)
        
        # NEU: Füge diese Signatur zur Liste der eindeutigen Signaturen hinzu, wenn sie nicht zu ähnlich zu bestehenden ist
        is_unique = True
        for existing_sig in self.unique_signatures[product_type]:
            similarity = self.calculate_similarity(signature, existing_sig)
            if similarity > 0.85:  # Sehr hohe Ähnlichkeit deutet auf gleiches Objekt hin
                is_unique = False
                break
        
        product_limit = self.max_objects_per_product.get(product_type, 3) if isinstance(self.max_objects_per_product, dict) else self.max_objects_per_product
        if is_unique or len(self.unique_signatures[product_type]) < product_limit:
            self.unique_signatures[product_type].append(signature)
            log_debug(f"Neue eindeutige Signatur für {product_type} gespeichert (gesamt: {len(self.unique_signatures[product_type])})")
        
        # Nur wenn neues Objekt: Aktualisiere den globalen Inventarbestand
        if not already_known or should_force:
            if not self.update_inventory_count(product_type, 1) and not should_force:
                # Wenn die Aktualisierung fehlschlägt und kein Force-Modus, entferne das Objekt wieder
                del self.active_objects[tracker_id]
                return None, False
            
            # Auch das strikte Inventory aktualisieren, aber im Force-Modus ignorieren wir Fehler
            if not strict_inventory.add_object(product_type, tracker_id) and not should_force:
                log_debug(f"⚠️ Konnte {product_type} nicht im strikten Inventory hinzufügen, aber ignoriere im Force-Modus")
        
        # Speichere das Erscheinungsbild des Objekts im Tracker-Objekt
        tracked_obj.signature = signature
        
        log_debug(f"Neues Objekt hinzugefügt: ID {tracker_id}, Typ {product_type}, Regal {shelf+1}, Force-Mode: {should_force}")
        
        return tracked_obj, False
    
    def remove_object(self, tracker_id):
        """Entfernt ein Objekt aus dem aktiven Tracking und speichert es im Gedächtnis"""
        if tracker_id not in self.active_objects:
            return
        
        obj = self.active_objects[tracker_id]
        
        # IMPORTANT: Remove from tracked_object_history to allow re-tracking
        # This is critical - we need to forget this object ID so it can be re-detected
        if tracker_id in self.tracked_object_history:
            self.tracked_object_history.remove(tracker_id)
            log_debug(f"Objekt ID {tracker_id} aus Tracker-Historie entfernt (ermöglicht Re-Tracking)")
        
        # Wenn das Objekt ein aktives removal_event hat, behalten wir es im Gedächtnis
        if obj.removal_event_active:
            if hasattr(obj, 'signature') and obj.signature is not None:
                # Füge das Objekt zum Gedächtnis hinzu
                self.memory_objects.append((
                    obj.signature,
                    obj.trk_id,
                    obj.current_shelf,
                    obj.product_type,
                    obj.removal_event_active,
                    obj.original_shelf
                ))
                log_debug(f"Objekt ID {tracker_id} zum Gedächtnis hinzugefügt.")
        else:
            # Wenn kein aktives Event, reduziere den Bestand 
            # VERBESSERT: Nur reduzieren, wenn das Objekt nicht mehr im Bild ist und genug Zeit vergangen ist
            # Dies verhindert, dass Objekte verschwinden und wieder auftauchen
            self.update_inventory_count(obj.product_type, -1)
            
            # Auch das strikte Inventory aktualisieren
            strict_inventory.remove_object(obj.product_type, tracker_id)
                
            log_debug(f"Objekt ID {tracker_id} aus Tracking entfernt und Bestand reduziert.")
        
        # Entferne das Objekt aus dem aktiven Tracking
        del self.active_objects[tracker_id]
    
    def clean_memory(self, current_time):
        """Entfernt zu alte Objekte aus dem Gedächtnis"""
        if not self.memory_objects:
            return
        
        # Finde Objekte, die zu lange im Gedächtnis sind
        too_old_indices = []
        for i, (sig, id, shelf, type, event_active, original_shelf) in enumerate(self.memory_objects):
            if current_time - sig.last_seen_time > self.max_memory_time:
                too_old_indices.append(i)
                log_debug(f"Objekt ID {id} aus Gedächtnis entfernt (zu alt).")
        
        # Entferne die alten Objekte in umgekehrter Reihenfolge, um Indexprobleme zu vermeiden
        for i in sorted(too_old_indices, reverse=True):
            del self.memory_objects[i]
    
    def calculate_similarity(self, sig1, sig2):
        """Berechnet die Ähnlichkeit zwischen zwei Objektsignaturen mit erhöhter Toleranz für Cups"""
        similarity = 0.0
        
        # Vergleiche Farb-Histogramme
        if sig1.color_hist is not None and sig2.color_hist is not None:
            # Cosine-Ähnlichkeit zwischen Histogrammen (1 = identisch, 0 = völlig unterschiedlich)
            hist_sim = 1.0 - cosine(sig1.color_hist, sig2.color_hist)
            similarity += 0.7 * hist_sim  # Farbe ist wichtiger
        
        # Vergleiche Dimensionen mit höherer Toleranz für Cups
        if sig1.dimensions is not None and sig2.dimensions is not None:
            # Berechne relative Größendifferenz
            width_ratio = min(sig1.dimensions[0], sig2.dimensions[0]) / max(sig1.dimensions[0], sig2.dimensions[0])
            height_ratio = min(sig1.dimensions[1], sig2.dimensions[1]) / max(sig1.dimensions[1], sig2.dimensions[1])
            dim_sim = (width_ratio + height_ratio) / 2.0
            
            # Bei Cups sind wir toleranter bei Dimensionen
            similarity += 0.3 * dim_sim
            
            # Bonus für ähnliche Proportionen bei Cups (da sie oft ähnlich aussehen)
            similarity += 0.1
        
        return similarity
    
    def get_all_active_objects(self):
        """Gibt alle aktiven Objekte zurück"""
        return self.active_objects
    
    def get_object(self, tracker_id):
        """Holt ein aktives Objekt nach ID"""
        return self.active_objects.get(tracker_id)
    
    def print_memory_status(self):
        """Gibt Debug-Informationen über den Gedächtniszustand aus"""
        log_debug(f"Gedächtnis enthält {len(self.memory_objects)} Objekte.")
        for i, (sig, id, shelf, type, event_active, original_shelf) in enumerate(self.memory_objects):
            age = time.time() - sig.last_seen_time
            log_debug(f"  {i+1}: ID {id}, Typ {type}, Alter: {age:.1f}s, Event: {event_active}")
            
    def synchronize_with_inventory(self, inventory_manager):
        """Synchronisiert den Tracker mit dem strengen Inventory-Manager."""
        for product_type, count in self.global_inventory.items():
            # Setze die Zählung im strikten Inventory
            inventory_manager.set_count(product_type, count)
        log_debug("Tracker mit Inventory-Manager synchronisiert.")

###############################################
# Funktion für erweiterte Signaturen
###############################################

def extract_object_signature(frame, x1, y1, x2, y2):
    """Extrahiert eine eindeutige Signatur für ein Objekt"""
    # Berechne ROI
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    
    # Berechne Farb-Histogramm im HSV-Farbraum (robust gegenüber Beleuchtungswechseln)
    hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    
    # Berechne das Histogramm mit mehr Bins für bessere Unterscheidung
    hist = cv2.calcHist([hsv_roi], [0, 1, 2], None, [16, 16, 16], [0, 180, 0, 256, 0, 256])
    cv2.normalize(hist, hist)
    
    # Speichere Objektdimensionen
    width, height = x2 - x1, y2 - y1
    
    # Erstelle und gib die Signatur zurück
    return ObjectSignature(
        color_hist=hist.flatten(),
        dimensions=(width, height),
        last_seen_time=time.time()
    )

###############################################
# Hilfsfunktionen für Visualisierung
###############################################

def draw_object_info(frame, obj, x1, y1, x2, y2):
    """Zeichnet detaillierte Informationen über ein Objekt auf den Frame"""
    # Standardinformationen zeichnen
    center_x = (x1 + x2) // 2
    center_y = (y1 + y2) // 2
    
    # Zeichne ID und Mittelpunkt
    cv2.putText(frame, f"ID: {obj.trk_id}", (x1, y1-20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
    cv2.circle(frame, (center_x, center_y), 4, (0, 255, 0), -1)
    
    # Zustandstext
    state_text = {
        ObjectState.IDLE: "Idle",
        ObjectState.POTENTIAL_REMOVAL: "Pot. Removal",
        ObjectState.REMOVED: "Removed",
        ObjectState.POTENTIAL_RETURN: "Pot. Return"
    }.get(obj.state, "Unknown")
    
    # Zeichne Zustand
    cv2.putText(frame, f"State: {state_text}", (x1, y1-35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
    
    # Wenn das Objekt ein aktives Removal-Event hat, zeige das Originalregal an
    if obj.removal_event_active:
        status_text = "misplaced" if obj.misplaced_updated else "not paid"
        cv2.putText(frame, f"Origin: {obj.original_shelf+1} ({status_text})", (x1, y1-50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 2)
    
    # Zeichne auch die Produktart und die Entnahmerichtung
    cv2.putText(frame, f"Type: {obj.product_type}", (x1, y1-65),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 165, 0), 2)
                
    # Zeige die Entnahmerichtung an, wenn vorhanden
    if hasattr(obj, 'removal_direction') and obj.removal_direction:
        cv2.putText(frame, f"Dir: {obj.removal_direction}", (x1, y1-80),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 2)
                
    # Zeige den ROI-Status an
    if hasattr(obj, 'is_inside_roi'):
        roi_status = "Inside ROI" if obj.is_inside_roi else "Outside ROI"
        roi_color = (0, 255, 0) if obj.is_inside_roi else (0, 0, 255)
        cv2.putText(frame, roi_status, (x1, y1-95),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, roi_color, 2)
                    
    # Zeichne eine Verbindungslinie zum zugehörigen Regal, wenn außerhalb der ROI
    if hasattr(obj, 'is_inside_roi') and not obj.is_inside_roi:
        # Zeichne einen roten Kreis um Objekte außerhalb der ROIs
        cv2.circle(frame, (center_x, center_y), 12, (0, 0, 255), 2)
        
        # Zeichne eine Linie zum letzten zugewiesenen Regal, wenn möglich
        if obj.current_shelf in rois:
            rx, ry, rw, rh = rois[obj.current_shelf]
            regal_center_x = rx + rw//2
            regal_center_y = ry + rh//2
            cv2.line(frame, (center_x, center_y), (regal_center_x, regal_center_y), (0, 0, 255), 1, cv2.LINE_AA)

###############################################
# Verbesserte Funktion zur Prüfung von doppelten Events
###############################################

def can_create_new_event(shelf_id, product_type, event_type="removal"):
    """
    Prüft, ob ein neues Event erstellt werden darf.
    Berücksichtigt den aktuellen Bestand und aktive Events.
    """
    # 1. Prüfe die Gesamtzahl der offenen Events dieses Typs
    conn = sqlite3.connect(db_utils.DB_NAME)
    c = conn.cursor()
    c.execute('''
        SELECT COUNT(*) FROM events
        WHERE product_type = ? AND event_type = ? AND resolved = 0
    ''', (product_type, event_type))
    open_events_count = c.fetchone()[0]
    
    # 2. Hole den aktuellen Inventarbestand
    c.execute('''
        SELECT SUM(current_count) FROM inventory
        WHERE product_type = ?
    ''', (product_type,))
    inventory_count = c.fetchone()[0] or 0
    conn.close()
    
    # 3. Erlaube genau die Anzahl Events, die im Bestand sind (strenger)
    max_allowed_events = OBJECT_LIMITS.get(product_type, 3)  # Limit je nach Objekttyp
    
    # 4. Debug-Ausgabe für besseres Verständnis
    log_debug(f"Prüfe Event-Erstellung: {product_type}, Regal {shelf_id+1}")
    log_debug(f"  Offene Events: {open_events_count}, Inventarbestand: {inventory_count}")
    log_debug(f"  Max. erlaubte Events: {max_allowed_events}")
    
    return open_events_count < max_allowed_events

# YOLO-Detektionsparameter - reduzierter Schwellenwert für bessere Cup-Erkennung
confidence_threshold = 0.35  # Reduziert, um mehr Cups zu erkennen

# Initialisiere den erweiterten SORT-Tracker mit Farb-Histogramm
# Verbesserte Parameter für bessere Cup-Unterscheidung
tracker = Sort(max_age=30, min_hits=2, alpha=0.6, beta=0.4, assignment_threshold=0.5)  # Reduzierter Schwellwert für bessere Objektunterscheidung

# Initialisiere das strikte Inventory-System mit den definierten Limits
strict_inventory = StrictInventory(max_objects_per_product=OBJECT_LIMITS)

# Initialisiere den Tracker mit allen Objekttypen
initial_inventory = {obj_type: 0 for obj_type in ALLOWED_CLASSES}
enhanced_tracker = EnhancedObjectTracker(
    max_memory_time=30,
    similarity_threshold=0.3,
    global_inventory=initial_inventory,
    max_objects_per_product=OBJECT_LIMITS  # Verwende die Objektlimits direkt
)

# Initialisiere den Inventory Initializer mit allen Objekttypen
inventory_initializer = InventoryInitializer(rois, expected_products, ALLOWED_CLASSES, confidence_threshold=0.35)
###############################################
# Zustandsmaschine & Event-Logik
###############################################

class ObjectState:
    IDLE = 0
    POTENTIAL_REMOVAL = 1
    REMOVED = 2
    POTENTIAL_RETURN = 3

# VERBESSERUNG: Neues Objekt für detaillierte Objektverfolgung
class TrackedObject:
    def __init__(self, trk_id, shelf, product_type):
        self.trk_id = trk_id
        self.state = ObjectState.IDLE
        self.current_shelf = shelf
        self.original_shelf = shelf  # Das Regal, aus dem das Objekt ursprünglich entfernt wurde
        self.product_type = product_type
        self.last_seen = time.time()
        self.frames_in_state = 0
        self.start_y = None
        self.start_x = None  # Neue Variable für horizontale Position
        self.removal_direction = None  # Neue Variable für Entnahmerichtung
        self.removal_event_active = False
        self.removal_time = None
        self.misplaced_updated = False
        self.is_inside_roi = True  # Neues Flag, um zu verfolgen, ob das Objekt innerhalb oder außerhalb eines ROIs ist
        
    def __str__(self):
        direction = f", Richtung: {self.removal_direction}" if hasattr(self, 'removal_direction') and self.removal_direction else ""
        roi_status = ", im ROI" if self.is_inside_roi else ", außerhalb ROI"
        return f"Objekt {self.trk_id} ({self.product_type}) - Zustand: {self.state}, Regal: {self.current_shelf}{direction}{roi_status}"

# Event-Handler-Funktionen
def handle_removal_event(tracked_obj):
    """
    Wird beim initialen Removal ausgelöst.
    Falls bereits ein Removal-Eintrag existiert, wird dieser auf "not paid" aktualisiert.
    """
    shelf = tracked_obj.current_shelf
    product_type = tracked_obj.product_type
    
    # Prüfen, ob für dieses Objekt bereits ein Removal-Event existiert
    if tracked_obj.removal_event_active:
        log_debug(f"Objekt-ID {tracked_obj.trk_id} bereits als removal erfasst. Ignoriere erneuten Removal.")
        return
    
    # Cooldown-Prüfung
    if not enhanced_tracker.can_create_event(product_type):
        log_debug(f"Event-Cooldown aktiv für {product_type}. Kein neues Event erzeugt.")
        return
    
    # Prüfe, ob ein neues Event erstellt werden darf
    if not can_create_new_event(shelf, product_type, "removal"):
        log_debug(f"⚠️ Maximale Anzahl an Events erreicht. Kein neues Event für Objekt-ID {tracked_obj.trk_id} erstellt.")
        return
    
    # Cooldown registrieren
    enhanced_tracker.register_event(product_type)
    
    if db_utils.event_exists(shelf, product_type, event_type="removal"):
        log_debug(f"Regal {shelf+1}: Objekt-ID {tracked_obj.trk_id} bereits als removal erfasst. Setze Status auf 'not paid'.")
        db_utils.update_event_status(shelf, product_type, "not paid", event_type="removal")
    else:
        log_debug(f"Regal {shelf+1}: Objekt-ID {tracked_obj.trk_id} wurde entfernt. Removal-Event wird getriggert.")
        db_utils.upsert_event(shelf, product_type, "removal", "not paid", quantity_increment=1, object_id=tracked_obj.trk_id)
    
    # Speichere Informationen über das Removal-Event
    tracked_obj.removal_event_active = True
    tracked_obj.original_shelf = shelf
    tracked_obj.removal_time = time.time()
    tracked_obj.misplaced_updated = False

# Ändere die handle_return_event-Funktion in yolo_monitor.py (circa Zeile 2052):

def handle_return_event(tracked_obj):
    """
    Wird beim Rückführen ausgelöst.
    - Kehrt das Objekt im richtigen Regal zurück (basierend auf Produkt-Shelf-Mapping), wird der Eintrag als 'returned' markiert.
    - Andernfalls wird der bestehende Removal-Eintrag als 'misplaced' aktualisiert.
    - In beiden Fällen wird jetzt ein explizites Return-Event erstellt.
    """
    current_shelf = tracked_obj.current_shelf
    removal_shelf = tracked_obj.original_shelf
    product_type = tracked_obj.product_type
    
    # Wenn kein aktives Removal-Event existiert, nichts tun
    if not tracked_obj.removal_event_active:
        log_debug(f"Objekt-ID {tracked_obj.trk_id} hat kein aktives Removal-Event. Return wird ignoriert.")
        return
    
    # Check if the product is in its designated shelf according to our mapping
    is_in_correct_shelf = is_product_in_correct_shelf(product_type, current_shelf)
    
    # GEÄNDERT: Originale Daten für das spätere Return-Event speichern
    object_id = tracked_obj.trk_id
    
    # Product is in the correct shelf according to our mapping
    if is_in_correct_shelf:
        log_debug(f"Regal {current_shelf+1}: Objekt-ID {tracked_obj.trk_id} kehrt ins richtige Regal zurück. Return-Event wird getriggert.")
        
        # VERBESSERT: Auch misplaced Events für diesen Shelf/Product auflösen
        # Suche und schließe alle zugehörigen Events für dieses Objekt/Produkt/Regal
        conn = sqlite3.connect(db_utils.DB_NAME)
        c = conn.cursor()
        resolution_time = int(time.time())
        
        # NEU: Suche nach ALLEN offenen misplaced Events für dieses Produkt, unabhängig vom Regal
        c.execute('''
            SELECT id FROM events
            WHERE product_type = ? AND status = "misplaced" AND resolved = 0
        ''', (product_type,))
        
        misplaced_events = c.fetchall()
        if misplaced_events:
            for event_id in misplaced_events:
                c.execute('''
                    UPDATE events
                    SET resolved = 1, resolution_time = ?, status = "returned"
                    WHERE id = ?
                ''', (resolution_time, event_id[0]))
                log_debug(f"Misplaced-Event ID {event_id[0]} wurde auf 'returned' gesetzt und geschlossen.")
        
        # WICHTIG: Suche auch nach ALLEN offenen not paid Events für dieses Objekt und markiere als returned
        c.execute('''
            SELECT id, quantity FROM events
            WHERE product_type = ? AND status = "not paid" AND resolved = 0
        ''', (product_type,))
        
        not_paid_events = c.fetchall()
        original_quantities = {}  # Speichern der ursprünglichen Mengen
        
        if not_paid_events:
            # Wenn wir ein aktives not paid Event für dieses Objekt haben, markieren wir es als returned
            for event_id, quantity in not_paid_events:
                # Speichere die ursprüngliche Menge vor dem Update
                original_quantities[event_id] = quantity
                
                c.execute('''
                    UPDATE events
                    SET resolved = 1, resolution_time = ?, status = "returned"
                    WHERE id = ?
                ''', (resolution_time, event_id))
                log_debug(f"Not-Paid-Event ID {event_id} wurde auf 'returned' gesetzt und geschlossen.")
        
        conn.commit()
        conn.close()
        
        # Ein neues Return-Event erstellen - WICHTIG: Explizites Return-Event mit der ursprünglichen Menge
        for event_id, original_quantity in original_quantities.items():
            db_utils.upsert_event(current_shelf, product_type, "return", "returned", 
                               quantity_increment=original_quantity, object_id=object_id)
            log_debug(f"Neues Return-Event für Objekt-ID {object_id} erstellt mit ursprünglicher Menge {original_quantity}")
        
        # Wenn keine Events aktualisiert wurden, trotzdem ein Return-Event erstellen
        if not original_quantities:
            db_utils.upsert_event(current_shelf, product_type, "return", "returned", object_id=object_id)
            log_debug(f"Fallback: Neues Return-Event für Objekt-ID {object_id} erstellt")
        
        # Setze removal_event_active zurück
        tracked_obj.removal_event_active = False
        tracked_obj.misplaced_updated = False
    else:
        # Product is not in its designated shelf - mark as misplaced
        if not tracked_obj.misplaced_updated:
            log_debug(f"Objekt-ID {tracked_obj.trk_id} ({product_type}) ist im falschen Regal {current_shelf+1}. Update Event auf 'misplaced'.")
            log_debug(f"Für diesen Produkttyp ist Regal {PRODUCT_SHELF_MAPPING.get(product_type, 'Beliebig')+1} vorgesehen.")
            
            # NEU: Ursprüngliche Menge des Removal-Events herausfinden
            conn = sqlite3.connect(db_utils.DB_NAME)
            c = conn.cursor()
            c.execute('''
                SELECT quantity FROM events
                WHERE product_type = ? AND object_id = ? AND event_type = "removal" AND resolved = 0
                LIMIT 1
            ''', (product_type, tracked_obj.trk_id))
            result = c.fetchone()
            conn.close()
            
            original_quantity = 1  # Standardwert
            if result:
                original_quantity = result[0]
            
            # Erstelle ein neues Event für misplaced mit der ursprünglichen Menge
            db_utils.upsert_event(removal_shelf, product_type, "removal", "misplaced", 
                               quantity_increment=original_quantity, object_id=tracked_obj.trk_id)
            tracked_obj.misplaced_updated = True
            
            # Erstelle auch ein "misplaced return" Event
            db_utils.upsert_event(current_shelf, product_type, "misplaced_return", "misplaced", 
                               quantity_increment=original_quantity, object_id=tracked_obj.trk_id)
            log_debug(f"Neues Misplaced-Return-Event für Objekt-ID {tracked_obj.trk_id} erstellt im Regal {current_shelf+1}")
            
            # Removal-Event bleibt aktiv, bis es korrekt zurückgeführt wird

###############################################
# YOLO-Modell laden & Kamera-Stream starten
###############################################

model_path = 'yolov8s.pt'
yolo_model = YOLO(model_path)
yolo_model.to(device)
print("Erstes Modellparameter-Gerät:", next(yolo_model.model.parameters()).device)

cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("Fehler: Kamera 1 konnte nicht geöffnet werden. Versuche Kamera 0...")
    cap = cv2.VideoCapture(1)
    if not cap.isOpened():
        print("Fehler: Keine Kamera konnte geöffnet werden.")
        exit()

# WICHTIG: Setze Fenster auf Vollbild-Modus
cv2.namedWindow("YOLO Monitoring", cv2.WND_PROP_FULLSCREEN)
cv2.setWindowProperty("YOLO Monitoring", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
print("YOLO Monitoring im Vollbild-Modus gestartet.")

###############################################
# Hilfsfunktionen
###############################################

def reset_tracker():
    """Reset the SORT tracker with default parameters"""
    return Sort(max_age=30, min_hits=2, alpha=0.6, beta=0.4, assignment_threshold=0.5)

tracker = reset_tracker()

def get_object_class(x1, y1, x2, y2, results):
    """Bestimmt die Klasse eines Objekts basierend auf den Detektionsergebnissen und der Bounding Box."""
    detected_classes = {}
    
    for det in results[0].boxes:
        conf = float(det.conf[0]) if hasattr(det.conf, '__getitem__') else float(det.conf)
        if conf < confidence_threshold:
            continue
            
        cls_idx = int(det.cls[0]) if hasattr(det.cls, '__getitem__') else int(det.cls)
        label = results[0].names.get(cls_idx, "unknown").lower()
        
        if label not in ALLOWED_CLASSES:
            continue
            
        box = det.xyxy[0].cpu().numpy()
        det_x1, det_y1, det_x2, det_y2 = box.astype(int)
        
        # Berechne IoU zwischen der Tracker-Box und der Detektions-Box
        inter_x1 = max(x1, det_x1)
        inter_y1 = max(y1, det_y1)
        inter_x2 = min(x2, det_x2)
        inter_y2 = min(y2, det_y2)
        
        if inter_x1 < inter_x2 and inter_y1 < inter_y2:
            inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
            box1_area = (x2 - x1) * (y2 - y1)
            box2_area = (det_x2 - det_x1) * (det_y2 - det_y1)
            iou = inter_area / float(box1_area + box2_area - inter_area)
            
            if iou > 0.5:  # Wenn genug Überlappung
                detected_classes[label] = detected_classes.get(label, 0) + conf
        
    if detected_classes:
        # Wähle die Klasse mit der höchsten Konfidenz
        return max(detected_classes.items(), key=lambda x: x[1])[0]

    # Standardmäßig den ersten erlaubten Objekttyp zurückgeben
    return ALLOWED_CLASSES[0] if ALLOWED_CLASSES else "unknown"
    

###############################################
# Hauptschleife: Detektion, Tracking & Event-Logik
###############################################

# Verzögerte Initialisierung Variablen
inventory_init_delay = 5  # 5 Sekunden Verzögerung vor Start der Inventarisierung
inventory_init_timer = None
inventory_init_started = False
frame_counter = 0  # Zur Stabilisierung
status_update_interval = 30  # Status alle 30 Sekunden aktualisieren
last_status_update = time.time()
last_missing_check_time = time.time()
missing_check_interval = 10  # Überprüfe alle 10 Sekunden

# Verzögerte Initialisierung Variablen
inventory_init_delay = 5  # 5 Sekunden Verzögerung vor Start der Inventarisierung
inventory_init_timer = None
inventory_init_started = False
frame_counter = 0  # Zur Stabilisierung
status_update_interval = 30  # Status alle 30 Sekunden aktualisieren
last_status_update = time.time()
last_missing_check_time = time.time()
missing_check_interval = 10  # Überprüfe alle 10 Sekunden

while True:
    ret, frame = cap.read()
    if not ret:
        break

    # Da die Kamera auf dem Kopf steht, drehen wir das Bild um 180 Grad
    frame = cv2.rotate(frame, cv2.ROTATE_180)

    # Frame-Zähler erhöhen
    frame_counter += 1
    
    # Verzögerte Initialisierung des Lagerbestands
    if not inventory_init_started and frame_counter > 30:  # Warte auf 30 Frames für Stabilität
        # Setze einen Timer für den verzögerten Start der Inventarisierung
        if inventory_init_timer is None:
            log_debug(f"Warte {inventory_init_delay} Sekunden vor Start der Inventarisierung...")
            cv2.putText(frame, f"Warte {inventory_init_delay}s vor Inventarisierung...",
                      (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            
            # Zeige den aktuellen Frame an
            cv2.imshow("YOLO Monitoring", frame)
            cv2.waitKey(1)
            
            # Definiere einen eigenen Timer für den verzögerten Start
            inventory_init_timer = time.time() + inventory_init_delay
    
    # Prüfe, ob der Timer abgelaufen ist
    if inventory_init_timer is not None and time.time() >= inventory_init_timer and not inventory_init_started:
        log_debug("Starte Inventarisierung...")
        inventory_initializer.start_initialization()
        inventory_init_started = True
        inventory_init_timer = None

    annotated_frame = frame.copy()
    current_time = time.time()

    # NEU: Periodische Statusanzeige für besseres Debugging
    if current_time - last_status_update > status_update_interval:
        log_debug("=== SYSTEM-STATUS ===")
        log_debug(f"Aktive Objekte: {len(enhanced_tracker.get_all_active_objects())}")
        log_debug(f"Gedächtnis-Objekte: {len(enhanced_tracker.memory_objects)}")
        strict_inventory.print_status()
        last_status_update = current_time

    ###############################################
    # Detektion: YOLO über den gesamten Frame
    ###############################################
    results = yolo_model(frame)
    # Verarbeite Erkennungen für die Lagerbestandsermittlung
    inventory_initializer.process_detections(annotated_frame, results)
    
    # Prüfe, ob eine Signaldatei zur Neuinitialisierung existiert
    if inventory_initializer.check_signal_file():
        log_debug("Neuinitialisierung des Lagerbestands gestartet.")
    
    detections = results[0].boxes if results and results[0].boxes is not None else []
    
    detections_all = []   # Format: [x1, y1, x2, y2, conf]
    detection_colors = [] # Farb-Histogramme (HSV) für jede Detektion

    for det in detections:
        conf = float(det.conf[0]) if hasattr(det.conf, '__getitem__') else float(det.conf)
        if conf < confidence_threshold:
            continue
        cls_idx = int(det.cls[0]) if hasattr(det.cls, '__getitem__') else int(det.cls)
        label = results[0].names.get(cls_idx, "unknown").lower()
        if label not in ALLOWED_CLASSES:
            continue

        box = det.xyxy[0].cpu().numpy()
        x1, y1, x2, y2 = box.astype(int)
        global_box = [x1, y1, x2, y2, conf]
        detections_all.append(global_box)
        
        # Berechne das Farb-Histogramm der Region
        roi_img = frame[y1:y2, x1:x2]
        if roi_img.size > 0:
            hsv_roi = cv2.cvtColor(roi_img, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv_roi], [0, 1, 2], None, [8, 8, 8], [0,180,0,256,0,256])
            cv2.normalize(hist, hist)
            detection_colors.append(hist.flatten())
        else:
            detection_colors.append(None)
        
        cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
        cv2.putText(annotated_frame, f"{label.capitalize()} {int(conf*100)}%", (x1, y1-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)

    ###############################################
    # Visualisierung: ROIs und virtuelle (rote) Linien
    ###############################################
    for shelf, (rx, ry, rw, rh) in rois.items():
        cv2.rectangle(annotated_frame, (rx, ry), (rx+rw, ry+rh), (0, 255, 255), 2)
        
        # Zeige die virtuelle Linie (rot) an
        line_y = ry + virtual_lines[shelf]
        cv2.line(annotated_frame, (rx, line_y), (rx+rw, line_y), (0, 0, 255), 2)
        
        cv2.putText(annotated_frame, f"Regal {shelf+1}", (rx, ry-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)

    ###############################################
    # Tracking: Aktualisiere den SORT-Tracker
    ###############################################
    if len(detections_all) > 0:
        det_array = np.array(detections_all)
        tracked_objects = tracker.update(det_array, detection_colors)
    else:
        tracked_objects = np.empty((0, 5))
    
    ###############################################
    # Verarbeitung der getrackten Objekte & Event-Logik
    ###############################################
    # Liste der aktuell sichtbaren Tracker-IDs
    visible_tracker_ids = set()

    for trk in tracked_objects:
        x1, y1, x2, y2, trk_id = trk.astype(int)
        center_x = (x1 + x2) // 2
        center_y = (y1 + y2) // 2
        visible_tracker_ids.add(trk_id)

        # Ermittle anhand des Mittelpunkts das zugeordnete Regal
        assigned_shelf = None
        inside_any_roi = False
        for shelf, (rx, ry, rw, rh) in rois.items():
            if rx <= center_x <= rx+rw and ry <= center_y <= ry+rh:
                assigned_shelf = shelf
                inside_any_roi = True
                break

        # Objekt ist in keinem Regal, aber bekannt und getrackt
        tracked_obj = enhanced_tracker.get_object(trk_id)
        if assigned_shelf is None and tracked_obj is not None:
            # Verwende das letzte bekannte Regal weiter
            assigned_shelf = tracked_obj.current_shelf
            
            # Wenn es ein aktives Objekt mit einem Shelf ist, verarbeite es mit dem Status "outside"
            if hasattr(tracked_obj, 'is_inside_roi') and tracked_obj.is_inside_roi:
                # Objekt hat gerade ein Regal verlassen!
                log_debug(f"Objekt ID {trk_id} hat Regal {assigned_shelf+1} verlassen!")
                tracked_obj.is_inside_roi = False
                
                # Wenn es im IDLE-Status war und jetzt draußen ist, setze es auf POTENTIAL_REMOVAL
                if tracked_obj.state == ObjectState.IDLE:
                    tracked_obj.state = ObjectState.POTENTIAL_REMOVAL
                    tracked_obj.frames_in_state = 1
                    tracked_obj.removal_direction = "outside"
                    log_debug(f"Objekt ID {trk_id} wechselt zu POTENTIAL_REMOVAL (Regal verlassen)")

        # Neues Objekt - wir können es nicht verfolgen, wenn es keinem Regal zugeordnet ist
        if assigned_shelf is None:
            continue

        # Hole die ROI-Informationen für das zugeordnete Regal
        rx, ry, rw, rh = rois[assigned_shelf]
        red_line = ry + virtual_lines[assigned_shelf]

        # Aktualisiere den ROI-Status für alle Objekte
        if tracked_obj is not None:
            # Aktualisiere den Zustand, ob das Objekt innerhalb oder außerhalb eines ROIs ist
            prev_inside = getattr(tracked_obj, 'is_inside_roi', True)
            tracked_obj.is_inside_roi = inside_any_roi
            
            # Wenn ein Objekt von außerhalb in ein Regal zurückkehrt
            if inside_any_roi and not prev_inside:
                log_debug(f"Objekt ID {trk_id} ist zurück in einem Regal (Regal {assigned_shelf+1})")

        # Ermittle die Objektklasse - immer "cup" für einheitliche Verfolgung
        obj_class = get_object_class(x1, y1, x2, y2, results)

        # Extrahiere eine eindeutige Signatur für dieses Objekt
        try:
            signature = extract_object_signature(frame, x1, y1, x2, y2)
            if signature is None:
                continue
        except Exception as e:
            log_debug(f"Fehler bei Signaturextraktion: {e}")
            continue

        # Verwende den verbesserten Object Tracker
        if tracked_obj is None:
            # Wenn das Objekt noch nicht im Tracker ist, füge es hinzu
            tracked_obj, is_reidentified = enhanced_tracker.add_object(
                trk_id, assigned_shelf, obj_class, signature, frame
            )
            
            if tracked_obj is None:
                # Wenn das Objekt abgelehnt wurde (z.B. wegen Bestandsgrenze), überspringe es
                continue
                
            # Wenn kein bereits bekanntes Objekt und wir sind in der Initialisierungsphase, aktualisiere den Bestand
            if not is_reidentified and inventory_initializer.is_initialized:
                current_shelf = assigned_shelf
                product_type = obj_class  # Verwende den erkannten Objekttyp
                # Nur den aktuellen Bestand für dieses Regal erhöhen, wenn es ein wirklich neues Objekt ist
                # und wir im Limit sind
                if strict_inventory.get_count(product_type) < OBJECT_LIMITS[product_type]:
                    db_utils.increment_inventory_count(current_shelf, product_type, 1)
        else:
            # Aktualisiere die Signatur des Objekts mit den neuen Beobachtungen
            if hasattr(tracked_obj, 'signature'):
                tracked_obj.signature.update(signature.color_hist, signature.dimensions)
            else:
                tracked_obj.signature = signature
        
        # Aktualisiere die Position und den Regal-Zustand
        tracked_obj.last_seen = current_time
        tracked_obj.current_shelf = assigned_shelf
        
        # Visualisiere detaillierte Informationen für dieses Objekt
        draw_object_info(annotated_frame, tracked_obj, x1, y1, x2, y2)

        # VERBESSERTE ZUSTANDSLOGIK mit fester Randerkennung
        if tracked_obj.state == ObjectState.IDLE:
            # Prüfen, ob das Objekt über einen der Ränder des Regals hinausgeht
            is_outside_right = center_x > rx+rw
            is_outside_left = center_x < rx
            is_outside_top = center_y < ry
            is_outside_bottom = center_y > red_line
            
            # Debug-Ausgabe für besseres Verständnis
            if is_outside_right or is_outside_left or is_outside_top or is_outside_bottom:
                direction = "rechts" if is_outside_right else "links" if is_outside_left else "oben" if is_outside_top else "unten"
                log_debug(f"Rand-Check: Objekt ID {trk_id} ist außerhalb: {direction}")
            
            # Übergang zu POTENTIAL_REMOVAL, wenn Mittelpunkt außerhalb des Regals ist
            if is_outside_bottom:
                tracked_obj.state = ObjectState.POTENTIAL_REMOVAL
                tracked_obj.frames_in_state = 1
                tracked_obj.start_y = center_y
                tracked_obj.removal_direction = "bottom"
                log_debug(f"Objekt ID {trk_id} wechselt zu POTENTIAL_REMOVAL (unten)")
            elif is_outside_left or is_outside_right or is_outside_top:
                tracked_obj.state = ObjectState.POTENTIAL_REMOVAL
                tracked_obj.frames_in_state = 1
                tracked_obj.start_y = center_y
                tracked_obj.start_x = center_x
                tracked_obj.removal_direction = "side"
                log_debug(f"Objekt ID {trk_id} wechselt zu POTENTIAL_REMOVAL (Rand: {direction})")
                
        elif tracked_obj.state == ObjectState.POTENTIAL_REMOVAL:
            # Überprüfe entsprechend der Richtung, ob das Objekt weiterhin außerhalb ist
            removal_confirmed = False
            
            if getattr(tracked_obj, 'removal_direction', 'bottom') == "bottom":
                # Original-Logik für untere Linie
                if center_y > red_line:
                    tracked_obj.frames_in_state += 1
                    if tracked_obj.frames_in_state >= 2:  # Reduziert von 3 auf 2 für schnellere Erkennung
                        removal_confirmed = True
                else:
                    # Zurück zu IDLE, wenn das Objekt wieder über der roten Linie ist
                    tracked_obj.state = ObjectState.IDLE
                    tracked_obj.frames_in_state = 0
                    tracked_obj.start_y = None
                    if hasattr(tracked_obj, 'start_x'):
                        del tracked_obj.start_x
                    if hasattr(tracked_obj, 'removal_direction'):
                        del tracked_obj.removal_direction
                    log_debug(f"Objekt ID {trk_id} wechselt zurück zu IDLE (in POTENTIAL_REMOVAL)")
            else:
                # Logik für seitliche/obere Entnahme - explizite Bedingungen für besseres Verständnis
                is_outside_right = center_x > rx+rw
                is_outside_left = center_x < rx
                is_outside_top = center_y < ry
                
                if is_outside_right or is_outside_left or is_outside_top:
                    tracked_obj.frames_in_state += 1
                    if tracked_obj.frames_in_state >= 2:  # Reduziert für schnellere Erkennung
                        removal_confirmed = True
                else:
                    # Zurück zu IDLE, wenn das Objekt wieder im Regalbereich ist
                    tracked_obj.state = ObjectState.IDLE
                    tracked_obj.frames_in_state = 0
                    tracked_obj.start_y = None
                    if hasattr(tracked_obj, 'start_x'):
                        del tracked_obj.start_x
                    if hasattr(tracked_obj, 'removal_direction'):
                        del tracked_obj.removal_direction
                    log_debug(f"Objekt ID {trk_id} wechselt zurück zu IDLE (in POTENTIAL_REMOVAL - Rand)")

            # Wenn Entnahme bestätigt, wechsle zu REMOVED
            if removal_confirmed:
                tracked_obj.state = ObjectState.REMOVED
                handle_removal_event(tracked_obj)
                tracked_obj.frames_in_state = 0
                tracked_obj.start_y = None
                if hasattr(tracked_obj, 'start_x'):
                    del tracked_obj.start_x
                log_debug(f"Objekt ID {trk_id} wechselt zu REMOVED")
                
        elif tracked_obj.state == ObjectState.REMOVED:
            # Objekt ist komplett innerhalb des Regalbereichs (über der roten Linie + komplett im Regal)
            if center_y < red_line and rx < center_x < rx+rw and center_y > ry:
                # Das Objekt ist zurück im Regal - betrachte es als POTENTIAL_RETURN
                tracked_obj.state = ObjectState.POTENTIAL_RETURN
                tracked_obj.frames_in_state = 1
                tracked_obj.start_y = center_y
                tracked_obj.start_x = center_x
                log_debug(f"Objekt ID {trk_id} wechselt zu POTENTIAL_RETURN (zurück im Regal)")
            elif assigned_shelf != tracked_obj.current_shelf:
                # Das Objekt hat das Regal gewechselt, während es REMOVED ist
                log_debug(f"Objekt ID {trk_id} hat das Regal gewechselt in REMOVED-Zustand (von {tracked_obj.current_shelf+1} zu {assigned_shelf+1})")
                tracked_obj.current_shelf = assigned_shelf
                
                # Wenn es bereits misplaced ist und in ein anderes Regal bewegt wird,
                # aktualisiere die misplacement-Information
                if tracked_obj.misplaced_updated:
                    log_debug(f"Objekt ID {trk_id} wurde in ein anderes Regal verschoben während es misplaced war")
                    db_utils.update_event_status(tracked_obj.original_shelf, tracked_obj.product_type, "misplaced", event_type="removal")
            
            # VERBESSERTE MISPLACED-ERKENNUNG:
            # Wenn das Objekt bereits als misplaced markiert ist UND außerhalb IRGENDEINES Regals ist
            # ODER außerhalb des aktuell zugewiesenen Regals
            if tracked_obj.misplaced_updated and not inside_any_roi:
                log_debug(f"Misplaced Objekt ID {trk_id} wurde aus dem Regal genommen und ist jetzt außerhalb aller Regale")
                
                # GEÄNDERT: Suche und markiere das misplaced Event als resolved
                conn = sqlite3.connect(db_utils.DB_NAME)
                c = conn.cursor()
                
                # Suche nach dem aktiven misplaced Event für dieses Objekt
                c.execute('''
                    SELECT id, quantity FROM events
                    WHERE product_type = ? AND status = "misplaced" AND resolved = 0 AND 
                          (object_id = ? OR (object_id = -1 AND shelf_id = ?))
                    LIMIT 1
                ''', (tracked_obj.product_type, tracked_obj.trk_id, tracked_obj.original_shelf))
                
                misplaced_event = c.fetchone()
                
                if misplaced_event:
                    misplaced_id, quantity = misplaced_event
                    resolution_time = int(time.time())
                    
                    # Markiere das misplaced Event als resolved
                    c.execute('''
                        UPDATE events
                        SET resolved = 1, resolution_time = ?
                        WHERE id = ?
                    ''', (resolution_time, misplaced_id))
                    
                    conn.commit()
                    
                    # NEU: Erstelle ein neues removal Event mit Status "not paid"
                    db_utils.upsert_event(
                        tracked_obj.original_shelf, 
                        tracked_obj.product_type, 
                        "removal", 
                        "not paid", 
                        quantity_increment=quantity,
                        object_id=tracked_obj.trk_id
                    )
                    
                    log_debug(f"Misplaced Event ID {misplaced_id} wurde als resolved markiert und ein neues Removal-Event erstellt")
                else:
                    # Fallback: Event-Status direkt ändern
                    db_utils.update_event_status(tracked_obj.original_shelf, tracked_obj.product_type, "not paid", event_type="removal")
                    log_debug(f"Status für Objekt ID {trk_id} von 'misplaced' zu 'not paid' geändert (Fallback)")
                
                conn.close()
                
                # Zurücksetzen, damit es erneut erkannt werden kann
                tracked_obj.misplaced_updated = False
                
                # NEU: Markiere dieses Objekt explizit als REMOVED
                tracked_obj.state = ObjectState.REMOVED
                handle_removal_event(tracked_obj)
                log_debug(f"Objekt ID {trk_id} wurde als REMOVED markiert, nachdem es als misplaced entfernt wurde")
                    
        elif tracked_obj.state == ObjectState.POTENTIAL_RETURN:
            # Objekt bleibt in POTENTIAL_RETURN, wenn es vollständig im Regalbereich bleibt
            if center_y < red_line and rx < center_x < rx+rw and center_y > ry:
                tracked_obj.frames_in_state += 1
                if tracked_obj.frames_in_state >= 3:
                    handle_return_event(tracked_obj)
                    tracked_obj.state = ObjectState.IDLE
                    tracked_obj.frames_in_state = 0
                    tracked_obj.start_y = None
                    if hasattr(tracked_obj, 'start_x'):
                        del tracked_obj.start_x
                    if hasattr(tracked_obj, 'removal_direction'):
                        del tracked_obj.removal_direction
                    log_debug(f"Objekt ID {trk_id} wurde zurückgeführt und wechselt zu IDLE")
            else:
                # Zurück zu REMOVED, wenn das Objekt den Regalbereich verlässt (unten, seitlich oder oben)
                tracked_obj.state = ObjectState.REMOVED
                tracked_obj.frames_in_state = 0
                tracked_obj.start_y = None
                if hasattr(tracked_obj, 'start_x'):
                    del tracked_obj.start_x
                    
                # Loggen der Richtung für bessere Diagnose
                direction = ""
                if center_y >= red_line:
                    direction = "unten"
                elif center_x <= rx:
                    direction = "links"
                elif center_x >= rx+rw:
                    direction = "rechts" 
                elif center_y <= ry:
                    direction = "oben"
                    
                log_debug(f"Objekt ID {trk_id} verließ den Regalbereich ({direction}), wechselt zurück zu REMOVED")
                
    # Entferne veraltete Objekte aus dem verbesserten Tracker
    for trk_id in list(enhanced_tracker.get_all_active_objects().keys()):
        if trk_id not in visible_tracker_ids:
            obj = enhanced_tracker.get_object(trk_id)
            if obj and current_time - obj.last_seen > 5.0:  # 5 Sekunden nicht gesehen
                enhanced_tracker.remove_object(trk_id)

    # Zeige aktuelle Inventarinformationen auf dem Display für bessere Diagnose
    status_text = "Objekte im Inventory: "
    for obj_type in ALLOWED_CLASSES:
        obj_count = strict_inventory.get_count(obj_type)
        active_objs = len([obj for obj in enhanced_tracker.active_objects.values() if obj.product_type == obj_type])
        status_text += f"{obj_type}: {obj_count}/{OBJECT_LIMITS[obj_type]} ({active_objs}) | "

    # Kürze den String, um ihn besser auf dem Bildschirm anzuzeigen
    if len(status_text) > 70:
        status_text = status_text[:70] + "..."

    cv2.putText(annotated_frame, status_text, (10, 60), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                
    # NEU: Zeige Hinweis zur Neuinitialisierung bei Problemen
    any_limit_exceeded = False
    for obj_type in ALLOWED_CLASSES:
        objects_detected = len([obj for obj in enhanced_tracker.active_objects.values() 
                            if obj.product_type == obj_type])
        objects_tracked = strict_inventory.get_count(obj_type)
        if objects_tracked < OBJECT_LIMITS[obj_type] and objects_detected >= OBJECT_LIMITS[obj_type]:
            any_limit_exceeded = True
            break

    if any_limit_exceeded:
        help_text = "Hinweis: Drücke 'r' für Neustart der Erkennung"
        cv2.putText(annotated_frame, help_text, (10, 120), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    # Optional: Zeige den Status des Tracker-Gedächtnisses alle 5 Sekunden an
    if int(current_time) % 5 == 0:
        enhanced_tracker.print_memory_status()

    # NEU: Zeige an, ob im Vollbildmodus
    cv2.putText(annotated_frame, "Vollbild-Modus aktiv", (annotated_frame.shape[1] - 250, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    
    # NEUE ÄNDERUNG: Synchronisiere sichtbare Objekte mit Datenbank vor Überprüfung 
    if current_time - last_missing_check_time > missing_check_interval:
        # Synchronisiere vor der Prüfung die Datenbank mit dem visuellen Status
        for shelf_id in rois.keys():
            for product_type in ALLOWED_CLASSES:
                visible_count = len([obj for obj in enhanced_tracker.get_all_active_objects().values() 
                                if obj.current_shelf == shelf_id and obj.product_type == product_type
                                and obj.state != ObjectState.REMOVED])
                db_utils.update_inventory(shelf_id, product_type, visible_count)
                log_debug(f"Synchronisiere Inventar: Regal {shelf_id+1}, {product_type}: {visible_count}")
            
        # Füge Debug-Informationen zu check_for_missing_objects hinzu
        log_debug("Starte periodische Überprüfung auf fehlende Objekte...")
        check_for_missing_objects(rois, enhanced_tracker)
        last_missing_check_time = current_time
        log_debug("Periodische Überprüfung abgeschlossen.")
    # Aktualisiere die erkannten Objekte in der Datenbank
    update_detected_objects_in_db(rois, enhanced_tracker)
    cv2.imshow("YOLO Monitoring", annotated_frame)
    key = cv2.waitKey(1) & 0xFF
    
    # NEU: Tastendruck für Vollbild umschalten/beenden
    if key == ord('f'):  # 'f' für Fullscreen toggle
        current_mode = cv2.getWindowProperty("YOLO Monitoring", cv2.WND_PROP_FULLSCREEN)
        if current_mode == cv2.WINDOW_FULLSCREEN:
            cv2.setWindowProperty("YOLO Monitoring", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL)
            log_debug("Vollbild-Modus deaktiviert")
        else:
            cv2.setWindowProperty("YOLO Monitoring", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
            log_debug("Vollbild-Modus aktiviert")
    elif key == ord('r'):  # 'r' für Reset der Erkennung
        log_debug("Manueller Reset der Objekt-Erkennung angefordert")
        
        # Setze das strikte Inventory zurück
        for obj_type in ALLOWED_CLASSES:
            strict_inventory.set_count(obj_type, 0)
        strict_inventory.active_object_ids = {obj_type: set() for obj_type in ALLOWED_CLASSES}
        
        # Setze den Enhanced Tracker zurück
        enhanced_tracker.active_objects.clear()
        enhanced_tracker.memory_objects.clear()
        enhanced_tracker.tracked_object_history.clear()
        enhanced_tracker.unique_signatures = {obj_type: [] for obj_type in ALLOWED_CLASSES}
        
        # Setze die globalen Inventarzähler zurück
        for obj_type in ALLOWED_CLASSES:
            enhanced_tracker.global_inventory[obj_type] = 0
        
        # Setze den SORT Tracker zurück durch Neuinstanziierung
        tracker = reset_tracker()
        
        # Lösche alle aktuellen Objekte aus der Datenbank
        try:
            db_utils.clear_current_events()
            log_debug("Aktuelle Events aus Datenbank gelöscht")
        except Exception as e:
            log_debug(f"Fehler beim Löschen der Events: {e}")
        
        # Starte die Initialisierung neu
        inventory_initializer.reset()
        inventory_initializer.start_initialization()
        log_debug("Objekt-Erkennung vollständig zurückgesetzt - bereit für erneute Erfassung")
    elif key == ord('q'):
        break

# Aufräumen: Kamera freigeben und Fenster schließen
cap.release()
cv2.destroyAllWindows()