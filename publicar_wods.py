#!/usr/bin/env python3
"""
Publica en CrossHero los WODs de la semana de HYROX365 (Performance Hub).

Reparto (lunes a sabado, Foundational NO se publica):
  lun Power #N   mar Engine #N   mie Complete #N
  jue Power #N+1 vie Engine #N+1 sab Complete #N+1

Uso:
  python3 publicar_wods.py --login      Abre Chromium para que TU inicies sesion en HYROX365 (una vez).
  python3 publicar_wods.py              Modo prueba: baja los WODs y muestra como quedarian. NO publica.
  python3 publicar_wods.py --publicar   Publica en CrossHero (salta los que ya se publicaron).
  python3 publicar_wods.py --prueba 2026-09-07
                                        Publica UN solo WOD de prueba en esa fecha.

Requisitos (una sola vez):
  pip3 install --user playwright && python3 -m playwright install chromium
La sesion de HYROX365 se guarda en ./hyrox-perfil (no se guarda tu contrasena).
CrossHero usa la misma contrasena del Llavero que reservar.py.
"""
import argparse, datetime as dt, html, json, os, re, sys
from reservar import (CrossHero, load_config, get_password, log, notify,
                      form_html, find_input_value, flash_messages, BASE)

DIR = os.path.dirname(os.path.abspath(__file__))
PERFIL = os.path.join(DIR, "hyrox-perfil")
ESTADO = os.path.join(DIR, "wods_publicados.json")
HUB = "https://performancehub.hyrox365.com/"
ORDEN = ["Power", "Engine", "Complete"]          # se repite: semana 1 (lun-mie), semana 2 (jue-sab)

QUERY = open(os.path.join(DIR, "hyrox_query.graphql"), encoding="utf-8").read()

# ------------------------------------------------------------ HYROX365
def bajar_semana(headless=True, esperado=None):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PERFIL, headless=headless)
        page = ctx.new_page()
        page.goto(HUB + "workouts", wait_until="networkidle")
        if "login" in page.url or "portal.hyrox365.com" in page.url:
            ctx.close()
            raise RuntimeError("La sesion de HYROX365 expiro. Corre: python3 publicar_wods.py --login")
        page.wait_for_selector('a[href^="/workout/"]', timeout=30000)
        # La pagina Workouts lista las semanas ("19/10-25/10 2026") con sus WODs debajo.
        semanas = page.evaluate("""() => { const out=[]; let cur=null;
            const w=document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT);
            while(w.nextNode()){ const n=w.currentNode;
              if(n.nodeType===3){ const t=n.textContent.trim();
                const m=t.match(/^(\\d{2})\\/(\\d{2})\\s*-\\s*(\\d{2})\\/(\\d{2})\\s+(\\d{4})$/);
                if(m){ cur={d:+m[1], m:+m[2], y:+m[5], links:[]}; out.push(cur); } }
              else if(n.tagName==='A' && cur){ const h=n.getAttribute('href')||'';
                if(h.startsWith('/workout/')) cur.links.push([n.textContent.trim(), h.split('/').pop()]); } }
            return out; }""")
        if not semanas:
            ctx.close(); raise RuntimeError("No encontre semanas en la pagina Workouts del Hub.")
        def fecha_de(x): return dt.date(x["y"], x["m"], x["d"])
        if esperado:
            elegida = next((x for x in semanas if fecha_de(x) == esperado), None)
            if not elegida:                       # todavia no sale la semana buscada
                ctx.close(); return 0, max(fecha_de(x) for x in semanas), []
        else:
            elegida = max(semanas, key=fecha_de)  # la mas reciente
        inicio = fecha_de(elegida)
        semana = int(inicio.strftime("%V"))
        vistos, wods = set(), []
        for nombre, lid in elegida["links"]:
            if lid in vistos or not nombre: continue
            vistos.add(lid); wods.append((nombre, lid))
        detalles = []
        for nombre, lid in wods:
            if nombre.lower().startswith("foundational"): continue
            lesson, data = None, {}
            for intento in range(3):
                data = page.evaluate("""async ([q,id,sw]) => {
                    const r = await fetch('/graphql', {method:'POST', credentials:'include',
                      headers:{'content-type':'application/json'},
                      body: JSON.stringify({operationName:'GetHubWorkoutDetail', variables:{id, withExerciseSwaps:sw}, query:q})});
                    return await r.json(); }""", [QUERY, lid, intento == 1])
                lesson = (data.get("data") or {}).get("lesson")
                if lesson: break
                page.wait_for_timeout(2000)
            if not lesson:
                log("Aviso: no pude leer %s (%s); se salta ese dia." % (nombre, lid))
                continue
            detalles.append(lesson)
        ctx.close()
    return semana, inicio, detalles

