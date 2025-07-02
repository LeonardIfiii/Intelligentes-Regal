import time
import os
import traceback

# Konfiguration für das Logging
LOG_TO_CONSOLE = True  # Setze auf False, um Konsolenausgabe zu deaktivieren
LOG_TO_FILE = True     # Setze auf False, um Dateiausgabe zu deaktivieren
LOG_FILE = "debug.log" # Dateiname für das Log
LOG_MAX_SIZE = 10 * 1024 * 1024  # 10 MB maximale Loggröße
LOG_LEVEL = "DEBUG"   # Mögliche Werte: "DEBUG", "INFO", "WARNING", "ERROR"

def log_debug(message, level="DEBUG"):
    """
    Loggt eine Nachricht mit Zeitstempel.
    
    Args:
        message: Die zu loggende Nachricht
        level: Log-Level (DEBUG, INFO, WARNING, ERROR)
    """
    # Prüfe, ob das gegebene Level geloggt werden soll
    log_levels = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
    if log_levels.get(level, 0) < log_levels.get(LOG_LEVEL, 0):
        return
    
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] [{level}] {message}"
    
    # Ausgabe in die Konsole
    if LOG_TO_CONSOLE:
        print(log_message)
    
    # Ausgabe in Datei
    if LOG_TO_FILE:
        try:
            # Prüfen, ob die Datei zu groß ist
            if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_SIZE:
                # Sichere die alte Logdatei
                if os.path.exists(LOG_FILE + ".old"):
                    os.remove(LOG_FILE + ".old")
                os.rename(LOG_FILE, LOG_FILE + ".old")
            
            # Anhängen an die Logdatei
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(log_message + "\n")
        except Exception as e:
            if LOG_TO_CONSOLE:
                print(f"Fehler beim Schreiben ins Logfile: {e}")

def log_exception(e, message="Eine Ausnahme ist aufgetreten"):
    """
    Loggt eine Exception mit Stacktrace.
    
    Args:
        e: Die Exception
        message: Eine optionale Nachricht
    """
    log_debug(f"{message}: {str(e)}", "ERROR")
    log_debug(f"Stacktrace: {traceback.format_exc()}", "ERROR")

def set_log_level(level):
    """
    Setzt das Log-Level.
    
    Args:
        level: Das neue Log-Level ("DEBUG", "INFO", "WARNING", "ERROR")
    """
    global LOG_LEVEL
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
    if level in valid_levels:
        LOG_LEVEL = level
        log_debug(f"Log-Level auf {level} gesetzt.")
    else:
        log_debug(f"Ungültiges Log-Level: {level}. Gültige Werte sind: {', '.join(valid_levels)}", "WARNING")