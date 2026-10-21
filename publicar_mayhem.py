#!/usr/bin/env python3
"""
Publica en CrossHero (programa CINCO DOS MAYHEM) los WODs de Mayhem que hay en SugarWOD.

Usa la API oficial de SugarWOD (https://api.sugarwod.com/v2). La API key se guarda en el
Llavero de macOS (servicio "sugarwod-api"); nunca en archivos.

Componentes (igual que los WODs de Mayhem que ya tienes en CrossHero):
  WARM UP                      -> Warmup
  "<Levantamiento> 1xN" (peso) -> Weightlifting (<levantamiento>), Peso
  WORKOUT y demas              -> Custom Metcon (Tiempo / Reps / Rondas y Reps / Peso / No puntuable)
  Todo lo que empieza con "OPTIONAL" (cool down, accessory, GOWOD) NO se publica.

Uso:
  python3 publicar_mayhem.py                      Prueba: muestra la proxima semana, NO publica.
  python3 publicar_mayhem.py --semana 2026-10-26  Prueba de esa semana (lunes).
  python3 publicar_mayhem.py --publicar           Publica la proxima semana (salta lo ya publicado).
  python3 publicar_mayhem.py --prueba 2026-09-27 --dia 2026-10-26
                                                  Publica UN dia (el 26/10 de SugarWOD) en la fecha 27/09.
"""
import argparse, datetime as dt, html, json, os, re, ssl, subprocess, sys, urllib.error, urllib.parse, urllib.request
from reservar import CrossHero, load_config, get_password, log, notify
import publicar_wods as pw

DIR = os.path.dirname(os.path.abspath(__file__))
ESTADO = os.path.join(DIR, "mayhem_publicados.json")
API = "https://api.sugarwod.com/v2"
PROGRAMA_MAYHEM = "629ebf9c9e34c20031f7ac3d"     # CINCO DOS MAYHEM
TRACK_NOMBRE = "Workout of the Day"
SCORE = {"time": ("time", "Tiempo"), "reps": ("reps", "Reps"), "rounds": ("rounds_reps", "Rondas y Reps"),
         "load": ("weight", "Peso"), "other": ("not_scorable", None)}

def api_key():
    if os.environ.get("SUGARWOD_API_KEY"):                                # modo nube (GitHub Actions)
        return os.environ["SUGARWOD_API_KEY"].strip()
    out = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "sugarwod-api", "-w"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("No pude leer la API key del Llavero (%s). Corre:\n"
                         "  security add-generic-password -U -s sugarwod-api -a cinco-dos -w"
                         % (out.stderr.strip() or "codigo %d" % out.returncode))
    if not out.stdout.strip():
        raise SystemExit("La API key en el Llavero esta VACIA (se guardo sin pegar nada). Corre otra vez:\n"
                         "  security add-generic-password -U -s sugarwod-api -a cinco-dos -w")
    return out.stdout.strip()

