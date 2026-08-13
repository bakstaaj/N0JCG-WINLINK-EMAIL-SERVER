(() => {
  const folderTitle = document.getElementById('folder-title');
  const folderView = document.getElementById('folder-view');
  const composeView = document.getElementById('compose-view');
  const form = composeView;
  const signatureView = document.getElementById('signature-view');
  const signatureForm = signatureView;
  let signature = '';
  const authGate = document.getElementById('auth-gate');
  const workspace = document.getElementById('mail-workspace');
  const mailUser = document.getElementById('mail-user');
  const logoutButton = document.getElementById('logout-button');
  const emptyCopy = {
    inbox: ['No mailbox connection', 'Pat is installed but not connected to a Winlink mailbox yet. Configure the Packet RMS gateway and start the client service from the operator console before expecting messages here.'],
    sent: ['No sent messages', 'Sent message history will appear here after the Pat mailbox is connected.'],
    drafts: ['No drafts', 'Drafts are stored locally only until the mailbox API is connected.'],
    queue: ['Send queue is empty', 'Messages queued for Packet transmission will appear here with delivery state and retry evidence.']
  };

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
  }

  function showFolder(folder) {
    const copy = emptyCopy[folder] || emptyCopy.inbox;
    folderTitle.textContent = folder.charAt(0).toUpperCase() + folder.slice(1);
    folderView.innerHTML = `<strong>${copy[0]}</strong><p>${copy[1]}</p>`;
    folderView.hidden = false;
    composeView.hidden = true;
    signatureView.hidden = true;
    document.querySelectorAll('[data-folder]').forEach((button) => button.classList.toggle('active', button.dataset.folder === folder));
    loadMessages(folder);
  }

  async function loadMessages(folder) {
    if (folder !== 'inbox' && folder !== 'sent' && folder !== 'drafts') return;
    try {
      const response = await fetch(`/api/v1/mail/messages?folder=${encodeURIComponent(folder)}`, { cache: 'no-store' });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Mailbox is unavailable.');
      const messages = body.messages || [];
      const count = messages.length;
      const title = folder === 'inbox' ? `Inbox ${count}` : `${folder.charAt(0).toUpperCase() + folder.slice(1)} ${count}`;
      folderView.innerHTML = count ? `<div class="message-list">${messages.map((message) => `<button class="message-row" type="button" data-message-id="${escapeHtml(message.MID)}"><strong>${escapeHtml(message.Subject || '(no subject)')}</strong><span>${escapeHtml(JSON.stringify(message.From || ''))}</span><time>${escapeHtml(message.Date || '')}</time></button>`).join('')}</div>` : `<strong>No messages</strong><p>This mailbox folder is empty.</p>`;
      folderTitle.textContent = title;
      folderView.querySelectorAll('[data-message-id]').forEach((button) => button.addEventListener('click', () => showMessage(folder, button.dataset.messageId)));
      const inboxButton = document.querySelector('[data-folder="inbox"]');
      if (inboxButton && folder === 'inbox') inboxButton.innerHTML = `Inbox <span aria-label="${count} messages">${count}</span>`;
    } catch (error) {
      folderView.innerHTML = `<strong>Mailbox unavailable</strong><p>${error.message}</p>`;
    }
  }

  async function showMessage(folder, mid) {
    folderView.innerHTML = '<p>Loading message…</p>';
    try {
      const response = await fetch(`/api/v1/mail/messages/${encodeURIComponent(mid)}?folder=${encodeURIComponent(folder)}`, { cache: 'no-store' });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || 'Message unavailable.');
      const message = body.message;
      folderTitle.textContent = message.Subject || '(no subject)';
      folderView.innerHTML = `<article class="message-detail"><p><strong>From:</strong> ${escapeHtml(JSON.stringify(message.From || ''))}</p><p><strong>Date:</strong> ${escapeHtml(message.Date || '')}</p><pre>${escapeHtml(message.Body || '')}</pre><button type="button" data-action="back-inbox">Back to ${escapeHtml(folder)}</button></article>`;
      folderView.querySelector('[data-action="back-inbox"]').addEventListener('click', () => showFolder(folder));
    } catch (error) {
      folderView.innerHTML = `<strong>Message unavailable</strong><p>${error.message}</p>`;
    }
  }

  function showCompose() {
    folderView.hidden = true;
    composeView.hidden = false;
    signatureView.hidden = true;
    folderTitle.textContent = 'New message';
    const body = composeView.querySelector('textarea[name="body"]');
    body.value = signature ? `\n\n${signature}` : '';
    composeView.querySelector('input[name="to"]').focus();
  }

  async function showSignature() {
    folderView.hidden = true;
    composeView.hidden = true;
    signatureView.hidden = false;
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
      try { await loadSignature(); } catch (_) { signature = ''; }
      document.querySelector('.status-badge').textContent = `Mailbox: Connected - ${body.callsign}`;
      showFolder('inbox');
    } catch (error) {
      authMessage(formElement, error.message + ' No mailbox data was opened.');
    } finally {
      button.disabled = false;
    }
  }

  document.getElementById('login-form').addEventListener('submit', submitAuth);
  logoutButton.addEventListener('click', logout);
  document.querySelectorAll('[data-folder]').forEach((button) => button.addEventListener('click', () => showFolder(button.dataset.folder)));
  document.querySelectorAll('[data-action="compose"]').forEach((button) => button.addEventListener('click', showCompose));
  document.querySelector('[data-action="signature"]').addEventListener('click', showSignature);
  document.querySelector('[data-action="cancel-compose"]').addEventListener('click', () => showFolder('inbox'));
  document.querySelector('[data-action="cancel-signature"]').addEventListener('click', () => showFolder('inbox'));
  document.querySelector('[data-action="refresh"]').addEventListener('click', () => showFolder('inbox'));
  document.querySelector('[data-action="save-draft"]').addEventListener('click', () => window.alert('Draft storage will be enabled with the mailbox API.'));
  form.addEventListener('submit', (event) => { event.preventDefault(); window.alert('Message queueing is unavailable until Pat and the Packet RMS path are verified.'); });
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
})();
