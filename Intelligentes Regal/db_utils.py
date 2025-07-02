import sqlite3
import time
from debug_utils import log_debug  # Debug-Logging einbinden

DB_NAME = "supermarkt.db"

# Neues Dictionary für Objektlimits, passend zu ALLOWED_CLASSES und OBJECT_LIMITS in yolo_monitor.py
OBJECT_LIMITS = {
    "cup": 3,
    "book": 3,
    "bottle": 3,
    "wine glass": 3
}

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Tabelle "events" mit zusätzlichem Feld "object_id" für bessere Objektverfolgung
    c.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shelf_id INTEGER,
            product_type TEXT,
            event_type TEXT,
            event_time INTEGER,
            resolved INTEGER DEFAULT 0,
            resolution_time INTEGER,
            status TEXT DEFAULT 'not paid',
            quantity INTEGER DEFAULT 1,
            object_id INTEGER DEFAULT -1
        )
    ''')
    # Tabelle "inventory" für den Start- und aktuellen Bestand
    c.execute('''
        CREATE TABLE IF NOT EXISTS inventory (
            shelf_id INTEGER,
            product_type TEXT,
            initial_count INTEGER,
            current_count INTEGER,
            last_update INTEGER,
            PRIMARY KEY (shelf_id, product_type)
        )
    ''')
    
    # VERBESSERUNG: Neue Tabelle für Objektverfolgung
    c.execute('''
        CREATE TABLE IF NOT EXISTS object_tracking (
            object_id INTEGER PRIMARY KEY,
            product_type TEXT,
            original_shelf INTEGER,
            current_shelf INTEGER,
            state INTEGER,
            removal_time INTEGER,
            last_seen INTEGER,
            active_event_id INTEGER DEFAULT -1
        )
    ''')
    
    # NEU: Tabelle für aktuell erkannte Objekte aus YOLO
    c.execute('''
        CREATE TABLE IF NOT EXISTS detected_objects (
            shelf_id INTEGER,
            product_type TEXT,
            count INTEGER,
            last_update INTEGER,
            PRIMARY KEY (shelf_id, product_type)
        )
    ''')
    
    conn.commit()
    conn.close()
    log_debug("Datenbank initialisiert.")

def event_exists(shelf_id, product_type, event_type):
    """
    Prüft, ob bereits ein offener (nicht resolved) Eintrag für die gegebene Kombination existiert.
    """
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        SELECT id FROM events
        WHERE shelf_id = ? AND product_type = ? AND event_type = ? AND resolved = 0
        LIMIT 1
    ''', (shelf_id, product_type, event_type))
    exists = c.fetchone() is not None
    conn.close()
    return exists

def update_event_status(shelf_id, product_type, new_status, event_type="removal"):
    """
    Aktualisiert den Status eines offenen Events (z. B. von 'removal' auf 'not paid' oder 'misplaced').
    Es wird der älteste offene Eintrag für die Kombination (shelf_id, product_type, event_type) aktualisiert.
    """
    resolution_time = int(time.time())
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        SELECT id FROM events
        WHERE shelf_id = ? AND product_type = ? AND event_type = ? AND resolved = 0
        ORDER BY event_time ASC
        LIMIT 1
    ''', (shelf_id, product_type, event_type))
    row = c.fetchone()
    if row:
        event_id = row[0]
        c.execute('''
            UPDATE events
            SET status = ?, resolution_time = ?
            WHERE id = ?
        ''', (new_status, resolution_time, event_id))
        log_debug(f"update_event_status: Regal {shelf_id+1} {product_type} aktualisiert auf Status = {new_status}.")
    conn.commit()
    conn.close()

def upsert_event(shelf_id, product_type, event_type, status, quantity_increment=1, object_id=-1):
    """
    Erstellt immer ein neues Event in der Datenbank, anstatt existierende zu aktualisieren.
    """
    now = int(time.time())
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Immer ein neues Event erstellen (kein UPDATE mehr, nur noch INSERT)
    c.execute('''
        INSERT INTO events (shelf_id, product_type, event_type, event_time, resolved, status, quantity, object_id)
        VALUES (?, ?, ?, ?, 0, ?, ?, ?)
    ''', (shelf_id, product_type, event_type, now, status, quantity_increment, object_id))
    
    log_debug(f"upsert_event: Neues Event für Regal {shelf_id+1} {product_type} ({event_type}) angelegt: Menge = {quantity_increment}, Status = {status}, Objekt-ID = {object_id}.")
    
    conn.commit()
    conn.close()
    return


def update_detected_objects(shelf_id, product_type, count):
    """Aktualisiert die Anzahl der aktuell erkannten Objekte in einem Regal."""
    now = int(time.time())
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO detected_objects (shelf_id, product_type, count, last_update)
        VALUES (?, ?, ?, ?)
    ''', (shelf_id, product_type, count, now))
    conn.commit()
    conn.close()
    log_debug(f"update_detected_objects: Regal {shelf_id+1} {product_type} aktualisiert auf {count}.")

