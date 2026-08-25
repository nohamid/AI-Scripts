from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
import os
import sys
import subprocess
import csv
import io
from datetime import datetime
from functools import wraps
from pathlib import Path
from werkzeug.utils import secure_filename
import logging
from ftp_backup_module import backup_device_config
from scp_upload_module import upload_ios_image, DEFAULT_DESTINATIONS
import tempfile
import re
import json
import base64
import random
import threading
import time as _time
from uuid import uuid4
logging.basicConfig(level=logging.INFO)

# Add the Self Written Scripts folder to path
config_gen_script_path = Path(__file__).parent / 'Self Written Scripts' / 'Config Generator' / 'main.py'
cns_healthcheck_script_path = Path(__file__).parent / 'Self Written Scripts' / 'CNS_Healthcheck'
cdp_to_pptx_script_path = Path(__file__).parent / 'Self Written Scripts' / 'CDP_to_PPTX'
netbox_devicetypes_script_path = Path(__file__).parent / 'Self Written Scripts' / 'NetBox_DeviceTypes'

# Add CNS Health Check to path for imports
sys.path.insert(0, str(cns_healthcheck_script_path))
sys.path.insert(0, str(cdp_to_pptx_script_path))
sys.path.insert(0, str(netbox_devicetypes_script_path))

# Import the healthcheck function
from healtcheck import run_cns_healthcheck
from cdp_to_pptx import generate_pptx_from_cdp
import devicetype_sync
from devicetype_sync import NetBox as NetBoxClient, SyncError as NetBoxSyncError
import device_onboard
from device_onboard import OnboardError

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = 'your-secret-key-change-this'

# Performance: Configure Flask for better performance
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000  # Cache static files for 1 year
app.config['JSON_SORT_KEYS'] = False  # Faster JSON serialization
# Cisco IOS images can be very large (multi-GB). Allow up to 4 GB uploads.
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024 * 1024

# In-memory store of in-flight IOS upload progress keyed by upload_id.
# Each entry: {phase, percent, total_sent, total_size, current_file, rate_bps, updated}
IOS_UPLOAD_PROGRESS: dict = {}
IOS_UPLOAD_PROGRESS_LOCK = threading.Lock()

def _set_ios_progress(upload_id: str, data: dict) -> None:
    if not upload_id:
        return
    with IOS_UPLOAD_PROGRESS_LOCK:
        existing = IOS_UPLOAD_PROGRESS.get(upload_id, {})
        existing.update(data)
        existing['updated'] = _time.time()
        IOS_UPLOAD_PROGRESS[upload_id] = existing

def _schedule_ios_progress_cleanup(upload_id: str, delay: float = 60.0) -> None:
    def _clean():
        with IOS_UPLOAD_PROGRESS_LOCK:
            IOS_UPLOAD_PROGRESS.pop(upload_id, None)
    threading.Timer(delay, _clean).start()

# Authentication credentials
VALID_USERNAME = 'admin'
VALID_PASSWORD = 'm1amivice19!'

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
    'cdp_to_pptx': {
        'name': 'CDP to PowerPoint',
        'description': 'Paste `show cdp neighbors detail` output and download a topology + matrix PowerPoint',
        'icon': '📝'
    },
    'ios_upload': {
        'name': 'IOS Image Upload',
        'description': 'Upload a Cisco IOS image from your local machine to a lab device via SCP',
        'icon': '💿'
    },
    'netbox_devicetypes': {
        'name': 'NetBox Device Types',
        'description': 'Compare NetBox with the devicetype-library and import new Cisco device types',
        'icon': '🗂️'
    },
    'netbox_onboard': {
        'name': 'Onboard Device to NetBox',
        'description': 'Read hostname, type and management IP from a device via SSH and create it in NetBox',
        'icon': '🔌'
    },
    'device_inventory': {
        'name': 'Device Inventory',
        'description': 'Does it makes sense to write a script to reset a Cisco device and prepare it for storage?',
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

# Login screen background wallpapers (rotated on every access)
WALLPAPER_DIR = Path(__file__).parent / 'static' / 'wallpapers'
WALLPAPER_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
VM_DIRECTORY_FILE = Path(__file__).parent / 'data' / 'vm_directory.json'
VM_DIRECTORY_LOCK = threading.Lock()
VM_ICON_DIR = Path(__file__).parent / 'static' / 'vm_icons'
VM_ICON_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}