def login_hyrox():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PERFIL, headless=False)
        page = ctx.new_page(); page.goto("https://portal.hyrox365.com/login")
        print("Inicia sesion en la ventana, entra a 'Open Performance Hub' y luego presiona Enter aqui.")
        input()
        ctx.close()

# ------------------------------------------------------------ formato
# Formato acordado con Paul (sin viñetas, mismo formato todos los dias):
#   Skill / Nombre / RPE / MINS / Equipment / descripcion
#   Warmup | Custom Metcon (Distancia) | Custom Metcon (Reps)  -> seccion, MINS, Rep/Interval Scheme, Zonas
def mmss(s): return "%02d:%02d" % (s // 60, s % 60)
def num(x):
    return str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)
def rng(m):
    if not m: return None
    if m.get("single") is not None: return num(m["single"])
    a, b = m.get("min"), m.get("max")
    if a is None and b is None: return None
    if a is not None and b is not None and a != b: return "%s/%s" % (num(a), num(b))
    return num(a if a is not None else b)

def linea(e):
    d = e.get("detailedMetrics") or {}
    reps = rng(d.get("repetitions")) or (num(e["repetitions"]) if e.get("repetitions") else None)
    dist = rng(d.get("distance")) or (num(e["distance"]) if e.get("distance") else None)
    cal = rng(d.get("calories"))
    peso = rng(d.get("weight")) or (num(e["weight"]) if e.get("weight") else None)
    nombre = (e.get("exercise") or {}).get("name") or ""
    p = []
    if e.get("duration"): p.append(mmss(e["duration"]))
    if reps: p.append("%sx" % reps)
    if dist: p.append("%sm" % dist)
    if cal: p.append("%s cal" % cal)
    if not p and e.get("description"): p.append("%s x" % re.sub(r"<[^>]+>", "", e["description"]).strip())
    t = " ".join(p + [nombre]).strip()
    if peso: t += " (%s kg)" % peso
    if e.get("rpe"): t += " @RPE %s" % num(e["rpe"])
    return t

def ejercicios(s):
    out = []
    for e in s.get("sectionExercises") or []: out.append(e)
    def rec(g):
        for e in g.get("sectionExercises") or []: out.append(e)
        for x in g.get("sectionExerciseGroups") or []: rec(x)
    for g in s.get("sectionExerciseGroups") or []: rec(g)
    return out

def etiqueta(s):
    nombre = (s.get("name") or "").lower()
    if "warm" in nombre or s.get("format") == "WARMUP": return "Warmup"
    ex = ejercicios(s)
    reps = sum(1 for e in ex if e.get("repetitions") or rng((e.get("detailedMetrics") or {}).get("repetitions"))
               or rng((e.get("detailedMetrics") or {}).get("calories")))
    otros = sum(1 for e in ex if e.get("duration") or e.get("distance")
                or rng((e.get("detailedMetrics") or {}).get("distance")))
    return "Custom Metcon (Reps)" if reps > otros else "Custom Metcon (Distancia)"

def esquema(s):
    if not s.get("workTime"): return None
    r = s.get("rounds") or 1
    rest = mmss(s["restTime"]) if s.get("restTime") else ""
    return "Rep/Interval Scheme: %s Work/%s Rest, %d %s" % (mmss(s["workTime"]), rest, r, "Round" if r == 1 else "Rounds")

def grupo_lineas(g):
    l = []
    if g.get("showTitle") and g.get("name"): l.append(g["name"])
    for e in g.get("sectionExercises") or []: l.append(linea(e))
    for x in g.get("sectionExerciseGroups") or []: l += grupo_lineas(x)
    return l

# Componentes de CrossHero (los titulos en negrita/grandes son el tipo de componente)
COMP = {
    "Skill":         dict(component_type="not_scorable",  ref="5b4740c7a6592a446f3b1e60"),
    "Warmup":        dict(component_type="not_scorable",  ref="5b4740c7a6592a446f3b1e5f"),
    "Custom Metcon": dict(component_type="custom_metcon", ref="5b4740c7a6592a446f3b1e5e"),
    "Weightlifting": dict(component_type="weightlifting", ref="5b4740c7a6592a446f3b1e5c"),
}

