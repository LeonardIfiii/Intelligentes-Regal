#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
GPU-Diagnose-Tool
-----------------
Dieses Skript testet systematisch die GPU-Erkennung und -Nutzbarkeit.
Führe es aus, um Probleme mit der CUDA/GPU-Nutzung in deiner Python-Umgebung zu identifizieren.
"""

import sys
import os
import time
import platform

print("="*80)
print("GPU-DIAGNOSE-TOOL")
print("="*80)
print(f"Python Version: {sys.version}")
print(f"System: {platform.system()} {platform.release()} {platform.machine()}")
print("="*80)

# Prüfe vorhandene Umgebungsvariablen
print("\n1. UMGEBUNGSVARIABLEN:")
print("-"*50)
cuda_vars = [var for var in os.environ if "CUDA" in var]
if cuda_vars:
    for var in cuda_vars:
        print(f"  {var} = {os.environ[var]}")
else:
    print("  Keine CUDA-bezogenen Umgebungsvariablen gefunden")

# Setze explizit CUDA_VISIBLE_DEVICES
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
print("  CUDA_VISIBLE_DEVICES auf '0' gesetzt")

# Teste PyTorch und CUDA-Erkennung
print("\n2. PYTORCH UND CUDA:")
print("-"*50)
try:
    import torch
    print(f"  PyTorch Version: {torch.__version__}")
    
    cuda_available = torch.cuda.is_available()
    print(f"  CUDA verfügbar: {cuda_available}")
    
    if cuda_available:
        print(f"  CUDA Version: {torch.version.cuda}")
        device_count = torch.cuda.device_count()
        print(f"  GPU-Anzahl: {device_count}")
        
        for i in range(device_count):
            print(f"  GPU {i}: {torch.cuda.get_device_name(i)}")
            
        # Aktuelles Standard-Gerät
        current_device = torch.cuda.current_device()
        print(f"  Aktuelles CUDA-Gerät: {current_device} ({torch.cuda.get_device_name(current_device)})")
        
        # Speicherinfos
        print(f"  Verfügbarer Speicher auf GPU 0: {torch.cuda.get_device_properties(0).total_memory / 1e9:.2f} GB")
    else:
        print("  ⚠️ CUDA ist nicht verfügbar!")
        print("  Prüfe deine PyTorch-Installation und GPU-Treiber.")
        
except ImportError:
    print("  ❌ PyTorch nicht installiert. Installiere es mit:")
    print("  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128")

# Teste Tensor-Operationen auf der GPU
print("\n3. EINFACHER TENSOR-TEST:")
print("-"*50)
try:
    if 'torch' in sys.modules and cuda_available:
        # Erstelle einen Tensor und verschiebe ihn auf die GPU
        print("  Erstelle Tensor auf CPU...")
        x_cpu = torch.randn(1000, 1000)
        print(f"  Tensor-Gerät: {x_cpu.device}")
        
        # Verschiebe auf GPU
        print("  Verschiebe Tensor auf GPU...")
        start_time = time.time()
        x_gpu = x_cpu.cuda()
        print(f"  Tensor-Gerät nach Verschiebung: {x_gpu.device}")
        
        # Einfache Operation auf GPU
        print("  Führe Matrixmultiplikation auf GPU aus...")
        y_gpu = torch.matmul(x_gpu, x_gpu)
        torch.cuda.synchronize()  # Warte auf GPU-Ausführung
        gpu_time = time.time() - start_time
        print(f"  GPU-Berechnung abgeschlossen in {gpu_time:.4f} Sekunden")
        
        # Teste die gleiche Operation auf CPU zum Vergleich
        print("  Führe gleiche Operation auf CPU aus...")
        start_time = time.time()
        y_cpu = torch.matmul(x_cpu, x_cpu)
        cpu_time = time.time() - start_time
        print(f"  CPU-Berechnung abgeschlossen in {cpu_time:.4f} Sekunden")
        
        # Vergleiche
        if gpu_time < cpu_time:
            speedup = cpu_time / gpu_time
            print(f"  ✅ GPU ist {speedup:.2f}x schneller als CPU - GPU funktioniert korrekt!")
        else:
            print(f"  ⚠️ GPU ist nicht schneller als CPU - möglicherweise ein Problem mit der GPU-Nutzung")
except Exception as e:
    print(f"  ❌ Fehler beim Tensor-Test: {e}")

# Teste YOLO-Modell
print("\n4. YOLO-MODELL-TEST:")
print("-"*50)
try:
    from ultralytics import YOLO
    print("  Ultralytics YOLO installiert")
    
    try:
        print("  Lade YOLO-Modell...")
        model = YOLO('yolov8n.pt')  # Kleineres Modell für schnelleren Test
        
        # Prüfe, auf welchem Gerät das Modell läuft
        device_info = next(model.model.parameters()).device
        print(f"  Modell-Gerät: {device_info}")
        
        if 'cuda' in str(device_info):
            print("  ✅ YOLO-Modell läuft auf GPU!")
        else:
            print("  ⚠️ YOLO-Modell läuft auf CPU!")
            print("  Versuche explizite GPU-Zuweisung...")
            
            # Explizit auf GPU verschieben
            model.to('cuda:0')
            device_info = next(model.model.parameters()).device
            print(f"  Modell-Gerät nach expliziter Zuweisung: {device_info}")
            
            if 'cuda' in str(device_info):
                print("  ✅ Explizite GPU-Zuweisung erfolgreich!")
            else:
                print("  ❌ Explizite GPU-Zuweisung fehlgeschlagen!")
        
        # Optional: Führe eine Inferenz durch
        print("  Führe Inferenz durch...")
        import numpy as np
        # Erstelle ein zufälliges Bild für den Test
        dummy_img = np.random.randint(0, 255, (640, 640, 3), dtype=np.uint8)
        
        start_time = time.time()
        results = model(dummy_img, device=0)  # Explizit GPU verwenden
        inference_time = time.time() - start_time
        print(f"  Inferenz abgeschlossen in {inference_time:.4f} Sekunden")
        
    except Exception as e:
        print(f"  ❌ Fehler beim YOLO-Test: {e}")
        
except ImportError:
    print("  Ultralytics YOLO nicht installiert. Installiere es mit:")
    print("  pip install ultralytics")

# Zusammenfassung
print("\n5. DIAGNOSE-ZUSAMMENFASSUNG:")
print("-"*50)

if 'torch' in sys.modules:
    if cuda_available:
        print("  ✅ CUDA ist verfügbar")
        
        if 'x_gpu' in locals() and 'cuda' in str(x_gpu.device):
            print("  ✅ Tensor-Operationen auf GPU erfolgreich")
        else:
            print("  ❌ Tensor-Operationen auf GPU fehlgeschlagen")
            
        if 'model' in locals() and 'cuda' in str(next(model.model.parameters()).device):
            print("  ✅ YOLO-Modell auf GPU erfolgreich")
        else:
            print("  ❌ YOLO-Modell auf GPU fehlgeschlagen oder nicht getestet")
            
        print("\nEMPFEHLUNGEN:")
        if not ('x_gpu' in locals() and 'cuda' in str(x_gpu.device)) or \
           not ('model' in locals() and 'cuda' in str(next(model.model.parameters()).device)):
            print("  1. Füge diese Zeilen am Anfang deines YOLO-Scripts ein:")
            print("     import torch")
            print("     torch.cuda.set_device(0)")
            print("     device = torch.device('cuda:0')")
            print("  2. Ändere die Modell-Initialisierung:")
            print("     model = YOLO('yolov8s.pt')")
            print("     model.to(device)")
            print("  3. Erzwinge GPU-Nutzung bei Inferenz:")
            print("     results = model(frame, device=0)")
    else:
        print("  ❌ CUDA ist nicht verfügbar")
        print("\nPROBLEMLÖSUNG:")
        print("  1. Installiere PyTorch mit CUDA-Unterstützung:")
        print("     pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128")
        print("  2. Stelle sicher, dass deine GPU-Treiber aktuell sind")
        print("  3. Prüfe, ob deine GPU CUDA-kompatibel ist")
else:
    print("  ❌ PyTorch nicht installiert")
    print("\nPROBLEMLÖSUNG:")
    print("  1. Installiere PyTorch mit CUDA-Unterstützung:")
    print("     pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128")

print("\n" + "="*80)
print("GPU-DIAGNOSE ABGESCHLOSSEN")
print("="*80)