// Dashboard functionality
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
});