ALIAS_LIFT = {"strict press": "shoulder press", "press": "shoulder press"}

def buscar_lift(lifts, titulo):
    """'Back Squat 1x4' -> id de 'Back Squat'; usa el nombre mas largo que coincida al inicio."""
    t = re.sub(r"\s*\d+\s*x\s*\d+.*$", "", titulo).strip().lower()
    if t in lifts: return lifts[t]
    for k in sorted(lifts, key=len, reverse=True):
        if t.startswith(k): return lifts[k]
    for k, v in ALIAS_LIFT.items():
        if t.startswith(k) and v in lifts: return lifts[v]
    return ""

def levantamientos(form):
    """{nombre en minusculas: id} de la lista de levantamientos del componente Weightlifting."""
    m = re.search(r'<select[^>]*name="wod\[components_attributes\]\[\d+\]\[parent_component_id\]"[^>]*>'
                  r'(?:(?!</select>).)*?Back Squat.*?</select>', form, re.S)
    if not m: return {}
    return {html.unescape(t).strip().lower(): v
            for v, t in re.findall(r'<option[^>]*value="([0-9a-f]{24})"[^>]*>([^<]*)', m.group(0))}
SCORE = {"Distancia": "distance", "Reps": "reps", "Tiempo": "time"}

def componentes(L):
    """Lista de componentes: {'tipo','score','score_label','lineas'}."""
    out = []
    l = [L["name"], "RPE %s" % num(L.get("rpe")), "%d MINS" % round(L["duration"] / 60)]
    eq = [x["name"] for x in (L.get("equipment") or []) if x.get("name")]
    if eq: l += ["Equipment:"] + eq
    if L.get("description"): l.append(L["description"].strip())
    out.append(dict(tipo="Skill", score="not_scorable", score_label=None, lineas=l))
    for s in L["sections"]:
        if not ejercicios(s): continue           # cooldown y secciones vacias no se publican
        et = etiqueta(s)                          # "Warmup" o "Custom Metcon (Reps)"
        m = re.match(r"Custom Metcon \((\w+)\)", et)
        l = [s["name"], "%d MINS" % round((s.get("duration") or 0) / 60)]
        esq = esquema(s)
        if esq: l.append(esq)
        if s.get("sectionExercises"):
            l.append("")
            for e in s["sectionExercises"]: l.append(linea(e))
        for g in s.get("sectionExerciseGroups") or []:
            l.append("")
            l += grupo_lineas(g)
        if m:
            out.append(dict(tipo="Custom Metcon", score=SCORE[m.group(1)], score_label=m.group(1), lineas=l))
        else:
            out.append(dict(tipo="Warmup", score="not_scorable", score_label=None, lineas=l))
    return out

def formatear_texto(L):
    l = []
    for c in componentes(L):
        l.append(c["tipo"] + (" (%s)" % c["score_label"] if c["score_label"] else ""))
        l += c["lineas"]
    return "\n".join(l)

def formatear(L):
    cli = componentes(L)
    # notas del coach en texto plano: sin viñetas, una linea en blanco entre secciones
    bloques = []
    for s in L["sections"]:
        if not s.get("notes"): continue
        n = re.sub(r"</(li|p|div)>|<br\s*/?>", "\n", s["notes"])
        n = html.unescape(re.sub(r"<[^>]+>", "", n))
        lineas = [x.strip() for x in n.split("\n") if x.strip()]
        if lineas: bloques.append([s["name"]] + lineas)
    coach = "<br><br>".join("<br>".join(html.escape(x) for x in b) for b in bloques)
    return cli, coach

def a_texto(h):
    if isinstance(h, list):   # componentes
        return "\n".join("\n".join(["## " + c["tipo"] + (" (%s)" % c["score_label"] if c["score_label"] else "")]
                                    + c["lineas"]) for c in h)
    return html.unescape(re.sub(r"<[^>]+>", "", h.replace("<br>", "\n")))

