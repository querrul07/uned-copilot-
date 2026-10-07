// State variables
let currentSubject = null;
let currentConversation = null;
let subjects = [];
let conversations = [];
let files = [];
let appConfig = {};
let syncEventSource = null;
let deferredPrompt = null;

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', async () => {
  // Register Service Worker for PWA
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js')
      .then(() => console.log('Service Worker registrado correctamente'))
      .catch((e) => console.log('Error registrando Service Worker:', e));
  }

  // PWA install banner listener
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    const banner = document.getElementById('pwaInstallBanner');
    if (banner) banner.classList.remove('hidden');
  });

  await loadConfig();
  await loadSubjects();

  // If first time, show tutorial
  if (!appConfig.onboarding_completed) {
    openTutorialModal();
  }

  // Setup textarea enter key
  const msgInput = document.getElementById('messageInput');
  msgInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
});

// --- PWA Installation for Android Tablets ---
function installPWA() {
  if (deferredPrompt) {
    deferredPrompt.prompt();
    deferredPrompt.userChoice.then((choiceResult) => {
      if (choiceResult.outcome === 'accepted') {
        const banner = document.getElementById('pwaInstallBanner');
        if (banner) banner.classList.add('hidden');
      }
      deferredPrompt = null;
    });
  }
}

// --- Sidebar Mobile/Tablet Toggling ---
function toggleSidebar() {
  const sidebar = document.getElementById('mainSidebar');
  const backdrop = document.getElementById('sidebarBackdrop');
  const isHidden = sidebar.classList.contains('-translate-x-full');
  
  if (isHidden) {
    sidebar.classList.remove('-translate-x-full');
    backdrop.classList.remove('hidden');
  } else {
    closeSidebar();
  }
}

function closeSidebar() {
  const sidebar = document.getElementById('mainSidebar');
  const backdrop = document.getElementById('sidebarBackdrop');
  sidebar.classList.add('-translate-x-full');
  backdrop.classList.add('hidden');
}

// --- API & Config ---
async function loadConfig() {
  try {
    const res = await fetch('/api/config');
    appConfig = await res.json();
    
    // Update badge
    const badge = document.getElementById('aiProviderBadge');
    if (badge) {
      badge.textContent = appConfig.ai_provider === 'gemini' ? 'Google Gemini' : 'OpenAI';
    }
  } catch (e) {
    console.error('Error cargando configuración:', e);
  }
}

function openSettingsModal() {
  document.getElementById('geminiApiKeyInput').value = appConfig.gemini_api_key || '';
  document.getElementById('openaiApiKeyInput').value = appConfig.openai_api_key || '';
  document.getElementById('unedUsernameInput').value = appConfig.uned_username || '';
  setProvider(appConfig.ai_provider || 'gemini');
  document.getElementById('settingsModal').classList.remove('hidden');
  closeSidebar();
}

function closeSettingsModal() {
  document.getElementById('settingsModal').classList.add('hidden');
}

function setProvider(provider) {
  appConfig.ai_provider = provider;
  const geminiBtn = document.getElementById('providerGeminiBtn');
  const openaiBtn = document.getElementById('providerOpenAIBtn');
  const geminiGroup = document.getElementById('geminiKeyGroup');
  const openaiGroup = document.getElementById('openaiKeyGroup');

  if (provider === 'gemini') {
    geminiBtn.className = "p-3 rounded-xl border border-uned-500 bg-uned-500/10 text-slate-100 text-left transition flex items-center justify-between";
    geminiBtn.querySelector('i').className = "fa-solid fa-check text-uned-500 text-sm";

    openaiBtn.className = "p-3 rounded-xl border border-slate-700 bg-slate-850 text-slate-400 text-left transition flex items-center justify-between hover:border-slate-600";
    openaiBtn.querySelector('i').className = "fa-solid fa-check text-transparent text-sm";

    geminiGroup.classList.remove('hidden');
    openaiGroup.classList.add('hidden');
  } else {
    openaiBtn.className = "p-3 rounded-xl border border-uned-500 bg-uned-500/10 text-slate-100 text-left transition flex items-center justify-between";
    openaiBtn.querySelector('i').className = "fa-solid fa-check text-uned-500 text-sm";

    geminiBtn.className = "p-3 rounded-xl border border-slate-700 bg-slate-850 text-slate-400 text-left transition flex items-center justify-between hover:border-slate-600";
    geminiBtn.querySelector('i').className = "fa-solid fa-check text-transparent text-sm";

    openaiGroup.classList.remove('hidden');
    geminiGroup.classList.add('hidden');
  }
}

