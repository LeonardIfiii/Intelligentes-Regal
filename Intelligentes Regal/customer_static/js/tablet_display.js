// Tablet-Display JavaScript

document.addEventListener('DOMContentLoaded', function() {
    // Speichere anfänglichen Produkt-Status und Lagerbestand
    const initialProduct = document.body.dataset.product || null;
    const initialStock = parseInt(document.body.dataset.stock || 0, 10);
    console.log('Initial state:', initialProduct, initialStock);

    // Auto-Refresh-Funktion, prüft alle 3 Sekunden, ob ein Update notwendig ist
    function startAutoRefresh() {
        let lastKnownState = {
            product: initialProduct,
            last_update: 0,
            remaining_seconds: parseInt(document.getElementById('timer-seconds')?.textContent || '15', 10)
        };
        
        // Aktualisiere den Timer jede Sekunde, aber rufe die API nur alle 3 Sekunden auf
        const timerInterval = setInterval(function() {
            const timerElement = document.getElementById('timer-seconds');
            if (timerElement) {
                let seconds = parseInt(timerElement.textContent, 10);
                if (seconds > 0) {
                    seconds--;
                    timerElement.textContent = seconds;
                    
                    // Aktualisiere auch die Fortschrittsleiste
                    const timerBar = document.getElementById('timer-bar');
                    if (timerBar) {
                        const percentRemaining = (seconds / 15) * 100;
                        timerBar.style.width = percentRemaining + '%';
                    }
                }
                // Wenn der Timer abgelaufen ist, Seite neu laden
                if (seconds <= 0) {
                    window.location.reload();
                }
            }
        }, 1000);
        
        // Weniger häufiges Polling für API-Updates
        const apiInterval = setInterval(function() {
            fetch('/api/current_view')
                .then(response => response.json())
                .then(data => {
                    // Wenn sich das Produkt geändert hat, Seite neu laden
                    if (data.product !== lastKnownState.product || 
                        data.last_update > lastKnownState.last_update) {
                        
                        console.log('State change detected:', data.product, lastKnownState.product);
                        // Update lastKnownState, um redundante Reloads zu vermeiden
                        lastKnownState = data;
                        
                        // Seite neu laden
                        window.location.reload();
                    } 
                })
                .catch(error => {
                    console.error('Fehler beim Abrufen des aktuellen Status:', error);
                });
        }, 3000);  // Alle 3 Sekunden prüfen
    }
    
    // Starte Auto-Refresh
    startAutoRefresh();
    
    // Initialisiere Timer-Anzeige, wenn wir uns auf einer Produktseite befinden
    const timerSeconds = document.getElementById('timer-seconds');
    const timerBar = document.getElementById('timer-bar');
    
    if (timerSeconds && timerBar) {
        const initialSeconds = parseInt(timerSeconds.textContent, 10);
        const percentRemaining = (initialSeconds / 15) * 100;
        timerBar.style.width = percentRemaining + '%';
    }
    
    // Für Produktvorschau in der Willkommensansicht
    document.querySelectorAll('.product-preview-item').forEach(card => {
        card.addEventListener('click', function() {
            // Produkttyp aus Datenattribut extrahieren
            const productType = this.dataset.productType;
            
            if (productType) {
                fetch('/api/set_product', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    body: `product_type=${productType}`
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        window.location.reload();
                    }
                })
                .catch(error => {
                    console.error('Fehler beim Setzen des Produkts:', error);
                });
            }
        });
    });
});