# ------------------------------------------------------------ CrossHero
def publicar(ch, program_id, fecha, cli, coach, visibilidad):
    url, page = ch.get("/dashboard/admin/wods/new")
    if "/sign_in" in url: raise RuntimeError("Sesion de CrossHero no valida.")
    form = form_html(page, 'action="/dashboard/admin/wods"')
    if not form: raise RuntimeError("No encontre el formulario de Nuevo workout.")
    open(os.path.join(DIR, "debug_form.html"), "w", encoding="utf-8").write(form)
    token = find_input_value(form, "authenticity_token")
    # valor del radio de visibilidad segun su etiqueta (p.ej. "Mismo día")
    vis = None
    for tag in re.findall(r'<input[^>]*type="radio"[^>]*>', form):
        if 'name="wod[visibility]"' not in tag: continue
        val = re.search(r'value="([^"]*)"', tag); idm = re.search(r'id="([^"]*)"', tag)
        if idm and val:
            lab = re.search(r'<label[^>]*for="%s"[^>]*>(.*?)</label>' % re.escape(idm.group(1)), form, re.S)
            if lab and visibilidad.lower() in html.unescape(re.sub(r"<[^>]+>", "", lab.group(1))).lower():
                vis = val.group(1)
    if not vis: raise RuntimeError("No encontre la opcion de visibilidad '%s'." % visibilidad)
    datos = {
        "authenticity_token": token, "wod[program_id]": program_id,
        "wod[date]": fecha.strftime("%d/%m/%Y"), "wod[visibility]": vis,
        "wod[visibility_time(1i)]": fecha.year, "wod[visibility_time(2i)]": fecha.month,
        "wod[visibility_time(3i)]": fecha.day, "wod[visibility_time(4i)]": "00", "wod[visibility_time(5i)]": "00",
        "wod[description]": "", "wod[coach_notes]": coach, "commit": "Guardar",
    }
    lifts = levantamientos(form)
    for i, c in enumerate(cli):
        k = "wod[components_attributes][%d]" % (1000 + i)
        parent = ""
        if c["tipo"] == "Weightlifting":
            parent = buscar_lift(lifts, c.get("lift") or "")
            if not parent:                         # levantamiento no encontrado -> Custom Metcon por peso
                c = dict(c, tipo="Custom Metcon", score="weight")
        datos.update({
            k + "[component_type]": COMP[c["tipo"]]["component_type"],
            k + "[component_type_ref_id]": COMP[c["tipo"]]["ref"],
            k + "[score_type]": c["score"],
            k + "[description]": "<div>%s</div>" % "<br>".join(html.escape(x) for x in c["lineas"]),
            k + "[position]": i + 1,
            k + "[parent_component_id]": parent,
            k + "[_destroy]": "false",
        })
    url, page = ch.post("/dashboard/admin/wods", datos, referer=BASE + "/dashboard/admin/wods/new")
    flashes = " ".join(flash_messages(page)).lower()
    if "creado con" in flashes:          # "Registro creado con éxito."
        return url
    if True:
        open(os.path.join(DIR, "debug_respuesta.html"), "w", encoding="utf-8").write(page)
        errs = re.findall(r'class="[^"]*(?:invalid-feedback|help-block|error|field_with_errors)[^"]*"[^>]*>(.*?)</', page, re.S)
        errs = [html.unescape(re.sub(r"<[^>]+>", "", e)).strip() for e in errs]
        if any("ya est" in e and "en uso" in e for e in errs):
            raise RuntimeError("Ya existe un WOD el %s en ese programa (CrossHero solo permite uno por dia). "
                               "No se toco nada." % fecha.strftime("%d/%m/%Y"))
        raise RuntimeError("CrossHero rechazo el WOD: %s | campos: %s (url %s)" % (
            " | ".join(flash_messages(page)), "; ".join(e for e in errs if e) or "?", url))
    return url

