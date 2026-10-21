#!/usr/bin/env python3
"""
CrossHero auto-reserva
----------------------
Reserva automaticamente una clase en CrossHero en cuanto se abren las reservas.

Uso:
  python3 reservar.py              Modo automatico (lo usa el programador de cada jueves):
                                   espera a la hora de apertura y reserva la clase de dentro de 6 dias.
  python3 reservar.py --probar     Solo revisa: inicia sesion, busca la proxima clase y dice su estado.
                                   NO reserva nada.
  python3 reservar.py --ya --fecha 2026-10-15
                                   Reserva AHORA la clase de esa fecha (a la hora de config.json).

Tu contrasena se lee del Llavero de macOS (servicio "crosshero-bot"); nunca se guarda en archivos.
Solo usa la libreria estandar de Python (no hay que instalar nada).
"""
import argparse
import datetime as dt
import html
import http.cookiejar
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://crosshero.com"
DIR = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(DIR, "reservas.log")
KEYCHAIN_SERVICE = "crosshero-bot"


# ---------------------------------------------------------------- utilidades
def load_config():
    ruta = os.path.join(DIR, "config.json")
    if not os.path.exists(ruta) and os.environ.get("CROSSHERO_EMAIL"):   # modo nube (GitHub Actions)
        return {"email": os.environ["CROSSHERO_EMAIL"]}
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def log(msg):
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = "%s  %s" % (now, msg)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def notify(title, msg):
    """Notificacion de macOS (si no se puede, no pasa nada)."""
    script = "display notification %s with title %s sound name \"Glass\"" % (
        json.dumps(msg, ensure_ascii=False), json.dumps(title, ensure_ascii=False))
    try:
        subprocess.run(["osascript", "-e", script], timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def get_password(email):
    if os.environ.get("CROSSHERO_PASSWORD"):                              # modo nube (GitHub Actions)
        return os.environ["CROSSHERO_PASSWORD"]
    try:
        out = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", email, "-w"],
            capture_output=True, text=True, timeout=15)
    except Exception as e:
        raise SystemExit("No pude leer el Llavero: %s" % e)
    if out.returncode != 0 or not out.stdout.strip():
        raise SystemExit("No encontre la contrasena en el Llavero (servicio '%s', cuenta '%s'). "
                         "Corre ./instalar.sh otra vez." % (KEYCHAIN_SERVICE, email))
    return out.stdout.rstrip("\n")


def strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def find_input_value(fragment, name):
    for tag in re.findall(r"<input[^>]*>", fragment):
        if ('name="%s"' % name) in tag:
            m = re.search(r'value="([^"]*)"', tag)
            if m:
                return html.unescape(m.group(1))
    return None


def form_html(page, marker):
    """Devuelve el HTML del <form> que contiene `marker` (id o action)."""
    for m in re.finditer(r"<form\b.*?</form>", page, re.S):
        head = m.group(0)[:600]
        if marker in head:
            return m.group(0)
    return None


def flash_messages(page):
    msgs = re.findall(r'<div[^>]*class="[^"]*\balert\b[^"]*"[^>]*>(.*?)</div>', page, re.S)
    return [strip_tags(m).lstrip("×").strip() for m in msgs if strip_tags(m)]


# ------------------------------------------------------------------ cliente
class NotLoggedIn(Exception):
    pass


