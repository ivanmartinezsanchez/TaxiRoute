"""Adaptador de envío de PIN para un cliente Gmail ya autorizado."""
import base64
from email.message import EmailMessage
from email.utils import parseaddr


class ErrorEnvioPIN(RuntimeError):
    """No se ha podido entregar el mensaje al proveedor."""


class EnviadorGmail:
    """Envía mediante un cliente Gmail inyectado con permiso gmail.send.

    No autoriza cuentas, no guarda credenciales y no selecciona el destinatario.
    La cuenta de Google autorizada es la remitente; el servicio PIN proporciona
    la dirección destinataria desde su configuración privada.
    """

    def __init__(self, servicio_gmail):
        self._servicio = servicio_gmail

    def __call__(self, destinatario, codigo):
        if not isinstance(destinatario, str) or len(destinatario) > 254:
            raise ValueError("El destinatario no es válido.")
        nombre, direccion = parseaddr(destinatario, strict=True)
        if (nombre or direccion != destinatario or direccion.count("@") != 1
                or not all(direccion.split("@")) or any(c in destinatario for c in "\r\n")):
            raise ValueError("El destinatario no es válido.")
        if not isinstance(codigo, str) or len(codigo) != 7 or any(
            c not in "0123456789" for c in codigo
        ):
            raise ValueError("El código debe tener siete cifras.")

        mensaje = EmailMessage()
        mensaje["To"] = destinatario
        mensaje["Subject"] = "TaxiRoute: código de acceso"
        mensaje.set_content(
            "Has solicitado acceso a la administración de TaxiRoute.\n\n"
            f"Tu código es: {codigo}\n\n"
            "Caduca a los cinco minutos y solo puede utilizarse una vez.\n"
        )
        contenido = base64.urlsafe_b64encode(mensaje.as_bytes()).decode("ascii")
        try:
            self._servicio.users().messages().send(
                userId="me", body={"raw": contenido}
            ).execute()
        except Exception:
            # Evitar que detalles del proveedor expongan contenido o credenciales.
            raise ErrorEnvioPIN("No se ha podido enviar el código.") from None
