# sort.py

import numpy as np
import cv2
from filterpy.kalman import KalmanFilter
from scipy.optimize import linear_sum_assignment

def compute_color_histogram(image, bbox):
    """
    Extrahiert ein Farb-Histogramm (HSV) aus der Region, die durch bbox definiert wird.
    bbox: [x1, y1, x2, y2]
    """
    x1, y1, x2, y2 = bbox
    roi = image[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv_roi], [0, 1, 2], None, [8,8,8], [0,180,0,256,0,256])
    cv2.normalize(hist, hist)
    return hist.flatten()

class KalmanBoxTracker:
    count = 0

    def __init__(self, bbox, detection_color=None):
        """
        bbox: [x1, y1, x2, y2]
        detection_color: Farb-Histogramm (als 1D numpy-Array) der Detektion
        """
        self.kf = KalmanFilter(dim_x=7, dim_z=4)
        self.kf.F = np.array([
            [1,0,0,0,1,0,0],
            [0,1,0,0,0,1,0],
            [0,0,1,0,0,0,1],
            [0,0,0,1,0,0,0],
            [0,0,0,0,1,0,0],
            [0,0,0,0,0,1,0],
            [0,0,0,0,0,0,1]
        ])
        self.kf.H = np.array([
            [1,0,0,0,0,0,0],
            [0,1,0,0,0,0,0],
            [0,0,1,0,0,0,0],
            [0,0,0,1,0,0,0]
        ])
        self.kf.R *= 10.
        self.kf.P *= 1000.
        self.kf.Q[-1,-1] *= 0.01
        self.kf.Q[4:,4:] *= 0.01
        self.kf.x[:4] = self.convert_bbox_to_z(bbox)
        self.time_since_update = 0
        self.id = KalmanBoxTracker.count
        KalmanBoxTracker.count += 1
        self.history = []
        self.hits = 0
        self.hit_streak = 0
        self.color_hist = detection_color

    def update(self, bbox, detection_color=None):
        """ Aktualisiert den Tracker mit der neuen Bounding Box und aktualisiert das Farb-Histogramm. """
        self.time_since_update = 0
        self.history = []
        self.hits += 1
        self.hit_streak += 1
        self.kf.update(self.convert_bbox_to_z(bbox))
        if detection_color is not None:
            if self.color_hist is None:
                self.color_hist = detection_color
            else:
                self.color_hist = 0.5 * self.color_hist + 0.5 * detection_color

    def predict(self):
        """ Führt eine Vorhersage für das nächste Frame durch. """
        if (self.kf.x[6] + self.kf.x[2]) <= 0:
            self.kf.x[6] *= 0.0
        self.kf.predict()
        self.time_since_update += 1
        self.history.append(self.convert_x_to_bbox(self.kf.x))
        return self.history[-1]

    def convert_bbox_to_z(self, bbox):
        """ Konvertiere Bounding Box [x1, y1, x2, y2] in (cx, cy, s, r) """
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        x = bbox[0] + w / 2.0
        y = bbox[1] + h / 2.0
        s = w * h
        r = w / float(h) if h != 0 else 0
        return np.array([x, y, s, r]).reshape((4, 1))

    def convert_x_to_bbox(self, x):
        """ Konvertiere Kalman-Filter-Zustand in Bounding Box [x1, y1, x2, y2] """
        w = np.sqrt(x[2] * x[3]) if x[3] > 0 else 0
        h = x[2] / w if w != 0 else 0
        return np.array([x[0] - w / 2.0,
                         x[1] - h / 2.0,
                         x[0] + w / 2.0,
                         x[1] + h / 2.0])

class Sort:
    def __init__(self, max_age=10, min_hits=3, alpha=0.5, beta=0.5, assignment_threshold=0.7):
        """
        max_age: Maximale Frames ohne Update, bevor ein Tracker gelöscht wird
        min_hits: Mindestanzahl von Updates, bevor ein Tracker als valide gilt
        alpha: Gewichtung für den IoU-Anteil im Kostenmodell
        beta: Gewichtung für den Farbanteil im Kostenmodell
        assignment_threshold: Maximal akzeptierte Kosten für eine Zuordnung
        """
        self.max_age = max_age
        self.min_hits = min_hits
        self.trackers = []
        self.frame_count = 0
        self.alpha = alpha
        self.beta = beta
        self.assignment_threshold = assignment_threshold

    def update(self, detections, detection_colors=None):
        """
        Aktualisiert alle Tracker mit neuen Detektionen.
        detections: Array [[x1, y1, x2, y2, score], ...]
        detection_colors: Liste von Farb-Histogrammen für jede Detektion (muss in gleicher Reihenfolge sein)
        """
        self.frame_count += 1
        updated_tracks = []
        for tracker in self.trackers:
            prediction = tracker.predict()
            if np.any(np.isnan(prediction)):
                continue
            updated_tracks.append(prediction)
        
        num_tracks = len(updated_tracks)
        num_detections = len(detections)
        cost_matrix = np.zeros((num_tracks, num_detections), dtype=np.float32)
        
        if num_tracks > 0 and num_detections > 0:
            for t, tracker in enumerate(self.trackers):
                for d in range(num_detections):
                    iou_val = self.iou(updated_tracks[t], detections[d][:4])
                    if detection_colors is not None and tracker.color_hist is not None and detection_colors[d] is not None:
                        correlation = cv2.compareHist(tracker.color_hist.astype('float32'),
                                                      detection_colors[d].astype('float32'),
                                                      cv2.HISTCMP_CORREL)
                        correlation = max(0, correlation)
                    else:
                        correlation = 0.0
                    cost_matrix[t, d] = self.alpha * (1 - iou_val) + self.beta * (1 - correlation)
            
            row_ind, col_ind = linear_sum_assignment(cost_matrix)
            matched_detections = set()
            for r, c in zip(row_ind, col_ind):
                if cost_matrix[r, c] < self.assignment_threshold:
                    self.trackers[r].update(detections[c][:4],
                                            detection_color=detection_colors[c] if detection_colors is not None else None)
                    matched_detections.add(c)
            for d in range(num_detections):
                if d not in matched_detections:
                    new_tracker = KalmanBoxTracker(detections[d][:4],
                                                   detection_color=detection_colors[d] if detection_colors is not None else None)
                    self.trackers.append(new_tracker)
        else:
            for d in range(num_detections):
                new_tracker = KalmanBoxTracker(detections[d][:4],
                                               detection_color=detection_colors[d] if detection_colors is not None else None)
                self.trackers.append(new_tracker)
        
        self.trackers = [t for t in self.trackers if t.time_since_update <= self.max_age]
        
        results = []
        for tracker in self.trackers:
            bbox = tracker.history[-1] if len(tracker.history) > 0 else tracker.predict()
            results.append(np.concatenate((bbox.flatten(), np.array([tracker.id]))))
        
        return np.array(results)

    def iou(self, bbox1, bbox2):
        """ Berechnet die IoU zweier Bounding Boxes """
        x1, y1, x2, y2 = bbox1
        x1g, y1g, x2g, y2g = bbox2
        inter_w = max(0, min(x2, x2g) - max(x1, x1g))
        inter_h = max(0, min(y2, y2g) - max(y1, y1g))
        inter_area = inter_w * inter_h
        bbox1_area = (x2 - x1) * (y2 - y1)
        bbox2_area = (x2g - x1g) * (y2g - y1g)
        return inter_area / float(bbox1_area + bbox2_area - inter_area + 1e-6)