class CrossHero(object):
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        cafile = "/etc/ssl/cert.pem" if os.path.exists("/etc/ssl/cert.pem") else None
        ctx = ssl.create_default_context(cafile=cafile)
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.HTTPSHandler(context=ctx))
        self.opener.addheaders = [
            ("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"),
            ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
            ("Accept-Language", "es-MX,es;q=0.9"),
        ]
        self.class_token = None

    def _open(self, req):
        try:
            with self.opener.open(req, timeout=20) as r:
                return r.geturl(), r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.geturl(), e.read().decode("utf-8", "replace")

    def get(self, path):
        return self._open(urllib.request.Request(BASE + path))

    def post(self, path, data, referer):
        req = urllib.request.Request(
            BASE + path, data=urllib.parse.urlencode(data).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Origin": BASE, "Referer": referer})
        return self._open(req)

    # --- sesion
    def login(self, email, password):
        url, page = self.get("/athletes/sign_in?athlete%5Bemail%5D&password_mode=1")
        if "/sign_in" not in url:
            return True  # ya habia sesion
        form = form_html(page, 'action="/athletes/sign_in"')
        if not form:
            raise RuntimeError("No encontre el formulario de inicio de sesion (cambio la pagina de CrossHero?)")
        token = find_input_value(form, "authenticity_token")
        url, page = self.post("/athletes/sign_in", {
            "authenticity_token": token, "password_mode": "1", "mobile_app_id": "",
            "athlete[email]": email, "athlete[password]": password,
        }, referer=BASE + "/athletes/sign_in?athlete%5Bemail%5D&password_mode=1")
        if "/sign_in" in url:
            msgs = flash_messages(page)
            raise RuntimeError("No pude iniciar sesion. %s" % (" | ".join(msgs) or "Revisa correo/contrasena."))
        return True

    # --- clases
    def find_class(self, date, time_str, program_id):
        q = urllib.parse.urlencode({"date": date.strftime("%d/%m/%Y"), "program_id": program_id})
        url, page = self.get("/dashboard/classes?" + q)
        if "/sign_in" in url:
            raise NotLoggedIn()
        m = re.search(r'name="class_reservation\[single_class_id\]".*?</select>', page, re.S)
        if not m:
            return None, []
        opts = re.findall(r'<option[^>]*value="([0-9a-f]{24})"[^>]*>\s*([^<]*?)\s*</option>', m.group(0))
        for value, text in opts:
            if text.strip() == time_str:
                return value, opts
        return None, opts

    def class_page(self, class_id):
        url, page = self.get("/dashboard/classes?id=" + class_id)
        if "/sign_in" in url:
            raise NotLoggedIn()
        form = form_html(page, 'id="new_class_reservation"')
        if form:
            self.class_token = find_input_value(form, "authenticity_token")
        return page

    @staticmethod
    def is_reserved(page, athlete_id):
        return ('data-athlete-id="%s"' % athlete_id) in page

    @staticmethod
    def occupancy(page):
        m = re.search(r'>\s*(\d+/\d+)\s*</span>\s*<span[^>]*>\s*Plazas ocupadas', page)
        return m.group(1) if m else "?"

    def reserve(self, class_id, athlete_id):
        if not self.class_token:
            self.class_page(class_id)
        url, page = self.post("/dashboard/class_reservations", {
            "authenticity_token": self.class_token or "", "redirect_to": "", "fullscreen": "",
            "class_reservation[single_class_id]": class_id,
            "class_reservation[athlete_id]": athlete_id,
        }, referer=BASE + "/dashboard/classes?id=" + class_id)
        if "/sign_in" in url:
            raise NotLoggedIn()
        form = form_html(page, 'id="new_class_reservation"')
        if form:  # token fresco para el siguiente intento
            self.class_token = find_input_value(form, "authenticity_token") or self.class_token
        return self.is_reserved(page, athlete_id), flash_messages(page), page


# ---------------------------------------------------------------- programa
def connect(cfg, password, until):
    """Inicia sesion reintentando (la red puede tardar en despertar)."""
    ch = CrossHero()
    last = None
    while True:
        try:
            ch.login(cfg["email"], password)
            return ch
        except RuntimeError as e:
            if "No pude iniciar sesion" in str(e):
                raise
            last = e
        except Exception as e:  # red no lista todavia, timeout, etc.
            last = e
        if time.time() > until:
            raise RuntimeError("Sin conexion con CrossHero: %s" % last)
        log("Esperando red... (%s)" % last)
        time.sleep(5)


def run(args):
    cfg = load_config()
    if not cfg.get("email"):
        raise SystemExit("Falta tu correo en config.json. Corre ./instalar.sh")
    password = get_password(cfg["email"])
    days = int(cfg.get("open_days_before", 6))
    weekdays = [int(d) for d in cfg.get("class_weekdays", [cfg.get("class_weekday", 2)])]

    if args.fecha:
        class_date = dt.datetime.strptime(args.fecha, "%Y-%m-%d").date()
    elif args.probar:  # proximo dia de clase configurado, a partir de manana
        class_date = dt.date.today() + dt.timedelta(days=1)
        while class_date.weekday() not in weekdays:
            class_date += dt.timedelta(days=1)
    else:
        class_date = dt.date.today() + dt.timedelta(days=days)
    # hora de la clase segun el dia (class_times: {"5": "05:30"} = sabado 5:30); si no, class_time
    class_time = cfg.get("class_times", {}).get(str(class_date.weekday()), cfg["class_time"])
    hh, mm = [int(x) for x in class_time.split(":")]
    class_dt = dt.datetime.combine(class_date, dt.time(hh, mm))
    open_dt = class_dt - dt.timedelta(days=days)
    dias = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
    label = "%s %s %s" % (dias[class_date.weekday()], class_date.strftime("%d/%m/%Y"), class_time)

    if not args.fecha and not args.probar and class_date.weekday() not in weekdays:
        log("Hoy no toca: la clase de dentro de %d dias (%s) no es el dia configurado. Nada que hacer." % (days, label))
        return 0

    log("=== Clase objetivo: %s  (reservas abren %s) ===" % (label, open_dt.strftime("%d/%m/%Y %H:%M:%S")))
    ch = connect(cfg, password, until=time.time() + 150)
    log("Sesion iniciada.")

    class_id, opts = ch.find_class(class_date, class_time, cfg["program_id"])
    if not class_id:
        log("No encontre la clase de las %s ese dia. Horarios disponibles: %s"
            % (class_time, ", ".join(t for _, t in opts) or "ninguno"))
        notify("CrossHero", "No encontre la clase %s" % label)
        return 2
    page = ch.class_page(class_id)
    log("Clase encontrada (id %s). Ocupacion: %s" % (class_id, ch.occupancy(page)))

    if ch.is_reserved(page, cfg["athlete_id"]):
        log("Ya estabas inscrito en esta clase. Nada que hacer.")
        return 0

    if args.probar:
        txt = strip_tags(page).lower()
        if "todavía no están abiertas" in txt or "todavia no estan abiertas" in txt:
            estado = "todavia NO abiertas (abren %s)" % open_dt.strftime("%d/%m/%Y %H:%M")
        elif "reservas cerradas" in txt:
            estado = "cerradas"
        else:
            estado = "ABIERTAS"
        log("Modo prueba: NO se reservo nada. Estado de reservas: %s" % estado)
        return 0

    if not args.ya:
        lead = float(cfg.get("lead_seconds", 0.3))
        wait = (open_dt - dt.datetime.now()).total_seconds() - lead
        if wait > 0:
            log("Esperando %.1f s hasta la apertura..." % wait)
            # dormir en tramos para no pasarse; refrescar token ~20 s antes
            while True:
                remaining = (open_dt - dt.datetime.now()).total_seconds() - lead
                if remaining <= 0:
                    break
                if 15 < remaining < 25:
                    try:
                        ch.class_page(class_id)
                    except NotLoggedIn:
                        ch = connect(cfg, password, until=time.time() + 30)
                        ch.class_page(class_id)
                    time.sleep(min(remaining - 15, 5))
                    continue
                time.sleep(min(max(remaining - 1, 0.02), 5) if remaining > 1 else 0.02)

    deadline = time.time() + float(cfg.get("retry_seconds", 90))
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            ok, msgs, page = ch.reserve(class_id, cfg["athlete_id"])
        except NotLoggedIn:
            log("Sesion expirada, entrando de nuevo...")
            ch = connect(cfg, password, until=time.time() + 30)
            continue
        except Exception as e:
            log("Intento %d: error de red: %s" % (attempt, e))
            time.sleep(0.5)
            continue
        log("Intento %d: %s" % (attempt, " | ".join(msgs) or "(sin mensaje)"))
        if ok:
            log("RESERVADA: %s" % label)
            notify("CrossHero", "Clase reservada: %s" % label)
            return 0
        joined = " ".join(msgs).lower()
        if "complet" in joined or "llen" in joined or "no quedan" in joined:
            log("La clase esta llena.")
            notify("CrossHero", "No se pudo: clase llena (%s)" % label)
            return 3
        time.sleep(0.4 if attempt < 20 else 2)

    log("No se logro reservar a tiempo.")
    notify("CrossHero", "No se pudo reservar %s" % label)
    return 4


def main():
    p = argparse.ArgumentParser(description="Reserva automatica en CrossHero")
    p.add_argument("--probar", action="store_true", help="solo revisar, no reservar")
    p.add_argument("--ya", action="store_true", help="reservar ahora sin esperar")
    p.add_argument("--fecha", help="fecha de la clase AAAA-MM-DD (por defecto: hoy + 6 dias)")
    args = p.parse_args()
    try:
        sys.exit(run(args))
    except SystemExit:
        raise
    except Exception as e:
        log("ERROR: %s" % e)
        notify("CrossHero", "Error: %s" % e)
        sys.exit(1)


if __name__ == "__main__":
    main()