def get_detected_objects():
    """Liefert die Anzahl der aktuell erkannten Objekte in allen Regalen."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT shelf_id, product_type, count, last_update FROM detected_objects')
    rows = c.fetchall()
    conn.close()
    return rows


def mark_event_returned(shelf_id, product_type, event_type="removal", num_events=1, override_event_type=None):
    """
    Aktualisiert einen offenen Eintrag:
      - Ohne override: Rückführung im richtigen Regal → Wenn num_events den offenen Betrag abdeckt, wird der Status auf "returned" (bei removal)
        oder "zurückgestellt" (bei anderen) gesetzt.
      - Mit override_event_type (z. B. "misplacement"): Es wird versucht, einen offenen removal-Eintrag zu finden und ihn auf misplacement zu ändern.
    """
    resolution_time = int(time.time())
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    if override_event_type:
        # Suche zuerst einen offenen removal-Eintrag
        c.execute('''
            SELECT id, quantity FROM events
            WHERE shelf_id = ? AND product_type = ? AND event_type = "removal" AND resolved = 0
            ORDER BY event_time ASC
            LIMIT 1
        ''', (shelf_id, product_type))
        row = c.fetchone()
        if row:
            event_id, quantity = row
            new_quantity = quantity - num_events
            if new_quantity <= 0:
                c.execute('''
                    UPDATE events
                    SET resolved = 1, resolution_time = ?, quantity = 0, status = "misplaced", event_type = ?
                    WHERE id = ?
                ''', (resolution_time, override_event_type, event_id))
                log_debug(f"mark_event_returned OVERRIDE: Regal {shelf_id+1} {product_type} von removal auf misplacement aktualisiert (vollständig).")
            else:
                c.execute('''
                    UPDATE events
                    SET quantity = ?, resolution_time = ?, status = "not paid"
                    WHERE id = ?
                ''', (new_quantity, resolution_time, event_id))
                log_debug(f"mark_event_returned OVERRIDE: Regal {shelf_id+1} {product_type} removal aktualisiert: neue Menge = {new_quantity}.")
        else:
            # Suche nach einem offenen misplacement-Eintrag
            c.execute('''
                SELECT id, quantity FROM events
                WHERE shelf_id = ? AND product_type = ? AND event_type = ? AND resolved = 0
                ORDER BY event_time ASC
                LIMIT 1
            ''', (shelf_id, product_type, override_event_type))
            row = c.fetchone()
            if row:
                event_id, quantity = row
                new_quantity = quantity + num_events
                c.execute('''
                    UPDATE events
                    SET quantity = ?, event_time = ?, status = "misplaced"
                    WHERE id = ?
                ''', (new_quantity, resolution_time, event_id))
                log_debug(f"mark_event_returned OVERRIDE: Bestehender misplacement-Eintrag in Regal {shelf_id+1} {product_type} um {num_events} erhöht, neue Menge = {new_quantity}.")
            else:
                c.execute('''
                    INSERT INTO events (shelf_id, product_type, event_type, event_time, resolved, status, quantity)
                    VALUES (?, ?, ?, ?, 0, "misplaced", ?)
                ''', (shelf_id, product_type, override_event_type, resolution_time, num_events))
                log_debug(f"mark_event_returned OVERRIDE: Neuer misplacement-Eintrag in Regal {shelf_id+1} {product_type} angelegt mit Menge = {num_events}.")
        conn.commit()
        conn.close()
        return

    # Normale Rückführung im richtigen Regal
    c.execute('''
        SELECT id, quantity FROM events
        WHERE shelf_id = ? AND product_type = ? AND event_type = ? AND resolved = 0
        ORDER BY event_time ASC
        LIMIT 1
    ''', (shelf_id, product_type, event_type))
    row = c.fetchone()
    if row:
        event_id, quantity = row
        new_quantity = quantity - num_events
        if new_quantity <= 0:
            final_status = 'returned' if event_type == "removal" else 'zurückgestellt'
            c.execute('''
                UPDATE events
                SET resolved = 1, resolution_time = ?, quantity = ?, status = ?
                WHERE id = ?
            ''', (resolution_time, quantity, final_status, event_id))  # Behalte ursprüngliche Menge bei
            log_debug(f"mark_event_returned: Regal {shelf_id+1} {product_type} ({event_type}) vollständig zurückgeführt, Status = {final_status}.")
            
            # NEU: Erstelle immer ein Return-Event beim Zurückführen
            upsert_event(shelf_id, product_type, "return", final_status, quantity_increment=quantity)
            log_debug(f"mark_event_returned: Neues Return-Event für Regal {shelf_id+1} {product_type} angelegt.")
        else:
            partial_status = 'not paid' if event_type == "removal" else 'misplaced'
            c.execute('''
                UPDATE events
                SET quantity = ?, resolution_time = ?, status = ?
                WHERE id = ?
            ''', (new_quantity, resolution_time, partial_status, event_id))
            
            # NEU: Erstelle auch bei Teilrückführung ein Return-Event
            upsert_event(shelf_id, product_type, "return", "partial_" + final_status, quantity_increment=num_events)
            log_debug(f"mark_event_returned: Neues Return-Event (Teilrückführung) für Regal {shelf_id+1} {product_type} angelegt.")
            
            log_debug(f"mark_event_returned: Regal {shelf_id+1} {product_type} ({event_type}) teilweise zurückgeführt, neue Menge = {new_quantity}.")
    conn.commit()
    conn.close()

def get_all_events():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT * FROM events ORDER BY event_time ASC')
    rows = c.fetchall()
    conn.close()
    return rows

def get_unresolved_events_older_than(seconds, event_type_filter=None):
    threshold = int(time.time()) - seconds
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    if event_type_filter:
        c.execute('''
            SELECT * FROM events
            WHERE event_time <= ? AND resolved = 0 AND event_type = ?
            ORDER BY event_time ASC
        ''', (threshold, event_type_filter))
    else:
        c.execute('''
            SELECT * FROM events
            WHERE event_time <= ? AND resolved = 0
            ORDER BY event_time ASC
        ''', (threshold,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_unresolved_count(shelf_id, product_type, event_type="removal"):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        SELECT SUM(quantity) FROM events
        WHERE shelf_id = ? AND product_type = ? AND event_type = ? AND resolved = 0
    ''', (shelf_id, product_type, event_type))
    result = c.fetchone()[0]
    conn.close()
    return result if result is not None else 0

def set_initial_inventory(shelf_id, product_type, count):
    """
    Setzt den initialen Lagerbestand für ein Regal und einen Produkttyp.
    Stellt sicher, dass count niemals negativ ist und dass die Summe der Werte
    für den gleichen Produkttyp einen globalen Maximalwert nicht überschreitet.
    """
    # Stelle sicher, dass der Count nicht negativ ist
    count = max(0, count)
    
    # Hole die Summe der aktuellen Werte für diesen Produkttyp über alle Regale
    # außer dem aktuellen Regal
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        SELECT SUM(current_count) FROM inventory
        WHERE product_type = ? AND shelf_id != ?
    ''', (product_type, shelf_id))
    other_shelves_sum = c.fetchone()[0] or 0
    
    # Für alle Produkttypen ein globales Maximum prüfen
    if product_type in OBJECT_LIMITS:
        global_max = OBJECT_LIMITS[product_type]  # Maximalwert für diesen Produkttyp
        if other_shelves_sum >= global_max:
            # Andere Regale haben bereits die maximale Anzahl, setze dieses auf 0
            count = 0
            log_debug(f"set_initial_inventory: Regal {shelf_id+1} {product_type} auf 0 gesetzt, da global bereits {other_shelves_sum}/{global_max} vorhanden.")
        elif other_shelves_sum + count > global_max:
            # Reduziere den Count für dieses Regal, um das globale Maximum einzuhalten
            count = global_max - other_shelves_sum
            log_debug(f"set_initial_inventory: Regal {shelf_id+1} {product_type} auf {count} begrenzt (global {other_shelves_sum}+{count}={other_shelves_sum+count}/{global_max}).")
    
    # Aktualisiere oder erstelle den Eintrag in der Datenbank
    now = int(time.time())
    c.execute('''
        INSERT OR REPLACE INTO inventory (shelf_id, product_type, initial_count, current_count, last_update)
        VALUES (?, ?, ?, ?, ?)
    ''', (shelf_id, product_type, count, count, now))
    conn.commit()
    conn.close()
    
    log_debug(f"set_initial_inventory: Regal {shelf_id+1} {product_type} initial auf {count} gesetzt.")
    return count  # Gib den tatsächlich gesetzten Wert zurück

def update_inventory(shelf_id, product_type, new_count):
    """
    Aktualisiert den Bestand eines Regals, unter Berücksichtigung globaler Maximalwerte.
    """
    # Stelle sicher, dass der Count nicht negativ ist
    new_count = max(0, new_count)
    
    # Prüfe auf globale Maximalwerte für alle Produkttypen
    if product_type in OBJECT_LIMITS:
        global_max = OBJECT_LIMITS[product_type]  # Maximalwert für diesen Produkttyp
        
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        
        # Hole aktuellen Wert für dieses Regal
        c.execute('''
            SELECT current_count FROM inventory
            WHERE shelf_id = ? AND product_type = ?
        ''', (shelf_id, product_type))
        current_count = c.fetchone()
        current_count = current_count[0] if current_count else 0
        
        # Berechne Differenz zum neuen Wert
        diff = new_count - current_count
        
        if diff > 0:
            # Wenn wir erhöhen wollen, prüfe, ob das global möglich ist
            c.execute('''
                SELECT SUM(current_count) FROM inventory
                WHERE product_type = ? AND shelf_id != ?
            ''', (product_type, shelf_id))
            other_shelves_sum = c.fetchone()[0] or 0
            
            if other_shelves_sum + new_count > global_max:
                # Begrenze den neuen Wert
                new_count = max(0, global_max - other_shelves_sum)
                log_debug(f"update_inventory: Regal {shelf_id+1} {product_type} auf {new_count} begrenzt (global max {global_max}).")
        
        now = int(time.time())
        c.execute('''
            UPDATE inventory
            SET current_count = ?, last_update = ?
            WHERE shelf_id = ? AND product_type = ?
        ''', (new_count, now, shelf_id, product_type))
        
        # Wenn noch kein Eintrag existiert, erstelle einen neuen
        if c.rowcount == 0:
            c.execute('''
                INSERT INTO inventory (shelf_id, product_type, initial_count, current_count, last_update)
                VALUES (?, ?, ?, ?, ?)
            ''', (shelf_id, product_type, new_count, new_count, now))
            log_debug(f"update_inventory: Neuer Inventareintrag für Regal {shelf_id+1} {product_type} erstellt mit {new_count}.")
        
        conn.commit()
        conn.close()
    else:
        # Für andere Produkttypen ohne globale Begrenzung
        now = int(time.time())
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''
            UPDATE inventory
            SET current_count = ?, last_update = ?
            WHERE shelf_id = ? AND product_type = ?
        ''', (new_count, now, shelf_id, product_type))
        
        # Wenn noch kein Eintrag existiert, erstelle einen neuen
        if c.rowcount == 0:
            c.execute('''
                INSERT INTO inventory (shelf_id, product_type, initial_count, current_count, last_update)
                VALUES (?, ?, ?, ?, ?)
            ''', (shelf_id, product_type, new_count, new_count, now))
            log_debug(f"update_inventory: Neuer Inventareintrag für Regal {shelf_id+1} {product_type} erstellt mit {new_count}.")
        
        conn.commit()
        conn.close()
    
    log_debug(f"update_inventory: Regal {shelf_id+1} {product_type} aktualisiert auf {new_count}.")

def increment_initial_inventory(shelf_id, product_type, diff):
    """Erhöht den initialen Bestand in der Inventartabelle um 'diff'."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        UPDATE inventory
        SET initial_count = initial_count + ?
        WHERE shelf_id = ? AND product_type = ?
    ''', (diff, shelf_id, product_type))
    conn.commit()
    conn.close()
    log_debug(f"increment_initial_inventory: Regal {shelf_id+1} {product_type} initial um {diff} erhöht.")

def get_inventory():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT * FROM inventory ORDER BY shelf_id, product_type')
    rows = c.fetchall()
    conn.close()
    return rows

def get_sales_data(shelf_id, product_type):
    """Liefert (verkauft, offen) basierend auf removal-Events."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        SELECT SUM(quantity) FROM events
        WHERE shelf_id = ? AND product_type = ? AND event_type = 'removal' AND status = 'paid'
    ''', (shelf_id, product_type))
    sold = c.fetchone()[0] or 0
    c.execute('''
        SELECT SUM(quantity) FROM events
        WHERE shelf_id = ? AND product_type = ? AND event_type = 'removal' AND status = 'not paid'
    ''', (shelf_id, product_type))
    unpaid = c.fetchone()[0] or 0
    conn.close()
    return sold, unpaid

