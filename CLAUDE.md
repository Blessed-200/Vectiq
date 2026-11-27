# Instrucciones para agentes en este repositorio

1. Lee primero `docs/CONTEXTO.md`: es el estado vigente (qué está en producción, qué es sombra, qué datos están contaminados). Los demás documentos de `docs/` son historia.
2. El dueño habla español, no es técnico: responde en lenguaje llano, sin rodeos, honesto con la incertidumbre. Tú decides con matemática; no sigas ciegamente ninguna opinión externa.
3. Producción (ST3, ST5, ST0) no se toca sin pruebas de regresión y sin avisar. La sombra (`signals/sombra.py`) nunca debe enviar a Telegram ni afectar a las alertas.
4. Pruebas: `PYTHONPATH=src python -m pytest -q` debe pasar antes de publicar. El dueño autorizó publicar directo a `main` tras pruebas.
5. No leas ni expongas `users.json`.
