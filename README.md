Intelligent Shelf System
This repository contains a complete intelligent shelf solution using computer vision to track products in retail environments. The system monitors shelf inventory in real-time, detects when items are removed or returned, and provides multiple interfaces for customers, sales staff, and warehouse management.

Features
Real-time object detection and tracking using YOLO and SORT algorithms
Multi-interface system with dedicated displays for:
Customers (product information when items are picked up)
Cashiers (point-of-sale system)
Warehouse staff (inventory and restocking management)
Managers (analytics dashboard)
Event tracking for item removals, returns, misplacements, and payments
Inventory management with automatic updates based on visual detection
Support for multiple product types with configurable thresholds and limits
System Architecture
The system consists of several interconnected components:

Core Detection System (yolo_monitor.py): Processes camera feed to detect and track objects on shelves
Database Utilities (db_utils.py): Manages product inventory and events
Web Interfaces:
Customer Display (customer_dispaly.py): Shows product information to customers
Warehouse Dashboard (warehouse_dashboard.py): Manages inventory and restocking
Analysis Dashboard (web_analysis_dashboard.py): Provides data visualization and analysis
Cash Register System (kassensystem.py): Processes payments
Setup Tools:
ROI Creator (regal_setup.py): Configures shelf detection regions
Auxiliary Components:
Analysis Screen (analysis_screen.py): Desktop GUI for data analysis
SORT Implementation (sort.py): Enhanced tracking algorithm
Debug Utilities (debug_utils.py): Logging and debugging tools
Requirements
Python 3.8+
OpenCV with GPU support (recommended)
CUDA-compatible GPU (recommended for YOLO performance)
Webcam or IP camera
Libraries: flask, numpy, opencv-python, ultralytics, torch, scipy, filterpy
Installation
Clone this repository:
git clone https://github.com/yourusername/intelligent-shelf-system.git
cd intelligent-shelf-system
Install required packages:
pip install -r requirements.txt
Download YOLOv8 small model:
wget https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8s.pt
Setup and Configuration
1. Define Shelf Regions
First, you need to configure the regions of interest (ROIs) for each shelf:

python regal_setup.py
Instructions:

Use the mouse to draw rectangles around each shelf area in the camera view
Press 'f' to toggle fullscreen mode
Press 's' to save the configuration
Press 'r' to reset the configuration
Press 'q' to quit
The tool will save your configuration to regal_config.json.

2. Configure Product Limits
When you run the main system for the first time, you'll be prompted to specify product limits:

python yolo_monitor.py
You can set the maximum number of each product type expected on the shelves. These limits help the system maintain accurate inventory tracking.

Usage
Starting the Complete System
For convenience, you can use the provided script to start all components:

python start_all.py
Or start individual components as needed:

Core Detection System
python yolo_monitor.py
This launches the main YOLO detection and tracking system. It will:

Detect objects on shelves using the camera
Track object movements between shelves
Generate events for removals and returns
Update the inventory database in real-time
Web Interfaces
Start each interface on a different port:

Customer Display
python customer_dispaly.py
Default: http://localhost:5001
Shows product information when customers pick up items
Automatically detects product removals from shelves
Warehouse Dashboard
python warehouse_dashboard.py
Default: http://localhost:5002
Shows inventory levels and restocking tasks
Allows warehouse staff to mark items as collected and refilled
Analysis Dashboard
python web_analysis_dashboard.py
Default: http://localhost:5000
Provides detailed inventory analysis and event tracking
Allows filtering by product type and event status
Cash Register System
python kassensystem.py
Default: http://localhost:5003
Shows unbilled items that have been removed from shelves
Allows cashiers to mark items as paid
Desktop Analysis Tool
python analysis_screen.py
This launches a Tkinter-based desktop application for real-time analysis and monitoring of:

Current inventory levels
Event history
Product-specific analytics
Key Files and Their Functions
yolo_monitor.py: Main detection and tracking system
db_utils.py: Database utilities for inventory and event management
warehouse_dashboard.py: Web interface for warehouse management
web_analysis_dashboard.py: Web interface for data analysis
customer_dispaly.py: Web interface for customer product information
kassensystem.py: Web interface for cashier/point-of-sale system
analysis_screen.py: Desktop GUI for analysis
regal_setup.py: Tool for configuring shelf regions
roi_creator.py: Alternative tool for ROI creation
sort.py: Enhanced SORT algorithm implementation
debug_utils.py: Debugging and logging utilities
Working Principles
Shelf Setup: Define shelf regions using the ROI creator
Inventory Initialization: Initial inventory is detected and saved
Real-time Monitoring: YOLO continuously monitors the shelves
Event Generation: When objects are removed or returned, events are recorded
Interfaces Update: All interfaces update to reflect the current state
Payment Processing: Cashier system processes payments for removed items
Troubleshooting
Camera Issues
If the default camera isn't detected, try changing the camera index in the code:
python
cap = cv2.VideoCapture(0)  # Try 1 or 2 for external cameras
YOLO Detection Issues
Adjust confidence_threshold in yolo_monitor.py (default is 0.35)
Ensure good lighting conditions for better detection
If using a CPU, expect slower performance
Port Conflicts
If web interfaces fail to start due to port conflicts, change the port numbers in the respective files
Database Reset
To reset the database and start fresh:

From the analysis dashboard, use the "Reset DB" button
Or delete the supermarkt.db file and restart the system
Customization
Adding New Product Types
Update ALLOWED_CLASSES in yolo_monitor.py
Update product_names and product_prices in relevant files
Changing Default Settings
Modify OBJECT_LIMITS in yolo_monitor.py to change default product limits
Adjust tracking parameters in yolo_monitor.py for different environments
Acknowledgements
This system uses several open-source projects:

Ultralytics YOLOv8
SORT (Simple Online and Realtime Tracking)
Flask
OpenCV
