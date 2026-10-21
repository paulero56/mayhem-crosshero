# Mayhem -> CrossHero (en la nube)

Publica cada semana los WODs de Mayhem (SugarWOD, track "Workout of the Day") en
CrossHero, programa CINCO DOS MAYHEM. Corre en GitHub Actions: jueves a domingo 8 pm y
domingo 10 pm (hora de Hermosillo). No necesita la Mac.

Secrets del repositorio (Settings -> Secrets and variables -> Actions):
- CROSSHERO_EMAIL
- CROSSHERO_PASSWORD
- SUGARWOD_API_KEY

Si un dia ya tiene WOD en CrossHero, se salta (no duplica).
Prueba manual: pestaña Actions -> "Publicar WODs Mayhem en CrossHero" -> Run workflow (modo prueba).