def _load_vm_entries_unlocked():
    try:
        with VM_DIRECTORY_FILE.open('r', encoding='utf-8') as file:
            data = json.load(file)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        logging.exception('VM directory data file is not valid JSON')
        return []

    if not isinstance(data, list):
        return []
    return data

def load_vm_entries():
    with VM_DIRECTORY_LOCK:
        return _load_vm_entries_unlocked()

def _save_vm_entries_unlocked(vm_entries):
    VM_DIRECTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = VM_DIRECTORY_FILE.with_suffix('.json.tmp')
    with temp_file.open('w', encoding='utf-8') as file:
        json.dump(vm_entries, file, indent=2)
        file.write('\n')
    temp_file.replace(VM_DIRECTORY_FILE)

def get_vm_form_data(form):
    vm_data = {
        'name': (form.get('name') or '').strip(),
        'link': (form.get('link') or '').strip(),
        'note': (form.get('note') or '').strip()
    }
    errors = []

    if not vm_data['name']:
        errors.append('VM name is required.')
    if not vm_data['link']:
        errors.append('VM link is required.')
    if vm_data['link'].lower().startswith(('javascript:', 'data:', 'vbscript:')):
        errors.append('Please use a normal web link for the VM.')

    return vm_data, errors

def save_vm_icon(file_storage):
    """Save an uploaded VM icon and return its static-relative path, or None."""
    if not file_storage or not file_storage.filename:
        return None

    extension = Path(secure_filename(file_storage.filename)).suffix.lower()
    if extension not in VM_ICON_EXTENSIONS:
        return None

    VM_ICON_DIR.mkdir(parents=True, exist_ok=True)
    filename = f'{uuid4().hex}{extension}'
    file_storage.save(VM_ICON_DIR / filename)
    return 'vm_icons/' + filename

def delete_vm_icon(icon_path):
    """Remove a previously saved VM icon file, if it exists within VM_ICON_DIR."""
    if not icon_path:
        return
    icon_file = Path(__file__).parent / 'static' / icon_path
    try:
        if icon_file.is_file() and VM_ICON_DIR in icon_file.parents:
            icon_file.unlink()
    except OSError:
        logging.exception('Failed to delete VM icon file: %s', icon_path)

def get_random_wallpaper():
    """Return a static-relative path to a random login wallpaper, or None."""
    try:
        wallpapers = [
            f.name for f in WALLPAPER_DIR.iterdir()
            if f.is_file() and f.suffix.lower() in WALLPAPER_EXTENSIONS
        ]
    except FileNotFoundError:
        wallpapers = []
    if not wallpapers:
        return None
    return 'wallpapers/' + random.choice(wallpapers)

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
            return render_template('login.html', error='Invalid username or password',
                                   wallpaper=get_random_wallpaper())
    
    return render_template('login.html', wallpaper=get_random_wallpaper())

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

@app.route('/floorplan')
@login_required
def floorplan():
    return render_template('floorplan.html')

@app.route('/beta')
@login_required
def beta():
    return render_template('beta.html')

@app.route('/vm-directory', methods=['GET', 'POST'])
@login_required
def vm_directory():
    if request.method == 'POST':
        vm_data, errors = get_vm_form_data(request.form)

        if errors:
            return render_template('vm_directory.html',
                                   vm_entries=load_vm_entries(),
                                   username=session.get('user'),
                                   error=' '.join(errors),
                                   form_values=vm_data)

        with VM_DIRECTORY_LOCK:
            vm_entries = _load_vm_entries_unlocked()
            vm_entries.append({
                'id': uuid4().hex,
                'name': vm_data['name'],
                'link': vm_data['link'],
                'note': vm_data['note'],
                'icon': save_vm_icon(request.files.get('icon')),
                'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'updated_by': session.get('user', 'team')
            })
            _save_vm_entries_unlocked(vm_entries)

        return redirect(url_for('vm_directory'))

    return render_template('vm_directory.html',
                           vm_entries=load_vm_entries(),
                           username=session.get('user'),
                           form_values={})

