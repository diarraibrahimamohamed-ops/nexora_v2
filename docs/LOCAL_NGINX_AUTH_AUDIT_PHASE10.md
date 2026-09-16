# Audit local Nginx + authentification — Phase 10

## Résultat
- Aucun `deny all`, `auth_basic` ou `return 403` dans la configuration Nginx.
- `/api/` est explicitement proxyfié vers FastAPI sans interception des erreurs API par Nginx.
- `/` et les fichiers frontend sont servis sans permettre à une URL de répertoire de devenir une réponse Nginx 403 de type index manquant.
- `/health` est une route de diagnostic locale explicite.
- Le JWT respecte maintenant le choix `remember`: `localStorage` uniquement avec l'option activée, sinon `sessionStorage`.
- Le pont frontend ne copie plus un token de session vers `localStorage`.

## Distinction attendue
Un `403` FastAPI doit apparaître comme une réponse JSON (par exemple pour une ressource réservée aux administrateurs), tandis qu'une page HTML `403 Forbidden nginx/...` indique que la requête a été rejetée par Nginx avant la réponse FastAPI.

## Test local recommandé
Après `docker compose up --build`, depuis l'hôte:

```bash
curl -i http://localhost/
curl -i http://localhost/nexora.html
curl -i http://localhost/health
curl -i http://localhost/api/v1/health
curl -i -X POST http://localhost/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"bad@example.invalid","password":"bad"}'
```

Pour le dernier appel, un `401`/`429` JSON est attendu selon l'état du mécanisme de lockout. Une page HTML signée Nginx n'est pas le comportement attendu.
