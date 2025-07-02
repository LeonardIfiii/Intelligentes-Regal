// Funktionen für den Kunden-Display

document.addEventListener('DOMContentLoaded', function() {
    // Funktion, um den Displaystatus zu prüfen und die Seite bei Bedarf zu aktualisieren
    function checkDisplayStatus() {
        fetch('/api/display_status')
            .then(response => response.json())
            .then(data => {
                // Wenn sich etwas geändert hat, die Seite neu laden
                const currentPath = window.location.pathname;
                const shouldBeOnWelcome = data.display_mode === 'welcome' && currentPath !== '/';
                const shouldBeOnDetail = data.display_mode === 'product_detail' && currentPath === '/';
                
                if (shouldBeOnWelcome || shouldBeOnDetail) {
                    window.location.href = '/';
                } else if (data.display_mode === 'product_detail') {
                    // Auf der richtigen Seite, aber evtl. falsches Produkt - Daten aktualisieren
                    updateProductData(data.current_product);
                }
            })
            .catch(error => {
                console.error('Fehler beim Prüfen des Displaystatus:', error);
            });
    }
    
    // Funktion, um Produktdaten zu aktualisieren, ohne die Seite neu zu laden
    function updateProductData(productType) {
        if (!productType) return;
        
        fetch(`/api/product_data?type=${productType}`)
            .then(response => response.json())
            .then(data => {
                if (!data.success) return;
                
                // Aktualisiere die "Letzte Aktualisierung"-Zeit
                document.getElementById('last-update').textContent = data.current_time;
                
                // Wenn wir auf der Detailseite sind, weitere Elemente aktualisieren
                if (window.location.pathname !== '/') {
                    // Hier könnten weitere DOM-Updates erfolgen
                }
            })
            .catch(error => {
                console.error('Fehler beim Aktualisieren der Produktdaten:', error);
            });
    }
    
    // Automatische periodische Prüfung des Displaystatus
    setInterval(checkDisplayStatus, 3000);
    
    // In einer echten Implementierung: Event-Listener für QR-Code-Scannen, 
    // Berührungsgesten usw. hinzufügen
    
    // Beispiel für einen Klick-Handler für Produktkarten (in einer echten Implementierung
    // würde dies durch Berührungserkennung oder Kameraverfolgung ersetzt)
    const productCards = document.querySelectorAll('.product-card');
    if (productCards) {
        productCards.forEach(card => {
            card.addEventListener('click', function() {
                // Produkttyp aus Datenattribut oder durch DOM-Traversierung extrahieren
                // In einer echten Implementierung würde dies anders funktionieren
                const productName = this.querySelector('h4').textContent;
                
                // Simuliere eine Produktauswahl (nur für Demo-Zwecke)
                fetch('/api/set_display_mode', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    body: new URLSearchParams({
                        'mode': 'product_detail',
                        'product': getProductTypeFromName(productName)
                    })
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        window.location.href = '/';
                    }
                })
                .catch(error => {
                    console.error('Fehler beim Setzen des Displaymodus:', error);
                });
            });
        });
    }
    
    // Hilfsfunktion, um den Produkttyp aus dem Namen zu extrapolieren (nur für Demo)
    function getProductTypeFromName(name) {
        name = name.toLowerCase();
        if (name.includes('cup') || name.includes('becher')) return 'cup';
        if (name.includes('glass') || name.includes('glas')) return 'glass';
        if (name.includes('spoon') || name.includes('löffel')) return 'spoon';
        if (name.includes('fork') || name.includes('gabel')) return 'fork';
        return 'cup'; // Fallback
    }
    
    // Füge Event-Listener für den Zurück-Button hinzu
    const backButton = document.querySelector('.back-button');
    if (backButton) {
        backButton.addEventListener('click', function(e) {
            e.preventDefault();
            fetch('/api/set_display_mode', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: new URLSearchParams({
                    'mode': 'welcome'
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    window.location.href = '/';
                }
            })
            .catch(error => {
                console.error('Fehler beim Setzen des Displaymodus:', error);
            });
        });
    }
    
    // Event-Listener für Tabs (wenn sie existieren)
    const tabElements = document.querySelectorAll('button[data-bs-toggle="tab"]');
    if (tabElements) {
        tabElements.forEach(tab => {
            tab.addEventListener('shown.bs.tab', function (event) {
                // Hier könnten Analysetracking-Codes oder andere Aktionen eingefügt werden
                console.log(`Tab gewechselt zu: ${event.target.id}`);
            });
        });
    }
    
    // Erstelle Platzhalterbilder, wenn Bildreferenzen nicht funktionieren
    function setupPlaceholderImages() {
        document.querySelectorAll('img').forEach(img => {
            img.addEventListener('error', function() {
                this.src = '/static/img/placeholder.jpg';
            });
        });
    }
    
    setupPlaceholderImages();
});