@app.route('/vm-directory/<vm_id>/update', methods=['POST'])
@login_required
def update_vm_entry(vm_id):
    vm_data, errors = get_vm_form_data(request.form)

    if errors:
        return render_template('vm_directory.html',
                               vm_entries=load_vm_entries(),
                               username=session.get('user'),
                               error=' '.join(errors),
                               form_values=vm_data,
                               edit_vm_id=vm_id)

    with VM_DIRECTORY_LOCK:
        vm_entries = _load_vm_entries_unlocked()
        for vm_entry in vm_entries:
            if vm_entry.get('id') == vm_id:
                new_icon = save_vm_icon(request.files.get('icon'))
                if request.form.get('remove_icon') == '1' and not new_icon:
                    delete_vm_icon(vm_entry.get('icon'))
                    new_icon = None
                elif new_icon:
                    delete_vm_icon(vm_entry.get('icon'))
                else:
                    new_icon = vm_entry.get('icon')

                vm_entry.update({
                    'name': vm_data['name'],
                    'link': vm_data['link'],
                    'note': vm_data['note'],
                    'icon': new_icon,
                    'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
                    'updated_by': session.get('user', 'team')
                })
                _save_vm_entries_unlocked(vm_entries)
                return redirect(url_for('vm_directory'))

    return render_template('vm_directory.html',
                           vm_entries=load_vm_entries(),
                           username=session.get('user'),
                           error='The VM entry could not be found.',
                           form_values={})

@app.route('/vm-directory/<vm_id>/move/<direction>', methods=['POST'])
@login_required
def move_vm_entry(vm_id, direction):
    if direction not in {'up', 'down'}:
        return redirect(url_for('vm_directory'))

    with VM_DIRECTORY_LOCK:
        vm_entries = _load_vm_entries_unlocked()
        current_index = next((index for index, item in enumerate(vm_entries) if item.get('id') == vm_id), None)

        if current_index is None:
            return redirect(url_for('vm_directory'))

        target_index = current_index - 1 if direction == 'up' else current_index + 1
        if 0 <= target_index < len(vm_entries):
            vm_entries[current_index], vm_entries[target_index] = vm_entries[target_index], vm_entries[current_index]
            _save_vm_entries_unlocked(vm_entries)

    return redirect(url_for('vm_directory'))

