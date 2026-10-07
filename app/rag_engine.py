import os
import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Tuple
import requests
from pypdf import PdfReader
from app.config import SUBJECTS_DIR, load_config

logger = logging.getLogger(__name__)

class DocumentExtractor:
    @staticmethod
    def extract_text(file_path: Path) -> List[Dict[str, Any]]:
        """Extracts text by page or section. Returns list of {page: int, text: str}."""
        if not file_path.exists():
            return []
        
        ext = file_path.suffix.lower()
        chunks = []
        
        if ext == ".pdf":
            try:
                reader = PdfReader(str(file_path))
                for idx, page in enumerate(reader.pages):
                    text = page.extract_text() or ""
                    clean = re.sub(r'\s+', ' ', text).strip()
                    if clean:
                        chunks.append({"page": idx + 1, "text": clean})
            except Exception as e:
                logger.error(f"Error reading PDF {file_path.name}: {e}")
        elif ext in [".txt", ".md", ".csv"]:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                    clean = re.sub(r'\s+', ' ', text).strip()
                    if clean:
                        chunks.append({"page": 1, "text": clean})
            except Exception as e:
                logger.error(f"Error reading text file {file_path.name}: {e}")
        return chunks

class RAGEngine:
    def __init__(self):
        pass

    def get_subject_documents(self, subject_id: str) -> List[Dict[str, Any]]:
        subject_path = SUBJECTS_DIR / subject_id
        if not subject_path.exists():
            return []
        
        docs = []
        for file_path in subject_path.iterdir():
            if file_path.is_file() and file_path.suffix.lower() in [".pdf", ".txt", ".md", ".csv"]:
                pages = DocumentExtractor.extract_text(file_path)
                docs.append({
                    "filename": file_path.name,
                    "path": str(file_path),
                    "pages": pages
                })
        return docs

    def retrieve_relevant_context(self, subject_id: str, query: str, max_chars: int = 40000) -> Tuple[str, List[Dict[str, Any]]]:
        docs = self.get_subject_documents(subject_id)
        if not docs:
            return "", []

        # Simple yet effective score based on terms in the query
        keywords = set(re.findall(r'\b[a-zA-ZáéíóúÁÉÍÓÚñÑ]{3,}\b', query.lower()))
        
        scored_sections = []
        for doc in docs:
            for p in doc["pages"]:
                text_lower = p["text"].lower()
                # Count matches
                score = sum(1 for kw in keywords if kw in text_lower)
                # If guide or syllabus, give extra boost
                if "guia" in doc["filename"].lower() or "temario" in doc["filename"].lower():
                    score += 1
                scored_sections.append({
                    "filename": doc["filename"],
                    "page": p["page"],
                    "text": p["text"],
                    "score": score
                })

        # Sort by score descending
        scored_sections.sort(key=lambda x: x["score"], reverse=True)

        context_parts = []
        sources = []
        current_len = 0

        # Always include top sections
        for sec in scored_sections:
            sec_text = f"--- [Documento: {sec['filename']} (Pág. {sec['page']})] ---\n{sec['text']}\n"
            if current_len + len(sec_text) > max_chars and current_len > 0:
                break
            context_parts.append(sec_text)
            current_len += len(sec_text)
            
            # Record source if not already recorded
            src_key = {"filename": sec["filename"], "page": sec["page"]}
            if not any(s["filename"] == sec["filename"] and s["page"] == sec["page"] for s in sources):
                sources.append(src_key)
            if len(sources) >= 5:
                break

        full_context = "\n".join(context_parts)
        return full_context, sources

    def query_gemini(self, api_key: str, model_name: str, system_prompt: str, chat_history: List[Dict[str, str]], user_message: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        
        # Build contents structure for Gemini
        contents = []
        
        for msg in chat_history:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })
            
        contents.append({
            "role": "user",
            "parts": [{"text": user_message}]
        })

        payload = {
            "system_instruction": {
                "parts": [{"text": system_prompt}]
            },
            "contents": contents,
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 4096
            }
        }

        resp = requests.post(url, json=payload, timeout=60)
        if resp.status_code != 200:
            error_data = resp.json().get("error", {})
            err_msg = error_data.get("message", resp.text)
            raise Exception(f"Gemini API Error ({resp.status_code}): {err_msg}")

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return "No se pudo generar respuesta del modelo."
        
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join([p.get("text", "") for p in parts])

    def query_openai(self, api_key: str, model_name: str, system_prompt: str, chat_history: List[Dict[str, str]], user_message: str) -> str:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        messages = [{"role": "system", "content": system_prompt}]
        for msg in chat_history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": user_message})

        payload = {
            "model": model_name or "gpt-4o-mini",
            "messages": messages,
            "temperature": 0.3
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code != 200:
            error_data = resp.json().get("error", {})
            err_msg = error_data.get("message", resp.text)
            raise Exception(f"OpenAI API Error ({resp.status_code}): {err_msg}")

        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def answer_query(self, subject_id: str, subject_name: str, conversation_history: List[Dict[str, str]], query: str) -> Tuple[str, List[Dict[str, Any]]]:
        config = load_config()
        provider = config.get("ai_provider", "gemini")
        
        context_text, sources = self.retrieve_relevant_context(subject_id, query)
        
        has_documents = bool(context_text.strip())
        
        system_prompt = f"""Eres el Asistente de Estudio Oficial para la asignatura de la UNED: '{subject_name}'.
Tu objetivo es ayudar al estudiante a entender la materia, resolver dudas de conceptos, preparar exámenes tipo test o de desarrollo, y explicar los criterios de evaluación.

NORMAS CRÍTICAS:
1. Responde basándote rigurosamente en los documentos oficiales y apuntes facilitados a continuación.
2. Si los documentos responden la pregunta, cita el documento específico y la página correspondiente.
3. Si los documentos no contienen la respuesta exacta, indícalo con honestidad al alumno ("En los documentos disponibles no se especifica explícitamente X, pero según la materia...").
4. Sé pedagógico, claro, bien estructurado con viñetas y títulos en formato Markdown.
5. Puedes generar preguntas tipo test de autoevaluación, resúmenes o explicaciones paso a paso si el alumno te lo pide.

DOCUMENTOS DE LA ASIGNATURA DISPONIBLES:
{context_text if has_documents else "Actualmente no hay documentos ni temarios cargados para esta asignatura. Indica al alumno que puede sincronizar con la UNED o arrastrar PDFs en el panel lateral para que puedas responder en base a su temario oficial."}
"""

        # Take last 8 messages for context memory
        trimmed_history = conversation_history[-8:] if len(conversation_history) > 8 else conversation_history

        if provider == "gemini":
            api_key = config.get("gemini_api_key", "").strip()
            if not api_key:
                raise Exception("Falta configurar la clave de Google Gemini API en Ajustes.")
            model = config.get("gemini_model", "gemini-2.5-flash")
            # Fallback to gemini-1.5-flash if needed
            answer = self.query_gemini(api_key, model, system_prompt, trimmed_history, query)
        else:
            api_key = config.get("openai_api_key", "").strip()
            if not api_key:
                raise Exception("Falta configurar la clave de OpenAI API en Ajustes.")
            model = config.get("openai_model", "gpt-4o-mini")
            answer = self.query_openai(api_key, model, system_prompt, trimmed_history, query)

        return answer, sources

rag_engine = RAGEngine()
