import os
import uuid
import json
import asyncio
import logging
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import (
    BASE_DIR, SUBJECTS_DIR, load_config, save_config
)
from app.database import (
    init_db, get_all_subjects, get_subject_by_id, upsert_subject, delete_subject,
    get_conversations_for_subject, create_conversation, delete_conversation,
    get_messages, add_message, get_files_for_subject, record_file, delete_file_meta,
    get_connection
)
from app.rag_engine import rag_engine
from app.sync_uned import sync_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="UNED Study Copilot", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize DB on startup
@app.on_event("startup")
def startup_event():
    init_db()
    # Create sample welcome subject if none exists so the student can immediately see how it looks
    subjects = get_all_subjects()
    if not subjects:
        welcome_id = "uned_demo_introduccion"
        upsert_subject(welcome_id, "📚 Guía de Bienvenida UNED", "DEMO-01")
        demo_folder = SUBJECTS_DIR / welcome_id
        demo_folder.mkdir(parents=True, exist_ok=True)
        demo_file = demo_folder / "Instrucciones_y_Bienvenida.txt"
        if not demo_file.exists():
            demo_content = """UNIVERSIDAD NACIONAL DE EDUCACIÓN A DISTANCIA (UNED)
GUÍA DE ESTUDIO Y FUNCIONAMIENTO DE UNED STUDY COPILOT

1. PRESENTACIÓN
¡Bienvenido a tu asistente de estudio inteligente para la UNED! Esta herramienta está diseñada específicamente para ayudarte con tus asignaturas del curso virtual (Ágora / Moodle).

2. CÓMO EMPEZAR
- Paso 1: Ve a Ajustes y añade tu clave gratuita de Google Gemini (o tu clave de OpenAI).
- Paso 2: Pulsa en "Sincronizar UNED" para que el programa entre automáticamente a tu campus virtual y descargue las guías y temarios oficiales.
- Paso 3: También puedes arrastrar tus propios apuntes, resúmenes o exámenes resueltos en el panel de archivos de la derecha.

3. PREGUNTAS QUE PUEDES HACER AL COPILOT
- "Hazme un resumen esquemático de los temas clave de la asignatura."
- "Genérame un examen tipo test de 5 preguntas con 4 opciones y soluciones comentadas."
- "Explícame este concepto complejo con un ejemplo cotidiano."
- "¿Cuáles son los criterios de evaluación y fechas según la guía docente?"
"""
            with open(demo_file, "w", encoding="utf-8") as f:
                f.write(demo_content)
            record_file(
                file_id=f"{welcome_id}_intro",
                subject_id=welcome_id,
                filename="Instrucciones_y_Bienvenida.txt",
                file_path=str(demo_file),
                file_size=len(demo_content.encode("utf-8")),
                source="demo"
            )
        
        # Create initial conversation
        conv_id = str(uuid.uuid4())
        create_conversation(conv_id, welcome_id, "Primeros pasos y bienvenida")
        add_message(
            conv_id, 
            "assistant", 
            "¡Hola! 👋 Soy tu copiloto de estudio para la UNED. He precargado esta asignatura de demostración para que veas cómo funciona. Cuando quieras, pulsa en **'Sincronizar UNED'** arriba para conectar tus asignaturas reales, o pregúntame lo que quieras sobre cómo empezar."
        )

# Mount static files
static_dir = BASE_DIR / "app" / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = static_dir / "index.html"
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>UNED Study Copilot iniciado</h1>")

# --- Settings Endpoints ---
class ConfigUpdate(BaseModel):
    ai_provider: Optional[str] = None
    gemini_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    openai_model: Optional[str] = None
    uned_username: Optional[str] = None
    onboarding_completed: Optional[bool] = None

@app.get("/api/config")
async def get_config():
    cfg = load_config()
    # Mask API keys for security in UI display
    masked_gemini = cfg.get("gemini_api_key", "")
    masked_openai = cfg.get("openai_api_key", "")
    return {
        "ai_provider": cfg.get("ai_provider", "gemini"),
        "gemini_api_key": masked_gemini,
        "openai_api_key": masked_openai,
        "gemini_model": cfg.get("gemini_model", "gemini-2.5-flash"),
        "openai_model": cfg.get("openai_model", "gpt-4o-mini"),
        "uned_username": cfg.get("uned_username", ""),
        "onboarding_completed": cfg.get("onboarding_completed", False),
        "has_gemini_key": bool(masked_gemini),
        "has_openai_key": bool(masked_openai)
    }

@app.post("/api/config")
async def update_config(update: ConfigUpdate):
    current = load_config()
    data = update.dict(exclude_unset=True)
    for k, v in data.items():
        if v is not None:
            current[k] = v
    saved = save_config(current)
    return {"status": "ok", "config": saved}

# --- Subjects Endpoints ---
class SubjectCreate(BaseModel):
    name: str
    code: Optional[str] = ""

@app.get("/api/subjects")
async def list_subjects():
    subjects = get_all_subjects()
    # Attach file counts
    for s in subjects:
        files = get_files_for_subject(s["id"])
        s["files_count"] = len(files)
    return subjects

@app.post("/api/subjects")
async def add_subject(sub: SubjectCreate):
    sub_id = f"custom_{uuid.uuid4().hex[:8]}"
    created = upsert_subject(sub_id, sub.name.strip(), sub.code.strip())
    sub_folder = SUBJECTS_DIR / sub_id
    sub_folder.mkdir(parents=True, exist_ok=True)
    return created

