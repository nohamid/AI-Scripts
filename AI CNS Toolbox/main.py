from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
import os
import sys
import subprocess
import csv
import io
from datetime import datetime
from functools import wraps
from pathlib import Path
import logging
from ftp_backup_module import backup_device_config
logging.basicConfig(level=logging.INFO)

# Add the Self Written Scripts folder to path
config_gen_script_path = Path(__file__).parent / 'Self Written Scripts' / 'Config Generator' / 'main.py'
cns_healthcheck_script_path = Path(__file__).parent / 'Self Written Scripts' / 'CNS_Healthcheck'

# Add CNS Health Check to path for imports
sys.path.insert(0, str(cns_healthcheck_script_path))

# Import the healthcheck function
from healtcheck import run_cns_healthcheck

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = 'your-secret-key-change-this'

# Authentication credentials
VALID_USERNAME = 'cisco'
VALID_PASSWORD = 'cisco'

# Available scripts in the toolbox
AVAILABLE_SCRIPTS = {
    'config_generator': {
        'name': 'Config Generator',
        'description': 'Automatically generate device configuration for management access',
        'icon': '⚙️'
    },
    'config_backup': {
        'name': 'Configuration Backup',
        'description': 'Backup device configuration to Everlast and copy to clipboard',
        'icon': '🥊'
    },
    'cns_healthcheck': {
        'name': 'CNS Health Check',
        'description': 'Run health check on a range of Cisco CNS Lab devices',
        'icon': '🏥'
    },
    'device_inventory': {
        'name': 'Device Inventory',
        'description': 'This script has no function yet, we are working on it',
        'icon': '📦'
    },
    'performance_report': {
        'name': 'Performance Report',
        'description': 'This script has no function yet, we are working on it',
        'icon': '📊'
    },
    'security_audit': {
        'name': 'Security Audit',
        'description': 'This script has no function yet, we are working on it',
        'icon': '🔒'
    },
    'log_analysis': {
        'name': 'Log Analysis',
        'description': 'This script has no function yet, we are working on it',
        'icon': '📝'
    }
}

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username == VALID_USERNAME and password == VALID_PASSWORD:
            session['user'] = username
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Invalid username or password')
    
    return render_template('login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', 
                         scripts=AVAILABLE_SCRIPTS,
                         username=session.get('user'),
                         current_time=datetime.now().strftime('%B %d, %Y'))

@app.route('/pdu-management')
@login_required
def pdu_management():
    return render_template('pdu_management.html')

