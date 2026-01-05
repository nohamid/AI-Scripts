# CNS Health Check Integration

## Overview
The CNS Health Check script has been successfully integrated into the AI CNS Toolbox website as an executable script. The original script is used as-is, allowing you to make changes to it without modifying the website code.

## Changes Made

### 1. **Refactored CNS Health Check Script** (`Self Written Scripts/CNS-Healthcheck/main.py`)
   - Created a reusable function `run_cns_healthcheck(ip_range, username, password)` that:
     - Accepts IP range as a parameter (format: "10.10.10.1 - 10.10.10.254")
     - Returns results as a dictionary with status, message, device data, and statistics
     - Maintains all original functionality
   - Kept the interactive mode for standalone script execution
   - No hardcoding of parameters - all values can be modified in the original script

### 2. **Updated Flask Application** (`main.py`)
   - Added import path for CNS Health Check module at startup
   - Added `'cns_healthcheck'` to `AVAILABLE_SCRIPTS` dictionary with:
     - Name: "CNS Health Check"
     - Description: "Run health check on a range of Cisco network devices"
     - Icon: 🏥
   - Added request handler for `/run-script/cns_healthcheck` that:
     - Accepts IP range from web request
     - Calls the `run_cns_healthcheck()` function
     - Returns results in JSON format with device details and statistics

### 3. **Updated Web Interface** (`templates/dashboard.html`)
   - Added new modal dialog for CNS Health Check input form
   - Input field for IP range with placeholder example
   - Added special handling in `runScript()` function to show the modal
   - Added result display with:
     - Summary statistics (total devices, success count, error count)
     - Detailed table showing:
       - IP Address
       - Hostname
       - Platform
       - Version
       - Management IP
       - Connection Status (✓ or ✗)
   - Responsive table with scrolling for large result sets

### 4. **JavaScript Functions** (Added to dashboard.html)
   - `showCnsHealthcheckModal()` - Opens the input modal
   - `closeCnsHealthcheckModal()` - Closes the modal and clears input
   - `submitCnsHealthcheck()` - Submits the form and calls Flask API
   - Event listeners for modal interaction and Enter key submission

## How It Works

### User Workflow:
1. User clicks "Run Script" on the CNS Health Check card
2. Modal dialog appears asking for IP range
3. User enters IP range (e.g., "10.10.10.1 - 10.10.10.254")
4. User clicks "Start Health Check"
5. Website sends request to Flask backend
6. Flask backend:
   - Imports the CNS Health Check module from `Self Written Scripts/CNS-Healthcheck/main.py`
   - Calls the `run_cns_healthcheck()` function
   - Returns results to the frontend
7. Results displayed in a formatted table with statistics

### Updating the Script:
Since the original script is used directly (not hardcoded), you can modify it anytime:
- Edit `/Self Written Scripts/CNS-Healthcheck/main.py`
- Changes will automatically be used by the website
- No need to update Flask code

## Key Features

✅ **Original Script Preserved** - The CNS Health Check script is used as-is
✅ **Easy to Modify** - Change the script without touching website code
✅ **Web Interface** - User-friendly modal for input
✅ **Rich Results Display** - Table view with all device details
✅ **Error Handling** - Shows which devices failed to connect
✅ **Statistics** - Displays total, success, and error counts
✅ **Responsive Design** - Works on all screen sizes

## File Structure

```
AI CNS Toolbox/
├── main.py (Flask app with CNS Health Check handler)
├── Self Written Scripts/
│   └── CNS-Healthcheck/
│       ├── main.py (Original script, refactored as module)
│       ├── test.py (Test script - unchanged)
│       └── output.csv (Results file)
└── templates/
    └── dashboard.html (Updated with CNS Health Check UI)
```

## API Endpoint

**POST** `/run-script/cns_healthcheck`

**Request Body:**
```json
{
  "ip_range": "10.10.10.1 - 10.10.10.254"
}
```

**Response (Success):**
```json
{
  "status": "success",
  "script": "CNS Health Check",
  "message": "Health check completed. X devices reachable, Y devices failed.",
  "total_devices": 254,
  "success_count": 250,
  "error_count": 4,
  "data": [
    {
      "ip": "10.10.10.1",
      "hostname": "Device-Name",
      "platform": "Cisco",
      "version": "15.2",
      "mgmt_ip": "10.49.x.x",
      "status": "success"
    }
    // ... more devices
  ],
  "timestamp": "2024-01-04T..."
}
```

## Testing

To test if the integration works:

```bash
# Verify imports work
python3 -c "from main import AVAILABLE_SCRIPTS; print('cns_healthcheck' in AVAILABLE_SCRIPTS)"

# Test the health check function directly
python3 << 'EOF'
from pathlib import Path
import sys
sys.path.insert(0, 'Self Written Scripts/CNS-Healthcheck')
from main import run_cns_healthcheck

# Test with invalid IP range
result = run_cns_healthcheck("10.10.10.1 - 10.10.10.5")
print(result)
EOF
```

## Future Enhancements

You can easily extend this by:
- Adding CSV export functionality
- Adding device filtering/sorting
- Storing results in database
- Scheduling periodic health checks
- Adding email notifications for failures
- Custom username/password input (currently hardcoded in script)
