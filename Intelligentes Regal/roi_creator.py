import cv2
import numpy as np

# Initialize global variables
rois = []  # List to store the coordinates of ROIs
drawing = False  # Flag to indicate ongoing drawing
start_point = None  # Starting point of rectangle
current_rectangle = None  # Temporarily store current rectangle coordinates

def mouse_event(event, x, y, flags, param):
    global start_point, drawing, rois, current_rectangle

    if event == cv2.EVENT_LBUTTONDOWN:
        if not drawing:
            drawing = True
            start_point = (x, y)

    elif event == cv2.EVENT_MOUSEMOVE:
        if drawing:
            current_rectangle = (start_point[0], start_point[1], x, y)

    elif event == cv2.EVENT_LBUTTONUP:
        drawing = False
        end_point = (x, y)
        # Ensure we have a valid rectangle
        if start_point != end_point:
            rois.append((start_point[0], start_point[1], abs(end_point[0] - start_point[0]), abs(end_point[1] - start_point[1])))
            current_rectangle = None

def main():
    global frame, current_rectangle
    cap = cv2.VideoCapture(1)

    # Fullscreen window setup
    cv2.namedWindow("Live Feed", cv2.WND_PROP_FULLSCREEN)
    cv2.setWindowProperty("Live Feed", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    cv2.setMouseCallback("Live Feed", mouse_event)

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Draw all completed ROIs
        for roi in rois:
            x, y, w, h = roi
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

        # Draw the current rectangle being created
        if current_rectangle:
            cv2.rectangle(frame, (current_rectangle[0], current_rectangle[1]), (current_rectangle[2], current_rectangle[3]), (0, 0, 255), 1)

        cv2.imshow("Live Feed", frame)

        # Handle quit with just 'q'
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    print("Defined ROIs:", rois)

if __name__ == "__main__":
    main()