"""
api/index.py — entrypoint único del AI WAF para Vercel.

Vercel ejecuta este archivo como una función serverless de Python; el
archivo vercel.json reescribe cualquier request a /api/* hacia esta
misma función, y FastAPI internamente decide la ruta exacta según el
path original (por eso las rutas aquí abajo ya incluyen el prefijo
/api/...).

Endpoints:
  POST /api/analyze              -> probar un prompt manualmente (dashboard)
  GET  /api/history?limit=N      -> últimos N análisis
  GET  /api/stats                -> totales y desglose por nivel de riesgo
  POST /api/whatsapp/webhook     -> mensajes entrantes de WhatsApp (vía Kapso)
  POST /api/web/message          -> mensajes entrantes de un widget de chat web

Nota sobre Kapso: el webhook de WhatsApp ya NO necesita un endpoint
GET de verificación (ese era el challenge/response clásico de Meta).
Con Kapso, el webhook se registra una sola vez llamando a SU API
(ver api/whatsapp_client.py), no desde aquí.

Todos los canales comparten la MISMA función `_run_firewall_and_log`
— es la prueba de que el producto no está atado a un solo canal.
"""

from __future__ import annotations

import os
import sys

# Vercel ejecuta este archivo con un loader que NO agrega su propia
# carpeta a sys.path automáticamente (a diferencia de correr
# `python index.py` directo). Sin esto, los imports de los módulos
# hermanos (claude_client, firewall, storage, whatsapp_client) fallan
# en producción con ModuleNotFoundError, aunque los archivos sí estén
# presentes junto a este.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dataclasses import asdict

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import claude_client
import firewall
import storage
import whatsapp_client

app = FastAPI(title="AI WAF", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mensaje que recibe el usuario cuando su mensaje se bloquea. Genérico
# a propósito: no revela qué regla se disparó (eso sería darle
# información a un atacante para que ajuste su próximo intento).
BLOCKED_REPLY = (
    "No puedo procesar ese mensaje. Si crees que esto es un error, "
    "intenta reformularlo o contacta a soporte."
)


# ---------------------------------------------------------------------------
# Núcleo compartido: el mismo firewall para cualquier canal
# ---------------------------------------------------------------------------

def _run_firewall_and_log(
    text: str,
    channel: str,
    from_number: str | None = None,
    forward: bool = True,
) -> dict:
    """Corre el firewall sobre `text`, decide bloquear o reenviar al
    bot, registra el resultado en Supabase, y regresa un dict con todo
    lo necesario para que cada canal (WhatsApp, web, etc.) construya
    su propia respuesta. Esta función NO sabe nada de WhatsApp ni de
    HTTP — por diseño, para que cualquier canal nuevo la reuse igual."""
    # Capa 1 — reglas regex: rápidas y deterministas.
    score, matches = firewall.analyze(text)
    matches_payload = [asdict(m) for m in matches]

    # Capa 2 — clasificador semántico (Claude): solo si las reglas no
    # bloquearon ya. Atrapa intención dañina disfrazada con eufemismos o
    # metáforas que el regex no puede ver (p. ej. "disolver un pollo de 75kg").
    if not firewall.is_blocked(score):
        verdict_ai = claude_client.moderate(text)
        if verdict_ai and verdict_ai["flagged"]:
            weight = {"alto": 70, "medio": 35, "bajo": 15}.get(verdict_ai["severity"], 70)
            matches_payload.append(
                {
                    "rule_id": "AI-MOD",
                    "category": "harmful_content",
                    "weight": weight,
                    "description": "Clasificador de IA: " + verdict_ai["reason"],
                    "snippet": text[:80] + ("…" if len(text) > 80 else ""),
                }
            )
            score = min(100, score + weight)

    level = firewall.risk_level(score)
    blocked = firewall.is_blocked(score)

    bot_text = None
    claude_stub = None
    claude_model = None
    forwarded = False

    if blocked:
        bot_text = BLOCKED_REPLY
    elif forward:
        forwarded = True
        claude_result = claude_client.ask_claude(text)
        claude_stub = claude_result["stub"]
        claude_model = claude_result["model"]
        bot_text = claude_result["text"]

    record_id = storage.save_analysis(
        prompt=text,
        risk_score=score,
        risk_level_=level,
        blocked=blocked,
        forwarded=forwarded,
        matches=matches_payload,
        claude_text=bot_text,
        claude_stub=claude_stub,
        channel=channel,
        from_number=from_number,
        bot_response=bot_text,
    )

    return {
        "id": record_id,
        "blocked": blocked,
        "risk_score": score,
        "risk_level": level,
        "matches": matches_payload,
        "reply": bot_text,
        "claude_stub": claude_stub,
        "claude_model": claude_model,
    }


# ---------------------------------------------------------------------------
# Dashboard / prueba manual
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    prompt: str
    forward: bool = True


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    result = _run_firewall_and_log(
        req.prompt, channel="dashboard", forward=req.forward
    )

    claude_payload = None
    if result["claude_stub"] is not None:
        claude_payload = {
            "stub": result["claude_stub"],
            "model": result["claude_model"],
            "text": result["reply"],
        }

    return {
        "id": result["id"],
        "verdict": {
            "risk_score": result["risk_score"],
            "risk_level": result["risk_level"],
            "blocked": result["blocked"],
            "matches": result["matches"],
        },
        "claude": claude_payload,
    }


@app.get("/api/history")
def history(limit: int = Query(default=20, ge=1, le=200)):
    return {"items": storage.get_history(limit=limit)}


@app.get("/api/stats")
def stats():
    return storage.get_stats()


# ---------------------------------------------------------------------------
# Canal: WhatsApp (vía Kapso)
# ---------------------------------------------------------------------------

@app.post("/api/whatsapp/webhook")
async def receive_whatsapp(request: Request):
    """Recibe mensajes entrantes de WhatsApp vía Kapso. SIEMPRE responde 200,
    o el proveedor reintenta el webhook en bucle pensando que falló (por eso
    el envío de la respuesta va envuelto en try/except: si Kapso rechaza el
    envío, el análisis ya quedó registrado y no queremos romper el webhook)."""
    try:
        payload = await request.json()
    except Exception:
        return {"status": "ignored"}

    incoming = whatsapp_client.extract_incoming_message(payload)
    if incoming is None:
        # No es un mensaje de texto entrante (status, entrega, saliente…).
        return {"status": "ignored"}

    result = _run_firewall_and_log(
        incoming["text"], channel="whatsapp", from_number=incoming["from_number"]
    )

    try:
        whatsapp_client.send_whatsapp_message(incoming["from_number"], result["reply"])
        sent = True
    except Exception:
        sent = False

    return {
        "status": "blocked" if result["blocked"] else "forwarded",
        "reply_sent": sent,
    }


# ---------------------------------------------------------------------------
# Canal: widget de chat web (genérico — demuestra que no depende de WhatsApp)
# ---------------------------------------------------------------------------

class WebMessageRequest(BaseModel):
    session_id: str
    message: str


@app.post("/api/web/message")
def receive_web_message(req: WebMessageRequest):
    """Mismo firewall, canal distinto. Pensado para un widget de chat
    embebido en cualquier sitio web (ver public/widget-demo.html)."""
    result = _run_firewall_and_log(
        req.message, channel="web", from_number=req.session_id
    )
    return {"blocked": result["blocked"], "reply": result["reply"]}


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "whatsapp_configured": whatsapp_client.is_configured(),
        "claude_stub_mode": claude_client.is_stub_mode(),
    }


