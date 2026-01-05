# CNS Skynet - Script Toolbox

A Flask-based web application for the CNS Lab Script Toolbox. This application provides a secure, user-friendly interface to manage and execute scripts.

## Features

- 🔐 **Secure Authentication**: Login page with username and password protection
- 🎨 **Modern UI**: Clean, responsive web interface with gradient design
- 🚀 **Script Management**: Easily run different lab automation scripts
- 📊 **Execution Results**: Real-time feedback on script execution
- 📱 **Responsive Design**: Works on desktop and mobile devices
- 🔒 **Session Management**: Secure session handling with logout functionality

## Installation

### 1. Clone or Download the Project

```bash
cd "AI CNS Toolbox"
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

Or install Flask directly:

```bash
pip install Flask==2.3.3
```

## Running the Application

### Start the Server

```bash
python main.py
```

The application will start on `http://localhost:5000`

### Access the Website

1. Open your web browser
2. Navigate to `http://localhost:5000`
3. You will be redirected to the login page

## Login Credentials

- **Username**: `cisco`
- **Password**: `cisco`

## Project Structure

```
AI CNS Toolbox/
├── main.py                 # Flask application main file
├── requirements.txt        # Python dependencies
├── README.md              # This file
└── templates/             # HTML templates
    ├── login.html         # Login page
    ├── dashboard.html     # Main dashboard with script selection
    ├── 404.html           # 404 error page
    └── 500.html           # 500 error page
```

## Available Scripts

The toolbox comes with the following pre-configured scripts:

1. **Network Diagnostics** (🌐) - Run network connectivity and diagnostic checks
2. **Device Inventory** (📦) - Retrieve and display device inventory information
3. **Configuration Backup** (💾) - Backup device configurations
4. **Performance Report** (📊) - Generate performance analytics and reports
5. **Security Audit** (🔒) - Run security checks and vulnerability scans
6. **Log Analysis** (📝) - Analyze system and application logs

## Customizing the Application

### Adding New Scripts

Edit the `AVAILABLE_SCRIPTS` dictionary in `main.py`:

```python
AVAILABLE_SCRIPTS = {
    'script_id': {
        'name': 'Display Name',
        'description': 'Script description',
        'icon': '🎯'  # Unicode emoji or text
    }
}
```

### Changing Login Credentials

In `main.py`, update:

```python
VALID_USERNAME = 'your_username'
VALID_PASSWORD = 'your_password'
```

### Implementing Script Execution

Modify the `run_script()` function in `main.py` to call your actual scripts:

```python
@app.route('/run-script/<script_id>', methods=['POST'])
@login_required
def run_script(script_id):
    # Add your script execution logic here
    pass
```

## Configuration

### Change Server Host/Port

In `main.py`, modify the `app.run()` call:

```python
app.run(debug=True, host='0.0.0.0', port=5000)
```

- `host='0.0.0.0'` - Accessible from any network interface
- `host='127.0.0.1'` - Only localhost
- `port=5000` - Change to any available port

### Production Deployment

Before deploying to production:

1. Change `debug=False` in `app.run()`
2. Set a strong secret key: `app.secret_key = 'your-secure-key'`
3. Use a production WSGI server like Gunicorn:

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 main:app
```

## Troubleshooting

### Port Already in Use

If port 5000 is already in use, change it in `main.py`:

```python
app.run(debug=True, host='0.0.0.0', port=5001)
```

### Session Issues

Clear browser cookies if you encounter login issues.

### Missing Templates

Ensure the `templates/` folder exists and contains all HTML files.

## Security Notes

⚠️ **Important for Production:**

1. Change the secret key in `app.secret_key`
2. Use environment variables for credentials instead of hardcoding
3. Set `debug=False` in production
4. Use HTTPS instead of HTTP
5. Implement proper input validation and sanitization
6. Use a production-grade WSGI server

## Browser Compatibility

- Chrome/Edge 80+
- Firefox 75+
- Safari 13+
- Mobile browsers (iOS Safari, Chrome Mobile)

## License

Internal Use Only

## Support

For issues or feature requests, contact your system administrator.

---

**CNS Skynet v1.0** | Lab Automation Toolbox
