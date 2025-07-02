from ultralytics import YOLO

# YOLOv8s Modell herunterladen
model = YOLO("yolov8s")

# Speichern des Modells als .pt Datei
model.export(format="torchscript")  # Speichert als .pt Datei