def reset_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS events")
    c.execute("DROP TABLE IF EXISTS inventory")
    c.execute("DROP TABLE IF EXISTS object_tracking")  # VERBESSERUNG: Neue Tabelle ebenfalls zurücksetzen
    c.execute("DROP TABLE IF EXISTS detected_objects")  # Auch die detected_objects Tabelle zurücksetzen
    conn.commit()
    conn.close()
    init_db()
    log_debug("reset_db: Datenbank wurde zurückgesetzt.")

def clear_current_events():
    """Löscht alle Einträge in der Events-Tabelle, behält aber die Inventardaten."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM events")
    c.execute("DELETE FROM object_tracking")  # VERBESSERUNG: Auch Objektverfolgung zurücksetzen
    conn.commit()
    conn.close()
    log_debug("clear_current_events: Alle Event-Einträge wurden gelöscht.")

def removal_event_exists_by_product(product_type):
    """
    Prüft, ob bereits ein offener Removal-Event für den gegebenen Produkttyp existiert,
    unabhängig von der Regalzuordnung.
    """
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        SELECT id FROM events
        WHERE product_type = ? AND event_type = "removal" AND resolved = 0
        LIMIT 1
    ''', (product_type,))
    exists = c.fetchone() is not None
    conn.close()
    return exists

# VERBESSERUNG: Neue Funktionen für Objektverfolgung
def get_active_removal_event_by_object(object_id):
    """
    Liefert Details zum aktiven Removal-Event des Objekts, falls vorhanden.
    Rückgabewert: (event_id, shelf_id, product_type) oder None
    """
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        SELECT id, shelf_id, product_type FROM events
        WHERE object_id = ? AND event_type = "removal" AND resolved = 0
        LIMIT 1
    ''', (object_id,))
    row = c.fetchone()
    conn.close()
    return row

def update_object_tracking(object_id, product_type, original_shelf, current_shelf, state, active_event_id=-1):
    """
    Aktualisiert oder erstellt einen Eintrag in der object_tracking Tabelle.
    """
    now = int(time.time())
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO object_tracking 
        (object_id, product_type, original_shelf, current_shelf, state, last_seen, active_event_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (object_id, product_type, original_shelf, current_shelf, state, now, active_event_id))
    conn.commit()
    conn.close()

def get_object_tracking(object_id):
    """
    Liefert die Tracking-Informationen für ein Objekt.
    """
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT * FROM object_tracking WHERE object_id = ?', (object_id,))
    row = c.fetchone()
    conn.close()
    return row

def get_inventory_count(shelf_id, product_type):
    """Liefert den aktuellen Bestand für ein Regal und Produkttyp."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        SELECT current_count FROM inventory
        WHERE shelf_id = ? AND product_type = ?
    ''', (shelf_id, product_type))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def increment_inventory_count(shelf_id, product_type, delta):
    """
    Erhöht oder verringert den aktuellen Bestand um delta.
    Stellt sicher, dass globale Maximalwerte eingehalten werden.
    """
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Hole den aktuellen Bestand für dieses Regal
    c.execute('''
        SELECT current_count FROM inventory
        WHERE shelf_id = ? AND product_type = ?
    ''', (shelf_id, product_type))
    
    row = c.fetchone()
    current_count = row[0] if row else 0
    
    # Wenn wir erhöhen wollen, prüfe globale Grenzen
    if delta > 0 and product_type in OBJECT_LIMITS:
        # Hole die Summe aller Regale für diesen Produkttyp
        c.execute('''
            SELECT SUM(current_count) FROM inventory
            WHERE product_type = ?
        ''', (product_type,))
        total_count = c.fetchone()[0] or 0
        
        global_max = OBJECT_LIMITS[product_type]  # Maximalwert für diesen Produkttyp
        if total_count >= global_max:
            log_debug(f"increment_inventory_count: Globales Maximum für {product_type} bereits erreicht ({total_count}/{global_max}). Keine Erhöhung möglich.")
            conn.close()
            return False
        elif total_count + delta > global_max:
            # Reduziere delta, um das globale Maximum einzuhalten
            old_delta = delta
            delta = global_max - total_count
            log_debug(f"increment_inventory_count: Delta für {product_type} von {old_delta} auf {delta} reduziert (global {total_count}+{delta}={total_count+delta}/{global_max}).")
    
    # Berechne den neuen Wert (verhindere negative Werte)
    new_count = max(0, current_count + delta)
    
    # Aktualisiere oder erstelle einen neuen Eintrag
    now = int(time.time())
    if row:
        c.execute('''
            UPDATE inventory
            SET current_count = ?, last_update = ?
            WHERE shelf_id = ? AND product_type = ?
        ''', (new_count, now, shelf_id, product_type))
    else:
        initial_count = max(0, delta)  # Bei negativem Delta starte mit 0
        c.execute('''
            INSERT INTO inventory (shelf_id, product_type, initial_count, current_count, last_update)
            VALUES (?, ?, ?, ?, ?)
        ''', (shelf_id, product_type, initial_count, new_count, now))
    
    conn.commit()
    conn.close()
        
    log_debug(f"increment_inventory_count: Regal {shelf_id+1} {product_type} Bestand von {current_count} auf {new_count} geändert.")
    return True