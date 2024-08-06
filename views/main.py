from flask import Blueprint, flash, render_template, redirect, url_for, request, jsonify, current_app, session
from flask_login import login_required, current_user, login_user
from database import get_db
from models import User
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import requests
import os
import mysql.connector

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
@login_required
def index():
    user_id = request.args.get('user_id')
    connection = get_db()
    cursor = connection.cursor()

    if current_user.role == 'admin':
        cursor.execute("""
            SELECT u.id, u.name, u.registration_date, h.completed 
            FROM users u
            LEFT JOIN health_checks h ON u.id = h.user_id AND h.check_date = CURDATE()
            WHERE u.role='karyawan' AND u.registration_date >= DATE_SUB(NOW(), INTERVAL 2 WEEK)
            ORDER BY u.registration_date DESC
        """)
        recent_users = cursor.fetchall()

        cursor.execute("""
            SELECT 
                DATE(check_date) as date, 
                AVG(completed) as daily_health
            FROM health_checks
            GROUP BY DATE(check_date)
            ORDER BY DATE(check_date) DESC
            LIMIT 30
        """)
        daily_health_data_raw = cursor.fetchall()

        # Get health checks for today, this week, and all
        today = datetime.now().date()
        start_of_week = today - timedelta(days=today.weekday())

        cursor.execute("""
            SELECT u.name, s.timestamp, h.completed, u.profile_image
            FROM users u
            LEFT JOIN sensor_data s ON u.id = s.user_id
            LEFT JOIN health_checks h ON u.id = h.user_id AND h.check_date = CURDATE()
            WHERE u.role='karyawan' AND DATE(s.timestamp) = CURDATE()
        """)
        today_health_checks = cursor.fetchall()

        cursor.execute("""
            SELECT u.name, s.timestamp, h.completed, u.profile_image
            FROM users u
            LEFT JOIN sensor_data s ON u.id = s.user_id
            LEFT JOIN health_checks h ON u.id = h.user_id AND h.check_date >= %s AND h.check_date <= %s
            WHERE u.role='karyawan' AND DATE(s.timestamp) >= %s AND DATE(s.timestamp) <= %s
        """, (start_of_week, today, start_of_week, today))
        weekly_health_checks = cursor.fetchall()

        cursor.execute("""
            SELECT u.name, s.timestamp, h.completed, u.profile_image
            FROM users u
            LEFT JOIN sensor_data s ON u.id = s.user_id
            LEFT JOIN health_checks h ON u.id = h.user_id
            WHERE u.role='karyawan'
        """)
        all_health_checks = cursor.fetchall()

        cursor.close()

        # Process the data for charts
        health_check_labels = [stat[0].strftime('%d %b') for stat in daily_health_data_raw]
        daily_health_data = [stat[1] * 100 for stat in daily_health_data_raw]  # Convert to percentage
        weekly_health_data = []
        monthly_health_data = []

        # Calculate weekly and monthly averages
        for i in range(0, len(daily_health_data_raw), 7):
            weekly_avg = sum(d[1] for d in daily_health_data_raw[i:i+7]) / 7 * 100
            weekly_health_data.append(weekly_avg)

        for i in range(0, len(daily_health_data_raw), 30):
            monthly_avg = sum(d[1] for d in daily_health_data_raw[i:i+30]) / 30 * 100
            monthly_health_data.append(monthly_avg)

        return render_template('home/index_admin.html',
                               recent_users=recent_users,
                               health_check_labels=health_check_labels,
                               daily_health_data=daily_health_data,
                               weekly_health_data=weekly_health_data,
                               monthly_health_data=monthly_health_data,
                               today_health_checks=today_health_checks,
                               weekly_health_checks=weekly_health_checks,
                               all_health_checks=all_health_checks)
    else:
        cursor.execute("""
            SELECT heart_rate, oxygen_level, temperature, activity_level 
            FROM sensor_data
            WHERE user_id = %s ORDER BY timestamp DESC LIMIT 1
        """, (current_user.id,))
        latest_health_data = cursor.fetchone()

        cursor.execute("""
            SELECT ecg_value, timestamp 
            FROM sensor_data
            WHERE user_id = %s ORDER BY timestamp DESC
        """, (current_user.id,))
        ecg_data = cursor.fetchall()

        cursor.close()

        ecg_values = [data[0] for data in ecg_data]
        ecg_timestamps = [data[1].strftime('%H:%M:%S') for data in ecg_data]

        return render_template('home/index_karyawan.html',
                               latest_health_data=latest_health_data,
                               ecg_values=ecg_values,
                               ecg_timestamps=ecg_timestamps)