@app.route('/vm-directory/<vm_id>/delete', methods=['POST'])
@login_required
def delete_vm_entry(vm_id):
    with VM_DIRECTORY_LOCK:
        vm_entries = _load_vm_entries_unlocked()
        deleted_entries = [vm_entry for vm_entry in vm_entries if vm_entry.get('id') == vm_id]
        filtered_entries = [vm_entry for vm_entry in vm_entries if vm_entry.get('id') != vm_id]

        if len(filtered_entries) != len(vm_entries):
            _save_vm_entries_unlocked(filtered_entries)
            for vm_entry in deleted_entries:
                delete_vm_icon(vm_entry.get('icon'))

    return redirect(url_for('vm_directory'))

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
            
            # Execute the runner code with timeout for performance
            result = subprocess.run(
                [sys.executable, '-c', runner_code],
                capture_output=True,
                text=True,
                cwd=str(script_dir),
                timeout=15
            )
            
            # Check stderr for IP status message
            stderr_output = result.stderr.strip() if result.stderr else ""
            stdout_output = result.stdout.strip() if result.stdout else ""
            
            # Check if IP is in use (exit code 1 or error message in stderr)
            if result.returncode != 0 or "already in use" in stderr_output.lower():
                return jsonify({
                    'status': 'error',
                    'script': script_name,
                    'message': stderr_output if stderr_output else "IP address is already in use!",
                    'ip_status': 'in_use',
                    'timestamp': datetime.now().isoformat()
                }), 400
            
            # Success - config was generated
            config_output = stdout_output
            ip_status_message = stderr_output  # "IP Address x.x.x.x is free."
            
            return jsonify({
                'status': 'success',
                'script': script_name,
                'message': 'Configuration generated successfully',
                'ip_status_message': ip_status_message,
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
                    'config': result.get('config'),
                    'ip': result.get('device_ip'),
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

@app.route('/generate-cdp-pptx', methods=['POST'])
@login_required
def generate_cdp_pptx():
    """Parse pasted CDP output and stream back a generated .pptx file."""
    try:
        data = request.get_json(silent=True) or {}
        cdp_text = (data.get('cdp_output') or '').strip()
        local_name = (data.get('local_name') or '').strip() or None

        if not cdp_text:
            return jsonify({
                'status': 'error',
                'message': 'CDP output is required.'
            }), 400

        tmp = tempfile.NamedTemporaryFile(
            prefix='cdp_topology_', suffix='.pptx', delete=False)
        tmp.close()

        try:
            result = generate_pptx_from_cdp(
                cdp_text, tmp.name, local_name=local_name)
        except Exception as exc:
            try:
                os.remove(tmp.name)
            except OSError:
                pass
            return jsonify({
                'status': 'error',
                'message': f'Failed to generate PowerPoint: {exc}'
            }), 500

        if result.get('status') != 'success':
            try:
                os.remove(tmp.name)
            except OSError:
                pass
            return jsonify(result), 400

        safe_name = re.sub(r'[^A-Za-z0-9._-]+', '_',
                           result.get('local_name') or 'device')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        download_name = f'CDP_Topology_{safe_name}_{timestamp}.pptx'

        response = send_file(
            tmp.name,
            mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation',
            as_attachment=True,
            download_name=download_name,
        )
        response.headers['X-Neighbor-Count'] = str(result.get('neighbor_count', 0))
        response.headers['X-Local-Name'] = result.get('local_name', '')
        response.headers['X-Renderer'] = result.get('renderer', 'none')
        response.headers['X-Icon-Dir'] = result.get('icon_dir', '')
        warnings = result.get('warnings') or []
        if warnings:
            # Use base64-encoded JSON so arbitrary text (paths, quotes,
            # newlines) survives HTTP header encoding cleanly.
            response.headers['X-Warnings-B64'] = base64.b64encode(
                json.dumps(warnings).encode('utf-8')
            ).decode('ascii')
        response.headers['Access-Control-Expose-Headers'] = (
            'X-Neighbor-Count, X-Local-Name, X-Renderer, X-Icon-Dir, X-Warnings-B64'
        )
        return response

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Unexpected error: {str(e)}'
        }), 500


