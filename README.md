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
- 
SQLite funciona para demostracion academica, pero en una version profesional conviene cambiar a PostgreSQL para conservar datos de forma mas segura en la nube.
