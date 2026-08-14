(() => {
  const folderTitle = document.getElementById('folder-title');
  const folderView = document.getElementById('folder-view');
  const composeView = document.getElementById('compose-view');
  const form = composeView;
  const signatureView = document.getElementById('signature-view');
  const signatureForm = signatureView;
  const folderManager = document.getElementById('folder-manager');
  let signature = '';
  const authGate = document.getElementById('auth-gate');
  const workspace = document.getElementById('mail-workspace');
  const mailUser = document.getElementById('mail-user');
  const logoutButton = document.getElementById('logout-button');
  let sessionTimer;
  const emptyCopy = {
    inbox: ['No mailbox connection', 'The Winlink client is installed but not connected to a mailbox yet. Configure the Packet RMS gateway and start the client service from the operator console before expecting messages here.'],
    sent: ['No sent messages', 'Sent message history will appear here after the Winlink mailbox is connected.'],
    drafts: ['No drafts', 'Drafts are stored locally only until the mailbox API is connected.'],
    queue: ['Send queue is empty', 'Messages queued for Packet transmission will appear here with delivery state and retry evidence.']
  };

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
  }

  function setFolderCount(folder, count) {
    const button = document.querySelector(`[data-folder="${folder}"]`);
    const counter = button && button.querySelector('.folder-count');
    if (counter) {
      counter.textContent = String(count);
      counter.setAttribute('aria-label', `${count} ${folder === 'drafts' ? 'drafts' : folder === 'queue' ? 'queued messages' : 'messages'}`);
    }
  }

  function showFolder(folder) {
    const copy = emptyCopy[folder] || emptyCopy.inbox;
    folderTitle.textContent = folder.startsWith('custom:') ? 'Custom folder' : folder.charAt(0).toUpperCase() + folder.slice(1);
    folderView.innerHTML = `<strong>${copy[0]}</strong><p>${copy[1]}</p>`;
    folderView.hidden = false;
    composeView.hidden = true;
    signatureView.hidden = true;
    folderManager.hidden = true;
    document.querySelectorAll('[data-folder]').forEach((button) => button.classList.toggle('active', button.dataset.folder === folder));
    loadMessages(folder);
  }

  async function loadMessages(folder) {
    if (folder.startsWith('custom:')) {
      const folderId = folder.slice(7);
      try {
        const response = await fetch(`/api/v1/mail/folders/${encodeURIComponent(folderId)}/messages`, { cache: 'no-store' });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || 'Folder messages are unavailable.');
        const messages = body.messages || [];
        folderTitle.textContent = `Custom folder ${messages.length}`;
        folderView.innerHTML = messages.length ? `<div class="message-list">${messages.map((message) => `<button class="message-row" draggable="true" type="button" data-message-id="${escapeHtml(message.MID)}" data-message-box="in"><strong>${escapeHtml(message.Subject || '(no subject)')}</strong><span>${escapeHtml(JSON.stringify(message.From || ''))}</span><time>${escapeHtml(message.Date || '')}</time></button>`).join('')}</div>` : '<strong>Folder is empty</strong><p>Drag a message here from Inbox to organize it.</p>';
        return;
      } catch (error) { folderView.innerHTML = `<strong>Folder unavailable</strong><p>${escapeHtml(error.message)}</p>`; return; }
    }
    if (folder === 'drafts') {
      try {
        const response = await fetch('/api/v1/mail/drafts', { cache: 'no-store' });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || 'Drafts are unavailable.');
        const drafts = body.drafts || [];
        setFolderCount('drafts', drafts.length);
        folderTitle.textContent = `Drafts ${drafts.length}`;
        folderView.innerHTML = drafts.length ? `<div class="message-list">${drafts.map((draft) => `<div class="message-row draft-row"><button class="draft-open" type="button" data-draft-id="${escapeHtml(draft.id)}"><strong>${escapeHtml(draft.subject || '(no subject)')}</strong><span>${escapeHtml(draft.recipient || '')}</span><time>${escapeHtml(new Date(draft.updated_at * 1000).toLocaleString())}</time></button><button class="draft-delete" type="button" data-delete-draft="${escapeHtml(draft.id)}">Delete</button></div>`).join('')}</div>` : '<strong>No drafts</strong><p>Saved drafts for this Winlink account will appear here.</p>';
        folderView.querySelectorAll('[data-draft-id]').forEach((button) => button.addEventListener('click', () => openDraft(drafts.find((draft) => String(draft.id) === button.dataset.draftId))));
        folderView.querySelectorAll('[data-delete-draft]').forEach((button) => button.addEventListener('click', async () => {
          if (!window.confirm('Delete this draft?')) return;
          const response = await fetch(`/api/v1/mail/drafts/${encodeURIComponent(button.dataset.deleteDraft)}`, { method: 'DELETE' });
          if (!response.ok) { window.alert('The draft could not be deleted.'); return; }
          await loadMessages('drafts');
        }));
      } catch (error) {
        folderView.innerHTML = `<strong>Drafts unavailable</strong><p>${escapeHtml(error.message)}</p>`;
      }
      return;
    }
    if (folder === 'queue') {
      try {
        const response = await fetch('/api/v1/mail/queue', { cache: 'no-store' });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || 'Send queue is unavailable.');
        const queue = body.queue || [];
        setFolderCount('queue', queue.length);
        folderView.innerHTML = queue.length ? `<div class="message-list">${queue.map((item) => `<div class="message-row queue-row"><div class="queue-summary"><strong>${escapeHtml(item.subject)}</strong><span>${escapeHtml(item.recipient)}</span><time>${escapeHtml(item.state)}</time></div>${item.state === 'QUEUED' ? `<button class="queue-cancel" type="button" data-cancel-queue="${escapeHtml(item.id)}">Cancel</button>` : ''}</div>`).join('')}</div>` : '<strong>Send queue is empty</strong><p>No messages are waiting for a verified Pat/Packet transmission path.</p>';
        folderTitle.textContent = `Send queue ${queue.length}`;
        folderView.querySelectorAll('[data-cancel-queue]').forEach((button) => button.addEventListener('click', async () => {
          if (!window.confirm('Cancel this queued message?')) return;
          const response = await fetch(`/api/v1/mail/queue/${encodeURIComponent(button.dataset.cancelQueue)}`, { method: 'DELETE' });
          if (!response.ok) { window.alert('The queued message could not be cancelled.'); return; }
          await loadMessages('queue');
        }));
      } catch (error) { folderView.innerHTML = `<strong>Queue unavailable</strong><p>${escapeHtml(error.message)}</p>`; }
      return;
    }
    if (folder !== 'inbox' && folder !== 'sent') return;
    try {
      const response = await fetch(`/api/v1/mail/messages?folder=${encodeURIComponent(folder)}`, { cache: 'no-store' });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Mailbox is unavailable.');
      const messages = body.messages || [];
      const count = messages.length;
      const unread = messages.filter((message) => message.Unread).length;
      setFolderCount(folder, folder === 'inbox' ? unread : count);
      const title = folder === 'inbox' ? `Inbox ${unread} unread / ${count} total` : `${folder.charAt(0).toUpperCase() + folder.slice(1)} ${count}`;
      const box = folder === 'inbox' ? 'in' : folder === 'sent' ? 'sent' : 'out';
      folderView.innerHTML = count ? `<div class="message-list">${messages.map((message) => `<button class="message-row${message.Unread ? ' unread' : ''}" draggable="true" type="button" data-message-id="${escapeHtml(message.MID)}" data-message-box="${box}"><strong>${escapeHtml(message.Subject || '(no subject)')}</strong><span>${escapeHtml(JSON.stringify(message.From || ''))}</span><time>${escapeHtml(message.Date || '')}</time></button>`).join('')}</div>` : `<strong>No messages</strong><p>This mailbox folder is empty.</p>`;
      folderTitle.textContent = title;
      folderView.querySelectorAll('[data-message-id]').forEach((button) => button.addEventListener('click', () => showMessage(folder, button.dataset.messageId)));
      folderView.querySelectorAll('[draggable="true"]').forEach((button) => button.addEventListener('dragstart', (event) => event.dataTransfer.setData('application/x-n0jcg-message', JSON.stringify({ mid: button.dataset.messageId, box: button.dataset.messageBox }))));
    } catch (error) {
      folderView.innerHTML = `<strong>Mailbox unavailable</strong><p>${error.message}</p>`;
    }
  }

  function openDraft(draft) {
    if (!draft) return;
    folderView.hidden = true;
    signatureView.hidden = true;
    folderManager.hidden = true;
    composeView.hidden = false;
    folderTitle.textContent = 'Edit draft';
    composeView.querySelector('input[name="to"]').value = draft.recipient || '';
    composeView.querySelector('input[name="subject"]').value = draft.subject || '';
    composeView.querySelector('textarea[name="body"]').value = draft.body || '';
    composeView.querySelector('input[name="attachment"]').value = '';
    document.getElementById('attachment-warning').textContent = '';
    composeView.dataset.draftId = draft.id;
  }

  async function showMessage(folder, mid) {
    folderView.innerHTML = '<p>Loading message…</p>';
    try {
      const response = await fetch(`/api/v1/mail/messages/${encodeURIComponent(mid)}?folder=${encodeURIComponent(folder)}`, { cache: 'no-store' });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Message unavailable.');
      const message = body.message;
      folderTitle.textContent = message.Subject || '(no subject)';
      folderView.innerHTML = `<article class="message-detail"><p><strong>From:</strong> ${escapeHtml(JSON.stringify(message.From || ''))}</p><p><strong>Date:</strong> ${escapeHtml(message.Date || '')}</p><pre>${escapeHtml(message.Body || '')}</pre><div class="message-actions"><button type="button" data-action="back-inbox">Back to ${escapeHtml(folder)}</button><button type="button" class="danger" data-action="delete-message">Delete</button></div></article>`;
      folderView.querySelector('[data-action="back-inbox"]').addEventListener('click', () => showFolder(folder));
      folderView.querySelector('[data-action="delete-message"]').addEventListener('click', async () => {
        if (!window.confirm('Delete this message?')) return;
        const response = await fetch(`/api/v1/mail/messages/${encodeURIComponent(mid)}?folder=${encodeURIComponent(folder)}`, { method: 'DELETE' });
        if (!response.ok) { window.alert('The message could not be deleted.'); return; }
        showFolder(folder);
      });
      await fetch(`/api/v1/mail/messages/${encodeURIComponent(mid)}/read`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ folder }) });
      if (folder === 'inbox' && message.Unread) {
        const counter = document.querySelector('[data-folder="inbox"] .folder-count');
        const current = counter ? Number.parseInt(counter.textContent, 10) || 0 : 0;
        setFolderCount('inbox', Math.max(0, current - 1));
      }
    } catch (error) {
      folderView.innerHTML = `<strong>Message unavailable</strong><p>${error.message}</p>`;
    }
  }

  function showCompose() {
    folderView.hidden = true;
    composeView.hidden = false;
    signatureView.hidden = true;
    folderTitle.textContent = 'New message';
    composeView.removeAttribute('data-draft-id');
    composeView.querySelector('input[name="to"]').value = '';
    composeView.querySelector('input[name="subject"]').value = '';
    composeView.querySelector('input[name="attachment"]').value = '';
    document.getElementById('attachment-warning').textContent = '';
    const body = composeView.querySelector('textarea[name="body"]');
    body.value = signature ? `\n\n${signature}` : '';
    composeView.querySelector('input[name="to"]').focus();
  }

  async function refreshFolderCounts() {
    await Promise.allSettled([loadMessages('drafts'), loadMessages('queue'), loadFolders()]);
    showFolder('inbox');
  }

  async function saveDraft() {
    if (!validateAttachment()) return;
    const data = Object.fromEntries(new FormData(composeView));
    const attachment = await readAttachment();
    const draftId = composeView.dataset.draftId;
    const response = await fetch('/api/v1/mail/drafts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: draftId || undefined, recipient: data.to, subject: data.subject, body: data.body, attachment }) });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || 'Draft could not be saved.');
    window.alert('Draft saved locally.');
    await loadMessages('drafts');
  }

  function validateAttachment() {
    const input = composeView.querySelector('input[name="attachment"]');
    const warning = document.getElementById('attachment-warning');
    const file = input.files && input.files[0];
    warning.textContent = '';
    if (!file) return true;
    if (file.size > 100 * 1024) {
      warning.textContent = 'Attachment exceeds the 100 KB maximum and cannot be used.';
      return false;
    }
    if (file.size > 10 * 1024) warning.textContent = 'Warning: attachments over 10 KB may be slow over packet radio.';
    return true;
  }

  function readAttachment() {
    const file = composeView.querySelector('input[name="attachment"]').files[0];
    if (!file) return Promise.resolve(null);
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve({ name: file.name, type: file.type || 'application/octet-stream', data: String(reader.result).split(',', 2)[1] || '' });
      reader.onerror = () => reject(new Error('Attachment could not be read.'));
      reader.readAsDataURL(file);
    });
  }

  async function showSignature() {
    folderView.hidden = true;
    composeView.hidden = true;
    signatureView.hidden = false;
    folderManager.hidden = true;
    signatureForm.querySelector('textarea').value = signature;
    signatureForm.querySelector('.auth-message').textContent = '';
    try {
      const response = await fetch('/api/v1/account/signature');
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Signature unavailable.');
      signature = body.signature || '';
      signatureForm.querySelector('textarea').value = signature;
    } catch (error) {
      signatureForm.querySelector('.auth-message').textContent = error.message;
    }
  }

  async function showFolderManager() {
    folderView.hidden = true;
    composeView.hidden = true;
    signatureView.hidden = true;
    folderManager.hidden = false;
    await loadFolders();
  }

  async function loadFolders() {
    const list = document.getElementById('custom-folder-list');
    const message = document.getElementById('folder-message');
    try {
      const response = await fetch('/api/v1/mail/folders', { cache: 'no-store' });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Folders are unavailable.');
      list.innerHTML = (body.folders || []).map((folder) => `<div class="custom-folder-row"><span>${escapeHtml(folder.name)}</span><button type="button" class="draft-delete" data-delete-folder="${escapeHtml(folder.id)}">Delete</button></div>`).join('') || '<p class="muted">No custom folders yet.</p>';
      document.getElementById('custom-folder-nav').innerHTML = (body.folders || []).map((folder) => `<button type="button" data-custom-folder="${escapeHtml(folder.id)}">↳ ${escapeHtml(folder.name)} <span class="folder-count">${folder.count || 0}</span></button>`).join('');
      document.querySelectorAll('[data-custom-folder]').forEach((button) => {
        button.addEventListener('click', () => showFolder(`custom:${button.dataset.customFolder}`));
        button.addEventListener('dragover', (event) => event.preventDefault());
        button.addEventListener('drop', async (event) => {
          event.preventDefault();
          const message = JSON.parse(event.dataTransfer.getData('application/x-n0jcg-message') || '{}');
          if (!message.mid) return;
          const response = await fetch(`/api/v1/mail/messages/${encodeURIComponent(message.mid)}/move`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ folder_id: button.dataset.customFolder, box: message.box }) });
          if (!response.ok) { window.alert('The message could not be moved.'); return; }
          await loadFolders();
          showFolder(`custom:${button.dataset.customFolder}`);
        });
      });
      list.querySelectorAll('[data-delete-folder]').forEach((button) => button.addEventListener('click', async () => {
        if (!window.confirm('Delete this folder?')) return;
        const response = await fetch(`/api/v1/mail/folders/${encodeURIComponent(button.dataset.deleteFolder)}`, { method: 'DELETE' });
        if (!response.ok) { message.textContent = 'The folder could not be deleted.'; return; }
        await loadFolders();
      }));
    } catch (error) { message.textContent = error.message; }
  }

  function authMessage(formElement, message) {
    formElement.querySelector('.auth-message').textContent = message;
  }

  function showSignedInUser(callsign) {
    const safeCallsign = String(callsign || '').trim().toUpperCase();
    if (!safeCallsign) return;
    mailUser.textContent = ` - ${safeCallsign}`;
    document.title = `N0JCG Winlink Email Server | Webmail - ${safeCallsign}`;
  }

  async function logout() {
    logoutButton.disabled = true;
    try { await fetch('/api/v1/auth/logout', { method: 'POST' }); } finally {
      signature = '';
      mailUser.textContent = '';
      document.title = 'N0JCG Winlink Email Server | Webmail';
      workspace.hidden = true;
      authGate.hidden = false;
      document.getElementById('login-form').reset();
      logoutButton.disabled = false;
      if (sessionTimer) window.clearTimeout(sessionTimer);
    }
  }

  async function loadSignature() {
    const response = await fetch('/api/v1/account/signature', { cache: 'no-store' });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || 'Signature unavailable.');
    signature = body.signature || '';
    return signature;
  }

  async function submitAuth(event) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const data = Object.fromEntries(new FormData(formElement));
    const button = formElement.querySelector('button[type="submit"]');
    button.disabled = true;
    authMessage(formElement, 'Validating through the Winlink client…');
    try {
      const response = await fetch('/api/v1/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || 'Mailbox validation is unavailable.');
      authGate.hidden = true;
      workspace.hidden = false;
      showSignedInUser(body.callsign);
      scheduleSessionCheck(body.expires_at);
      try { await loadSignature(); } catch (_) { signature = ''; }
      document.querySelector('.status-badge').textContent = `Mailbox: Connected - ${body.callsign}`;
      refreshFolderCounts();
    } catch (error) {
      authMessage(formElement, error.message + ' No mailbox data was opened.');
    } finally {
      button.disabled = false;
    }
  }

  async function restoreSession() {
    try {
      const response = await fetch('/api/v1/auth/session', { cache: 'no-store' });
      const body = await response.json();
      if (!response.ok || !body.authenticated) return;
      authGate.hidden = true;
      workspace.hidden = false;
      showSignedInUser(body.callsign);
      scheduleSessionCheck(body.expires_at);
      try { await loadSignature(); } catch (_) { signature = ''; }
      document.querySelector('.status-badge').textContent = `Mailbox: Connected - ${body.callsign}`;
      refreshFolderCounts();
    } catch (_) {
      // The login form remains available when the session endpoint is offline.
    }
  }

  function scheduleSessionCheck(expiresAt) {
    if (sessionTimer) window.clearTimeout(sessionTimer);
    const delay = Math.max(1000, (Number(expiresAt || 0) * 1000) - Date.now() + 1000);
    sessionTimer = window.setTimeout(async () => {
      try {
        const response = await fetch('/api/v1/auth/session', { cache: 'no-store' });
        const body = await response.json().catch(() => ({}));
        if (!response.ok || !body.authenticated) {
          workspace.hidden = true;
          authGate.hidden = false;
          authMessage(document.getElementById('login-form'), 'Your Webmail session expired. Please sign in again.');
          return;
        }
        scheduleSessionCheck(body.expires_at);
      } catch (_) { scheduleSessionCheck(Date.now() / 1000 + 60); }
    }, delay);
  }

  document.getElementById('login-form').addEventListener('submit', submitAuth);
  document.getElementById('show-password').addEventListener('change', (event) => {
    document.getElementById('login-password').type = event.currentTarget.checked ? 'text' : 'password';
  });
  logoutButton.addEventListener('click', logout);
  document.querySelector('[data-action="folders"]').addEventListener('click', showFolderManager);
  document.querySelector('[data-action="cancel-folders"]').addEventListener('click', () => showFolder('inbox'));
  document.getElementById('folder-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const message = document.getElementById('folder-message');
    try {
      const name = new FormData(formElement).get('name');
      const response = await fetch('/api/v1/mail/folders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Folder could not be created.');
      formElement.reset();
      message.textContent = 'Folder created.';
      await loadFolders();
    } catch (error) { message.textContent = error.message; }
  });
  document.querySelectorAll('[data-folder]').forEach((button) => button.addEventListener('click', () => showFolder(button.dataset.folder)));
  document.querySelectorAll('[data-action="compose"]').forEach((button) => button.addEventListener('click', showCompose));
  document.querySelector('[data-action="signature"]').addEventListener('click', showSignature);
  document.querySelector('[data-action="cancel-compose"]').addEventListener('click', () => showFolder('inbox'));
  document.querySelector('[data-action="cancel-signature"]').addEventListener('click', () => showFolder('inbox'));
  document.querySelector('[data-action="refresh"]').addEventListener('click', () => showFolder('inbox'));
  document.querySelector('[data-action="save-draft"]').addEventListener('click', async () => {
    try { await saveDraft(); } catch (error) { window.alert(error.message); }
  });
  document.querySelector('input[name="attachment"]').addEventListener('change', validateAttachment);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!validateAttachment()) return;
    try {
      const data = Object.fromEntries(new FormData(form));
      const attachment = await readAttachment();
      const response = await fetch('/api/v1/mail/queue', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ recipient: data.to, subject: data.subject, body: data.body, attachment }) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || 'Message could not be queued.');
      window.alert('Message queued locally. Transmission remains disabled until the Winlink/Packet path is verified.');
      composeView.removeAttribute('data-draft-id');
      showFolder('queue');
    } catch (error) { window.alert(error.message); }
  });
  signatureForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const message = signatureForm.querySelector('.auth-message');
    const nextSignature = signatureForm.querySelector('textarea').value;
    try {
      const response = await fetch('/api/v1/account/signature', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ signature: nextSignature }) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || 'Signature could not be saved.');
      signature = body.signature || '';
      message.textContent = 'Signature saved.';
    } catch (error) {
      message.textContent = error.message;
    }
  });
  restoreSession();
})();
