# Benefix

Aplicacion web con Flask, SQLite, sesiones reales, dashboard, QR y panel administrador.

## Usuario administrador

```text
correo: admin@benefix.com
contrasena: admin123
```

## Funciones principales

- Login y registro con sesiones.
- Contrasenas cifradas con Werkzeug.
- Base de datos SQLite con tablas de usuarios, descuentos e historial.
- Dashboard moderno con tarjeta digital y codigo QR.
- Historial dinamico de actividad.
- Perfil editable.
- Panel administrador para ver usuarios, eliminar usuarios y agregar descuentos.
- Modulo de comercios aliados.
- Validacion de membresia por codigo QR o codigo de tarjeta.
- Registro de validaciones realizadas en punto de venta.
- Reportes administrativos con graficas.
- Exportacion CSV de validaciones.
- Vista Mi Benefix para el usuario.
- Favoritos, comprobantes de uso y ahorro estimado.
- Notificaciones internas para eventos importantes.
- Recuperacion de contrasena simulada.

## Como ejecutarla

1. Instala Python 3 desde <https://www.python.org/downloads/> y marca la opcion **Add python.exe to PATH** durante la instalacion.
2. Abre PowerShell en esta carpeta:

```powershell
cd C:\Users\camil\Desktop\ing.prueba
```

3. Instala las dependencias:

```powershell
python -m pip install -r requirements.txt
```

4. Ejecuta la app:

```powershell
python app.py
```

5. Abre en el navegador:

```text
http://127.0.0.1:5000
```

## Como publicarla en Render

1. Sube este proyecto a GitHub.
2. Entra a <https://render.com> y crea un **New Web Service**.
3. Conecta el repositorio de GitHub.
4. Usa esta configuracion:

```text
Build Command: pip install -r requirements.txt
Start Command: gunicorn app:app
```

5. Agrega estas variables de entorno:

```text
SECRET_KEY: una clave larga y secreta
FLASK_DEBUG: 0
DATABASE_PATH: benefix.db
```

Render generara una URL publica para abrir la aplicacion.

Nota: SQLite funciona para demostracion academica, pero en una version profesional conviene cambiar a PostgreSQL para conservar datos de forma mas segura en la nube.