def api(path, key):
    cafile = "/etc/ssl/cert.pem" if os.path.exists("/etc/ssl/cert.pem") else None
    req = urllib.request.Request(API + path, headers={
        "Authorization": key, "Accept": "application/json",
        "User-Agent": "crosshero-bot/1.0 (Cinco Dos; python urllib)"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context(cafile=cafile)) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode("utf-8", "replace")[:300].replace(key, "***")
        raise RuntimeError("SugarWOD respondio %s en %s: %s" % (e.code, path.split("?")[0], cuerpo or e.reason))

def probar_key(key):
    b = api("/box", key).get("data", {}).get("attributes", {})
    log("SugarWOD: key valida para '%s'." % b.get("name"))

def track_id(key):
    for t in api("/tracks", key).get("data", []):
        if (t.get("attributes") or {}).get("name", "").strip().lower() == TRACK_NOMBRE.lower():
            return t["id"]
    raise RuntimeError("No encontre el track '%s' en SugarWOD." % TRACK_NOMBRE)

def workouts(key, tid, lunes):
    rango = "%s-%s" % (lunes.strftime("%Y%m%d"), (lunes + dt.timedelta(days=6)).strftime("%Y%m%d"))
    path = "/workouts?" + urllib.parse.urlencode({"dates": rango, "track_id": tid, "page[limit]": 100})
    dias = {}
    for w in api(path, key).get("data", []):
        a = w.get("attributes") or {}
        d = dt.datetime.strptime(str(a["scheduled_date_int"]), "%Y%m%d").date()
        dias.setdefault(d, []).append(a)
    return dias

def texto(desc):
    t = html.unescape(re.sub(r"<[^>]+>", "", (desc or "").replace("<br>", "\n")))
    return [x.rstrip() for x in t.replace("\r", "").split("\n")]

def deducir_score(t):
    t = t.lower()
    m = re.findall(r"\((?:score[sd]?|scored)[^)]*\)", t)
    pista = m[-1] if m else ""
    if not pista: return None
    if "no measure" in pista: return "other"
    if "round" in pista: return "rounds"
    if "time" in pista: return "time"
    if "weight" in pista or "load" in pista: return "load"
    if "rep" in pista or "cal" in pista or "distance" in pista or "meter" in pista: return "reps"
    return None

def componentes(lista):
    out = []
    for a in lista:
        titulo = (a.get("title") or "").strip()
        if titulo.upper().startswith("OPTIONAL"): continue
        lineas = [titulo] + texto(a.get("description"))
        while lineas and not lineas[-1].strip(): lineas.pop()
        st = (a.get("score_type") or "other").lower()
        if st not in SCORE or st == "other":      # la API a veces no trae el tipo: se deduce del texto
            st = deducir_score(" ".join(lineas)) or "other"
        if "WARM" in titulo.upper():
            out.append(dict(tipo="Warmup", score="not_scorable", score_label=None, lineas=lineas))
        elif st == "load" and re.search(r"\d+\s*x\s*\d+", titulo):
            out.append(dict(tipo="Weightlifting", score="weight", score_label="Peso", lineas=lineas, lift=titulo))
        else:
            score, label = SCORE.get(st, ("not_scorable", None))
            out.append(dict(tipo="Custom Metcon", score=score, score_label=label, lineas=lineas))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--publicar", action="store_true")
    ap.add_argument("--semana", help="lunes AAAA-MM-DD (por defecto: el proximo lunes)")
    ap.add_argument("--prueba", metavar="AAAA-MM-DD", help="publica un solo dia en esta fecha")
    ap.add_argument("--dia", metavar="AAAA-MM-DD", help="dia de SugarWOD a usar con --prueba")
    a = ap.parse_args()
    cfg = load_config()
    hoy = dt.date.today()
    lunes = (dt.datetime.strptime(a.semana, "%Y-%m-%d").date() if a.semana
             else hoy + dt.timedelta(days=(7 - hoy.weekday()) or 7))
    if a.dia: lunes = dt.datetime.strptime(a.dia, "%Y-%m-%d").date() - dt.timedelta(
        days=dt.datetime.strptime(a.dia, "%Y-%m-%d").date().weekday())
    estado = json.load(open(ESTADO)) if os.path.exists(ESTADO) else {}
    if a.publicar and not a.prueba and all((lunes + dt.timedelta(days=i)).isoformat() in estado for i in range(7)):
        return 0
    key = api_key()
    probar_key(key)
    dias = workouts(key, track_id(key), lunes)
    if not dias:
        log("Mayhem: SugarWOD todavia no tiene WODs para la semana del %s." % lunes)
        return 0
    if a.prueba:
        origen = dt.datetime.strptime(a.dia, "%Y-%m-%d").date() if a.dia else min(dias)
        comps = componentes(dias.get(origen, []))
        print("\n===== PRUEBA %s (de %s) =====\n%s\n" % (a.prueba, origen, pw.a_texto(comps)))
        ch = CrossHero(); ch.login(cfg["email"], get_password(cfg["email"]))
        url = pw.publicar(ch, PROGRAMA_MAYHEM, dt.datetime.strptime(a.prueba, "%Y-%m-%d").date(),
                          comps, "", cfg.get("wod_visibilidad", "Mismo día"))
        log("Mayhem PRUEBA publicada: %s (de %s) -> %s" % (a.prueba, origen, url))
        return 0
    ch, hechos, saltados = None, [], []
    for fecha in sorted(dias):
        comps = componentes(dias[fecha])
        if not comps: continue
        if not a.publicar:
            print("\n===== %s =====\n%s" % (fecha.strftime("%a %d/%m"), pw.a_texto(comps))); continue
        if fecha.isoformat() in estado: continue
        if ch is None:
            ch = CrossHero(); ch.login(cfg["email"], get_password(cfg["email"]))
        try:
            pw.publicar(ch, PROGRAMA_MAYHEM, fecha, comps, "", cfg.get("wod_visibilidad", "Mismo día"))
        except RuntimeError as e:
            if "Ya existe un WOD" in str(e):
                log("Mayhem saltado: %s" % e); saltados.append(fecha.strftime("%a %d/%m")); continue
            raise
        estado[fecha.isoformat()] = "mayhem"; json.dump(estado, open(ESTADO, "w"), indent=2)
        hechos.append(fecha.strftime("%a %d/%m")); log("Mayhem publicado: %s" % fecha.strftime("%d/%m/%Y"))
    if not a.publicar:
        print("\nModo prueba: no se publico nada. Usa --publicar para subirlos."); return 0
    if hechos or saltados:
        notify("CrossHero Mayhem", "Publicados: %s%s" % (", ".join(hechos) or "ninguno",
               (" | ya tenian WOD: %s" % ", ".join(saltados)) if saltados else ""))
    return 0

if __name__ == "__main__":
    try: sys.exit(main())
    except SystemExit: raise
    except Exception as e:
        log("ERROR Mayhem: %s" % e); notify("CrossHero Mayhem", "Error: %s" % e); sys.exit(1)