@app.delete("/api/subjects/{subject_id}")
async def remove_subject(subject_id: str):
    delete_subject(subject_id)
    sub_folder = SUBJECTS_DIR / subject_id
    if sub_folder.exists():
        import shutil
        shutil.rmtree(sub_folder, ignore_errors=True)
    return {"status": "ok"}

# --- Subject Files Endpoints ---
@app.get("/api/subjects/{subject_id}/files")
async def list_subject_files(subject_id: str):
    files = get_files_for_subject(subject_id)
    return files

@app.post("/api/subjects/{subject_id}/files")
async def upload_subject_file(subject_id: str, file: UploadFile = File(...)):
    sub = get_subject_by_id(subject_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Asignatura no encontrada")

    sub_folder = SUBJECTS_DIR / subject_id
    sub_folder.mkdir(parents=True, exist_ok=True)

    filename = sync_manager.sanitize_filename(file.filename)
    dest_path = sub_folder / filename

    content = await file.read()
    with open(dest_path, "wb") as f:
        f.write(content)

    file_id = f"{subject_id}_{filename}"
    record_file(
        file_id=file_id,
        subject_id=subject_id,
        filename=filename,
        file_path=str(dest_path),
        file_size=len(content),
        source="manual"
    )

    return {"status": "ok", "filename": filename, "size": len(content)}

@app.delete("/api/files/{file_id}")
async def remove_file(file_id: str):
    # Find in DB
    # We can search through subjects or delete directly
    delete_file_meta(file_id)
    return {"status": "ok"}

# --- Conversations & Chat Endpoints ---
class ConversationCreate(BaseModel):
    title: Optional[str] = "Nueva conversación"

@app.get("/api/subjects/{subject_id}/conversations")
async def list_conversations(subject_id: str):
    return get_conversations_for_subject(subject_id)

@app.post("/api/subjects/{subject_id}/conversations")
async def new_conversation(subject_id: str, conv: ConversationCreate):
    conv_id = str(uuid.uuid4())
    created = create_conversation(conv_id, subject_id, conv.title)
    return created

@app.delete("/api/conversations/{conv_id}")
async def remove_conversation(conv_id: str):
    delete_conversation(conv_id)
    return {"status": "ok"}

@app.get("/api/conversations/{conv_id}/messages")
async def list_messages(conv_id: str):
    return get_messages(conv_id)

class SendMessageRequest(BaseModel):
    content: str

@app.post("/api/conversations/{conv_id}/messages")
async def post_message(conv_id: str, req: SendMessageRequest):
    user_query = req.content.strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="El mensaje no puede estar vacío.")

    # Get conversation details
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT c.subject_id, s.name as subject_name FROM conversations c JOIN subjects s ON c.subject_id = s.id WHERE c.id = ?", (conv_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Conversación no encontrada.")
        subject_id, subject_name = row["subject_id"], row["subject_name"]

    # Save user message
    add_message(conv_id, "user", user_query)

    # Get chat history for context
    history_messages = get_messages(conv_id)
    # Format for RAG
    formatted_history = [{"role": m["role"], "content": m["content"]} for m in history_messages[:-1]]

    # Query RAG Engine
    try:
        assistant_reply, sources = rag_engine.answer_query(
            subject_id=subject_id,
            subject_name=subject_name,
            conversation_history=formatted_history,
            query=user_query
        )
    except Exception as e:
        logger.error(f"Error generando respuesta: {e}")
        assistant_reply = f"⚠️ Ocurrió un problema al consultar la IA:\n\n`{str(e)}`\n\nPor favor, comprueba tu clave de API en los **Ajustes** (icono de engranaje)."
        sources = []

    # Save assistant message
    msg_id = add_message(conv_id, "assistant", assistant_reply, sources=sources)

    return {
        "id": msg_id,
        "role": "assistant",
        "content": assistant_reply,
        "sources": sources
    }

# --- UNED Synchronization Endpoints ---
@app.post("/api/sync/start")
async def start_sync_endpoint(background_tasks: BackgroundTasks):
    if sync_manager.is_running:
        return {"status": "already_running", "message": "Ya hay una sincronización en marcha."}
    
    cfg = load_config()
    username = cfg.get("uned_username", "")
    headless = cfg.get("browser_headless", False)

    # Run in background
    async def run_in_bg():
        async for _ in sync_manager.run_sync(username_prefill=username, headless=headless):
            pass

    asyncio.create_task(run_in_bg())
    return {"status": "started", "message": "Sincronización iniciada con éxito."}

@app.get("/api/sync/status")
async def get_sync_status():
    return {
        "is_running": sync_manager.is_running,
        "status": sync_manager.status,
        "logs": sync_manager.logs[-20:] # last 20 logs
    }

@app.get("/api/sync/stream")
async def sync_stream():
    """Server-Sent Events for real-time progress feedback."""
    async def event_generator():
        last_log_count = 0
        while True:
            if len(sync_manager.logs) > last_log_count:
                new_logs = sync_manager.logs[last_log_count:]
                last_log_count = len(sync_manager.logs)
                for log in new_logs:
                    data = json.dumps({"type": "log", "message": log}, ensure_ascii=False)
                    yield f"data: {data}\n\n"

            if not sync_manager.is_running and last_log_count > 0:
                data = json.dumps({"type": "status", "status": "completed"}, ensure_ascii=False)
                yield f"data: {data}\n\n"
                break

            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