@main_bp.route('/notifications')
@login_required
def notifications():
    if current_user.role != 'admin':
        return jsonify({"error": "Unauthorized"}), 403

    new_notifications = get_new_logins_for_admin()
    old_notifications = get_old_logins_for_admin()
    new_logins_count = len(new_notifications)

    return render_template('includes/navigation.html',
                           new_notifications=new_notifications,
                           old_notifications=old_notifications,
                           new_logins_count=new_logins_count)

def get_new_logins_for_admin():
    connection = get_db()
    cursor = connection.cursor()
    cursor.execute("""
        SELECT name, last_login, TIMESTAMPDIFF(MINUTE, last_login, NOW()) as time_ago
        FROM users
        WHERE last_login >= CURDATE()
        ORDER BY last_login DESC
    """)
    logins = cursor.fetchall()
    cursor.close()
    return [{'user_name': login[0], 'time_ago': f'{login[2]} min', 'message': 'Baru login'} for login in logins]

def get_old_logins_for_admin():
    connection = get_db()
    cursor = connection.cursor()
    cursor.execute("""
        SELECT name, last_login, TIMESTAMPDIFF(MINUTE, last_login, NOW()) as time_ago
        FROM users
        WHERE last_login < CURDATE()
        ORDER BY last_login DESC
    """)
    logins = cursor.fetchall()
    cursor.close()
    return [{'user_name': login[0], 'time_ago': f'{login[2]} min', 'message': 'Login sebelumnya'} for login in logins]

@main_bp.route('/dashboard/<int:user_id>')
@login_required
def dashboard(user_id):
    return redirect(url_for('main.index'))

@main_bp.route('/health_check')
@login_required
def health_check():
    user_id = current_user.id
    connection = get_db()
    cursor = connection.cursor()
    
    cursor.execute("""
        SELECT completed 
        FROM health_checks 
        WHERE user_id = %s AND check_date = CURDATE()
    """, (user_id,))
    health_check = cursor.fetchone()
    
    cursor.close()
    return jsonify({'health_check_completed': health_check and health_check[0]})

@main_bp.route('/health_check_modal')
@login_required
def health_check_modal():
    return render_template('health_check_modal.html')

@main_bp.route('/get_sensor_data/<sensor>', methods=['GET'])
@login_required
def get_sensor_data(sensor):
    try:
        esp32_ip = '192.168.1.16'
        response = requests.get(f'http://{esp32_ip}/sensor_data/{sensor}')
        data = response.json()
        if response.status_code == 200:
            return jsonify({'status': 'sukses', 'value': data['value']})
        else:
            return jsonify({'status': 'gagal', 'message': data['message']}), 400
    except Exception as e:
        return jsonify({'status': 'gagal', 'message': str(e)}), 500

@main_bp.route('/sensor_data', methods=['POST'])
def sensor_data():
    data = request.get_json()
    user_id = data.get('user_id')
    heart_rate = data.get('heart_rate')
    oxygen_level = data.get('oxygen_level')
    temperature = data.get('temperature')
    activity_level = data.get('activity_level')
    ecg_value = data.get('ecg_value')
    
    if user_id == 0 or user_id is None:
        current_app.logger.error("Invalid user_id received.")
        return jsonify({"status": "gagal", "message": "Invalid user_id"}), 400
    
    connection = get_db()
    cursor = connection.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO sensor_data (user_id, heart_rate, oxygen_level, temperature, activity_level, ecg_value)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, heart_rate, oxygen_level, temperature, activity_level, ecg_value))
        
        check_date = datetime.now().date()
        cursor.execute("UPDATE health_checks SET completed = TRUE WHERE user_id = %s AND check_date = %s", (user_id, check_date))
        connection.commit()
        return jsonify({"status": "sukses"})
    except mysql.connector.errors.IntegrityError as e:
        connection.rollback()
        current_app.logger.error(f"Database error: {e}")
        return jsonify({"status": "gagal", "message": str(e)}), 500
    finally:
        cursor.close()

