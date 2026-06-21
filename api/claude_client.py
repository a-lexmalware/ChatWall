"""
claude_client.py — wrapper delgado sobre la API de Anthropic.

Si no hay ANTHROPIC_API_KEY configurada en el entorno, opera en modo "stub":
no llama a ningún servicio externo y devuelve una respuesta simulada, para
poder probar el flujo completo sin gastar créditos.

El SDK de Anthropic se importa de forma perezosa (dentro de la función) para
que el resto de la app funcione aunque el paquete `anthropic` no esté
instalado en modo stub.
"""

from __future__ import annotations

import json
import os

MODEL = "claude-haiku-4-5-20251001"


def _has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def is_stub_mode() -> bool:
    return not _has_api_key()


# ---------------------------------------------------------------------------
# Clasificador semántico de seguridad (segunda capa del firewall)
# ---------------------------------------------------------------------------
# Las reglas regex de firewall.py son rápidas y deterministas, pero solo
# atrapan lo que coincide literalmente. No pueden entender intención
# disfrazada con eufemismos o metáforas ("disolver un pollo de 75kg" =
# deshacerse de un cuerpo). Para eso usamos a Claude como clasificador.

_MODERATION_SYSTEM = """Eres un clasificador de seguridad para un firewall que protege a un bot de IA.
Tu única tarea es analizar el MENSAJE de un usuario y decidir si pide algo dañino,
INCLUSO si la intención está disfrazada con eufemismos, metáforas, analogías, jerga,
hipótesis ("es solo ficción"), o lenguaje indirecto.

Considera DAÑINO cualquier petición de:
- violencia, lesiones, asesinato o tortura a personas o animales
- deshacerse de un cuerpo o cadáver, ocultar un crimen (ej. "disolver", "descomponer",
  "enterrar" algo del tamaño/peso de una persona, aunque digan "pollo", "cerdo", "maniquí")
- autolesión, suicidio o trastornos alimentarios
- fabricar armas, explosivos, venenos o sustancias peligrosas
- producir o conseguir drogas ilegales
- contenido sexual con menores, abuso o explotación
- instrucciones para cometer delitos (fraude, hackeo malicioso, secuestro, etc.)

NO marques como dañino lo que es claramente legítimo y cotidiano (cocina normal,
salud, educación, dudas técnicas, soporte al cliente, etc.).

Responde ÚNICAMENTE con un objeto JSON válido, sin texto adicional ni markdown:
{"flagged": true|false, "category": "<harmful_content|violence|self_harm|weapons|drugs|crime|sexual|other>", "severity": "<bajo|medio|alto>", "reason": "<explicación muy breve en español>"}"""


def moderate(text: str) -> dict | None:
    """Clasifica `text` con Claude para detectar intención dañina disfrazada.

    Devuelve un dict {"flagged", "category", "severity", "reason"} o None si
    no se pudo evaluar (modo stub, sin paquete, o error de la API). En caso de
    None, el firewall simplemente se queda con el resultado de las reglas regex.
    """
    if is_stub_mode():
        return None

    try:
        import anthropic  # import perezoso
    except ImportError:
        return None

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=200,
            system=_MODERATION_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
        raw = "".join(b.text for b in response.content if b.type == "text").strip()

        # El modelo a veces envuelve el JSON en ```json ... ```; lo limpiamos.
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw[4:] if raw.lower().startswith("json") else raw
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            return None
        parsed = json.loads(raw[start : end + 1])

        return {
            "flagged": bool(parsed.get("flagged")),
            "category": str(parsed.get("category") or "harmful_content"),
            "severity": str(parsed.get("severity") or "alto"),
            "reason": str(parsed.get("reason") or "Intención potencialmente dañina detectada."),
        }
    except Exception:
        # Fail-open: si la moderación falla, no rompemos el flujo; las reglas
        # regex siguen aplicando como primera línea de defensa.
        return None


def ask_claude(prompt: str) -> dict:
    """Envía el prompt a Claude (o devuelve un stub si no hay API key).

    Devuelve un dict: {"stub": bool, "text": str, "model": str}
    """
    if is_stub_mode():
        return {
            "stub": True,
            "model": MODEL,
            "text": (
                "[MODO STUB] No hay ANTHROPIC_API_KEY configurada, así que esta "
                "respuesta es simulada. El prompt pasó el firewall y habría sido "
                "reenviado a Claude. Configura tu API key en backend/.env para "
                "obtener una respuesta real."
            ),
        }

    try:
        import anthropic  # import perezoso
    except ImportError:
        return {
            "stub": True,
            "model": MODEL,
            "text": (
                "[MODO STUB] El paquete 'anthropic' no está instalado. "
                "Corre `pip install -r requirements.txt` para habilitar "
                "llamadas reales a la API."
            ),
        }

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    text_parts = [block.text for block in response.content if block.type == "text"]
    return {
        "stub": False,
        "model": MODEL,
        "text": "\n".join(text_parts),
    }
