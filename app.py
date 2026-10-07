from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import time, math, threading, sqlite3, datetime, requests

app = Flask(__name__)
CORS(app)

APP_SECRET_TOKEN = "SYS-GPS-X99-2026-BOGOTA"
ADMIN_USER = "vektor_admin"
ADMIN_PASS = "VektorSeguro2026"
active_simulations = {} 

def get_db_connection():
    conn = sqlite3.connect('vektor.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, pin TEXT, status TEXT, role TEXT, failed_attempts INTEGER DEFAULT 0, expires_at REAL)''')
    try: conn.execute('ALTER TABLE users ADD COLUMN ip_address TEXT')
    except: pass
    try: conn.execute('ALTER TABLE users ADD COLUMN name TEXT DEFAULT "Técnico"')
    except: pass
    try: conn.execute('ALTER TABLE users ADD COLUMN color TEXT DEFAULT "#00D2FF"')
    except: pass
    conn.execute('CREATE TABLE IF NOT EXISTS sugerencias (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, mensaje TEXT, fecha TEXT)')
    conn.commit()
    conn.close()

init_db()

@app.before_request
def block_unauthorized_requests():
    if request.path.startswith('/api/'):
        if request.headers.get('X-App-Secret') != APP_SECRET_TOKEN:
            return jsonify({"error": "Acceso denegado"}), 403

@app.route('/')
def index(): return render_template('index.html')

@app.route('/admin')
def panel_admin(): return render_template('admin.html')

@app.route('/api/request-access', methods=['POST'])
def request_access():
    phone = request.json.get('phone')
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    expires_at = time.time() + 300
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE phone = ?', (phone,)).fetchone()
    if user is None: conn.execute('INSERT INTO users (phone, pin, status, role, failed_attempts, expires_at, ip_address, name, color) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (phone, "", "waiting_admin", "user", 0, expires_at, client_ip, f"Tech-{phone[-4:]}", "#00D2FF"))
    else: conn.execute('UPDATE users SET status = ?, failed_attempts = 0, expires_at = ?, ip_address = ? WHERE phone = ?', ("waiting_admin", expires_at, client_ip, phone))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/login', methods=['POST'])
def login():
    phone, pin = request.json.get('phone'), request.json.get('pin')
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE phone = ?', (phone,)).fetchone()
    if user:
        if user['status'] == "blocked": return jsonify({"error": "Bloqueado por el admin."})
        if user['status'] == "waiting_admin": return jsonify({"error": "El administrador aún no te ha dado el aval."})
        if user['pin'] == pin and user['status'] == 'approved':
            conn.execute('UPDATE users SET failed_attempts = 0 WHERE phone = ?', (phone,))
            conn.commit()
            return jsonify({"success": True})
        elif user['status'] == 'approved':
            intentos = user['failed_attempts'] + 1
            conn.execute('UPDATE users SET failed_attempts = ? WHERE phone = ?', (intentos, phone))
            conn.commit()
            return jsonify({"error": f"PIN incorrecto. Intento {intentos}/3"})
    return jsonify({"error": "Número no encontrado."})

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))

def simulate_movement(phone, start_lat, start_lng, target_lat, target_lng, time_minutes):
    global active_simulations
    try:
        url = f"http://router.project-osrm.org/route/v1/driving/{start_lng},{start_lat};{target_lng},{target_lat}?overview=full&geometries=geojson"
        r = requests.get(url, timeout=5).json()
        coords = r['routes'][0]['geometry']['coordinates']
        route = [[c[1], c[0]] for c in coords]
    except:
        route = [[start_lat, start_lng], [target_lat, target_lng]]

    total_seconds = int(time_minutes * 60)
    if total_seconds < 15: total_seconds = 15

    segments, total_dist = [], 0
    for i in range(len(route)-1):
        d = haversine(route[i][0], route[i][1], route[i+1][0], route[i+1][1])
        segments.append(d)
        total_dist += d

    interpolated = []
    if total_dist == 0: interpolated = [route[0]] * total_seconds
    else:
        speed = total_dist / total_seconds
        for sec in range(total_seconds):
            target_d = speed * sec
            acc = 0
            for i, seg_d in enumerate(segments):
                if acc + seg_d >= target_d:
                    ratio = (target_d - acc) / seg_d if seg_d > 0 else 0
                    lat = route[i][0] + (route[i+1][0] - route[i][0]) * ratio
                    lng = route[i][1] + (route[i+1][1] - route[i][1]) * ratio
                    interpolated.append([lat, lng])
                    break
                acc += seg_d
        interpolated.append(route[-1])

    for pt in interpolated:
        if phone not in active_simulations or active_simulations[phone].get("stop"): break
        active_simulations[phone].update({"lat": pt[0], "lng": pt[1], "status": "En Ruta"})
        active_simulations[phone]["path"].append(pt)
        if len(active_simulations[phone]["path"]) > 400: active_simulations[phone]["path"].pop(0) 
        time.sleep(1)
    
    if phone in active_simulations and not active_simulations[phone].get("stop"): 
        active_simulations[phone].update({"lat": target_lat, "lng": target_lng, "status": "Llegó"})

@app.route('/api/simulate', methods=['POST'])
def start_simulation():
    data = request.json
    phone = data['phone']
    time_minutes = float(data.get('time', 5))
    active_simulations[phone] = {"lat": float(data['start_lat']), "lng": float(data['start_lng']), "status": "Iniciando", "stop": False, "path": [[float(data['start_lat']), float(data['start_lng'])]]}
    threading.Thread(target=simulate_movement, args=(phone, float(data['start_lat']), float(data['start_lng']), float(data['target_lat']), float(data['target_lng']), time_minutes)).start()
    return jsonify({"success": True})

