// Warehouse Dashboard functionality
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
});