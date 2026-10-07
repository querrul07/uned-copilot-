import os
import sys
import json
import time
import asyncio
import logging
import re
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, List
from playwright.async_api import async_playwright, BrowserContext, Page
from app.config import SESSIONS_DIR, SUBJECTS_DIR, load_config
from app.database import upsert_subject, record_file

logger = logging.getLogger(__name__)

SESSION_STATE_FILE = SESSIONS_DIR / "uned_session.json"
SSO_LOGIN_URL = "https://sso.uned.es/sso/index.aspx?URL=https://login.uned.es/ssouned/login.jsp"
CAMPUS_COURSES_URL = "https://cursosvirtuales.uned.es"

class UNEDSyncManager:
    def __init__(self):
        self.is_running = False
        self.logs: List[str] = []
        self.status = "idle"

    def add_log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        self.logs.append(entry)
        logger.info(entry)

    def sanitize_filename(self, name: str) -> str:
        clean = re.sub(r'[\\/*?:"<>|]', "", name).strip()
        return clean[:120] if clean else "documento"

    async def run_sync(self, username_prefill: str = "", headless: bool = False) -> AsyncGenerator[Dict[str, Any], None]:
        if self.is_running:
            yield {"status": "busy", "message": "Ya hay una sincronización en curso."}
            return

        self.is_running = True
        self.logs = []
        self.status = "running"
        self.add_log("Iniciando motor de sincronización de la UNED...")
        yield {"status": "starting", "message": "Iniciando navegador..."}

        playwright = None
        browser = None
        try:
            playwright = await async_playwright().start()
            
            # Use persistent state if available
            storage_state = str(SESSION_STATE_FILE) if SESSION_STATE_FILE.exists() else None

            # Launch visible browser so the student can verify login or solve 2FA if needed
            self.add_log(f"Abriendo navegador Chromium ({'oculto' if headless else 'visible'})...")
            browser = await playwright.chromium.launch(
                headless=headless,
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"]
            )
            
            context: BrowserContext = await browser.new_context(
                storage_state=storage_state,
                viewport=None,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            page: Page = await context.new_page()

            # Step 1: Navigate to Courses or SSO
            self.add_log("Comprobando sesión en Cursos Virtuales de la UNED...")
            yield {"status": "checking_session", "message": "Comprobando si la sesión previa sigue activa..."}

            await page.goto(CAMPUS_COURSES_URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)

            # Check if redirected to SSO login page
            current_url = page.url
            if "sso.uned.es" in current_url or "login" in current_url.lower():
                self.add_log("Sesión no iniciada. Redirigiendo a pantalla de acceso de la UNED...")
                yield {
                    "status": "waiting_login", 
                    "message": "Por favor, introduce tu usuario y contraseña en la ventana del navegador de la UNED y pulsa 'Enviar'."
                }

                # If username provided, try to prefill
                if username_prefill:
                    try:
                        user_input = page.locator('input[name="username"], input[name="txtUsuario"], #username, input[type="text"]').first
                        if await user_input.is_visible():
                            await user_input.fill(username_prefill)
                    except Exception:
                        pass

                # Wait for user to successfully log in and reach campus or courses
                self.add_log("Esperando a que completes el inicio de sesión en el navegador...")
                
                # Polling wait until URL is inside UNED campus/cursos and not login
                max_wait_seconds = 300  # 5 minutes
                start_time = time.time()
                logged_in = False

                while time.time() - start_time < max_wait_seconds:
                    cur_url = page.url
                    # Success condition: courses dashboard or portal logged in
                    if ("cursosvirtuales.uned.es" in cur_url or "campus.uned.es" in cur_url or "uned.es/portal" in cur_url) and "sso" not in cur_url and "login" not in cur_url:
                        logged_in = True
                        break
                    await asyncio.sleep(2)

                if not logged_in:
                    raise Exception("Tiempo de espera agotado para el inicio de sesión en la UNED.")

                # Save authenticated storage state
                self.add_log("¡Inicio de sesión exitoso! Guardando credenciales de sesión localmente...")
                yield {"status": "logged_in", "message": "Inicio de sesión correcto. Guardando sesión..."}
                await context.storage_state(path=str(SESSION_STATE_FILE))

            # Now we are logged in, make sure we are at cursosvirtuales.uned.es
            if "cursosvirtuales.uned.es" not in page.url:
                self.add_log("Navegando a la plataforma de Cursos Virtuales (Ágora / Moodle)...")
                await page.goto(CAMPUS_COURSES_URL, wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(3)

            # Detect enrolled courses (Moodle course cards / links)
            self.add_log("Analizando asignaturas matriculadas...")
            yield {"status": "scanning_courses", "message": "Buscando tus asignaturas en el aula virtual..."}

            # Find course links (typical Moodle links have /course/view.php?id=...)
            course_elements = await page.locator('a[href*="/course/view.php?id="]').all()
            courses_detected = []
            
            seen_ids = set()
            for el in course_elements:
                try:
                    href = await el.get_attribute("href") or ""
                    title = (await el.inner_text()).strip()
                    # Filter out noise links
                    if not title or len(title) < 4 or "área personal" in title.lower() or "inicio" in title.lower():
                        continue
                    
                    # Extract course id
                    match = re.search(r'id=(\d+)', href)
                    if match:
                        c_id = match.group(1)
                        if c_id not in seen_ids:
                            seen_ids.add(c_id)
                            # Clean course title
                            clean_title = re.sub(r'\(.*?\)', '', title).strip() or title
                            courses_detected.append({
                                "id": f"uned_course_{c_id}",
                                "name": clean_title,
                                "url": href
                            })
                except Exception:
                    continue

            self.add_log(f"Asignaturas detectadas: {len(courses_detected)}")
            yield {"status": "courses_found", "count": len(courses_detected), "message": f"Se han encontrado {len(courses_detected)} asignaturas."}

            # If no courses detected via direct links, check dashboard headers or enrolled list
            if not courses_detected:
                self.add_log("Buscando en panel principal de cursos...")
                # Fallback: check course-title headers or cards
                cards = await page.locator('.coursename, .course-info-container, .card-body h3').all()
                for idx, c in enumerate(cards):
                    c_text = (await c.inner_text()).strip()
                    if c_text and len(c_text) > 4:
                        courses_detected.append({
                            "id": f"uned_course_card_{idx+1}",
                            "name": c_text,
                            "url": page.url
                        })

            # Process each course
            for idx, course in enumerate(courses_detected):
                c_name = course["name"]
                c_id = course["id"]
                upsert_subject(c_id, c_name)
                
                c_folder = SUBJECTS_DIR / c_id
                c_folder.mkdir(parents=True, exist_ok=True)

                self.add_log(f"[{idx+1}/{len(courses_detected)}] Explorando contenidos de: '{c_name}'...")
                yield {
                    "status": "processing_course", 
                    "course": c_name, 
                    "progress": f"{idx+1}/{len(courses_detected)}",
                    "message": f"Analizando y descargando documentos de '{c_name}'..."
                }

                # Navigate to course page if it has a direct link
                if course.get("url") and "/course/view.php" in course["url"]:
                    try:
                        await page.goto(course["url"], wait_until="domcontentloaded", timeout=30000)
                        await asyncio.sleep(2)

                        # Look for downloadable resource links:
                        # PDFs, resource view, folder view, etc.
                        resource_links = await page.locator('a[href*="/mod/resource/view.php"], a[href*=".pdf"], a[href*="/mod/folder/view.php"]').all()
                        self.add_log(f"Recursos encontrados en '{c_name}': {len(resource_links)}")

                        for r_idx, r_el in enumerate(resource_links[:15]):  # limit per course to avoid overload
                            try:
                                r_href = await r_el.get_attribute("href") or ""
                                r_text = (await r_el.inner_text()).strip()
                                if not r_text:
                                    r_text = f"recurso_{r_idx+1}.pdf"
                                
                                clean_name = self.sanitize_filename(r_text)
                                if not clean_name.endswith(".pdf"):
                                    clean_name += ".pdf"
                                
                                target_path = c_folder / clean_name
                                
                                # If file doesn't already exist, download it
                                if not target_path.exists():
                                    try:
                                        # Use page download listener or new tab
                                        async with page.expect_download(timeout=10000) as download_info:
                                            await r_el.click(timeout=3000)
                                        download = await download_info.value
                                        await download.save_as(str(target_path))
                                        
                                        file_size = target_path.stat().st_size
                                        record_file(
                                            file_id=f"{c_id}_{clean_name}",
                                            subject_id=c_id,
                                            filename=clean_name,
                                            file_path=str(target_path),
                                            file_size=file_size,
                                            source="uned_sync"
                                        )
                                        self.add_log(f"Descargado: {clean_name}")
                                    except Exception:
                                        # Click might open directly without download trigger
                                        pass
                                else:
                                    # Already downloaded, make sure recorded
                                    record_file(
                                        file_id=f"{c_id}_{clean_name}",
                                        subject_id=c_id,
                                        filename=clean_name,
                                        file_path=str(target_path),
                                        file_size=target_path.stat().st_size,
                                        source="uned_sync"
                                    )
                            except Exception:
                                continue
                    except Exception as e:
                        self.add_log(f"Aviso al acceder a '{c_name}': {e}")

            self.add_log("¡Sincronización con la UNED completada con éxito!")
            yield {"status": "completed", "message": "¡Sincronización completada con éxito!"}

        except Exception as e:
            err_msg = f"Error en la sincronización: {str(e)}"
            self.add_log(err_msg)
            yield {"status": "error", "message": err_msg}
        finally:
            self.is_running = False
            self.status = "idle"
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            if playwright:
                try:
                    await playwright.stop()
                except Exception:
                    pass

sync_manager = UNEDSyncManager()
