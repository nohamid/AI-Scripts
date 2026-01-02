from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import os
from datetime import datetime
from functools import wraps
from ftp_backup_module import backup_device_config
from config_generator_module import generate_config

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = 'your-secret-key-change-this'

# Authentication credentials
VALID_USERNAME = 'cisco'
VALID_PASSWORD = 'cisco'

# Available scripts in the toolbox
AVAILABLE_SCRIPTS = {
    'config_generator': {
        'name': 'Config Generator',
        'description': 'Automatically generate device configuration with IP assignment',
        'icon': '⚙️'
    },
    'config_backup': {
        'name': 'Configuration Backup',
        'description': 'Backup device configurations',
        'icon': '💾'
    },
    'device_inventory': {
        'name': 'Device Inventory',
        'description': 'Retrieve and display device inventory information',
        'icon': '📦'
    },
    'performance_report': {
        'name': 'Performance Report',
        'description': 'Generate performance analytics and reports',
        'icon': '📊'
    },
    'security_audit': {
        'name': 'Security Audit',
        'description': 'Run security checks and vulnerability scans',
        'icon': '🔒'
    },
    'log_analysis': {
        'name': 'Log Analysis',
        'description': 'Analyze system and application logs',
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
            
            # Run the config generator
            result = generate_config(interface, ip_with_prefix)
            
            # Format result for frontend
            if result['status'] == 'success':
                return jsonify({
                    'status': 'success',
                    'script': script_name,
                    'message': result['message'],
                    'interface': result.get('interface'),
                    'ip': result.get('ip'),
                    'netmask': result.get('netmask'),
                    'gateway': result.get('gateway'),
                    'config': result.get('config'),
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
    app.run(debug=True, host='0.0.0.0', port=8080)
