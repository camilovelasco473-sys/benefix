import base64
import csv
import io
import os
import secrets
import sqlite3
from datetime import datetime
from functools import wraps

import qrcode
from flask import Flask, Response, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import psycopg2
    from psycopg2.extras import DictCursor
except ImportError:
    psycopg2 = None
    DictCursor = None


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "benefix-dev-secret")
DEFAULT_DATABASE = (
    os.path.join(os.environ["RENDER_DISK_PATH"], "benefix.db")
    if os.environ.get("RENDER_DISK_PATH")
    else "benefix.db"
)
DATABASE = os.environ.get("DATABASE_PATH", DEFAULT_DATABASE)
DATABASE_URL = os.environ.get("DATABASE_URL")
USING_POSTGRES = bool(DATABASE_URL)
NIVELES = {"Vital": 1, "Gold": 2, "Black": 3}
ESTADOS_MEMBRESIA = ("Activa", "Suspendida", "Vencida")


class PostgresResult:
    def __init__(self, cursor, lastrowid=None):
        self.cursor = cursor
        self.lastrowid = lastrowid

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


class PostgresDB:
    def __init__(self, url):
        if psycopg2 is None:
            raise RuntimeError("psycopg2-binary no esta instalado. Ejecuta pip install -r requirements.txt")
        self.connection = psycopg2.connect(url, cursor_factory=DictCursor)

    def _prepare(self, query):
        query = query.strip()
        conflict = False
        if query.upper().startswith("INSERT OR IGNORE INTO"):
            query = "INSERT INTO" + query[len("INSERT OR IGNORE INTO") :]
            conflict = True

        query = query.replace("?", "%s")
        needs_returning = query.upper().startswith("INSERT INTO") and "RETURNING" not in query.upper()
        if conflict:
            query += " ON CONFLICT DO NOTHING"
        if needs_returning:
            query += " RETURNING id"
        return query, needs_returning

    def execute(self, query, params=()):
        prepared, needs_returning = self._prepare(query)
        cursor = self.connection.cursor()
        cursor.execute(prepared, params)
        lastrowid = None
        if needs_returning:
            row = cursor.fetchone()
            if row:
                lastrowid = row["id"]
        return PostgresResult(cursor, lastrowid)

    def executemany(self, query, rows):
        prepared, _ = self._prepare(query)
        prepared = prepared.replace(" RETURNING id", "")
        cursor = self.connection.cursor()
        cursor.executemany(prepared, rows)
        return PostgresResult(cursor)

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()


