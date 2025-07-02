import tkinter as tk
from tkinter import ttk, messagebox
import time
import threading
import db_utils

# Aktualisierungsintervall auf 1 Sekunde erhöht (1000ms statt 300ms)
REFRESH_INTERVAL = 1000

class AnalysisScreen(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Analyse- und Warnscreen")
        self.geometry("900x600")
        # Flag, um zu prüfen, ob der Screen aktiv ist
        self.is_active = True
        self.create_widgets()
        self.update_data()

    def create_widgets(self):
        # Status-Anzeige oben
        status_frame = tk.Frame(self, bg="#f0f0f0")
        status_frame.pack(fill=tk.X, padx=10, pady=5)
        self.status_label = tk.Label(status_frame, text="Status: Verbunden", bg="#f0f0f0", font=("Arial", 10, "bold"))
        self.status_label.pack(side=tk.LEFT)
        
        self.last_update_label = tk.Label(status_frame, text="Letzte Aktualisierung: -", bg="#f0f0f0")
        self.last_update_label.pack(side=tk.RIGHT)
        
        # Filter-Frame
        filter_frame = tk.Frame(self)
        filter_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(filter_frame, text="Filter nach Status:").pack(side=tk.LEFT)
        # Status-Auswahl 
        self.status_filter = ttk.Combobox(filter_frame, 
                                          values=["Alle", "not paid", "paid", "misplaced", "returned", "replenished", "zurückgestellt"])
        self.status_filter.current(0)
        self.status_filter.pack(side=tk.LEFT, padx=5)

        tk.Label(filter_frame, text="Filter nach Produkt:").pack(side=tk.LEFT, padx=10)
        self.product_filter = ttk.Combobox(filter_frame, values=["Alle", "glass", "cup", "spoon", "fork"])
        self.product_filter.current(0)
        self.product_filter.pack(side=tk.LEFT, padx=5)

        tk.Button(filter_frame, text="Filter anwenden", command=self.update_data).pack(side=tk.LEFT, padx=10)
        tk.Button(filter_frame, text="Reset DB", command=self.reset_db).pack(side=tk.RIGHT, padx=10)
        tk.Button(filter_frame, text="Aktuelle Einträge löschen", command=self.clear_current_events).pack(side=tk.RIGHT, padx=10)

        # Treeview für Events mit Scrollbar
        event_frame = tk.Frame(self)
        event_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Scrollbars
        tree_scroll_y = tk.Scrollbar(event_frame)
        tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        tree_scroll_x = tk.Scrollbar(event_frame, orient=tk.HORIZONTAL)
        tree_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Treeview mit verbesserten Spalten
        columns = ("ID", "Regal", "Produkt", "Event-Typ", "Zeit", "Status", "Menge", "Objekt-ID")
        self.tree = ttk.Treeview(event_frame, columns=columns, show="headings",
                                 yscrollcommand=tree_scroll_y.set,
                                 xscrollcommand=tree_scroll_x.set)
        
        # Scrollbars mit Treeview verbinden
        tree_scroll_y.config(command=self.tree.yview)
        tree_scroll_x.config(command=self.tree.xview)
        
        # Spaltenbreiten und -überschriften
        for col in columns:
            self.tree.heading(col, text=col)
            width = 80 if col == "ID" or col == "Regal" or col == "Menge" or col == "Objekt-ID" else 120
            self.tree.column(col, width=width, minwidth=50)
        
        self.tree.pack(fill=tk.BOTH, expand=True)
        
        # Formatierung für verschiedene Status
        self.tree.tag_configure('not_paid', background='#FFD2D2')  # Hellrot
        self.tree.tag_configure('paid', background='#D2FFD2')      # Hellgrün
        self.tree.tag_configure('misplaced', background='#FFE8D2')  # Hellorange
        self.tree.tag_configure('returned', background='#D2D2FF')  # Hellblau

        # Inventar-Display
        inventory_frame = tk.Frame(self)
        inventory_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Inventar-Label mit besserer Formatierung
        inventory_header = tk.Frame(inventory_frame, bg="#e0e0e0")
        inventory_header.pack(fill=tk.X)
        tk.Label(inventory_header, text="Aktueller Lagerbestand", bg="#e0e0e0", font=("Arial", 10, "bold")).pack(anchor=tk.W, padx=5, pady=2)
        
        # Scrollbarer Text für Inventaranzeige
        inventory_text_frame = tk.Frame(inventory_frame)
        inventory_text_frame.pack(fill=tk.X)
        
        inventory_scroll = tk.Scrollbar(inventory_text_frame)
        inventory_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.inventory_text = tk.Text(inventory_text_frame, height=12, yscrollcommand=inventory_scroll.set)
        self.inventory_text.pack(fill=tk.X)
        inventory_scroll.config(command=self.inventory_text.yview)
        
        # Formatierungen für den Inventartext
        self.inventory_text.tag_configure("header", font=("Arial", 9, "bold"))
        self.inventory_text.tag_configure("normal", font=("Arial", 9))
        self.inventory_text.tag_configure("total", font=("Arial", 9, "bold"), foreground="blue")

        # Status-Bar unten
        self.status_bar = tk.Label(self, text="Bereit", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Automatische Inventarermittlung
        auto_inventory_frame = tk.Frame(self)
        auto_inventory_frame.pack(fill=tk.X, padx=10, pady=5)
        tk.Button(auto_inventory_frame, text="Inventar neu bestimmen", 
                  command=self.refresh_inventory).pack(side=tk.LEFT)
        
        # Einfacher Timer, der die vergangene Zeit seit Start anzeigt
        self.timer_label = tk.Label(auto_inventory_frame, text="Laufzeit: 0s")
        self.timer_label.pack(side=tk.RIGHT)
        self.start_time = time.time()
        self.update_timer()

    def update_timer(self):
        """Aktualisiert die Laufzeitanzeige"""
        if self.is_active:
            elapsed = int(time.time() - self.start_time)
            self.timer_label.config(text=f"Laufzeit: {elapsed}s")
            self.after(1000, self.update_timer)

    def update_data(self):
        """Aktualisiert die Anzeige mit Daten aus der Datenbank"""
        if not self.is_active:
            return
            
        try:
            # Treeview leeren
            for item in self.tree.get_children():
                self.tree.delete(item)

            status_filter = self.status_filter.get()
            product_filter = self.product_filter.get()
            events = db_utils.get_all_events()
            current_time = time.time()

            # Filtern und Anzeigen der Events
            filtered_events = []
            for event in events:
                # Anpassung für den Fall, dass object_id in der DB vorhanden ist
                if len(event) == 10:
                    event_id, shelf_id, product_type, event_type, event_time, resolved, resolution_time, status, quantity, object_id = event
                elif len(event) == 9:
                    event_id, shelf_id, product_type, event_type, event_time, resolved, resolution_time, status, quantity = event
                    object_id = -1
                else:
                    event_id, shelf_id, product_type, event_type, event_time, resolved, resolution_time, status = event
                    quantity = 1
                    object_id = -1
                    
                if status_filter != "Alle" and status.lower() != status_filter.lower():
                    continue
                if product_filter != "Alle" and product_type.lower() != product_filter.lower():
                    continue
                filtered_events.append((event_id, shelf_id, product_type, event_type, event_time, resolved, resolution_time, status, quantity, object_id))

            # Sortiere Events nach Zeit (neueste zuerst)
            filtered_events.sort(key=lambda x: x[4], reverse=True)
            
            for event in filtered_events:
                event_id, shelf_id, product_type, event_type, event_time, resolved, resolution_time, status, quantity, object_id = event
                event_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(event_time))
                
                # Tags für Formatierung
                tags = ()
                if status.lower() == "not paid":
                    tags = ('not_paid',)
                elif status.lower() == "paid":
                    tags = ('paid',)
                elif status.lower() == "misplaced":
                    tags = ('misplaced',)
                elif status.lower() == "returned":
                    tags = ('returned',)
                
                # Event in Treeview einfügen
                self.tree.insert("", "end", values=(
                    event_id, 
                    shelf_id+1, 
                    product_type.capitalize(), 
                    event_type.capitalize(), 
                    event_time_str, 
                    status.capitalize(), 
                    quantity,
                    object_id if object_id != -1 else "N/A"
                ), tags=tags)

            # Inventar aktualisieren
            self.update_inventory_display()
            
            # Status updaten
            self.last_update_label.config(text=f"Letzte Aktualisierung: {time.strftime('%H:%M:%S')}")
            self.status_label.config(text="Status: Verbunden", fg="green")
            self.status_bar.config(text=f"Daten aktualisiert. {len(filtered_events)} Events angezeigt.")
            
            # Nächste Aktualisierung planen
            self.after(REFRESH_INTERVAL, self.update_data)
            
        except Exception as e:
            self.status_label.config(text=f"Status: Fehler", fg="red")
            self.status_bar.config(text=f"Fehler bei Datenaktualisierung: {str(e)}")
            messagebox.showerror("Fehler", f"Fehler bei der Datenaktualisierung:\n{str(e)}")
            # Bei Fehler trotzdem versuchen, weiter zu aktualisieren
            self.after(REFRESH_INTERVAL * 2, self.update_data)

# In the update_inventory_display method of AnalysisScreen class (in paste-3.txt)

    def update_inventory_display(self):
        """Aktualisiert nur die Inventaranzeige mit verbesserten Informationen"""
        try:
            inventory = db_utils.get_inventory()
            self.inventory_text.delete("1.0", tk.END)
            
            # Formatierte Ausgabe mit Spaltenüberschriften
            header = "{:<15} {:<10} {:<15} {:<15} {:<15}\n".format(
                "Produkt", "Regal", "Startbestand", "Aktuell", "Status")
            self.inventory_text.insert(tk.END, header, "header")
            self.inventory_text.insert(tk.END, "-" * 70 + "\n", "normal")
            
            total_inventory = {}
            for inv in inventory:
                shelf_id, product_type, initial_count, current_count, last_update = inv
                sold, unpaid = db_utils.get_sales_data(shelf_id, product_type)
                
                # Ermittle den Status basierend auf dem aktuellen Bestand
                status = "OK"
                if current_count < initial_count * 0.3:
                    status = "Kritisch"
                elif current_count < initial_count * 0.7:
                    status = "Niedrig"
                
                # Formatierte Zeit des letzten Updates
                update_time = time.strftime("%H:%M:%S", time.localtime(last_update))
                
                line = "{:<15} {:<10} {:<15} {:<15} {:<15}\n".format(
                    product_type.capitalize(), 
                    f"Regal {shelf_id+1}", 
                    f"{initial_count} ({update_time})", 
                    f"{current_count} ({sold} verkauft)", 
                    status
                )
                self.inventory_text.insert(tk.END, line, "normal")
                
                # Sammle Gesamtbestand
                total_inventory[product_type] = total_inventory.get(product_type, 0) + current_count
            
            # Leerzeile und Gesamtbestand
            self.inventory_text.insert(tk.END, "\n", "normal")
            self.inventory_text.insert(tk.END, "Gesamter Lagerbestand:\n", "total")
            
            for product, count in total_inventory.items():
                self.inventory_text.insert(tk.END, f"{product.capitalize()}: {count}\n", "total")
                
        except Exception as e:
            self.inventory_text.delete("1.0", tk.END)
            self.inventory_text.insert(tk.END, f"Fehler beim Laden des Inventars: {str(e)}", "normal")

    def reset_db(self):
        """Setzt die Datenbank zurück"""
        response = messagebox.askyesno("Datenbank zurücksetzen", 
                                       "Möchtest du die Datenbank wirklich vollständig zurücksetzen? Alle Daten gehen verloren!")
        if response:
            try:
                db_utils.reset_db()
                messagebox.showinfo("Erfolg", "Datenbank wurde erfolgreich zurückgesetzt.")
                self.update_data()
            except Exception as e:
                messagebox.showerror("Fehler", f"Fehler beim Zurücksetzen der Datenbank:\n{str(e)}")

    def clear_current_events(self):
        """Löscht alle Einträge in der Events-Tabelle"""
        response = messagebox.askyesno("Events löschen", 
                                       "Möchtest du alle aktuellen Events löschen? Die Inventardaten bleiben erhalten.")
        if response:
            try:
                db_utils.clear_current_events()
                messagebox.showinfo("Erfolg", "Alle Events wurden erfolgreich gelöscht.")
                self.update_data()
            except Exception as e:
                messagebox.showerror("Fehler", f"Fehler beim Löschen der Events:\n{str(e)}")
    
    def refresh_inventory(self):
        """Sendet ein Signal an das YOLO-Monitoring, den Lagerbestand neu zu ermitteln"""
        response = messagebox.askyesno("Lagerbestand neu bestimmen", 
                                      "Möchtest du den Lagerbestand basierend auf der aktuellen Erkennung neu bestimmen?")
        if response:
            try:
                # Hier könnte man ein Signal an das YOLO Monitoring senden
                # Alternativ direkt die globale Variable setzen, wenn sie im selben Prozess läuft
                # Für diese Demo simulieren wir, dass das Signal gesendet wurde
                self.status_bar.config(text="Signal zur Neubestimmung des Lagerbestands gesendet...")
                
                # Dies wäre die tatsächliche Funktion in einer verbundenen Umgebung:
                # with open('inventory_refresh.signal', 'w') as f:
                #     f.write('1')
                
                messagebox.showinfo("Info", "Signal zur Neubestimmung des Lagerbestands wurde gesendet.")
            except Exception as e:
                messagebox.showerror("Fehler", f"Fehler beim Senden des Signals:\n{str(e)}")

    def on_closing(self):
        """Handler für das Schließen des Fensters"""
        self.is_active = False
        self.destroy()

if __name__ == "__main__":
    db_utils.init_db()
    app = AnalysisScreen()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()