@app.route('/run-script/<script_id>', methods=['POST'])
@login_required
def run_script(script_id):
    if script_id not in AVAILABLE_SCRIPTS:
        return jsonify({'error': 'Script not found'}), 404
    
    script_name = AVAILABLE_SCRIPTS[script_id]['name']
    
    # Handle Config Generator
    if script_id == 'config_generator':
        try:
            data = request.get_json() or {}
            interface = data.get('interface')
            ip_with_prefix = data.get('ip_with_prefix')
            
            if not interface or not ip_with_prefix:
                return jsonify({
                    'status': 'error',
                    'message': 'Interface and IP address (CIDR notation) are required',
                    'timestamp': datetime.now().isoformat()
                }), 400
            
            # Run the Config Generator script via subprocess to load the actual module
            script_dir = Path(__file__).parent / 'Self Written Scripts' / 'Config Generator'
            
            # Create a wrapper that runs the actual config generator module
            runner_code = f"""
import sys
sys.path.insert(0, r'{str(script_dir)}')

from unittest.mock import patch

interface = r'{interface}'
ip_with_prefix = r'{ip_with_prefix}'

# Simulate user inputs by mocking input()
inputs = [interface, ip_with_prefix]
with patch('builtins.input', side_effect=inputs):
    try:
        # Execute the actual config generator script with mocked inputs
        exec(open(r'{str(script_dir)}/main.py').read())
    except SystemExit:
        pass  # Ignore sys.exit() calls from the script
"""
            
            # Execute the runner code
            result = subprocess.run(
                [sys.executable, '-c', runner_code],
                capture_output=True,
                text=True,
                cwd=str(script_dir)
            )
            
            if result.returncode != 0:
                error_msg = result.stderr if result.stderr else result.stdout if result.stdout else "Unknown error occurred"
                return jsonify({
                    'status': 'error',
                    'script': script_name,
                    'message': error_msg,
                    'stdout': result.stdout,
                    'stderr': result.stderr,
                    'timestamp': datetime.now().isoformat()
                }), 400
            
            config_output = result.stdout.strip()
            
            return jsonify({
                'status': 'success',
                'script': script_name,
                'message': 'Configuration generated successfully',
                'interface': interface,
                'ip': ip_with_prefix,
                'config': config_output,
                'timestamp': datetime.now().isoformat()
            })
        
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': f'Config generation failed: {str(e)}',
                'timestamp': datetime.now().isoformat()
            }), 500
    
    # Handle Configuration Backup with FTP integration
    if script_id == 'config_backup':
        try:
            # Get device IP from request
            data = request.get_json() or {}
            device_ip = data.get('device_ip')
            
            if not device_ip:
                return jsonify({
                    'status': 'error',
                    'message': 'Device IP address is required',
                    'timestamp': datetime.now().isoformat()
                }), 400
            
            # Run the FTP backup
            result = backup_device_config(device_ip)
            
            # Format result for frontend
            if result['status'] == 'success':
                return jsonify({
                    'status': 'success',
                    'script': script_name,
                    'message': result['message'],
                    'hostname': result.get('hostname'),
                    'filename': result.get('filename'),
                    'timestamp': datetime.now().isoformat()
                })
            else:
                return jsonify({
                    'status': 'error',
                    'script': script_name,
                    'message': result['message'],
                    'timestamp': datetime.now().isoformat()
                }), 400
        
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': f'Backup failed: {str(e)}',
                'timestamp': datetime.now().isoformat()
            }), 500
    
    # Handle CNS Health Check
    if script_id == 'cns_healthcheck':
        try:            
            # Get IP range from request
            data = request.get_json() or {}
            ip_range = data.get('ip_range')
            
            if not ip_range:
                return jsonify({
                    'status': 'error',
                    'message': 'IP range is required (format: 10.10.10.1 - 10.10.10.254)',
                    'timestamp': datetime.now().isoformat()
                }), 400
            
            # Run the health check
            result = run_cns_healthcheck(ip_range)
            
            if result['status'] == 'success':
                return jsonify({
                    'status': 'success',
                    'script': script_name,
                    'message': result['message'],
                    'data': result['data'],
                    'total_devices': result['total_devices'],
                    'success_count': result['success_count'],
                    'error_count': result['error_count'],
                    'timestamp': datetime.now().isoformat()
                })
            else:
                return jsonify({
                    'status': 'error',
                    'script': script_name,
                    'message': result['message'],
                    'timestamp': datetime.now().isoformat()
                }), 400
        
        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': f'Health check failed: {str(e)}',
                'timestamp': datetime.now().isoformat()
            }), 500
    
    # Default placeholder for other scripts
    result = {
        'status': 'success',
        'script': script_name,
        'message': f'{script_name} executed successfully!',
        'timestamp': datetime.now().isoformat()
    }
    
    return jsonify(result)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/download-health-check-csv', methods=['POST'])
@login_required
def download_health_check_csv():
    """Generate and download CNS Health Check results as CSV"""
    try:
        data = request.get_json() or {}
        devices = data.get('devices', [])
        
        if not devices:
            return jsonify({
                'status': 'error',
                'message': 'No device data provided'
            }), 400
        
        # Create CSV in memory
        output = io.StringIO()
        
        # Define CSV columns
        fieldnames = ['IP Address', 'Hostname', 'Platform', 'Version', 'Serial Number', 
                      'Management Interface', 'Management IP', 'Uptime', 'Status']
        
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        
        # Write device data
        for device in devices:
            writer.writerow({
                'IP Address': device.get('ip', 'N/A'),
                'Hostname': device.get('hostname', 'N/A'),
                'Platform': device.get('platform', 'N/A'),
                'Version': device.get('version', 'N/A'),
                'Serial Number': device.get('serial', 'N/A'),
                'Management Interface': device.get('mgmt_interface', 'N/A'),
                'Management IP': device.get('mgmt_ip', 'N/A'),
                'Uptime': device.get('uptime', 'N/A'),
                'Status': device.get('status', 'N/A')
            })
        
        # Create BytesIO object from string
        output.seek(0)
        csv_bytes = io.BytesIO(output.getvalue().encode('utf-8'))
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'CNS_HealthCheck_{timestamp}.csv'
        
        return send_file(
            csv_bytes,
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
    
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Failed to generate CSV: {str(e)}'
        }), 500

@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('500.html'), 500

if __name__ == '__main__':
    # Ensure templates directory exists
    os.makedirs('templates', exist_ok=True)
    
    # Run the Flask app
    # Set debug=False in production
    app.run(debug=False, host='0.0.0.0', port=8080)
