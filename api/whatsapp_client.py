"""
whatsapp_client.py — wrapper delgado para enviar/recibir mensajes de
WhatsApp vía Kapso (capa sobre la WhatsApp Cloud API de Meta).

Por qué Kapso y no Meta directo: evita el papeleo de App Review y
verificación de negocio de Meta — Kapso ya tiene el número conectado
y expone una API compatible con el formato de Meta.

Requiere las variables de entorno:
  KAPSO_API_KEY            (Project Settings → API Keys en el dashboard de Kapso)
  KAPSO_PHONE_NUMBER_ID    (el que Kapso te asigna al conectar tu número)

Configuración del webhook (se hace UNA VEZ, no en este código):
  Registra un webhook de tipo "meta" en Kapso para que te reenvíe el
  payload EXACTO de Meta sin modificar — así extract_incoming_message()
  funciona sin cambios respecto a una integración directa con Meta:

    curl -X POST https://api.kapso.ai/platform/v1/whatsapp/phone_numbers/{KAPSO_PHONE_NUMBER_ID}/webhooks \\
      -H "X-API-Key: {KAPSO_API_KEY}" \\
      -H "Content-Type: application/json" \\
      -d '{
        "whatsapp_webhook": {
          "kind": "meta",
          "url": "https://tu-proyecto.vercel.app/api/whatsapp/webhook",
          "active": true
        }
      }'
"""

from __future__ import annotations

import os

KAPSO_API_BASE = "https://api.kapso.ai"
GRAPH_API_VERSION = "v24.0"


def _env(name: str) -> str | None:
    """Lee una variable de entorno limpiando espacios y comillas que se
    cuelan al pegar valores en el dashboard de Vercel."""
    value = os.environ.get(name)
    if value is None:
        return None
    cleaned = value.strip().strip('"').strip("'").strip()
    return cleaned or None


KAPSO_API_KEY = _env("KAPSO_API_KEY")
KAPSO_PHONE_NUMBER_ID = _env("KAPSO_PHONE_NUMBER_ID")


def is_configured() -> bool:
    return bool(KAPSO_API_KEY and KAPSO_PHONE_NUMBER_ID)


def send_whatsapp_message(to: str, text: str) -> dict:
    """Envía un mensaje de texto plano por WhatsApp vía Kapso. Si no hay
    credenciales configuradas, no falla — regresa un resultado "stub"
    para poder probar el flujo de bloqueo/permiso sin mandar mensajes
    reales todavía."""
    if not is_configured():
        return {"stub": True, "to": to, "text": text}

    import requests  # import perezoso

    url = f"{KAPSO_API_BASE}/meta/whatsapp/{GRAPH_API_VERSION}/{KAPSO_PHONE_NUMBER_ID}/messages"
    headers = {
        "X-API-Key": KAPSO_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }

    response = requests.post(url, json=payload, headers=headers, timeout=10)
    response.raise_for_status()
    return {"stub": False, "to": to, "response": response.json()}


def extract_incoming_message(payload: dict) -> dict | None:
    """Extrae el primer mensaje de texto entrante del payload estándar
    de webhook de Meta (Kapso lo reenvía sin modificar cuando el
    webhook se registra con kind="meta"). Regresa None si el payload
    no contiene un mensaje de texto (puede ser un evento de status,
    confirmación de entrega, etc.)."""
    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]
        value = change["value"]
        messages = value.get("messages")
        if not messages:
            return None
        message = messages[0]
        if message.get("type") != "text":
            return None
        return {
            "from_number": message["from"],
            "text": message["text"]["body"],
            "message_id": message.get("id"),
        }
    except (KeyError, IndexError, TypeError):
        return None
