import os
from io import BytesIO
from datetime import datetime, date, time
from functools import wraps
from flask import (Flask, request, render_template, redirect, url_for, send_file,
                   flash, session, jsonify)
from werkzeug.utils import secure_filename
import pandas as pd
import qrcode
from flask_sqlalchemy import SQLAlchemy

# Config
ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASS = os.environ.get('ADMIN_PASS', 'password')
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'qrcodes')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXT = {'.xlsx', '.xls', '.csv'}

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET', 'supersecret')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///data.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Database Model
class Registration(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    regno = db.Column(db.String(50), nullable=False, unique=True)
    data = db.Column(db.JSON, nullable=False)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime, nullable=True)

with app.app_context():
    db.create_all()

# Helpers
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('admin_login', next=request.path))
        return f(*args, **kwargs)
    return decorated

def allowed_file(filename):
    _, ext = os.path.splitext(filename.lower())
    return ext in ALLOWED_EXT

def generate_qr_for_text(text, filename_path):
    img = qrcode.make(text)
    img.save(filename_path)
    return filename_path

# Routes
@app.route('/')
def index():
    return redirect(url_for('upload'))

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('admin'):
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if username == ADMIN_USER and password == ADMIN_PASS:
            session['admin'] = True
            flash('Logged in as admin', 'success')
            return redirect(url_for('admin_dashboard'))
        flash('Invalid credentials', 'danger')
    return render_template('login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    flash('Logged out', 'info')
    return redirect(url_for('admin_login'))

@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    if request.method == 'POST':
        file = request.files.get('file')
        if not file:
            flash('No file uploaded', 'warning')
            return redirect(request.url)

        filename = secure_filename(file.filename)
        if not allowed_file(filename):
            flash('File type not allowed. Use .xlsx, .xls, .csv', 'warning')
            return redirect(request.url)

        try:
            df = pd.read_csv(file) if filename.lower().endswith('.csv') else pd.read_excel(file, engine='openpyxl')
        except Exception as e:
            flash(f'Error reading file: {e}', 'danger')
            return redirect(request.url)

        created = 0
        for _, row in df.iterrows():
            record = row.to_dict()

            # Convert any datetime/date/time to string
            for k, v in record.items():
                if isinstance(v, (datetime, date, time)):
                    record[k] = str(v)

            regno = str(record.get('Reg No') or record.get('regno') or record.get('ID') or '').strip()
            if not regno:
                continue

            # Skip duplicates
            if Registration.query.filter_by(regno=regno).first():
                continue

            # Generate QR
            qr_filename = f"{regno}.png"
            qr_path = os.path.join(UPLOAD_FOLDER, qr_filename)
            generate_qr_for_text(regno, qr_path)

            # Save in DB
            reg = Registration(
                regno=regno,
                data=record,
                status='pending'
            )
            db.session.add(reg)
            created += 1

        db.session.commit()
        flash(f'Processed file. {created} records added to pending.', 'success')
        return redirect(request.url)

    return render_template('upload.html')

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    pending_count = Registration.query.filter_by(status='pending').count()
    approved_count = Registration.query.filter_by(status='approved').count()
    recent_pending = Registration.query.filter_by(status='pending').order_by(Registration.created_at.desc()).limit(10).all()
    return render_template('admin_dashboard.html', pending_count=pending_count,
                           approved_count=approved_count, recent_pending=recent_pending)

@app.route('/admin/scan')
@login_required
def admin_scan():
    return render_template('admin_scan.html')

# API to get record by regno
@app.route('/api/get_by_regno/<regno>')
@login_required
def api_get_by_regno(regno):
    rec = Registration.query.filter_by(regno=regno).first()
    if not rec:
        return jsonify({'error': 'not found'}), 404
    return jsonify({'id': rec.id, 'data': rec.data, 'status': rec.status})

# API to approve record by ID
@app.route('/api/approve/<int:rec_id>', methods=['POST'])
@login_required
def api_approve(rec_id):
    rec = Registration.query.get(rec_id)
    if not rec:
        return jsonify({'error': 'not found'}), 404
    rec.status = 'approved'
    rec.approved_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/admin/download')
@login_required
def admin_download_page():
    approved_count = Registration.query.filter_by(status='approved').count()
    return render_template('admin_download.html', approved_count=approved_count)

@app.route('/admin/download/export')
@login_required
def admin_download_export():
    docs = Registration.query.filter_by(status='approved').all()
    if not docs:
        flash('No approved records to download', 'warning')
        return redirect(url_for('admin_download_page'))

    records = [d.data for d in docs]
    df = pd.DataFrame(records)
    bio = BytesIO()
    df.to_excel(bio, index=False, engine='openpyxl')
    bio.seek(0)
    return send_file(bio, download_name='approved_records.xlsx', as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