async function saveSettings() {
  const payload = {
    ai_provider: appConfig.ai_provider,
    gemini_api_key: document.getElementById('geminiApiKeyInput').value.trim(),
    openai_api_key: document.getElementById('openaiApiKeyInput').value.trim(),
    uned_username: document.getElementById('unedUsernameInput').value.trim()
  };

  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      await loadConfig();
      closeSettingsModal();
      showToast('Ajustes guardados correctamente.');
    }
  } catch (e) {
    alert('Error al guardar ajustes.');
  }
}

// --- Subjects ---
async function loadSubjects() {
  try {
    const res = await fetch('/api/subjects');
    subjects = await res.json();

    const select = document.getElementById('subjectSelect');
    select.innerHTML = '';

    if (subjects.length === 0) {
      select.innerHTML = '<option value="" disabled selected>No hay asignaturas</option>';
      return;
    }

    subjects.forEach((s) => {
      const opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = `${s.name} (${s.files_count || 0} docs)`;
      select.appendChild(opt);
    });

    if (!currentSubject || !subjects.some(s => s.id === currentSubject.id)) {
      currentSubject = subjects[0];
      select.value = currentSubject.id;
    } else {
      select.value = currentSubject.id;
    }

    updateSubjectHeader();
    await loadSubjectData();
  } catch (e) {
    console.error('Error cargando asignaturas:', e);
  }
}

async function onSubjectChange() {
  const select = document.getElementById('subjectSelect');
  const selectedId = select.value;
  currentSubject = subjects.find(s => s.id === selectedId);
  updateSubjectHeader();
  await loadSubjectData();
  closeSidebar();
}

function updateSubjectHeader() {
  if (!currentSubject) return;
  document.getElementById('currentSubjectTitle').textContent = currentSubject.name;
  const count = currentSubject.files_count || 0;
  document.getElementById('currentSubjectSub').textContent = `${count} documento(s) disponible(s)`;
  document.getElementById('filesBadgeCount').textContent = count;
}

async function loadSubjectData() {
  if (!currentSubject) return;
  await loadConversations();
  await loadFiles();
}

function openNewSubjectModal() {
  document.getElementById('newSubjectName').value = '';
  document.getElementById('newSubjectCode').value = '';
  document.getElementById('newSubjectModal').classList.remove('hidden');
  closeSidebar();
}

function closeNewSubjectModal() {
  document.getElementById('newSubjectModal').classList.add('hidden');
}

async function createSubject() {
  const name = document.getElementById('newSubjectName').value.trim();
  const code = document.getElementById('newSubjectCode').value.trim();
  if (!name) {
    alert('Introduce el nombre de la asignatura');
    return;
  }

  try {
    const res = await fetch('/api/subjects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, code })
    });
    if (res.ok) {
      closeNewSubjectModal();
      await loadSubjects();
      showToast('Asignatura creada con éxito.');
    }
  } catch (e) {
    alert('Error al crear asignatura');
  }
}

// --- Conversations ---
async function loadConversations() {
  if (!currentSubject) return;
  try {
    const res = await fetch(`/api/subjects/${currentSubject.id}/conversations`);
    conversations = await res.json();

    const list = document.getElementById('conversationsList');
    list.innerHTML = '';

    if (conversations.length === 0) {
      await createNewConversation('Dudas y preparación');
      return;
    }

    conversations.forEach((c) => {
      const btn = document.createElement('div');
      const isActive = currentConversation && currentConversation.id === c.id;
      btn.className = `group flex items-center justify-between px-3 py-2.5 rounded-xl text-xs font-medium cursor-pointer transition ${
        isActive ? 'bg-uned-500/20 text-slate-100 border border-uned-500/40' : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
      }`;
      btn.innerHTML = `
        <div class="flex items-center gap-2 truncate" onclick="selectConversation('${c.id}')">
          <i class="fa-regular fa-message text-[11px] ${isActive ? 'text-uned-500' : 'text-slate-500'}"></i>
          <span class="truncate">${c.title}</span>
        </div>
        <button onclick="event.stopPropagation(); deleteConversation('${c.id}')" class="opacity-0 group-hover:opacity-100 p-1.5 text-slate-500 hover:text-red-400 rounded transition" title="Eliminar chat">
          <i class="fa-solid fa-trash-can text-[10px]"></i>
        </button>
      `;
      list.appendChild(btn);
    });

    if (!currentConversation || !conversations.some(c => c.id === currentConversation.id)) {
      currentConversation = conversations[0];
      await loadMessages();
    }
  } catch (e) {
    console.error('Error cargando conversaciones:', e);
  }
}

