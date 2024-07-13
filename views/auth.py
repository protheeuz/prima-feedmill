import cv2
import numpy as np
from flask import Blueprint, request, jsonify, redirect, url_for, render_template, send_file, flash
from flask_login import login_user, logout_user, login_required
from database import get_db
from deepface import DeepFace
from datetime import datetime
import json
import bcrypt
import qrcode
import io
from models import User
import random
import string
import logging
from sklearn.metrics.pairwise import cosine_similarity

auth_bp = Blueprint('auth', __name__)

def generate_unique_code():
    return ''.join(random.choices(string.digits, k=4))

logging.basicConfig(level=logging.DEBUG)

def encode_face(face_encoding):
    return json.dumps(face_encoding)

def decode_face(stored_encoding):
    return np.array(json.loads(stored_encoding))

def calculate_cosine_similarity(embedding1, embedding2):
    embedding1 = np.array(embedding1).reshape(1, -1)
    embedding2 = np.array(embedding2).reshape(1, -1)
    return cosine_similarity(embedding1, embedding2)[0][0]

@auth_bp.route('/check_existing_user', methods=['POST'])
def check_existing_user():
    nik = request.form['nik']
    email = request.form['email']
    
    connection = get_db()
    cursor = connection.cursor()
    
    cursor.execute("SELECT id FROM users WHERE nik=%s OR email=%s", (nik, email))
    existing_user = cursor.fetchone()
    cursor.close()

    if existing_user:
        return jsonify({"status": "gagal", "pesan": "NIK atau Email sudah terdaftar"}), 400
    
    return jsonify({"status": "sukses"})

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        nik = request.form['nik']
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        role = request.form['role']
        registration_date = datetime.now()
        unique_code = generate_unique_code()

        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

        connection = get_db()
        cursor = connection.cursor()
        
        cursor.execute("SELECT id FROM users WHERE nik=%s OR email=%s", (nik, email))
        existing_user = cursor.fetchone()
        if existing_user:
            cursor.close()
            return jsonify({"status": "gagal", "pesan": "NIK atau Email sudah terdaftar"}), 400

        cursor.execute("INSERT INTO users (nik, name, email, password, registration_date, role, unique_code) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                       (nik, name, email, hashed_password, registration_date, role, unique_code))
        connection.commit()
        user_id = cursor.lastrowid
        cursor.close()

        return jsonify({"status": "sukses", "user_id": user_id})
    return render_template('auth/register.html')

@auth_bp.route('/register_face', methods=['POST'])
def register_face():
    nik = request.form['nik']
    name = request.form['name']
    email = request.form['email']
    password = request.form['password']
    role = request.form['role']
    registration_date = datetime.now()
    unique_code = generate_unique_code()
    
    face_image = request.files['face_image']
    npimg = np.frombuffer(face_image.read(), np.uint8)
    img = cv2.imdecode(npimg, cv2.IMREAD_COLOR)

    try:
        result = DeepFace.represent(img, model_name='Facenet', enforce_detection=False)
        face_encoding = result[0]["embedding"]

        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

        connection = get_db()
        cursor = connection.cursor()
        cursor.execute("INSERT INTO users (nik, name, email, password, registration_date, role, unique_code) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                       (nik, name, email, hashed_password, registration_date, role, unique_code))
        connection.commit()
        user_id = cursor.lastrowid
        cursor.execute("INSERT INTO faces (user_id, encoding) VALUES (%s, %s)", (user_id, encode_face(face_encoding)))
        connection.commit()
        cursor.close()
        return jsonify({"status": "sukses", "user_id": user_id})
    except Exception as e:
        logging.exception("Terjadi kesalahan saat memproses wajah")
        return jsonify({"status": "gagal", "pesan": "Wajah tidak ditemukan"}), 400

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nik = request.form['nik']
        password = request.form['password']
        connection = get_db()
        cursor = connection.cursor()
        cursor.execute("SELECT id, password, role FROM users WHERE nik=%s", (nik,))
        user_data = cursor.fetchone()

        if user_data and bcrypt.checkpw(password.encode('utf-8'), user_data[1].encode('utf-8')):
            user = User.get(user_data[0])
            login_user(user)
            cursor.execute("UPDATE users SET last_login=NOW() WHERE id=%s", (user_data[0],))
            connection.commit()
            cursor.close()
            return redirect(url_for('main.index'))

        cursor.close()
        return render_template('auth/login.html', msg='NIK atau password salah')

    return render_template('auth/login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

@auth_bp.route('/login_face', methods=['POST'])
def login_face():
    logging.debug("Memulai proses login wajah")
    
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        logging.error("Tidak dapat mengakses kamera")
        return jsonify({"status": "gagal", "pesan": "Tidak dapat mengakses kamera"}), 400

    ret, frame = cap.read()
    cap.release()

    if not ret:
        logging.error("Tidak dapat mengambil frame dari kamera")
        return jsonify({"status": "gagal", "pesan": "Tidak dapat mengambil frame dari kamera"}), 400

    try:
        logging.debug("Mengambil representasi wajah")
        result = DeepFace.represent(frame, model_name='Facenet', enforce_detection=False)
        face_encoding = result[0]["embedding"]
        logging.debug(f"Representasi wajah berhasil diambil: {face_encoding}")

        user_id = recognize_face(face_encoding)
        if user_id:
            logging.debug(f"Wajah dikenali, user_id: {user_id}")
            connection = get_db()
            cursor = connection.cursor()
            cursor.execute("UPDATE users SET last_login=NOW() WHERE id=%s", (user_id,))
            connection.commit()

            user = User.get(user_id)
            login_user(user)

            cursor.close()
            return jsonify({"status": "sukses", "user_id": user_id})
        else:
            logging.error("Wajah tidak dikenali")
            return jsonify({"status": "gagal", "pesan": "Wajah tidak dikenali"}), 401
    except Exception as e:
        logging.exception("Terjadi kesalahan saat memproses wajah")
        return jsonify({"status": "gagal", "pesan": "Tidak ada wajah yang ditemukan"}), 400

@auth_bp.route('/generate_qr', methods=['GET'])
def generate_qr():
    nik = request.args.get('nik')
    connection = get_db()
    cursor = connection.cursor()
    cursor.execute("SELECT unique_code FROM users WHERE nik=%s", (nik,))
    user = cursor.fetchone()
    cursor.close()

    if user:
        unique_code = user[0]
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(unique_code)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        buf = io.BytesIO()
        img.save(buf)
        buf.seek(0)

        return send_file(buf, mimetype='image/png')
    else:
        return jsonify({"status": "gagal", "pesan": "NIK tidak ditemukan"}), 404

@auth_bp.route('/login_qr', methods=['POST'])
def login_qr():
    data = request.get_json()
    qr_code = data.get('qr_code')
    user_code = data.get('user_code')
    
    logging.debug(f'Received qr_code: {qr_code}, user_code: {user_code}')
    
    connection = get_db()
    cursor = connection.cursor()
    cursor.execute("SELECT id, unique_code FROM users WHERE nik=%s", (qr_code,))
    user = cursor.fetchone()
    
    if user:
        logging.debug(f'User found: {user}')
        if user_code == user[1]:
            user_id = user[0]
            cursor.execute("UPDATE users SET last_login=NOW() WHERE id=%s", (user_id,))
            connection.commit()
            cursor.close()

            user = User.get(user_id)
            login_user(user)

            return jsonify({"status": "sukses", "user_id": user_id})
        else:
            logging.debug('Invalid unique code')
            cursor.close()
            return jsonify({"status": "gagal", "pesan": "Kode unik tidak valid"}), 401
    else:
        logging.debug('Invalid QR code')
        cursor.close()
        return jsonify({"status": "gagal", "pesan": "QR Code tidak valid"}), 401

@auth_bp.route('/delete_user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    connection = get_db()
    cursor = connection.cursor()

    try:
        cursor.execute("DELETE FROM faces WHERE user_id=%s", (user_id,))
        cursor.execute("DELETE FROM users WHERE id=%s", (user_id,))
        connection.commit()
        cursor.close()
        
        return jsonify({"status": "sukses", "pesan": "User berhasil dihapus"})
    except Exception as e:
        connection.rollback()
        cursor.close()
        return jsonify({"status": "gagal", "pesan": str(e)}), 500

def recognize_face(face_encoding):
    connection = get_db()
    cursor = connection.cursor()
    cursor.execute("SELECT user_id, encoding FROM faces")
    rows = cursor.fetchall()
    cursor.close()

    for row in rows:
        user_id, stored_encoding = row
        stored_encoding = decode_face(stored_encoding)
        similarity = calculate_cosine_similarity(face_encoding, stored_encoding)
        if similarity > 0.8:
            return user_id
    return None