def get_db():
    if "db" not in g:
        if USING_POSTGRES:
            g.db = PostgresDB(DATABASE_URL)
        else:
            g.db = sqlite3.connect(DATABASE)
            g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def cerrar_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def crear_tablas_postgres(db):
    statements = [
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            nombre TEXT NOT NULL,
            correo TEXT NOT NULL UNIQUE,
            contrasena TEXT NOT NULL,
            foto TEXT,
            nivel TEXT NOT NULL DEFAULT 'Vital',
            estado TEXT NOT NULL DEFAULT 'Activa',
            rol TEXT NOT NULL DEFAULT 'usuario',
            reset_token TEXT,
            reset_expira TEXT,
            creado_en TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS descuentos (
            id SERIAL PRIMARY KEY,
            nombre TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            categoria TEXT NOT NULL,
            porcentaje INTEGER NOT NULL,
            usos INTEGER NOT NULL DEFAULT 0,
            ahorro_estimado INTEGER NOT NULL DEFAULT 10000,
            nivel_minimo TEXT NOT NULL DEFAULT 'Vital'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS historial (
            id SERIAL PRIMARY KEY,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            accion TEXT NOT NULL,
            fecha TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS comercios (
            id SERIAL PRIMARY KEY,
            nombre TEXT NOT NULL,
            categoria TEXT NOT NULL,
            direccion TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'Activo'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS validaciones (
            id SERIAL PRIMARY KEY,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            comercio_id INTEGER REFERENCES comercios(id),
            descuento_id INTEGER REFERENCES descuentos(id),
            codigo TEXT NOT NULL,
            resultado TEXT NOT NULL,
            fecha TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS favoritos (
            id SERIAL PRIMARY KEY,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            descuento_id INTEGER NOT NULL REFERENCES descuentos(id),
            fecha TEXT NOT NULL,
            UNIQUE(usuario_id, descuento_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS comprobantes (
            id SERIAL PRIMARY KEY,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            descuento_id INTEGER NOT NULL REFERENCES descuentos(id),
            codigo TEXT NOT NULL UNIQUE,
            ahorro INTEGER NOT NULL,
            fecha TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS notificaciones (
            id SERIAL PRIMARY KEY,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            titulo TEXT NOT NULL,
            mensaje TEXT NOT NULL,
            leida INTEGER NOT NULL DEFAULT 0,
            fecha TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS cuentas (
            id SERIAL PRIMARY KEY,
            usuario_id INTEGER NOT NULL UNIQUE REFERENCES usuarios(id),
            saldo INTEGER NOT NULL DEFAULT 150000,
            moneda TEXT NOT NULL DEFAULT 'COP',
            creada_en TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS movimientos (
            id SERIAL PRIMARY KEY,
            cuenta_id INTEGER NOT NULL REFERENCES cuentas(id),
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
            tipo TEXT NOT NULL,
            monto INTEGER NOT NULL,
            descripcion TEXT NOT NULL,
            destinatario TEXT,
            codigo TEXT NOT NULL UNIQUE,
            saldo_final INTEGER NOT NULL,
            fecha TEXT NOT NULL
        )
        """,
    ]
    for statement in statements:
        db.execute(statement)


def init_db():
    db = get_db()
    if USING_POSTGRES:
        crear_tablas_postgres(db)
    else:
        db.executescript(
            """
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            correo TEXT NOT NULL UNIQUE,
            contrasena TEXT NOT NULL,
            foto TEXT,
            nivel TEXT NOT NULL DEFAULT 'Vital',
            estado TEXT NOT NULL DEFAULT 'Activa',
            rol TEXT NOT NULL DEFAULT 'usuario',
            reset_token TEXT,
            reset_expira TEXT,
            creado_en TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS descuentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            categoria TEXT NOT NULL,
            porcentaje INTEGER NOT NULL,
            usos INTEGER NOT NULL DEFAULT 0,
            ahorro_estimado INTEGER NOT NULL DEFAULT 10000,
            nivel_minimo TEXT NOT NULL DEFAULT 'Vital'
        );

        CREATE TABLE IF NOT EXISTS historial (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            accion TEXT NOT NULL,
            fecha TEXT NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS comercios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            categoria TEXT NOT NULL,
            direccion TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'Activo'
        );

        CREATE TABLE IF NOT EXISTS validaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            comercio_id INTEGER,
            descuento_id INTEGER,
            codigo TEXT NOT NULL,
            resultado TEXT NOT NULL,
            fecha TEXT NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
            FOREIGN KEY (comercio_id) REFERENCES comercios(id),
            FOREIGN KEY (descuento_id) REFERENCES descuentos(id)
        );

        CREATE TABLE IF NOT EXISTS favoritos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            descuento_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            UNIQUE(usuario_id, descuento_id),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
            FOREIGN KEY (descuento_id) REFERENCES descuentos(id)
        );

        CREATE TABLE IF NOT EXISTS comprobantes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            descuento_id INTEGER NOT NULL,
            codigo TEXT NOT NULL UNIQUE,
            ahorro INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
            FOREIGN KEY (descuento_id) REFERENCES descuentos(id)
        );

        CREATE TABLE IF NOT EXISTS notificaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            titulo TEXT NOT NULL,
            mensaje TEXT NOT NULL,
            leida INTEGER NOT NULL DEFAULT 0,
            fecha TEXT NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS cuentas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL UNIQUE,
            saldo INTEGER NOT NULL DEFAULT 150000,
            moneda TEXT NOT NULL DEFAULT 'COP',
            creada_en TEXT NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        );

        CREATE TABLE IF NOT EXISTS movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cuenta_id INTEGER NOT NULL,
            usuario_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            monto INTEGER NOT NULL,
            descripcion TEXT NOT NULL,
            destinatario TEXT,
            codigo TEXT NOT NULL UNIQUE,
            saldo_final INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            FOREIGN KEY (cuenta_id) REFERENCES cuentas(id),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        );
        """
        )
    asegurar_columna("usuarios", "estado", "TEXT NOT NULL DEFAULT 'Activa'")
    asegurar_columna("usuarios", "reset_token", "TEXT")
    asegurar_columna("usuarios", "reset_expira", "TEXT")
    asegurar_columna("descuentos", "ahorro_estimado", "INTEGER NOT NULL DEFAULT 10000")
    asegurar_columna("descuentos", "nivel_minimo", "TEXT NOT NULL DEFAULT 'Vital'")
    db.commit()
    seed_data()


def asegurar_columna(tabla, columna, definicion):
    db = get_db()
    if USING_POSTGRES:
        columnas = [
            fila["column_name"]
            for fila in db.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = ?
                """,
                (tabla,),
            ).fetchall()
        ]
    else:
        columnas = [fila["name"] for fila in db.execute(f"PRAGMA table_info({tabla})").fetchall()]
    if columna not in columnas:
        db.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")


def seed_data():
    db = get_db()
    total_usuarios = db.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
    if total_usuarios == 0:
        cursor = db.execute(
            """
            INSERT INTO usuarios (nombre, correo, contrasena, nivel, rol, creado_en)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "Administrador Benefix",
                "admin@benefix.com",
                generate_password_hash("admin123"),
                "Black",
                "admin",
                ahora(),
            ),
        )
        db.commit()
        asegurar_cuenta(cursor.lastrowid)

    total_descuentos = db.execute("SELECT COUNT(*) FROM descuentos").fetchone()[0]
    if total_descuentos == 0:
        db.executemany(
            """
            INSERT INTO descuentos (nombre, descripcion, categoria, porcentaje, usos, ahorro_estimado, nivel_minimo)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("Farmacia Salud", "Medicamentos y vitaminas seleccionadas.", "Salud", 20, 7, 18500, "Vital"),
                ("Supermercado Ahorro", "Compras mayores a $80.000.", "Mercado", 15, 5, 24000, "Vital"),
                ("Gimnasio Vital", "Primer mes con tarifa especial.", "Bienestar", 30, 3, 36000, "Gold"),
                ("Optica Clara", "Monturas y lentes formulados.", "Vision", 18, 4, 22000, "Black"),
            ],
        )

    total_comercios = db.execute("SELECT COUNT(*) FROM comercios").fetchone()[0]
    if total_comercios == 0:
        db.executemany(
            """
            INSERT INTO comercios (nombre, categoria, direccion, estado)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("Farmacia Salud", "Salud", "Centro comercial principal", "Activo"),
                ("Supermercado Ahorro", "Mercado", "Avenida 12 #45-20", "Activo"),
                ("Gimnasio Vital", "Bienestar", "Zona deportiva local 3", "Activo"),
            ],
        )
    db.commit()


def ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def registrar_historial(usuario_id, accion):
    db = get_db()
    db.execute(
        "INSERT INTO historial (usuario_id, accion, fecha) VALUES (?, ?, ?)",
        (usuario_id, accion, ahora()),
    )
    db.commit()


def crear_notificacion(usuario_id, titulo, mensaje):
    db = get_db()
    db.execute(
        """
        INSERT INTO notificaciones (usuario_id, titulo, mensaje, fecha)
        VALUES (?, ?, ?, ?)
        """,
        (usuario_id, titulo, mensaje, ahora()),
    )
    db.commit()


def asegurar_cuenta(usuario_id):
    db = get_db()
    cuenta = db.execute("SELECT * FROM cuentas WHERE usuario_id = ?", (usuario_id,)).fetchone()
    if cuenta:
        return cuenta

    cursor = db.execute(
        """
        INSERT INTO cuentas (usuario_id, saldo, moneda, creada_en)
        VALUES (?, ?, ?, ?)
        """,
        (usuario_id, 150000, "COP", ahora()),
    )
    cuenta_id = cursor.lastrowid
    codigo = f"MOV-{datetime.now().strftime('%Y%m%d%H%M%S')}-{usuario_id}-INI"
    db.execute(
        """
        INSERT INTO movimientos
        (cuenta_id, usuario_id, tipo, monto, descripcion, destinatario, codigo, saldo_final, fecha)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cuenta_id,
            usuario_id,
            "Ingreso",
            150000,
            "Saldo inicial de bienvenida",
            "Benefix",
            codigo,
            150000,
            ahora(),
        ),
    )
    db.commit()
    return db.execute("SELECT * FROM cuentas WHERE id = ?", (cuenta_id,)).fetchone()


