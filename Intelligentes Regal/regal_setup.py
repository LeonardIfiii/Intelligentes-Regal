import cv2
import numpy as np
import json
import os.path

# Konfigurationen
CONFIG_FILE = "regal_config.json"
MAX_SHELVES = 4  # Maximale Anzahl von Regalen

class ROICreator:
    def __init__(self):
        # Initialisiere Variablen
        self.rois = {}  # Format: {shelf_id: (x, y, w, h)}
        self.drawing = False
        self.start_point = None
        self.current_rectangle = None
        self.current_shelf = 0
        self.frame = None
        
        # Initialisiere Kamera
        self.cap = cv2.VideoCapture(0)  # 1 für externe Webcam, ändern Sie zu 0 für interne Kamera
        if not self.cap.isOpened():
            print("Konnte Kamera nicht öffnen. Versuche Kamera 0...")
            self.cap = cv2.VideoCapture(0)
            if not self.cap.isOpened():
                raise Exception("Keine Kamera konnte geöffnet werden")
        
        # Fenstereinstellungen
        cv2.namedWindow("ROI Creator", cv2.WND_PROP_FULLSCREEN)
        cv2.setMouseCallback("ROI Creator", self.mouse_event)
        
        # Lade bestehende Konfiguration, falls vorhanden
        self.load_config()

    def load_config(self):
        """Lädt bestehende Konfiguration, falls vorhanden"""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                    self.rois = {int(k): tuple(v) for k, v in config.get('rois', {}).items()}
                print(f"Konfiguration geladen: {len(self.rois)} Regale")
            except Exception as e:
                print(f"Fehler beim Laden der Konfiguration: {e}")

    def save_config(self):
        """Speichert die Konfiguration in eine Datei"""
        config = {
            'rois': {str(k): v for k, v in self.rois.items()}
        }
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=4)
            print(f"Konfiguration gespeichert: {len(self.rois)} Regale")
        except Exception as e:
            print(f"Fehler beim Speichern der Konfiguration: {e}")

    def mouse_event(self, event, x, y, flags, param):
        """Verarbeitet Mausereignisse für das Zeichnen von Regalbereichen"""
        if event == cv2.EVENT_LBUTTONDOWN:
            if not self.drawing:
                self.drawing = True
                self.start_point = (x, y)

        elif event == cv2.EVENT_MOUSEMOVE:
            if self.drawing:
                self.current_rectangle = (self.start_point[0], self.start_point[1], x, y)

        elif event == cv2.EVENT_LBUTTONUP:
            self.drawing = False
            end_point = (x, y)
            # Stelle sicher, dass wir ein gültiges Rechteck haben
            if self.start_point != end_point:
                # Erstelle das Rechteck
                x1, y1 = self.start_point
                x2, y2 = end_point
                # Stelle sicher, dass x1,y1 die obere linke Ecke ist
                if x1 > x2:
                    x1, x2 = x2, x1
                if y1 > y2:
                    y1, y2 = y2, y1
                # Speichere das Regal als (x, y, w, h)
                self.rois[self.current_shelf] = (x1, y1, x2-x1, y2-y1)
                print(f"Regal {self.current_shelf+1} definiert: {self.rois[self.current_shelf]}")
                self.current_rectangle = None
                
                # Wenn noch Platz für weitere Regale ist, erhöhe den Zähler
                if self.current_shelf < MAX_SHELVES - 1:
                    self.current_shelf += 1

    def draw_ui(self, frame):
        """Zeichnet die Benutzeroberfläche"""
        # Zeichne alle definierten Regale
        for shelf_id, (rx, ry, rw, rh) in self.rois.items():
            color = (0, 255, 0)  # Grün für bereits definierte Regale
            cv2.rectangle(frame, (rx, ry), (rx+rw, ry+rh), color, 2)
            cv2.putText(frame, f"Regal {shelf_id+1}", (rx, ry-10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        
        # Zeichne das aktuelle Rechteck während der Erstellung
        if self.current_rectangle:
            x1, y1, x2, y2 = self.current_rectangle
            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
        
        # Anzeige für den aktuellen Modus
        cv2.putText(frame, "Regale definieren", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        
        # Anleitung
        instructions = [
            "Taste 's' - Speichern",
            "Taste 'r' - Reset",
            "Taste 'f' - Vollbild ein/aus",
            "Taste 'q' - Beenden"
        ]
        for i, text in enumerate(instructions):
            cv2.putText(frame, text, (10, 70 + i*30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
                        
        # Zeige an, welches Regal als nächstes gezeichnet wird
        if self.current_shelf < MAX_SHELVES:
            cv2.putText(frame, f"Zeichne Regal {self.current_shelf+1}/{MAX_SHELVES}", (10, frame.shape[0]-20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                        
        # Zeige an, ob Konfiguration bereit ist
        all_shelves_defined = len(self.rois) == MAX_SHELVES
        
        status_text = "Status: "
        if all_shelves_defined:
            status_text += "Bereit! (Alle Regale definiert)"
            status_color = (0, 255, 0)
        else:
            status_text += f"Regale fehlen ({len(self.rois)}/{MAX_SHELVES})"
            status_color = (0, 165, 255)
            
        cv2.putText(frame, status_text, (frame.shape[1]//2 - 200, frame.shape[0]-20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)

    def run(self):
        """Hauptschleife für die ROI-Erstellung"""
        while True:
            ret, frame = self.cap.read()
            if not ret:
                print("Fehler beim Lesen der Kamera")
                break
                
            # Da die Kamera auf dem Kopf steht, drehen wir das Bild um 180 Grad
            frame = cv2.rotate(frame, cv2.ROTATE_180)
            
            self.frame = frame.copy()
            self.draw_ui(self.frame)
            
            cv2.imshow("ROI Creator", self.frame)
            key = cv2.waitKey(1) & 0xFF
            
            if key == ord('q'):
                break
            elif key == ord('s'):  # Speichern
                self.save_config()
            elif key == ord('r'):  # Reset
                self.rois = {}
                self.current_shelf = 0
                self.drawing = False
                self.current_rectangle = None
                print("Konfiguration zurückgesetzt")
            elif key == ord('f'):  # Vollbild umschalten
                current_mode = cv2.getWindowProperty("ROI Creator", cv2.WND_PROP_FULLSCREEN)
                if current_mode == cv2.WINDOW_FULLSCREEN:
                    cv2.setWindowProperty("ROI Creator", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL)
                    print("Vollbild-Modus deaktiviert")
                else:
                    cv2.setWindowProperty("ROI Creator", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
                    print("Vollbild-Modus aktiviert")
        
        # Aufräumen
        self.cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    print("ROI Creator wird gestartet...")
    try:
        creator = ROICreator()
        creator.run()
    except Exception as e:
        print(f"Fehler: {e}")