@app.route('/upload-ios', methods=['POST'])
@login_required
def upload_ios():
    """Receive IOS image file(s) from the browser and SCP them to a Cisco device."""
    temp_paths: list[str] = []
    upload_id = (request.form.get('upload_id') or '').strip()
    try:
        host = (request.form.get('device_ip') or '').strip()
        port_raw = (request.form.get('port') or '22').strip()
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        destination = (request.form.get('destination') or 'flash:/').strip()

        if not host or not username or not password:
            return jsonify({
                'status': 'error',
                'message': 'Device IP, SSH username, and password are required.'
            }), 400

        try:
            port = int(port_raw)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            return jsonify({
                'status': 'error',
                'message': 'SSH port must be an integer between 1 and 65535.'
            }), 400

        uploaded = request.files.getlist('files')
        if not uploaded:
            return jsonify({
                'status': 'error',
                'message': 'At least one IOS image file is required.'
            }), 400

        # Persist each uploaded stream to a temp file using the original
        # client filename so SCP places it at <destination>/<filename>.
        local_files: list[str] = []
        upload_dir = tempfile.mkdtemp(prefix='ios_upload_')
        for storage in uploaded:
            if not storage or not storage.filename:
                continue
            safe_name = os.path.basename(storage.filename)
            safe_name = re.sub(r'[^A-Za-z0-9._-]+', '_', safe_name)
            if not safe_name:
                continue
            local_path = os.path.join(upload_dir, safe_name)
            storage.save(local_path)
            local_files.append(local_path)
            temp_paths.append(local_path)

        if not local_files:
            return jsonify({
                'status': 'error',
                'message': 'No valid files were received from the browser.'
            }), 400

        # Seed an initial progress entry so the first poll sees something.
        if upload_id:
            _set_ios_progress(upload_id, {
                'phase': 'connecting',
                'percent': 0.0,
                'total_sent': 0,
                'total_size': sum(os.path.getsize(p) for p in local_files),
                'current_file': '',
                'rate_bps': 0.0,
            })

        def _progress_cb(p):
            _set_ios_progress(upload_id, {'phase': 'uploading', **p})

        result = upload_ios_image(
            host=host,
            port=port,
            username=username,
            password=password,
            files=local_files,
            destination=destination,
            progress_callback=_progress_cb if upload_id else None,
        )

        if upload_id:
            _set_ios_progress(upload_id, {
                'phase': 'done',
                'percent': 100.0 if result['status'] == 'success' else None,
                'final_status': result['status'],
            })
            _schedule_ios_progress_cleanup(upload_id, delay=60.0)

        return jsonify({
            'status': result['status'],
            'script': 'IOS Image Upload',
            'message': result['message'],
            'host': host,
            'port': port,
            'destination': destination,
            'results': result['results'],
            'timestamp': datetime.now().isoformat()
        }), (200 if result['status'] == 'success' else 400)

    except Exception as e:
        if upload_id:
            _set_ios_progress(upload_id, {
                'phase': 'done',
                'final_status': 'error',
                'error': str(e),
            })
            _schedule_ios_progress_cleanup(upload_id, delay=60.0)
        return jsonify({
            'status': 'error',
            'message': f'IOS upload failed: {str(e)}'
        }), 500

    finally:
        # Best-effort cleanup of the temp files and parent dir.
        for tp in temp_paths:
            try:
                os.remove(tp)
            except OSError:
                pass
        try:
            if temp_paths:
                os.rmdir(os.path.dirname(temp_paths[0]))
        except OSError:
            pass


@app.route('/upload-ios-progress/<upload_id>', methods=['GET'])
@login_required
def upload_ios_progress(upload_id):
    with IOS_UPLOAD_PROGRESS_LOCK:
        data = IOS_UPLOAD_PROGRESS.get(upload_id)
    if data is None:
        return jsonify({'phase': 'unknown'}), 200
    return jsonify(data), 200


@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({
        'status': 'error',
        'message': 'The uploaded file is too large. Maximum size is 4 GB.'
    }), 413


# ---------------------------------------------------------------------------
# NetBox device type sync
# ---------------------------------------------------------------------------
# API tokens are kept server side only; the browser session just holds a
# random handle pointing at the entry below.
NETBOX_SESSIONS: dict = {}
NETBOX_SESSIONS_LOCK = threading.Lock()
NETBOX_SESSION_TTL = 60 * 60  # seconds
DEFAULT_NETBOX_URL = devicetype_sync.DEFAULT_NETBOX_URL


def _prune_netbox_sessions():
    cutoff = _time.time() - NETBOX_SESSION_TTL
    with NETBOX_SESSIONS_LOCK:
        for handle in [k for k, v in NETBOX_SESSIONS.items() if v['created'] < cutoff]:
            NETBOX_SESSIONS.pop(handle, None)


def _store_netbox_session(url, token, verify):
    _prune_netbox_sessions()
    handle = uuid4().hex
    with NETBOX_SESSIONS_LOCK:
        NETBOX_SESSIONS[handle] = {
            'url': url,
            'token': token,
            'verify': verify,
            'created': _time.time(),
        }
    session['netbox_handle'] = handle