@app.get("/api/debug-fs")
def debug_fs():
    """Endpoint TEMPORAL de diagnóstico — bórralo una vez que el sitio
    cargue bien. Muestra exactamente qué archivos/carpetas existen en
    producción junto a este script, para confirmar si `public/` llegó
    al deploy o no."""
    here = Path(__file__).resolve().parent
    parent = here.parent

    def safe_listdir(p: Path):
        try:
            return sorted(os.listdir(p))
        except Exception as e:
            return f"ERROR: {e}"

    public_dir = parent / "static"

    return {
        "__file__": str(Path(__file__).resolve()),
        "dir_of_this_file": str(here),
        "contents_of_dir_of_this_file": safe_listdir(here),
        "parent_dir": str(parent),
        "contents_of_parent_dir": safe_listdir(parent),
        "public_dir_checked": str(public_dir),
        "public_dir_exists": public_dir.exists(),
        "public_dir_contents": safe_listdir(public_dir) if public_dir.exists() else None,
    }


# ---------------------------------------------------------------------------
# Archivos estáticos (dashboard + widget-demo)
# ---------------------------------------------------------------------------
# Vercel detectó este proyecto como "FastAPI" y enruta TODAS las rutas
# (no solo /api/*) hacia esta función — incluido "/". Por eso la propia
# app sirve los estáticos directamente, en vez de depender del hosting
# estático nativo de Vercel. Este mount va AL FINAL: las rutas /api/*
# de arriba siempre tienen prioridad sobre este catch-all.
from pathlib import Path

from fastapi.staticfiles import StaticFiles

# OJO: la carpeta NO puede llamarse "public" — ese nombre es reservado
# en Vercel y su contenido se sube al CDN estático y se EXCLUYE del
# bundle de la función serverless, por lo que aquí no existiría. Por eso
# se llama "static": así viaja dentro de la función y la app la sirve.
_PUBLIC_DIR = Path(__file__).resolve().parent.parent / "static"
if _PUBLIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_PUBLIC_DIR), html=True), name="static")