# ------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--publicar", action="store_true")
    ap.add_argument("--ver", action="store_true", help="mostrar el navegador mientras baja los WODs")
    ap.add_argument("--prueba", metavar="AAAA-MM-DD",
                    help="publica SOLO un WOD (Power) en esa fecha, para probar; no toca la semana")
    ap.add_argument("--programa", help="id de programa de CrossHero para la prueba (opcional)")
    ap.add_argument("--semana-nueva", action="store_true",
                    help="modo programado: solo publica si el Hub ya muestra la semana que empieza el proximo lunes")
    a = ap.parse_args()
    if a.login: login_hyrox(); return 0
    cfg = load_config()
    if a.semana_nueva:
        hoy = dt.date.today()
        lunes = hoy + dt.timedelta(days=(7 - hoy.weekday()) % 7)   # domingo -> manana; lunes -> hoy
        estado = json.load(open(ESTADO)) if os.path.exists(ESTADO) else {}
        hechas = {k.split("|")[0] for k in estado}
        if all((lunes + dt.timedelta(days=i)).isoformat() in hechas for i in range(6)):
            return 0                                   # esa semana ya se publico; nada que hacer
    semana, inicio, wods = bajar_semana(headless=not a.ver, esperado=lunes if a.semana_nueva else None)
    if a.semana_nueva and inicio != lunes:
        log("La semana del %s todavia no sale en HYROX365 (el Hub muestra la del %s)." % (lunes, inicio))
        if hoy.weekday() == 0 and dt.datetime.now().hour >= 3:
            notify("CrossHero WODs", "HYROX todavia no publica la semana del %s" % lunes.strftime("%d/%m"))
        return 0
    # dia por nombre: Power/Engine/Complete del numero mas bajo = lun/mar/mie; del siguiente = jue/vie/sab
    info = []
    for L in wods:
        m = re.match(r"(Power|Engine|Complete)\s*#\s*(\d+)", L["name"])
        if m: info.append((int(m.group(2)), ORDEN.index(m.group(1)), L))
    if not info: raise RuntimeError("No encontre WODs Power/Engine/Complete en el Hub.")
    base = min(n for n, _, _ in info)
    plan = sorted(((inicio + dt.timedelta(days=(n - base) * 3 + k), L) for n, k, L in info
                   if 0 <= (n - base) * 3 + k <= 5), key=lambda x: x[0])
    if len(plan) < 6:
        faltan = sorted(set(range(6)) - {(f - inicio).days for f, _ in plan})
        dias = ["lun", "mar", "mie", "jue", "vie", "sab"]
        log("Aviso: faltan WODs para: %s" % ", ".join(dias[i] for i in faltan))
    log("HYROX semana %d (inicia %s): %s" % (semana, inicio, ", ".join(L["name"] for _, L in plan)))
    if a.prueba:
        fecha = dt.datetime.strptime(a.prueba, "%Y-%m-%d").date()
        L = plan[0][1]
        cli, coach = formatear(L)
        print("\n===== PRUEBA %s =====\n%s\n" % (fecha.strftime("%d/%m/%Y"), a_texto(cli)))
        ch = CrossHero(); ch.login(cfg["email"], get_password(cfg["email"]))
        prog = a.programa or cfg.get("wod_program_id", cfg["program_id"])
        url = publicar(ch, prog, fecha, cli, coach, cfg.get("wod_visibilidad", "Mismo día"))
        log("PRUEBA publicada: %s %s (programa %s) -> %s" % (fecha.strftime("%d/%m/%Y"), L["name"], prog, url))
        print("Revisa el calendario de CrossHero en %s y borra el WOD de prueba." % fecha.strftime("%d/%m/%Y"))
        return 0
    estado = json.load(open(ESTADO)) if os.path.exists(ESTADO) else {}
    ch = None
    saltados = []
    for fecha, L in plan:
        cli, coach = formatear(L)
        clave = "%s|%s" % (fecha.isoformat(), L["id"])
        if not a.publicar:
            print("\n===== %s  %s =====\n%s" % (fecha.strftime("%a %d/%m"), L["name"], a_texto(cli))); continue
        if clave in estado: log("Ya publicado: %s %s" % (fecha, L["name"])); continue
        if ch is None:
            ch = CrossHero(); ch.login(cfg["email"], get_password(cfg["email"]))
        try:
            publicar(ch, cfg.get("wod_program_id", cfg["program_id"]), fecha, cli, coach,
                     cfg.get("wod_visibilidad", "Mismo día"))
        except RuntimeError as e:
            if "Ya existe un WOD" in str(e):
                log("Saltado: %s" % e); saltados.append(fecha.strftime("%a %d/%m")); continue
            raise
        estado[clave] = L["name"]; json.dump(estado, open(ESTADO, "w"), indent=2)
        log("Publicado: %s %s" % (fecha.strftime("%d/%m/%Y"), L["name"]))
    if a.publicar:
        extra = (" (ya tenian WOD: %s)" % ", ".join(saltados)) if saltados else ""
        notify("CrossHero", "WODs HYROX semana %d publicados%s" % (semana, extra))
    else: print("\nModo prueba: no se publico nada. Usa --publicar para subirlos.")
    return 0

if __name__ == "__main__":
    try: sys.exit(main())
    except Exception as e:
        log("ERROR WODs: %s" % e); notify("CrossHero WODs", "Error: %s" % e); sys.exit(1)