def _service_netbox_client():
    """NetBox client built from the configured service account, or None."""
    try:
        config = devicetype_sync.load_config()
    except NetBoxSyncError:
        logging.exception('Could not read the NetBox service configuration')
        return None
    token = (config.get('NETBOX_TOKEN') or '').strip()
    if not token or token.startswith('paste-your'):
        return None
    verify = str(config.get('NETBOX_VERIFY_SSL', 'true')).strip().lower() not in {
        'false', '0', 'no',
    }
    return NetBoxClient(config.get('NETBOX_URL') or DEFAULT_NETBOX_URL, token, verify=verify)


def _get_netbox_client():
    """Return a NetBox client for the interactive session, else the service account."""
    _prune_netbox_sessions()
    handle = session.get('netbox_handle')
    if handle:
        with NETBOX_SESSIONS_LOCK:
            entry = NETBOX_SESSIONS.get(handle)
        if entry:
            return NetBoxClient(entry['url'], entry['token'], verify=entry['verify'])
    return _service_netbox_client()


@app.route('/netbox/session', methods=['GET'])
@login_required
def netbox_session():
    """Report whether NetBox access is already available without a login prompt."""
    _prune_netbox_sessions()
    mode = 'user' if session.get('netbox_handle') else 'service'
    netbox = _get_netbox_client()
    if netbox is None:
        return jsonify({'authenticated': False, 'mode': 'none'})
    try:
        version = netbox.status().get('netbox-version', 'unknown')
    except Exception:
        logging.warning('NetBox service account check failed', exc_info=True)
        return jsonify({'authenticated': False, 'mode': 'none'})
    return jsonify({
        'authenticated': True,
        'mode': mode,
        'url': netbox.url,
        'netbox_version': version,
    })


@app.route('/netbox/auth', methods=['POST'])
@login_required
def netbox_auth():
    """Authenticate against NetBox with username/password or an API token."""
    data = request.get_json() or {}
    url = (data.get('url') or DEFAULT_NETBOX_URL).strip()
    token = (data.get('token') or '').strip()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    verify = bool(data.get('verify_ssl', True))

    if not url.startswith(('http://', 'https://')):
        return jsonify({'status': 'error', 'message': 'NetBox URL must start with http:// or https://'}), 400

    try:
        if not token:
            if not username or not password:
                return jsonify({
                    'status': 'error',
                    'message': 'Provide either an API token or a NetBox username and password.'
                }), 400
            token = NetBoxClient.provision_token(url, username, password, verify=verify)
        status = NetBoxClient(url, token, verify=verify).status()
    except NetBoxSyncError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 401
    except Exception as exc:
        logging.exception('NetBox authentication failed')
        return jsonify({'status': 'error', 'message': f'Could not reach NetBox: {exc}'}), 502

    _store_netbox_session(url, token, verify)
    return jsonify({
        'status': 'success',
        'url': url,
        'netbox_version': status.get('netbox-version', 'unknown')
    })


@app.route('/netbox/signout', methods=['POST'])
@login_required
def netbox_signout():
    handle = session.pop('netbox_handle', None)
    if handle:
        with NETBOX_SESSIONS_LOCK:
            NETBOX_SESSIONS.pop(handle, None)
    return jsonify({'status': 'success'})


@app.route('/netbox/device-types/check', methods=['POST'])
@login_required
def netbox_device_types_check():
    """Compare NetBox against the devicetype-library Cisco folder."""
    netbox = _get_netbox_client()
    if netbox is None:
        return jsonify({'status': 'auth_required', 'message': 'Authenticate against NetBox first.'}), 401

    try:
        existing = netbox.cisco_device_types()
        library = devicetype_sync.load_library()
        missing = devicetype_sync.find_missing(library, existing)
    except NetBoxSyncError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 502
    except Exception as exc:
        logging.exception('NetBox device type check failed')
        return jsonify({'status': 'error', 'message': f'Check failed: {exc}'}), 500

    return jsonify({
        'status': 'success',
        'existing_count': len(existing),
        'library_count': len(library),
        'missing': [
            {
                'file': file_name,
                'model': definition.get('model'),
                'part_number': definition.get('part_number') or '',
                'u_height': definition.get('u_height'),
                'slug': definition.get('slug') or '',
            }
            for file_name, definition in missing
        ],
    })