async function selectConversation(convId) {
  currentConversation = conversations.find(c => c.id === convId);
  await loadConversations();
  await loadMessages();
  closeSidebar();
}

async function createNewConversation(title = 'Nueva conversación') {
  if (!currentSubject) return;
  try {
    const res = await fetch(`/api/subjects/${currentSubject.id}/conversations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title })
    });
    if (res.ok) {
      const created = await res.json();
      currentConversation = created;
      await loadConversations();
      await loadMessages();
      closeSidebar();
    }
  } catch (e) {
    console.error('Error al crear conversación:', e);
  }
}

async function deleteConversation(convId) {
  if (!confirm('¿Deseas eliminar este chat y su historial?')) return;
  try {
    await fetch(`/api/conversations/${convId}`, { method: 'DELETE' });
    currentConversation = null;
    await loadConversations();
  } catch (e) {
    console.error(e);
  }
}

// --- Messages & Chat ---
async function loadMessages() {
  const container = document.getElementById('chatMessages');
  const emptyState = document.getElementById('emptyState');

  if (!currentConversation) {
    container.innerHTML = '';
    container.appendChild(emptyState);
    emptyState.classList.remove('hidden');
    return;
  }

  try {
    const res = await fetch(`/api/conversations/${currentConversation.id}/messages`);
    const messages = await res.json();

    container.innerHTML = '';

    if (messages.length === 0) {
      container.appendChild(emptyState);
      emptyState.classList.remove('hidden');
      return;
    }

    emptyState.classList.add('hidden');

    messages.forEach((msg) => {
      appendMessageToUI(msg.role, msg.content, msg.sources);
    });

    scrollToBottom();
  } catch (e) {
    console.error('Error cargando mensajes:', e);
  }
}

function appendMessageToUI(role, content, sources = []) {
  const container = document.getElementById('chatMessages');
  const emptyState = document.getElementById('emptyState');
  if (emptyState) emptyState.classList.add('hidden');

  const wrapper = document.createElement('div');
  const isUser = role === 'user';

  wrapper.className = `flex gap-3 max-w-4xl mx-auto w-full ${isUser ? 'justify-end' : 'justify-start'}`;

  let sourcesHtml = '';
  if (sources && sources.length > 0) {
    sourcesHtml = `
      <div class="mt-3 pt-2 border-t border-slate-800 flex flex-wrap gap-1.5 items-center">
        <span class="text-[10px] text-slate-500 font-semibold uppercase tracking-wider">Fuentes:</span>
        ${sources.map(s => `
          <span class="text-[11px] bg-slate-800 text-uned-400 px-2 py-0.5 rounded-md border border-slate-700 flex items-center gap-1">
            <i class="fa-solid fa-file-pdf text-[9px]"></i>
            ${s.filename} (pág. ${s.page})
          </span>
        `).join('')}
      </div>
    `;
  }

  const parsedContent = isUser ? escapeHtml(content) : marked.parse(content);

  if (isUser) {
    wrapper.innerHTML = `
      <div class="max-w-2xl bg-uned-600 text-white rounded-2xl rounded-tr-sm px-4 py-3 text-sm shadow-md">
        <p class="whitespace-pre-wrap">${parsedContent}</p>
      </div>
    `;
  } else {
    wrapper.innerHTML = `
      <div class="w-8 h-8 rounded-xl bg-uned-500/20 border border-uned-500/30 text-uned-500 flex items-center justify-center flex-shrink-0 text-sm mt-1">
        <i class="fa-solid fa-graduation-cap"></i>
      </div>
      <div class="max-w-3xl flex-1 bg-slate-900 border border-slate-800 rounded-2xl rounded-tl-sm px-4 md:px-5 py-4 text-sm shadow-sm chat-bubble-ai text-slate-200">
        <div class="prose prose-invert max-w-none text-slate-200">${parsedContent}</div>
        ${sourcesHtml}
      </div>
    `;
  }

  container.appendChild(wrapper);
  scrollToBottom();
}

async function sendMessage() {
  const input = document.getElementById('messageInput');
  const text = input.value.trim();
  if (!text || !currentConversation) return;

  input.value = '';
  input.style.height = 'auto';

  appendMessageToUI('user', text);

  const loaderId = appendTypingLoader();
  const sendBtn = document.getElementById('sendBtn');
  sendBtn.disabled = true;

  try {
    const res = await fetch(`/api/conversations/${currentConversation.id}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: text })
    });

    removeTypingLoader(loaderId);

    if (res.ok) {
      const data = await res.json();
      appendMessageToUI('assistant', data.content, data.sources);
    } else {
      const err = await res.json();
      appendMessageToUI('assistant', `⚠️ Error: ${err.detail || 'No se pudo generar respuesta'}`);
    }
  } catch (e) {
    removeTypingLoader(loaderId);
    appendMessageToUI('assistant', `⚠️ Error de conexión: ${e.message}`);
  } finally {
    sendBtn.disabled = false;
  }
}

