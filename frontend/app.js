const state = { username: null, sessionId: null };
const $ = (id) => document.getElementById(id);
const authView = $('auth-view');
const chatView = $('chat-view');

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || 'Request failed');
  return body;
}

function showError(message) { $('auth-error').textContent = message; }
function showChat() { authView.classList.add('hidden'); chatView.classList.remove('hidden'); $('welcome').textContent = `Welcome, ${state.username}`; loadSessions(); }
function showAuth() { chatView.classList.add('hidden'); authView.classList.remove('hidden'); }
function renderMarkdown(element, text) {
  if (window.marked && window.DOMPurify) {
    element.innerHTML = window.DOMPurify.sanitize(window.marked.parse(text));
  } else {
    element.textContent = text;
  }
}
function addMessage(sender, text) {
  const empty = document.querySelector('.empty'); if (empty) empty.remove();
  const element = document.createElement('div'); element.className = `message ${sender}`;
  if (sender === 'assistant') renderMarkdown(element, text); else element.textContent = text;
  $('messages').appendChild(element); $('messages').scrollTop = $('messages').scrollHeight; return element;
}

async function loadSessions() {
  const sessions = await api(`/api/sessions?username=${encodeURIComponent(state.username)}`);
  $('sessions').innerHTML = '';
  sessions.forEach((session) => {
    const row = document.createElement('div'); row.className = 'session-row';
    const button = document.createElement('button'); button.className = 'session'; button.textContent = session.session_name; button.onclick = () => selectSession(session.session_id, button);
    const deleteButton = document.createElement('button'); deleteButton.className = 'delete-session'; deleteButton.type = 'button'; deleteButton.textContent = '×'; deleteButton.title = 'Delete session';
    deleteButton.onclick = async () => { if (!window.confirm('Delete this chat session and its messages?')) return; try { await api(`/api/sessions/${session.session_id}`, { method: 'DELETE' }); if (state.sessionId === session.session_id) { state.sessionId = null; $('messages').innerHTML = '<div class="empty">Choose a conversation or start a new one.</div>'; } await loadSessions(); } catch (error) { window.alert(error.message); } };
    row.append(button, deleteButton); $('sessions').appendChild(row);
  });
}
async function selectSession(id, button) {
  state.sessionId = id; document.querySelectorAll('.session').forEach((item) => item.classList.remove('active')); button.classList.add('active');
  const messages = await api(`/api/sessions/${id}/messages`); $('messages').innerHTML = ''; messages.forEach((message) => addMessage(message.username === state.username ? 'user' : 'assistant', message.message));
}

$('login-form').onsubmit = async (event) => { event.preventDefault(); showError(''); const data = Object.fromEntries(new FormData(event.target)); try { await api('/api/login', { method: 'POST', body: JSON.stringify(data) }); state.username = data.username; showChat(); } catch (error) { showError(error.message); } };
$('register-form').onsubmit = async (event) => { event.preventDefault(); showError(''); const data = Object.fromEntries(new FormData(event.target)); try { await api('/api/register', { method: 'POST', body: JSON.stringify(data) }); $('register-form').classList.add('hidden'); $('login-form').classList.remove('hidden'); showError('Account created. Sign in to continue.'); } catch (error) { showError(error.message); } };
$('show-register').onclick = () => { $('login-form').classList.add('hidden'); $('show-register').classList.add('hidden'); $('register-form').classList.remove('hidden'); };
$('show-login').onclick = () => { $('register-form').classList.add('hidden'); $('login-form').classList.remove('hidden'); $('show-register').classList.remove('hidden'); };
$('logout').onclick = () => { state.username = null; state.sessionId = null; showAuth(); };
$('delete-account').onclick = async () => { const password = window.prompt('Enter your password to permanently delete your account:'); if (password === null) return; if (!window.confirm('Delete your account, sessions, and messages permanently?')) return; try { await api(`/api/users/${encodeURIComponent(state.username)}`, { method: 'DELETE', body: JSON.stringify({ password }) }); state.username = null; state.sessionId = null; $('login-form').reset(); $('register-form').reset(); showAuth(); showError('Account deleted.'); } catch (error) { window.alert(error.message); } };
$('new-session').onclick = async () => { const session = await api('/api/sessions', { method: 'POST', body: JSON.stringify({ username: state.username }) }); state.sessionId = session.session_id; $('messages').innerHTML = '<div class="empty">Ask your first question.</div>'; await loadSessions(); };
$('message-form').onsubmit = async (event) => { event.preventDefault(); if (!state.sessionId) return; const input = $('message-input'); const text = input.value.trim(); if (!text) return; input.value = ''; addMessage('user', text); const reply = addMessage('assistant', ''); let replyText = ''; try { const response = await fetch(`/api/sessions/${state.sessionId}/generate-stream`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: state.username, message: text }) }); if (!response.ok) throw new Error('Unable to generate a response'); const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; while (true) { const result = await reader.read(); if (result.done) break; buffer += decoder.decode(result.value, { stream: true }); const events = buffer.split('\n\n'); buffer = events.pop(); events.forEach((event) => { if (!event.startsWith('data: ')) return; const data = JSON.parse(event.slice(6)); if (data.content) { replyText += data.content; renderMarkdown(reply, replyText); $('messages').scrollTop = $('messages').scrollHeight; } if (data.error) throw new Error(data.error); }); } } catch (error) { reply.textContent = error.message; reply.classList.add('error'); } };