@app.route('/netbox/device-types/import', methods=['POST'])
@login_required
def netbox_device_types_import():
    """Import the selected device type definitions into NetBox."""
    netbox = _get_netbox_client()
    if netbox is None:
        return jsonify({'status': 'auth_required', 'message': 'Authenticate against NetBox first.'}), 401

    files = (request.get_json() or {}).get('files') or []
    if not isinstance(files, list) or not files:
        return jsonify({'status': 'error', 'message': 'No device types selected.'}), 400

    try:
        library = devicetype_sync.load_library()
        manufacturer_id = netbox.manufacturer_id()
    except NetBoxSyncError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 502
    except Exception as exc:
        logging.exception('NetBox device type import setup failed')
        return jsonify({'status': 'error', 'message': f'Import failed: {exc}'}), 500

    imported, errors = [], []
    for file_name in files:
        definition = library.get(file_name)
        if definition is None:
            errors.append(f'{file_name}: not part of the Cisco device type library')
            continue
        try:
            netbox.create_device_type(definition, manufacturer_id)
        except Exception as exc:
            errors.append(f'{file_name}: {exc}')
            continue
        imported.append(definition.get('model') or file_name)

    return jsonify({
        'status': 'success' if not errors else 'partial',
        'imported': imported,
        'imported_count': len(imported),
        'errors': errors,
    })


@app.route('/netbox/device/discover', methods=['POST'])
@login_required
def netbox_device_discover():
    """SSH to a device and read the details required to register it in NetBox."""
    netbox = _get_netbox_client()
    if netbox is None:
        return jsonify({'status': 'auth_required', 'message': 'Authenticate against NetBox first.'}), 401

    data = request.get_json() or {}
    ip = (data.get('ip') or '').strip()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    secret = data.get('secret') or ''

    if not ip or not username or not password:
        return jsonify({
            'status': 'error',
            'message': 'Management IP, username and password are required.'
        }), 400

    try:
        discovered = device_onboard.discover_device(ip, username, password, secret)
        sites = device_onboard.list_sites(netbox)
    except (OnboardError, NetBoxSyncError) as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400
    except Exception as exc:
        logging.exception('Device discovery failed')
        return jsonify({'status': 'error', 'message': f'Discovery failed: {exc}'}), 500

    return jsonify({
        'status': 'success',
        'device': discovered,
        'sites': sites,
        'roles': [
            {'key': key, 'name': name}
            for key, (name, _slug) in device_onboard.DEVICE_ROLES.items()
        ],
    })


@app.route('/netbox/device/create', methods=['POST'])
@login_required
def netbox_device_create():
    """Create the discovered device in NetBox."""
    netbox = _get_netbox_client()
    if netbox is None:
        return jsonify({'status': 'auth_required', 'message': 'Authenticate against NetBox first.'}), 401

    data = request.get_json() or {}
    try:
        site_id = int(data.get('site_id'))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Select a site.'}), 400

    try:
        result = device_onboard.onboard_device(
            netbox,
            hostname=data.get('hostname') or '',
            role_key=data.get('role') or '',
            model=data.get('model') or '',
            site_id=site_id,
            management_ip=data.get('management_ip') or '',
            management_interface=data.get('management_interface') or '',
            prefix_length=int(data.get('prefix_length') or 32),
            serial=data.get('serial') or '',
        )
    except (OnboardError, NetBoxSyncError) as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400
    except Exception as exc:
        logging.exception('Device onboarding failed')
        return jsonify({'status': 'error', 'message': f'Creation failed: {exc}'}), 500

    return jsonify({'status': 'success', **result})


@app.route('/logout')
def logout():
    handle = session.get('netbox_handle')
    if handle:
        with NETBOX_SESSIONS_LOCK:
            NETBOX_SESSIONS.pop(handle, None)
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

# Performance: Add cache headers for static files
@app.after_request
def add_cache_headers(response):
    if 'static' in request.path:
        response.cache_control.max_age = 31536000
        response.cache_control.public = True
    return response

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
    # threaded=True enables concurrent request handling
    app.run(debug=False, host='0.0.0.0', port=8080, threaded=True)