@app.route('/api/location/<phone>', methods=['GET'])
def get_location(phone): 
    conn = get_db_connection()
    user = conn.execute("SELECT status FROM users WHERE phone = ?", (phone,)).fetchone()
    conn.close()
    if not user: return jsonify({"status": "eliminado"}) 
    elif user['status'] == 'blocked': return jsonify({"status": "bloqueado"}) 
    return jsonify(active_simulations.get(phone, {"status": "offline"}))

@app.route('/api/stop', methods=['POST'])
def stop_simulation():
    phone = request.json.get('phone')
    if phone in active_simulations: active_simulations[phone]["stop"] = True
    return jsonify({"success": True})

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    if request.json.get('username') == ADMIN_USER and request.json.get('password') == ADMIN_PASS: return jsonify({"success": True})
    return jsonify({"error": "Inválido"})

@app.route('/api/admin/solicitudes', methods=['GET'])
def ver_solicitudes():
    conn = get_db_connection()
    usuarios = conn.execute("SELECT * FROM users WHERE status = 'waiting_admin'").fetchall()
    sols = [{"phone": u['phone'], "ip": u['ip_address'], "tiempo_restante": int(u['expires_at'] - time.time())} for u in usuarios if int(u['expires_at'] - time.time()) > 0]
    return jsonify({"success": True, "solicitudes": sols})

@app.route('/api/admin/tecnicos', methods=['GET'])
def ver_tecnicos():
    conn = get_db_connection()
    usuarios = conn.execute("SELECT phone, pin, name, color, status FROM users WHERE status = 'approved' OR status = 'blocked'").fetchall()
    conn.close()
    return jsonify({"success": True, "data": [{"phone": u['phone'], "pin": u['pin'], "name": u['name'], "color": u['color'], "status": u['status']} for u in usuarios]})

@app.route('/api/admin/configurar-tecnico', methods=['POST'])
def configurar_tecnico():
    conn = get_db_connection()
    conn.execute("UPDATE users SET name = ?, color = ? WHERE phone = ?", (request.json.get('name'), request.json.get('color'), request.json.get('phone')))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/admin/aprobar', methods=['POST'])
def aprobar():
    conn = get_db_connection()
    conn.execute("UPDATE users SET pin = ?, status = 'approved', failed_attempts = 0 WHERE phone = ?", (request.json.get('pin'), request.json.get('phone')))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/admin/editar-pin', methods=['POST'])
def editar_pin():
    conn = get_db_connection()
    conn.execute("UPDATE users SET pin = ? WHERE phone = ?", (request.json.get('pin'), request.json.get('phone')))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/admin/eliminar', methods=['POST'])
def eliminar():
    conn = get_db_connection()
    conn.execute("DELETE FROM users WHERE phone = ?", (request.json.get('phone'),))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/admin/crear-perfil', methods=['POST'])
def crear_perfil():
    conn = get_db_connection()
    try:
        conn.execute('INSERT INTO users (phone, pin, status, role, failed_attempts, expires_at, name, color) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (request.json.get('phone'), request.json.get('pin'), "approved", "user", 0, time.time() + 31536000, "Técnico Manual", "#22c55e"))
        conn.commit()
        exito = True
    except: exito = False
    conn.close()
    return jsonify({"success": exito})

@app.route('/api/admin/todas-ubicaciones', methods=['GET'])
def todas_ubicaciones():
    conn = get_db_connection()
    activos = []
    for phone, data in active_simulations.items():
        if not data.get("stop") and data.get("status") != "offline":
            u = conn.execute("SELECT name, color FROM users WHERE phone = ?", (phone,)).fetchone()
            if u: activos.append({"phone": phone, "name": u['name'], "color": u['color'], "lat": data['lat'], "lng": data['lng'], "status": data['status'], "path": data.get("path", [])})
    conn.close()
    return jsonify({"success": True, "data": activos})

@app.route('/api/admin/metricas', methods=['GET'])
def ver_metricas():
    conn = get_db_connection()
    t = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    a = conn.execute("SELECT COUNT(*) FROM users WHERE status = 'approved'").fetchone()[0]
    e = conn.execute("SELECT COUNT(*) FROM users WHERE status = 'waiting_admin'").fetchone()[0]
    b = conn.execute("SELECT COUNT(*) FROM users WHERE status = 'blocked'").fetchone()[0]
    conn.close()
    hora_actual = datetime.datetime.now().hour
    timeline = {"labels": [f"{hora_actual-4}:00", f"{hora_actual-3}:00", f"{hora_actual-2}:00", f"{hora_actual-1}:00", f"{hora_actual}:00"], "activos": [max(0, a-2), max(0, a-1), a+1, a, a], "errores": [0, b, max(0, b-1), 0, b]}
    return jsonify({"success": True, "data": {"labels": ["Total", "Aprobados", "En Espera", "Bloqueados"], "values": [t, a, e, b], "timeline": timeline}})

@app.route('/api/enviar-sugerencia', methods=['POST'])
def recibir_sugerencia():
    conn = get_db_connection()
    conn.execute("INSERT INTO sugerencias (phone, mensaje, fecha) VALUES (?, ?, ?)", (request.json.get('phone'), request.json.get('mensaje'), datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    return jsonify({"success": True})

@app.route('/api/admin/ver-sugerencias', methods=['GET'])
def ver_sugerencias():
    conn = get_db_connection()
    sug = conn.execute("SELECT * FROM sugerencias ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify({"success": True, "data": [{"phone": s['phone'], "mensaje": s['mensaje'], "fecha": s['fecha']} for s in sug]})

if __name__ == '__main__': app.run(host='0.0.0.0', port=5000)