def nivel_permitido(usuario_nivel, nivel_minimo):
    return NIVELES.get(usuario_nivel, 0) >= NIVELES.get(nivel_minimo, 1)


def membresia_activa(usuario):
    return usuario and usuario["estado"] == "Activa"


def usuario_actual():
    usuario_id = session.get("usuario_id")
    if not usuario_id:
        return None
    return get_db().execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()


def login_requerido(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("usuario_id"):
            return redirect(url_for("login"))
        return func(*args, **kwargs)

    return wrapper


def admin_requerido(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        usuario = usuario_actual()
        if not usuario or usuario["rol"] != "admin":
            return redirect(url_for("dashboard"))
        return func(*args, **kwargs)

    return wrapper


def generar_qr(texto):
    qr = qrcode.QRCode(box_size=7, border=2)
    qr.add_data(texto)
    qr.make(fit=True)
    imagen = qr.make_image(fill_color="#12302a", back_color="white")
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("utf-8")


def codigo_usuario(usuario_id):
    return f"BF-{usuario_id:04d}"


def extraer_usuario_por_codigo(codigo):
    codigo = codigo.strip()
    if not codigo:
        return None

    if "usuario:" in codigo:
        codigo = codigo.split("usuario:", 1)[1].split("|", 1)[0]

    if codigo.upper().startswith("BF-"):
        codigo = codigo.split("-", 1)[1]

    db = get_db()
    if codigo.isdigit():
        return db.execute("SELECT * FROM usuarios WHERE id = ?", (int(codigo),)).fetchone()

    return db.execute("SELECT * FROM usuarios WHERE correo = ?", (codigo.lower(),)).fetchone()


def buscar_usuario_destinatario(valor):
    valor = valor.strip()
    if not valor:
        return None
    return extraer_usuario_por_codigo(valor)


@app.context_processor
def inyectar_usuario():
    usuario = usuario_actual()
    pendientes = 0
    if usuario:
        pendientes = get_db().execute(
            "SELECT COUNT(*) FROM notificaciones WHERE usuario_id = ? AND leida = 0",
            (usuario["id"],),
        ).fetchone()[0]
    return {"usuario_sesion": usuario, "notificaciones_pendientes": pendientes}


@app.route("/")
def inicio():
    if session.get("usuario_id"):
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/registro", methods=["GET", "POST"])
def registro():
    error = None
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        correo = request.form.get("correo", "").strip().lower()
        contrasena = request.form.get("password", "").strip()

        if not nombre or not correo or not contrasena:
            error = "Completa todos los campos."
        else:
            db = get_db()
            existe = db.execute("SELECT id FROM usuarios WHERE correo = ?", (correo,)).fetchone()
            if existe:
                error = "Este correo ya esta registrado."
            else:
                cursor = db.execute(
                    """
                    INSERT INTO usuarios (nombre, correo, contrasena, creado_en)
                    VALUES (?, ?, ?, ?)
                    """,
                    (nombre, correo, generate_password_hash(contrasena), ahora()),
                )
                db.commit()
                session["usuario_id"] = cursor.lastrowid
                asegurar_cuenta(cursor.lastrowid)
                registrar_historial(cursor.lastrowid, "Creo su cuenta Benefix")
                return redirect(url_for("dashboard"))

    return render_template("registro.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        correo = request.form.get("correo", "").strip().lower()
        contrasena = request.form.get("password", "").strip()
        usuario = get_db().execute("SELECT * FROM usuarios WHERE correo = ?", (correo,)).fetchone()

        if usuario and check_password_hash(usuario["contrasena"], contrasena):
            if usuario["estado"] != "Activa" and usuario["rol"] != "admin":
                error = f"Tu membresia esta {usuario['estado'].lower()}. Contacta al administrador."
                return render_template("login.html", error=error)
            session.clear()
            session["usuario_id"] = usuario["id"]
            asegurar_cuenta(usuario["id"])
            registrar_historial(usuario["id"], "Inicio sesion")
            return redirect(url_for("dashboard"))

        error = "Correo o contrasena incorrectos."

    return render_template("login.html", error=error)


@app.route("/dashboard")
@login_requerido
def dashboard():
    db = get_db()
    usuario = usuario_actual()
    descuentos = [
        item
        for item in db.execute("SELECT * FROM descuentos ORDER BY porcentaje DESC").fetchall()
        if nivel_permitido(usuario["nivel"], item["nivel_minimo"])
    ][:3]
    historial = db.execute(
        "SELECT * FROM historial WHERE usuario_id = ? ORDER BY fecha DESC LIMIT 5",
        (usuario["id"],),
    ).fetchall()
    stats = {
        "descuentos": db.execute("SELECT COUNT(*) FROM descuentos").fetchone()[0],
        "usos": db.execute("SELECT COALESCE(SUM(usos), 0) FROM descuentos").fetchone()[0],
        "actividad": db.execute(
            "SELECT COUNT(*) FROM historial WHERE usuario_id = ?", (usuario["id"],)
        ).fetchone()[0],
        "ahorro": db.execute(
            "SELECT COALESCE(SUM(ahorro), 0) FROM comprobantes WHERE usuario_id = ?",
            (usuario["id"],),
        ).fetchone()[0],
        "saldo": asegurar_cuenta(usuario["id"])["saldo"],
    }
    codigo = codigo_usuario(usuario["id"])
    qr = generar_qr(f"BENEFIX|usuario:{usuario['id']}|codigo:{codigo}|correo:{usuario['correo']}")
    return render_template(
        "dashboard.html",
        usuario=usuario,
        descuentos=descuentos,
        historial=historial,
        stats=stats,
        qr=qr,
        codigo=codigo,
    )


@app.route("/descuentos")
@login_requerido
def descuentos():
    db = get_db()
    usuario = usuario_actual()
    descuentos_lista = db.execute("SELECT * FROM descuentos ORDER BY porcentaje DESC").fetchall()
    favoritos = db.execute(
        "SELECT descuento_id FROM favoritos WHERE usuario_id = ?", (session["usuario_id"],)
    ).fetchall()
    favoritos_ids = {fila["descuento_id"] for fila in favoritos}
    return render_template(
        "descuentos.html",
        descuentos=descuentos_lista,
        favoritos_ids=favoritos_ids,
        usuario=usuario,
        nivel_permitido=nivel_permitido,
    )


@app.route("/usar-descuento/<int:descuento_id>", methods=["POST"])
@login_requerido
def usar_descuento(descuento_id):
    db = get_db()
    usuario = usuario_actual()
    descuento = db.execute("SELECT * FROM descuentos WHERE id = ?", (descuento_id,)).fetchone()
    if descuento:
        if not membresia_activa(usuario) or not nivel_permitido(usuario["nivel"], descuento["nivel_minimo"]):
            crear_notificacion(
                usuario["id"],
                "Beneficio no disponible",
                "Tu membresia no cumple las condiciones para usar este descuento.",
            )
            return redirect(url_for("descuentos"))
        db.execute("UPDATE descuentos SET usos = usos + 1 WHERE id = ?", (descuento_id,))
        codigo = f"OP-{datetime.now().strftime('%Y%m%d%H%M%S')}-{session['usuario_id']}-{descuento_id}"
        cursor = db.execute(
            """
            INSERT INTO comprobantes (usuario_id, descuento_id, codigo, ahorro, fecha)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session["usuario_id"],
                descuento_id,
                codigo,
                descuento["ahorro_estimado"],
                ahora(),
            ),
        )
        db.commit()
        registrar_historial(session["usuario_id"], f"Uso descuento en {descuento['nombre']}")
        crear_notificacion(
            session["usuario_id"],
            "Descuento usado",
            f"Generamos tu comprobante por {descuento['nombre']}.",
        )
        return redirect(url_for("comprobante", comprobante_id=cursor.lastrowid))
    return redirect(url_for("descuentos"))


@app.route("/favorito/<int:descuento_id>", methods=["POST"])
@login_requerido
def favorito(descuento_id):
    db = get_db()
    existe = db.execute(
        "SELECT id FROM favoritos WHERE usuario_id = ? AND descuento_id = ?",
        (session["usuario_id"], descuento_id),
    ).fetchone()
    if existe:
        db.execute("DELETE FROM favoritos WHERE id = ?", (existe["id"],))
        accion = "Quito un descuento de favoritos"
    else:
        db.execute(
            "INSERT OR IGNORE INTO favoritos (usuario_id, descuento_id, fecha) VALUES (?, ?, ?)",
            (session["usuario_id"], descuento_id, ahora()),
        )
        accion = "Agrego un descuento a favoritos"
    db.commit()
    registrar_historial(session["usuario_id"], accion)
    return redirect(url_for("descuentos"))


@app.route("/comprobante/<int:comprobante_id>")
@login_requerido
def comprobante(comprobante_id):
    comprobante_item = get_db().execute(
        """
        SELECT comprobantes.*, descuentos.nombre AS descuento, descuentos.porcentaje,
               descuentos.categoria, usuarios.nombre AS usuario, usuarios.correo
        FROM comprobantes
        JOIN descuentos ON descuentos.id = comprobantes.descuento_id
        JOIN usuarios ON usuarios.id = comprobantes.usuario_id
        WHERE comprobantes.id = ?
        """,
        (comprobante_id,),
    ).fetchone()
    usuario = usuario_actual()
    if not comprobante_item or (
        comprobante_item["usuario_id"] != usuario["id"] and usuario["rol"] != "admin"
    ):
        return redirect(url_for("dashboard"))
    return render_template("comprobante.html", comprobante=comprobante_item)


@app.route("/mi-benefix")
@login_requerido
def mi_benefix():
    db = get_db()
    usuario = usuario_actual()
    comprobantes = db.execute(
        """
        SELECT comprobantes.*, descuentos.nombre AS descuento, descuentos.porcentaje
        FROM comprobantes
        JOIN descuentos ON descuentos.id = comprobantes.descuento_id
        WHERE comprobantes.usuario_id = ?
        ORDER BY comprobantes.fecha DESC
        LIMIT 8
        """,
        (usuario["id"],),
    ).fetchall()
    favoritos = db.execute(
        """
        SELECT descuentos.*
        FROM favoritos
        JOIN descuentos ON descuentos.id = favoritos.descuento_id
        WHERE favoritos.usuario_id = ?
        ORDER BY favoritos.fecha DESC
        """,
        (usuario["id"],),
    ).fetchall()
    stats = {
        "ahorro": db.execute(
            "SELECT COALESCE(SUM(ahorro), 0) FROM comprobantes WHERE usuario_id = ?",
            (usuario["id"],),
        ).fetchone()[0],
        "usados": db.execute(
            "SELECT COUNT(*) FROM comprobantes WHERE usuario_id = ?", (usuario["id"],)
        ).fetchone()[0],
        "favoritos": len(favoritos),
        "actividad": db.execute(
            "SELECT COUNT(*) FROM historial WHERE usuario_id = ?", (usuario["id"],)
        ).fetchone()[0],
    }
    codigo = codigo_usuario(usuario["id"])
    qr = generar_qr(f"BENEFIX|usuario:{usuario['id']}|codigo:{codigo}|correo:{usuario['correo']}")
    return render_template(
        "mi_benefix.html",
        usuario=usuario,
        codigo=codigo,
        qr=qr,
        comprobantes=comprobantes,
        favoritos=favoritos,
        stats=stats,
    )


@app.route("/notificaciones")
@login_requerido
def notificaciones():
    db = get_db()
    items = db.execute(
        "SELECT * FROM notificaciones WHERE usuario_id = ? ORDER BY fecha DESC",
        (session["usuario_id"],),
    ).fetchall()
    db.execute("UPDATE notificaciones SET leida = 1 WHERE usuario_id = ?", (session["usuario_id"],))
    db.commit()
    return render_template("notificaciones.html", notificaciones=items)


@app.route("/billetera", methods=["GET", "POST"])
@login_requerido
def billetera():
    db = get_db()
    usuario = usuario_actual()
    cuenta = asegurar_cuenta(usuario["id"])
    error = None

    if request.method == "POST":
        tipo = request.form.get("tipo", "Pago")
        monto_texto = request.form.get("monto", "0").strip()
        descripcion = request.form.get("descripcion", "").strip()
        destinatario = request.form.get("destinatario", "").strip()

        if tipo not in ("Ingreso", "Pago", "Transferencia"):
            error = "Tipo de movimiento no valido."
        elif not monto_texto.isdigit() or int(monto_texto) <= 0:
            error = "Ingresa un monto valido."
        elif not descripcion:
            error = "La descripcion es obligatoria."
        elif tipo == "Transferencia" and not destinatario:
            error = "Para transferir debes escribir el correo o codigo Benefix del destinatario."
        else:
            monto = int(monto_texto)
            saldo_actual = cuenta["saldo"]
            es_salida = tipo in ("Pago", "Transferencia")
            usuario_destino = buscar_usuario_destinatario(destinatario) if tipo == "Transferencia" else None

            if tipo == "Transferencia" and not usuario_destino:
                error = "No encontramos un usuario Benefix con ese correo o codigo."
            elif tipo == "Transferencia" and usuario_destino["id"] == usuario["id"]:
                error = "No puedes transferirte a tu misma cuenta."
            elif es_salida and monto > saldo_actual:
                error = "Saldo insuficiente para realizar esta transaccion."
            else:
                saldo_final = saldo_actual - monto if es_salida else saldo_actual + monto
                codigo = f"MOV-{datetime.now().strftime('%Y%m%d%H%M%S')}-{usuario['id']}-{secrets.token_hex(3).upper()}"
                cursor = db.execute(
                    """
                    INSERT INTO movimientos
                    (cuenta_id, usuario_id, tipo, monto, descripcion, destinatario, codigo, saldo_final, fecha)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cuenta["id"],
                        usuario["id"],
                        tipo,
                        monto,
                        descripcion,
                        usuario_destino["correo"] if usuario_destino else destinatario or None,
                        codigo,
                        saldo_final,
                        ahora(),
                    ),
                )
                db.execute("UPDATE cuentas SET saldo = ? WHERE id = ?", (saldo_final, cuenta["id"]))

                if tipo == "Transferencia":
                    cuenta_destino = asegurar_cuenta(usuario_destino["id"])
                    saldo_destino = cuenta_destino["saldo"] + monto
                    codigo_destino = f"MOV-{datetime.now().strftime('%Y%m%d%H%M%S')}-{usuario_destino['id']}-{secrets.token_hex(3).upper()}"
                    db.execute(
                        """
                        INSERT INTO movimientos
                        (cuenta_id, usuario_id, tipo, monto, descripcion, destinatario, codigo, saldo_final, fecha)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cuenta_destino["id"],
                            usuario_destino["id"],
                            "Ingreso",
                            monto,
                            f"Transferencia recibida de {usuario['nombre']}",
                            usuario["correo"],
                            codigo_destino,
                            saldo_destino,
                            ahora(),
                        ),
                    )
                    db.execute("UPDATE cuentas SET saldo = ? WHERE id = ?", (saldo_destino, cuenta_destino["id"]))
                    registrar_historial(usuario_destino["id"], f"Recibio transferencia de {usuario['nombre']}")
                    crear_notificacion(
                        usuario_destino["id"],
                        "Transferencia recibida",
                        f"Recibiste ${monto:,} de {usuario['nombre']}.",
                    )

                db.commit()
                registrar_historial(usuario["id"], f"Realizo movimiento de billetera: {tipo}")
                crear_notificacion(
                    usuario["id"],
                    "Movimiento registrado",
                    f"Se genero un recibo por {tipo.lower()} de ${monto:,}.",
                )
                return redirect(url_for("recibo_movimiento", movimiento_id=cursor.lastrowid))

    cuenta = asegurar_cuenta(usuario["id"])
    movimientos = db.execute(
        """
        SELECT * FROM movimientos
        WHERE usuario_id = ?
        ORDER BY fecha DESC, id DESC
        LIMIT 12
        """,
        (usuario["id"],),
    ).fetchall()
    return render_template("billetera.html", cuenta=cuenta, movimientos=movimientos, error=error)


@app.route("/movimiento/<int:movimiento_id>")
@login_requerido
def recibo_movimiento(movimiento_id):
    usuario = usuario_actual()
    movimiento = get_db().execute(
        """
        SELECT movimientos.*, usuarios.nombre AS usuario, usuarios.correo
        FROM movimientos
        JOIN usuarios ON usuarios.id = movimientos.usuario_id
        WHERE movimientos.id = ?
        """,
        (movimiento_id,),
    ).fetchone()
    if not movimiento or (movimiento["usuario_id"] != usuario["id"] and usuario["rol"] != "admin"):
        return redirect(url_for("dashboard"))
    return render_template("recibo_movimiento.html", movimiento=movimiento)


@app.route("/historial")
@login_requerido
def historial():
    registros = get_db().execute(
        "SELECT * FROM historial WHERE usuario_id = ? ORDER BY fecha DESC",
        (session["usuario_id"],),
    ).fetchall()
    return render_template("historial.html", historial=registros)


@app.route("/perfil", methods=["GET", "POST"])
@login_requerido
def perfil():
    usuario = usuario_actual()
    mensaje = None
    error = None
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        correo = request.form.get("correo", "").strip().lower()
        foto = request.form.get("foto", "").strip()
        contrasena = request.form.get("password", "").strip()

        if not nombre or not correo:
            error = "Nombre y correo son obligatorios."
        else:
            db = get_db()
            existe = db.execute(
                "SELECT id FROM usuarios WHERE correo = ? AND id != ?",
                (correo, usuario["id"]),
            ).fetchone()
            if existe:
                error = "Ese correo ya pertenece a otro usuario."
            else:
                if contrasena:
                    db.execute(
                        """
                        UPDATE usuarios
                        SET nombre = ?, correo = ?, foto = ?, contrasena = ?
                        WHERE id = ?
                        """,
                        (nombre, correo, foto, generate_password_hash(contrasena), usuario["id"]),
                    )
                    registrar_historial(usuario["id"], "Actualizo su perfil y contrasena")
                else:
                    db.execute(
                        "UPDATE usuarios SET nombre = ?, correo = ?, foto = ? WHERE id = ?",
                        (nombre, correo, foto, usuario["id"]),
                    )
                    registrar_historial(usuario["id"], "Actualizo su perfil")
                db.commit()
                mensaje = "Perfil actualizado correctamente."
                usuario = usuario_actual()

    return render_template("perfil.html", usuario=usuario, mensaje=mensaje, error=error)


@app.route("/validar", methods=["GET", "POST"])
@login_requerido
@admin_requerido
def validar():
    db = get_db()
    codigo = request.values.get("codigo", "").strip()
    usuario = extraer_usuario_por_codigo(codigo) if codigo else None
    mensaje = None
    error = None

    if request.method == "POST":
        comercio_id = request.form.get("comercio_id") or None
        descuento_id = request.form.get("descuento_id") or None
        if not usuario:
            error = "No se encontro una membresia activa con ese codigo."
        elif not membresia_activa(usuario):
            error = f"La membresia existe, pero esta {usuario['estado'].lower()}."
        else:
            db.execute(
                """
                INSERT INTO validaciones
                (usuario_id, comercio_id, descuento_id, codigo, resultado, fecha)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (usuario["id"], comercio_id, descuento_id, codigo, "Aprobada", ahora()),
            )
            if descuento_id:
                db.execute("UPDATE descuentos SET usos = usos + 1 WHERE id = ?", (descuento_id,))
            db.commit()
            registrar_historial(usuario["id"], "Membresia validada por administrador")
            mensaje = "Validacion aprobada y registrada correctamente."

    comercios = db.execute("SELECT * FROM comercios ORDER BY nombre").fetchall()
    descuentos_lista = db.execute("SELECT * FROM descuentos ORDER BY nombre").fetchall()
    validaciones = db.execute(
        """
        SELECT validaciones.*, usuarios.nombre AS usuario, comercios.nombre AS comercio,
               descuentos.nombre AS descuento
        FROM validaciones
        JOIN usuarios ON usuarios.id = validaciones.usuario_id
        LEFT JOIN comercios ON comercios.id = validaciones.comercio_id
        LEFT JOIN descuentos ON descuentos.id = validaciones.descuento_id
        ORDER BY validaciones.fecha DESC
        LIMIT 10
        """
    ).fetchall()
    return render_template(
        "validar.html",
        codigo=codigo,
        usuario=usuario,
        mensaje=mensaje,
        error=error,
        comercios=comercios,
        descuentos=descuentos_lista,
        validaciones=validaciones,
    )


@app.route("/admin", methods=["GET", "POST"])
@login_requerido
@admin_requerido
def admin():
    db = get_db()
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        descripcion = request.form.get("descripcion", "").strip()
        categoria = request.form.get("categoria", "").strip()
        porcentaje = request.form.get("porcentaje", "0").strip()
        ahorro_estimado = request.form.get("ahorro_estimado", "10000").strip()
        nivel_minimo = request.form.get("nivel_minimo", "Vital").strip()
        if nombre and descripcion and categoria and porcentaje.isdigit():
            db.execute(
                """
                INSERT INTO descuentos (nombre, descripcion, categoria, porcentaje, ahorro_estimado, nivel_minimo)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    nombre,
                    descripcion,
                    categoria,
                    int(porcentaje),
                    int(ahorro_estimado) if ahorro_estimado.isdigit() else 10000,
                    nivel_minimo if nivel_minimo in NIVELES else "Vital",
                ),
            )
            db.commit()
            registrar_historial(session["usuario_id"], f"Admin agrego descuento {nombre}")
        return redirect(url_for("admin"))

    usuarios = db.execute("SELECT * FROM usuarios ORDER BY creado_en DESC").fetchall()
    descuentos_lista = db.execute("SELECT * FROM descuentos ORDER BY usos DESC").fetchall()
    comercios = db.execute("SELECT * FROM comercios ORDER BY nombre").fetchall()
    actividad = db.execute(
        """
        SELECT historial.*, usuarios.nombre
        FROM historial
        JOIN usuarios ON usuarios.id = historial.usuario_id
        ORDER BY historial.fecha DESC
        LIMIT 8
        """
    ).fetchall()
    stats = {
        "usuarios": db.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0],
        "descuentos": db.execute("SELECT COUNT(*) FROM descuentos").fetchone()[0],
        "usos": db.execute("SELECT COALESCE(SUM(usos), 0) FROM descuentos").fetchone()[0],
        "comercios": db.execute("SELECT COUNT(*) FROM comercios").fetchone()[0],
        "validaciones": db.execute("SELECT COUNT(*) FROM validaciones").fetchone()[0],
    }
    return render_template(
        "admin.html",
        usuarios=usuarios,
        descuentos=descuentos_lista,
        comercios=comercios,
        actividad=actividad,
        stats=stats,
        niveles=list(NIVELES.keys()),
        estados=ESTADOS_MEMBRESIA,
    )


@app.route("/admin/usuario/<int:usuario_id>/membresia", methods=["POST"])
@login_requerido
@admin_requerido
def actualizar_membresia(usuario_id):
    nivel = request.form.get("nivel", "Vital")
    estado = request.form.get("estado", "Activa")
    if nivel not in NIVELES:
        nivel = "Vital"
    if estado not in ESTADOS_MEMBRESIA:
        estado = "Activa"

    db = get_db()
    db.execute("UPDATE usuarios SET nivel = ?, estado = ? WHERE id = ?", (nivel, estado, usuario_id))
    db.commit()
    registrar_historial(session["usuario_id"], f"Admin actualizo membresia de usuario {usuario_id}")
    crear_notificacion(
        usuario_id,
        "Membresia actualizada",
        f"Tu membresia ahora esta en nivel {nivel} con estado {estado}.",
    )
    return redirect(url_for("admin"))


@app.route("/admin/comercios", methods=["POST"])
@login_requerido
@admin_requerido
def agregar_comercio():
    nombre = request.form.get("nombre", "").strip()
    categoria = request.form.get("categoria", "").strip()
    direccion = request.form.get("direccion", "").strip()
    estado = request.form.get("estado", "Activo").strip()
    if nombre and categoria and direccion:
        db = get_db()
        db.execute(
            """
            INSERT INTO comercios (nombre, categoria, direccion, estado)
            VALUES (?, ?, ?, ?)
            """,
            (nombre, categoria, direccion, estado),
        )
        db.commit()
        registrar_historial(session["usuario_id"], f"Admin agrego comercio {nombre}")
    return redirect(url_for("admin"))


@app.route("/reportes")
@login_requerido
@admin_requerido
def reportes():
    db = get_db()
    descuentos_top = db.execute(
        "SELECT nombre, usos FROM descuentos ORDER BY usos DESC LIMIT 6"
    ).fetchall()
    validaciones_comercio = db.execute(
        """
        SELECT COALESCE(comercios.nombre, 'Sin comercio') AS nombre, COUNT(validaciones.id) AS total
        FROM validaciones
        LEFT JOIN comercios ON comercios.id = validaciones.comercio_id
        GROUP BY COALESCE(comercios.nombre, 'Sin comercio')
        ORDER BY total DESC
        LIMIT 6
        """
    ).fetchall()
    actividad_dia = db.execute(
        """
        SELECT substr(fecha, 1, 10) AS dia, COUNT(id) AS total
        FROM historial
        GROUP BY substr(fecha, 1, 10)
        ORDER BY dia DESC
        LIMIT 7
        """
    ).fetchall()
    validaciones = db.execute(
        """
        SELECT validaciones.*, usuarios.nombre AS usuario, comercios.nombre AS comercio,
               descuentos.nombre AS descuento
        FROM validaciones
        JOIN usuarios ON usuarios.id = validaciones.usuario_id
        LEFT JOIN comercios ON comercios.id = validaciones.comercio_id
        LEFT JOIN descuentos ON descuentos.id = validaciones.descuento_id
        ORDER BY validaciones.fecha DESC
        LIMIT 12
        """
    ).fetchall()
    stats = {
        "usuarios": db.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0],
        "descuentos": db.execute("SELECT COUNT(*) FROM descuentos").fetchone()[0],
        "comercios": db.execute("SELECT COUNT(*) FROM comercios").fetchone()[0],
        "validaciones": db.execute("SELECT COUNT(*) FROM validaciones").fetchone()[0],
    }
    return render_template(
        "reportes.html",
        descuentos_top=descuentos_top,
        validaciones_comercio=validaciones_comercio,
        actividad_dia=list(reversed(actividad_dia)),
        validaciones=validaciones,
        stats=stats,
    )


@app.route("/reportes/exportar")
@login_requerido
@admin_requerido
def exportar_reporte():
    db = get_db()
    filas = db.execute(
        """
        SELECT validaciones.fecha, validaciones.codigo, validaciones.resultado,
               usuarios.nombre AS usuario, usuarios.correo,
               COALESCE(comercios.nombre, 'Sin comercio') AS comercio,
               COALESCE(descuentos.nombre, 'Sin descuento') AS descuento
        FROM validaciones
        JOIN usuarios ON usuarios.id = validaciones.usuario_id
        LEFT JOIN comercios ON comercios.id = validaciones.comercio_id
        LEFT JOIN descuentos ON descuentos.id = validaciones.descuento_id
        ORDER BY validaciones.fecha DESC
        """
    ).fetchall()
    salida = io.StringIO()
    writer = csv.writer(salida)
    writer.writerow(["fecha", "codigo", "resultado", "usuario", "correo", "comercio", "descuento"])
    for fila in filas:
        writer.writerow(
            [
                fila["fecha"],
                fila["codigo"],
                fila["resultado"],
                fila["usuario"],
                fila["correo"],
                fila["comercio"],
                fila["descuento"],
            ]
        )
    return Response(
        salida.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=benefix_validaciones.csv"},
    )


@app.route("/admin/eliminar-usuario/<int:usuario_id>", methods=["POST"])
@login_requerido
@admin_requerido
def eliminar_usuario(usuario_id):
    if usuario_id != session["usuario_id"]:
        db = get_db()
        db.execute("DELETE FROM historial WHERE usuario_id = ?", (usuario_id,))
        db.execute("DELETE FROM validaciones WHERE usuario_id = ?", (usuario_id,))
        db.execute("DELETE FROM movimientos WHERE usuario_id = ?", (usuario_id,))
        db.execute("DELETE FROM cuentas WHERE usuario_id = ?", (usuario_id,))
        db.execute("DELETE FROM favoritos WHERE usuario_id = ?", (usuario_id,))
        db.execute("DELETE FROM comprobantes WHERE usuario_id = ?", (usuario_id,))
        db.execute("DELETE FROM notificaciones WHERE usuario_id = ?", (usuario_id,))
        db.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
        db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/eliminar-descuento/<int:descuento_id>", methods=["POST"])
@login_requerido
@admin_requerido
def eliminar_descuento(descuento_id):
    db = get_db()
    db.execute("UPDATE validaciones SET descuento_id = NULL WHERE descuento_id = ?", (descuento_id,))
    db.execute("DELETE FROM descuentos WHERE id = ?", (descuento_id,))
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/eliminar-comercio/<int:comercio_id>", methods=["POST"])
@login_requerido
@admin_requerido
def eliminar_comercio(comercio_id):
    db = get_db()
    db.execute("UPDATE validaciones SET comercio_id = NULL WHERE comercio_id = ?", (comercio_id,))
    db.execute("DELETE FROM comercios WHERE id = ?", (comercio_id,))
    db.commit()
    return redirect(url_for("admin"))


@app.route("/recuperar", methods=["GET", "POST"])
def recuperar():
    mensaje = None
    enlace = None
    if request.method == "POST":
        correo = request.form.get("correo", "").strip().lower()
        db = get_db()
        usuario = db.execute("SELECT id FROM usuarios WHERE correo = ?", (correo,)).fetchone()
        if usuario:
            token = secrets.token_urlsafe(32)
            expira = datetime.now().timestamp() + 3600
            db.execute(
                "UPDATE usuarios SET reset_token = ?, reset_expira = ? WHERE id = ?",
                (token, str(expira), usuario["id"]),
            )
            db.commit()
            registrar_historial(usuario["id"], "Solicito recuperacion de contrasena")
            enlace = url_for("restablecer", token=token, _external=True)
        mensaje = "Si el correo existe, se genero un enlace de recuperacion."
    return render_template("recuperar.html", mensaje=mensaje, enlace=enlace)


@app.route("/restablecer/<token>", methods=["GET", "POST"])
def restablecer(token):
    db = get_db()
    usuario = db.execute("SELECT * FROM usuarios WHERE reset_token = ?", (token,)).fetchone()
    error = None
    mensaje = None

    if not usuario:
        error = "El enlace de recuperacion no es valido."
    elif float(usuario["reset_expira"] or 0) < datetime.now().timestamp():
        error = "El enlace de recuperacion ya vencio."

    if request.method == "POST" and not error:
        contrasena = request.form.get("password", "").strip()
        confirmar = request.form.get("confirmar", "").strip()
        if len(contrasena) < 6:
            error = "La contrasena debe tener al menos 6 caracteres."
        elif contrasena != confirmar:
            error = "Las contrasenas no coinciden."
        else:
            db.execute(
                """
                UPDATE usuarios
                SET contrasena = ?, reset_token = NULL, reset_expira = NULL
                WHERE id = ?
                """,
                (generate_password_hash(contrasena), usuario["id"]),
            )
            db.commit()
            registrar_historial(usuario["id"], "Restablecio su contrasena")
            mensaje = "Contrasena actualizada. Ya puedes iniciar sesion."

    return render_template("restablecer.html", error=error, mensaje=mensaje)


@app.route("/logout")
@login_requerido
def logout():
    registrar_historial(session["usuario_id"], "Cerro sesion")
    session.clear()
    return redirect(url_for("inicio"))


with app.app_context():
    init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
