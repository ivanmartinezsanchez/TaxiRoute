
import socket, ssl, time, urllib.request
from pathlib import Path
for _ in range(100):
    if Path("/prueba/certificado.pem").is_file() and Path("/prueba/clave.pem").is_file():
        break
    time.sleep(.2)
else:
    raise RuntimeError("El certificado ficticio no se generó a tiempo.")
contexto = ssl.create_default_context(cafile="/prueba/certificado.pem")
url = "https://127.0.0.1:18771/acceso"
limite = time.monotonic() + 20
while True:
    try:
        with urllib.request.urlopen(url, context=contexto, timeout=2) as r:
            print("Antes:", r.status)
        break
    except Exception:
        if time.monotonic() >= limite: raise
        time.sleep(.2)
import http.client, json, uuid
def req(metodo, ruta, *, autoridad="127.0.0.1:18771", headers=None, cuerpo=None):
    c = http.client.HTTPSConnection("127.0.0.1", 18771, context=contexto, timeout=3)
    h = {"Host": autoridad}
    h.update(headers or {})
    try:
        c.request(metodo, ruta, body=cuerpo, headers=h)
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    finally:
        c.close()
for invalido in ("atacante.example", "127.0.0.1:18772", "127.0.0.1:18770"):
    estado, _, _ = req("GET", "/acceso", autoridad=invalido,
        headers={"X-Forwarded-Host":"127.0.0.1:18771", "X-Forwarded-Proto":"https"})
    assert estado == 400, (invalido,estado)
print("Host incorrecto y cabeceras suplantadas: 400")
base = {"Content-Type":"application/json"}
credenciales = json.dumps({"usuario":"AnaDemo", "contrasena":"ConductorDemo123!"})
estado, _, _ = req("POST", "/acceso/conductor",
    headers={**base, "Origin":"https://malicioso.example"}, cuerpo=credenciales)
assert estado == 403, estado
print("Inicio con Origin ajeno: 403")
origen="https://127.0.0.1:18771"
estado, h, _ = req("POST", "/acceso/conductor", headers={**base,"Origin":origen}, cuerpo=credenciales)
assert estado == 200, estado
cookie = h["Set-Cookie"].split(";",1)[0]
assert all(x.lower() in h["Set-Cookie"].lower() for x in ("secure","httponly","samesite=strict"))
print("Inicio de sesión y cookie segura: 200")
cabeceras = {**base,"Origin":origen,"Cookie":cookie}
estado, _, datos = req("POST", "/api/carrera/iniciar", headers=cabeceras,
    cuerpo=json.dumps({"revision":0,"id_operacion":str(uuid.uuid4())}))
assert estado == 200, (estado,datos)
revision = json.loads(datos)["revision"]
estado, _, datos = req("POST", "/api/carrera/finalizar", headers=cabeceras,
    cuerpo=json.dumps({"revision":revision}))
assert estado == 200, (estado,datos)
estado, _, datos = req("GET", "/api/historial", headers={"Cookie":cookie})
assert estado == 200 and len(json.loads(datos)["carreras"]) >= 1, (estado,datos)
print("Carrera iniciada, finalizada y consultada: 200")
from concurrent.futures import ThreadPoolExecutor
creds_luis = json.dumps({"usuario":"LuisDemo","contrasena":"ConductorDemo123!"})
estado, h, datos = req("POST", "/acceso/conductor", headers={**base,"Origin":origen}, cuerpo=creds_luis)
assert estado == 200, (estado,datos)
cookie_luis = h["Set-Cookie"].split(";",1)[0]
def inicio_paralelo(dato):
    token, revision = dato
    return req("POST", "/api/carrera/iniciar", headers={**base,"Origin":origen,"Cookie":token},
       cuerpo=json.dumps({"revision":revision,"id_operacion":str(uuid.uuid4())}))
with ThreadPoolExecutor(max_workers=2) as pool:
    inicios = list(pool.map(inicio_paralelo, [(cookie,2),(cookie_luis,0)]))
assert all(x[0]==200 for x in inicios), inicios
def finalizar_paralelo(dato):
    token, respuesta = dato
    return req("POST", "/api/carrera/finalizar", headers={**base,"Origin":origen,"Cookie":token},
       cuerpo=json.dumps({"revision":json.loads(respuesta[2])["revision"]}))
with ThreadPoolExecutor(max_workers=2) as pool:
    finales = list(pool.map(finalizar_paralelo, [(cookie,inicios[0]),(cookie_luis,inicios[1])]))
assert all(x[0]==200 for x in finales), finales
for token, cantidad in ((cookie,2),(cookie_luis,1)):
    estado, _, datos=req("GET", "/api/historial",headers={"Cookie":token})
    assert estado == 200 and len(json.loads(datos)["carreras"])==cantidad, (estado, datos)
print("Dos conductores y carreras concurrentes con historial aislado: 200")
clientes = []
try:
    for i in range(12):
        s = socket.create_connection(("127.0.0.1", 18771), timeout=2)
        if i % 2: s.sendall(b"\x16")
        clientes.append(s)
    time.sleep(0.5)
    with urllib.request.urlopen(url, context=contexto, timeout=3) as r:
        print("Mientras hay 12 conexiones TLS incompletas:", r.status)
    time.sleep(6)
    cerradas = 0
    for s in clientes:
        s.settimeout(2)
        try:
            if s.recv(1) == b"": cerradas += 1
        except ConnectionResetError:
            cerradas += 1
    assert cerradas == len(clientes), (cerradas, len(clientes))
    print("Conexiones incompletas cerradas tras el plazo:", cerradas, "/", len(clientes))
    with urllib.request.urlopen(url, context=contexto, timeout=3) as r:
        print("Después:", r.status)
finally:
    for s in clientes: s.close()