@main_bp.route('/request_sensor_data', methods=['POST'])
@login_required
def request_sensor_data():
    esp32_ip = request.json.get('esp32_ip')
    sensor = request.json.get('sensor')
    user_id = session.get('user_id')

    try:
        response = requests.get(f'http://{esp32_ip}/get_sensor_data/{sensor}')
        data = response.json()

        if response.status_code == 200:
            connection = get_db()
            cursor = connection.cursor()
            cursor.execute(f"""
                INSERT INTO sensor_data (user_id, {sensor})
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE {sensor} = %s
            """, (user_id, data['value'], data['value']))
            connection.commit()
            cursor.close()

            return jsonify({'status': 'sukses', 'value': data['value']})
        else:
            return jsonify({'status': 'gagal', 'message': data['message']}), 400
    except Exception as e:
        return jsonify({'status': 'gagal', 'message': str(e)}), 500

@main_bp.route('/poll_health_check_status', methods=['GET'])
def poll_health_check_status():
    session_token = request.headers.get('Session-Token')
    user_id = session.get('user_id')
    current_app.logger.debug(f'Polling health check status, session user_id: {user_id}, session_token: {session_token}')
    if session_token and session_token == session.get('session_token'):
        return jsonify({"user_id": user_id})
    else:
        return jsonify({"user_id": -1})

@main_bp.route('/profile')
@login_required
def profile():
    return render_template('home/profile.html')

def save_profile_image(image_file):
    filename = secure_filename(image_file.filename)
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    image_file.save(filepath)
    return 'uploads/' + filename

@main_bp.route('/update_profile', methods=['POST'])
@login_required
def update_profile():
    name = request.form['name']
    address = request.form['address']
    about = request.form['about']
    
    connection = get_db()
    cursor = connection.cursor()
    
    cursor.execute("""
        UPDATE users
        SET name = %s, address = %s, about = %s
        WHERE id = %s
    """, (name, address, about, current_user.id))
    
    connection.commit()
    cursor.close()

    user = User.get(current_user.id)
    login_user(user)

    flash('Profil berhasil diperbarui.', 'success')
    return redirect(url_for('main.profile'))

@main_bp.route('/update_profile_image', methods=['POST'])
@login_required
def update_profile_image():
    profile_image = request.files.get('profile_image')
    if (profile_image):
        profile_image_filename = save_profile_image(profile_image)
        
        connection = get_db()
        cursor = connection.cursor()
        cursor.execute("""
            UPDATE users
            SET profile_image = %s
            WHERE id = %s
        """, (profile_image_filename, current_user.id))
        
        connection.commit()
        cursor.close()

        user = User.get(current_user.id)
        login_user(user)

        flash('Foto profil berhasil diperbarui.', 'success')
    return redirect(url_for('main.profile'))

@main_bp.route('/employee_list')
@login_required
def employee_list():
    if current_user.role != 'admin':
        return redirect(url_for('main.index'))
    
    connection = get_db()
    cursor = connection.cursor()
    
    cursor.execute("""
        SELECT nik, name, registration_date, last_login, address, about
        FROM users
        WHERE role = 'karyawan'
        ORDER BY registration_date DESC
    """)
    employees = cursor.fetchall()
    
    cursor.close()
    
    return render_template('home/employee_list.html', employees=employees)

@main_bp.route('/send_lcd_data', methods=['POST'])
@login_required
def send_lcd_data():
    step = request.json.get('step')
    value = request.json.get('value')
    esp32_ip = '192.168.1.16'

    try:
        response = requests.post(f'http://{esp32_ip}/update_lcd', json={'step': step, 'value': value})
        data = response.json()

        if response.status_code == 200 and data['status'] == 'sukses':
            return jsonify({'status': 'sukses'})
        else:
            return jsonify({'status': 'gagal', 'message': data.get('message', 'Unknown error')}), 400
    except Exception as e:
        return jsonify({'status': 'gagal', 'message': str(e)}), 500