function appendTypingLoader() {
  const container = document.getElementById('chatMessages');
  const id = 'loader_' + Date.now();
  const wrapper = document.createElement('div');
  wrapper.id = id;
  wrapper.className = 'flex gap-3 max-w-4xl mx-auto w-full justify-start';
  wrapper.innerHTML = `
    <div class="w-8 h-8 rounded-xl bg-uned-500/20 border border-uned-500/30 text-uned-500 flex items-center justify-center flex-shrink-0 text-sm mt-1">
      <i class="fa-solid fa-graduation-cap"></i>
    </div>
    <div class="bg-slate-900 border border-slate-800 rounded-2xl rounded-tl-sm px-4 py-3 flex items-center gap-1.5 shadow-sm">
      <span class="w-2 h-2 rounded-full bg-uned-500 animate-bounce"></span>
      <span class="w-2 h-2 rounded-full bg-uned-500 animate-bounce [animation-delay:0.2s]"></span>
      <span class="w-2 h-2 rounded-full bg-uned-500 animate-bounce [animation-delay:0.4s]"></span>
    </div>
  `;
  container.appendChild(wrapper);
  scrollToBottom();
  return id;
}

function removeTypingLoader(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

function usePromptPrompt(promptText) {
  const input = document.getElementById('messageInput');
  input.value = promptText;
  sendMessage();
}

function scrollToBottom() {
  const container = document.getElementById('chatMessages');
  container.scrollTop = container.scrollHeight;
}

// --- Files Drawer & Management ---
function toggleFilesDrawer() {
  const drawer = document.getElementById('filesDrawer');
  drawer.classList.toggle('translate-x-full');
}

async function loadFiles() {
  if (!currentSubject) return;
  try {
    const res = await fetch(`/api/subjects/${currentSubject.id}/files`);
    files = await res.json();

    const list = document.getElementById('filesList');
    list.innerHTML = '';

    if (files.length === 0) {
      list.innerHTML = `
        <div class="text-center py-8 text-slate-500 text-xs">
          <i class="fa-regular fa-folder-open text-2xl mb-2 block"></i>
          No hay archivos todavía en esta asignatura.
        </div>
      `;
      return;
    }

    files.forEach((f) => {
      const item = document.createElement('div');
      item.className = "flex items-center justify-between p-2.5 rounded-xl bg-slate-850 border border-slate-800 text-xs";
      const sizeKb = Math.round((f.file_size || 0) / 1024);
      const isUned = f.source === 'uned_sync';
      item.innerHTML = `
        <div class="flex items-center gap-2.5 truncate flex-1 pr-2">
          <i class="fa-solid fa-file-pdf text-rose-500 text-sm"></i>
          <div class="truncate">
            <span class="text-slate-200 font-medium truncate block">${escapeHtml(f.filename)}</span>
            <span class="text-[10px] text-slate-400">${sizeKb} KB • <span class="${isUned ? 'text-uned-400' : 'text-blue-400'} font-semibold">${isUned ? 'UNED' : 'Manual'}</span></span>
          </div>
        </div>
        <button onclick="deleteFile('${f.id}')" class="p-2 text-slate-500 hover:text-red-400 rounded transition" title="Eliminar archivo">
          <i class="fa-solid fa-trash-can text-xs"></i>
        </button>
      `;
      list.appendChild(item);
    });

    updateSubjectHeader();
  } catch (e) {
    console.error('Error cargando archivos:', e);
  }
}

async function uploadFileToCurrentSubject(file) {
  if (!currentSubject) {
    alert('Selecciona una asignatura primero.');
    return;
  }
  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch(`/api/subjects/${currentSubject.id}/files`, {
      method: 'POST',
      body: formData
    });
    if (res.ok) {
      showToast(`Archivo "${file.name}" añadido correctamente.`);
      await loadFiles();
      await loadSubjects();
    } else {
      alert('Error al subir archivo');
    }
  } catch (e) {
    alert('Error al subir archivo');
  }
}

