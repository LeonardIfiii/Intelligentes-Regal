// Kassensystem functionality
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
});