function handleQuickUpload(input) {
  if (input.files && input.files[0]) {
    uploadFileToCurrentSubject(input.files[0]);
    input.value = '';
  }
}

function handleDrawerUpload(input) {
  if (input.files && input.files[0]) {
    uploadFileToCurrentSubject(input.files[0]);
    input.value = '';
  }
}

function handleDragOver(e) {
  e.preventDefault();
  document.getElementById('dropZone').classList.add('border-uned-500', 'bg-uned-500/10');
}

function handleDragLeave(e) {
  e.preventDefault();
  document.getElementById('dropZone').classList.remove('border-uned-500', 'bg-uned-500/10');
}

function handleDrop(e) {
  e.preventDefault();
  document.getElementById('dropZone').classList.remove('border-uned-500', 'bg-uned-500/10');
  if (e.dataTransfer.files && e.dataTransfer.files[0]) {
    uploadFileToCurrentSubject(e.dataTransfer.files[0]);
  }
}

async function deleteFile(fileId) {
  if (!confirm('¿Deseas eliminar este archivo de la asignatura?')) return;
  try {
    await fetch(`/api/files/${fileId}`, { method: 'DELETE' });
    await loadFiles();
    await loadSubjects();
  } catch (e) {
    console.error(e);
  }
}

// --- UNED Synchronization Modal & Execution ---
function openSyncModal() {
  document.getElementById('syncModal').classList.remove('hidden');
  closeSidebar();
}

function closeSyncModal() {
  document.getElementById('syncModal').classList.add('hidden');
}

async function startSync() {
  const startBtn = document.getElementById('startSyncBtn');
  const logsBox = document.getElementById('syncLogsBox');
  const badge = document.getElementById('syncStatusBadge');

  startBtn.disabled = true;
  badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-uned-500 animate-pulse"></span><span>Sincronizando...</span>`;
  logsBox.innerHTML = '<div class="text-uned-400">Iniciando proceso de sincronización con la UNED...</div>';

  try {
    const res = await fetch('/api/sync/start', { method: 'POST' });
    const data = await res.json();

    if (data.status === 'already_running') {
      logsBox.innerHTML += '<div>Ya hay una sincronización en ejecución. Mostrando logs...</div>';
    }

    if (syncEventSource) syncEventSource.close();
    syncEventSource = new EventSource('/api/sync/stream');

    syncEventSource.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.type === 'log') {
        const line = document.createElement('div');
        line.textContent = payload.message;
        logsBox.appendChild(line);
        logsBox.scrollTop = logsBox.scrollHeight;
      } else if (payload.type === 'status' && payload.status === 'completed') {
        badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-500"></span><span>Completado</span>`;
        startBtn.disabled = false;
        syncEventSource.close();
        loadSubjects();
        showToast('¡Sincronización con la UNED finalizada!');
      }
    };

    syncEventSource.onerror = () => {
      badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-slate-500"></span><span>Finalizado</span>`;
      startBtn.disabled = false;
      if (syncEventSource) syncEventSource.close();
      loadSubjects();
    };

  } catch (e) {
    logsBox.innerHTML += `<div class="text-rose-400">Error: ${e.message}</div>`;
    startBtn.disabled = false;
  }
}

// --- Tutorial Modal ---
function openTutorialModal() {
  document.getElementById('tutorialModal').classList.remove('hidden');
  closeSidebar();
}

async function closeTutorialModal() {
  document.getElementById('tutorialModal').classList.add('hidden');
  if (!appConfig.onboarding_completed) {
    await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ onboarding_completed: true })
    });
    appConfig.onboarding_completed = true;
  }
}

// --- Helpers ---
function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function showToast(msg) {
  const toast = document.createElement('div');
  toast.className = 'fixed bottom-5 right-5 bg-slate-900 border border-uned-500 text-slate-100 text-xs px-4 py-3 rounded-xl shadow-2xl z-50 flex items-center gap-2 animate-fade-in';
  toast.innerHTML = `<i class="fa-solid fa-circle-check text-uned-500"></i> <span>${msg}</span>`;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3500);
}
