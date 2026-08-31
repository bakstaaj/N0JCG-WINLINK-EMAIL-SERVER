(() => {
  const folderTitle = document.getElementById('folder-title');
  const folderView = document.getElementById('folder-view');
  const composeView = document.getElementById('compose-view');
  const form = composeView;
  const signatureView = document.getElementById('signature-view');
  const signatureForm = signatureView;
  const folderManager = document.getElementById('folder-manager');
  const templateView = document.getElementById('template-view');
  let signature = '';
  const authGate = document.getElementById('auth-gate');
  const workspace = document.getElementById('mail-workspace');
  const mailUser = document.getElementById('mail-user');
  const logoutButton = document.getElementById('logout-button');
  const syncStatus = document.getElementById('mail-sync-status');
  const syncDebugButton = document.getElementById('sync-debug-button');
  const syncDebugDialog = document.getElementById('sync-debug-dialog');
  const syncDebugSummary = document.getElementById('sync-debug-summary');
  const syncDebugEvents = document.getElementById('sync-debug-events');
  const mailboxBadge = document.querySelector('.status-badge');
  const trialNotice = document.getElementById('trial-notice');
  let sessionTimer;
  let syncTimer;
  let authProgressTimer;
  let queueTimer;
  let autoSyncTimer;
  let syncDebugTimer;
  let loginRetryTimer;
  let loginRetryUntil = 0;
  let redirectingToLogin = false;
  let currentCallsign = '';
  function updateRegistrationNotice(registration) {
    if (!trialNotice) return;
    const trial = !registration || registration.mode !== 'registered';
    trialNotice.hidden = !trial;
    trialNotice.textContent = trial ? 'Trial: Limited to one message per login.' : '';
    trialNotice.setAttribute('aria-hidden', trial ? 'false' : 'true');
  }
  const nativeFetch = window.fetch.bind(window);
  const emptyCopy = {
    inbox: ['No mailbox connection', 'The Winlink client is installed but not connected to a mailbox yet. Configure the Packet RMS gateway and start the client service from the operator console before expecting messages here.'],
    sent: ['No sent messages', 'Sent message history will appear here after the Winlink mailbox is connected.'],
    drafts: ['No drafts', 'Drafts are stored locally only until the mailbox API is connected.'],
    queue: ['Send queue is empty', 'Messages queued for Packet transmission will appear here with delivery state and retry evidence.']
  };

  async function requireLogin(message = 'Your Winlink authentication is no longer valid. Please sign in again.') {
    if (redirectingToLogin) return;
    redirectingToLogin = true;
    window.clearTimeout(syncTimer);
    window.clearTimeout(authProgressTimer);
    window.clearTimeout(queueTimer);
    window.clearTimeout(autoSyncTimer);
    window.clearTimeout(syncDebugTimer);
    try {
      await nativeFetch('/api/v1/auth/logout', { method: 'POST', credentials: 'same-origin' });
    } catch (_) { /* continue to clear the local view even if the server is unavailable */ }
    signature = '';
    resetMailboxState();
    document.title = 'N0JCG Winlink Email Server | Webmail';
    workspace.hidden = true;
    authGate.hidden = false;
    document.getElementById('login-form').reset();
    authMessage(document.getElementById('login-form'), message);
  }

  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    const requestUrl = String(args[0] || '');
    const protectedWebmailRequest = requestUrl.startsWith('/api/v1/mail/')
      || requestUrl.startsWith('/api/v1/account/')
      || requestUrl.startsWith('/api/v1/templates');
    if (response.status === 401 && protectedWebmailRequest) {
      void requireLogin();
    }
    return response;
  };

  async function pollMailboxSync() {
    if (!syncStatus || authGate.hidden === false) return;
    try {
      const response = await fetch('/api/v1/mail/sync', { cache: 'no-store' });
      if (!response.ok) return;
      const body = await response.json();
      const active = ['CONNECTING', 'AUTHENTICATING'].includes(body.state)
        || ['rms_connected', 'username_sent', 'password_sent', 'cms_connected', 'authenticating', 'mailbox_index', 'mailbox_records', 'mailbox_selection', 'downloading', 'uploading'].includes(body.stage)
        || (body.state === 'AUTHENTICATED' && !['complete', 'no_messages', 'failed'].includes(body.stage));
      const syncFailedAfterLogin = body.state === 'AUTHENTICATED' && body.stage === 'failed';
      setRefreshAvailability(active);
      if (body.state === 'ERROR') {
        window.clearTimeout(syncTimer);
        await requireLogin(body.message || 'The RMS server stopped responding before authentication completed. Check the selected RMS gateway, radio frequency, audio, and PTT path.');
        return;
      }
      if (mailboxBadge && currentCallsign) {
        mailboxBadge.className = `status-badge ${body.state === 'AUTHENTICATED' ? 'status-ready' : 'status-unknown'}`;
        const pendingLabel = body.stage === 'complete' ? 'downloaded' : 'offered';
        const pending = Number.isInteger(body.pending_count) ? ` · ${body.pending_count} message${body.pending_count === 1 ? '' : 's'} ${pendingLabel}` : (body.outgoing_count ? ` · ${body.outgoing_count} message${body.outgoing_count === 1 ? '' : 's'} ${body.stage === 'complete' ? 'sent' : 'sending'}` : '');
        mailboxBadge.textContent = `${body.state === 'AUTHENTICATED' ? '✓ Mailbox: Connected' : '… Mailbox: Connecting'} - ${currentCallsign}${pending}`;
      }
      syncStatus.hidden = !(active || syncFailedAfterLogin);
      if (syncDebugButton) syncDebugButton.hidden = !(active || syncFailedAfterLogin || body.stage === 'complete' || body.stage === 'no_messages');
      if (active || syncFailedAfterLogin) {
        const message = escapeHtml(body.message || 'Synchronizing the Winlink mailbox…');
        const total = Number.isInteger(body.pending_count) ? body.pending_count : 0;
        const received = Number.isInteger(body.received) ? body.received : 0;
        const remaining = Number.isInteger(body.remaining_count) ? body.remaining_count : Math.max(total - received, 0);
        const percent = Math.max(0, Math.min(100, Number(body.progress_percent) || 0));
        const windowHint = Number.isInteger(body.window_count) && body.window_count > 0
          ? `<span class="sync-window-hint">Current RMS transfer window: ${body.window_count}</span>` : '';
        syncStatus.innerHTML = total > 0
          ? `${message}${windowHint}<span class="sync-progress" role="status" aria-label="Mailbox download progress"><span class="sync-progress-track"><span class="sync-progress-bar" style="width:${percent}%"></span></span><span class="sync-progress-meta"><span>${received} of ${total} downloaded</span><span>${remaining} remaining</span></span></span>`
          : message;
      } else {
        syncStatus.textContent = body.message || '';
      }
      if (active) {
        window.clearTimeout(syncTimer);
        syncTimer = window.setTimeout(pollMailboxSync, 1000);
      }
      if (active && document.querySelector('[data-folder="inbox"].active')) loadMessages('inbox');
      if (!active && body.stage === 'complete' && document.querySelector('[data-folder="inbox"].active')) {
        loadMessages('inbox');
      }
      if (!active && ['complete', 'no_messages'].includes(body.stage)) {
        if (document.querySelector('[data-folder="queue"].active')) loadMessages('queue');
        refreshNavCounts();
      }
    } catch (_) { /* mailbox status is advisory */ }
  }

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

  function installBulkActions(folder) {
    const selected = () => [...folderView.querySelectorAll('.message-select:checked')];
    folderView.querySelector('[data-bulk-read]')?.addEventListener('click', async () => {
      const items = selected();
      if (!items.length) return window.alert('Select at least one message.');
      await Promise.all(items.map((item) => fetch(`/api/v1/mail/messages/${encodeURIComponent(item.dataset.messageId)}/read`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ folder }) })));
      await loadMessages(folder);
    });
    folderView.querySelector('[data-bulk-delete]')?.addEventListener('click', async () => {
      const items = selected();
      if (!items.length) return window.alert('Select at least one message.');
      if (!window.confirm(`Delete ${items.length} selected message${items.length === 1 ? '' : 's'}?`)) return;
      await Promise.all(items.map((item) => fetch(`/api/v1/mail/messages/${encodeURIComponent(item.dataset.messageId)}?folder=${encodeURIComponent(folder)}`, { method: 'DELETE' })));
      await loadMessages(folder);
      if (folder.startsWith('custom:')) await loadFolders();
    });
    folderView.querySelectorAll('.message-select').forEach((checkbox) => checkbox.addEventListener('click', (event) => event.stopPropagation()));
  }

  function bulkBar() {
    return '<div class="bulk-actions" role="toolbar" aria-label="Bulk message actions"><button type="button" data-bulk-read>Mark selected read</button><button type="button" data-bulk-delete>Delete selected</button></div>';
  }

  async function refreshNavCounts() {
    const [draftResponse, queueResponse] = await Promise.all([
      fetch('/api/v1/mail/drafts', { cache: 'no-store' }),
      fetch('/api/v1/mail/queue', { cache: 'no-store' })
    ]);
    if (draftResponse.ok) setFolderCount('drafts', ((await draftResponse.json()).drafts || []).length);
    if (queueResponse.ok) setFolderCount('queue', ((await queueResponse.json()).queue || []).length);
  }

  async function pollQueueUntilSettled() {
    try {
      const response = await fetch('/api/v1/mail/queue', { cache: 'no-store' });
      if (!response.ok) return;
      const queue = (await response.json()).queue || [];
      setFolderCount('queue', queue.length);
      if (queue.some((item) => item.state === 'STAGED')) {
        window.clearTimeout(queueTimer);
        queueTimer = window.setTimeout(pollQueueUntilSettled, 1000);
      } else if (document.querySelector('[data-folder="queue"].active')) {
        await loadMessages('queue');
      }
    } catch (_) { /* queue status is advisory */ }
  }

  async function scheduleAutomaticSync() {
    window.clearTimeout(autoSyncTimer);
    try {
      const response = await fetch('/api/v1/mail/settings', { cache: 'no-store' });
      if (!response.ok) return;
      const minutes = Number((await response.json()).auto_sync_minutes);
      if (!Number.isFinite(minutes) || minutes < 5) return;
      autoSyncTimer = window.setTimeout(async () => {
        try { await fetch('/api/v1/mail/sync', { method: 'POST' }); } finally { pollMailboxSync(); scheduleAutomaticSync(); }
      }, minutes * 60 * 1000);
    } catch (_) { /* operator setting is advisory */ }
  }

  function showFolder(folder) {
    const copy = emptyCopy[folder] || emptyCopy.inbox;
    folderTitle.textContent = folder.startsWith('custom:') ? 'Custom folder' : folder.charAt(0).toUpperCase() + folder.slice(1);
    folderView.innerHTML = `<strong>${copy[0]}</strong><p>${copy[1]}</p>`;
    folderView.hidden = false;
    composeView.hidden = true;
    signatureView.hidden = true;
    folderManager.hidden = true;
    templateView.hidden = true;
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
        folderView.innerHTML = messages.length ? `${bulkBar()}<div class="message-list">${messages.map((message) => `<button class="message-row${message.Unread ? ' unread' : ''}" draggable="true" type="button" data-message-id="${escapeHtml(message.MID)}" data-message-box="${escapeHtml(message.box || 'in')}"><input class="message-select" type="checkbox" data-message-id="${escapeHtml(message.MID)}" aria-label="Select message"><strong>${escapeHtml(message.Subject || '(no subject)')}</strong><span>${escapeHtml(JSON.stringify(message.From || ''))}</span><time>${escapeHtml(message.Date || '')}</time></button>`).join('')}</div>` : '<strong>Folder is empty</strong><p>Drag a message here from Inbox to organize it.</p>';
        if (messages.length) installBulkActions(folder);
        folderView.querySelectorAll('button[data-message-id]').forEach((button) => {
          button.title = 'Double-click to open this message';
          button.addEventListener('dblclick', () => showMessage(folder, button.dataset.messageId));
        });
        folderView.querySelectorAll('[draggable="true"]').forEach((button) => button.addEventListener('dragstart', (event) => event.dataTransfer.setData('application/x-n0jcg-message', JSON.stringify({ mid: button.dataset.messageId, box: button.dataset.messageBox, sourceFolder: folder }))));
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
        if (queue.some((item) => item.state === 'STAGED')) {
          window.clearTimeout(queueTimer);
          queueTimer = window.setTimeout(pollQueueUntilSettled, 1000);
        }
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
      if (body.state === 'SYNCING') {
        folderTitle.textContent = 'Inbox';
        const sync = body.sync || {};
        folderView.innerHTML = `<strong>${escapeHtml(sync.stage_label || 'Mailbox synchronization in progress')}</strong><p>${escapeHtml(sync.message || 'Your Winlink login is active. Messages will appear as the Packet RMS transfer completes.')}</p>`;
        return;
      }
      if (body.state === 'NO_MESSAGES') {
        folderTitle.textContent = 'Inbox';
        folderView.innerHTML = '<strong>No downloadable messages reported by the RMS</strong><p>The mailbox was authenticated, but this exchange returned no message proposals. Select Refresh to start another synchronization.</p>';
        return;
      }
      const messages = body.messages || [];
      const count = messages.length;
      const unread = messages.filter((message) => message.Unread).length;
      setFolderCount(folder, folder === 'inbox' ? unread : count);
      const title = folder === 'inbox' ? `Inbox ${unread} unread / ${count} total` : `${folder.charAt(0).toUpperCase() + folder.slice(1)} ${count}`;
      const box = folder === 'inbox' ? 'in' : folder === 'sent' ? 'sent' : 'out';
      folderView.innerHTML = count ? `${bulkBar()}<div class="message-list">${messages.map((message) => `<button class="message-row${message.Unread ? ' unread' : ''}" draggable="true" type="button" data-message-id="${escapeHtml(message.MID)}" data-message-box="${box}"><input class="message-select" type="checkbox" data-message-id="${escapeHtml(message.MID)}" aria-label="Select message"><strong>${escapeHtml(message.Subject || '(no subject)')}</strong><span>${escapeHtml(JSON.stringify(message.From || ''))}</span><time>${escapeHtml(message.Date || '')}</time></button>`).join('')}</div>` : `<strong>No messages</strong><p>This mailbox folder is empty.</p>`;
      if (count) installBulkActions(folder);
      folderTitle.textContent = title;
      folderView.querySelectorAll('button[data-message-id]').forEach((button) => {
        button.title = 'Double-click to open this message';
        button.addEventListener('dblclick', () => showMessage(folder, button.dataset.messageId));
      });
      folderView.querySelectorAll('[draggable="true"]').forEach((button) => button.addEventListener('dragstart', (event) => event.dataTransfer.setData('application/x-n0jcg-message', JSON.stringify({ mid: button.dataset.messageId, box: button.dataset.messageBox, sourceFolder: folder }))));
    } catch (error) {
      folderView.innerHTML = `<strong>Mailbox unavailable</strong><p>${error.message}</p>`;
    }
  }

  function openDraft(draft) {
    if (!draft) return;
    folderView.hidden = true;
    signatureView.hidden = true;
    folderManager.hidden = true;
    templateView.hidden = true;
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
      const readResponse = await fetch(`/api/v1/mail/messages/${encodeURIComponent(mid)}/read`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ folder }) });
      if (!readResponse.ok) {
        const readBody = await readResponse.json().catch(() => ({}));
        throw new Error(readBody.error || 'The message could not be marked as read.');
      }
      if (folder === 'inbox' && message.Unread) {
        const counter = document.querySelector('[data-folder="inbox"] .folder-count');
        const current = counter ? Number.parseInt(counter.textContent, 10) || 0 : 0;
        setFolderCount('inbox', Math.max(0, current - 1));
      }
      message.Unread = false;
    } catch (error) {
      folderView.innerHTML = `<strong>Message unavailable</strong><p>${error.message}</p>`;
    }
  }

  function showCompose() {
    folderView.hidden = true;
    composeView.hidden = false;
    signatureView.hidden = true;
    folderManager.hidden = true;
    templateView.hidden = true;
    folderTitle.textContent = 'Compose';
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
    await refreshNavCounts();
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
    templateView.hidden = true;
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

  async function showTemplates() {
    folderView.hidden = true;
    composeView.hidden = true;
    signatureView.hidden = true;
    folderManager.hidden = true;
    templateView.hidden = false;
    folderTitle.textContent = 'Templates';
    const content = document.getElementById('template-content');
    content.innerHTML = '<p>Loading official Standard Forms…</p>';
    try {
      const response = await fetch('/api/v1/templates', { cache: 'no-store' });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || 'Standard Forms are unavailable.');
      const templates = body.templates || [];
      const categories = [...new Set(templates.map((item) => item.category).filter(Boolean))].sort((a, b) => a.localeCompare(b));
      content.innerHTML = `<div class="template-filters"><input id="template-search" type="search" placeholder="Search forms" aria-label="Search Standard Forms"><select id="template-category" aria-label="Filter by category"><option value="">All categories</option>${categories.map((category) => `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`).join('')}</select></div><div id="template-results"></div>`;
      const results = document.getElementById('template-results');
      const renderList = () => {
        const query = document.getElementById('template-search').value.trim().toLowerCase();
        const category = document.getElementById('template-category').value;
        const filtered = templates.filter((item) => (!category || item.category === category) && (!query || `${item.name} ${item.category} ${item.id}`.toLowerCase().includes(query)));
        results.innerHTML = filtered.length ? `<div class="template-list">${filtered.map((item) => `<button class="template-choice" type="button" data-template-id="${escapeHtml(item.id)}"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.category)} · ${item.fields.length} fields</small></button>`).join('')}</div><p class="muted">Showing ${filtered.length} of ${templates.length} forms · Library version ${escapeHtml(body.version)}</p>` : '<div class="mail-empty"><strong>No matching forms</strong><p>Try a different search or category.</p></div>';
        results.querySelectorAll('[data-template-id]').forEach((button) => button.addEventListener('click', () => showTemplateFields(templates.find((item) => item.id === button.dataset.templateId))));
      };
      document.getElementById('template-search').addEventListener('input', renderList);
      document.getElementById('template-category').addEventListener('change', renderList);
      renderList();
    } catch (error) { content.innerHTML = `<strong>Templates unavailable</strong><p>${escapeHtml(error.message)}</p>`; }
  }

  function showTemplateFields(template) {
    if (!template) return;
    const content = document.getElementById('template-content');
    content.innerHTML = `<h4>${escapeHtml(template.name)}</h4><p class="muted">${escapeHtml(template.category)} · Standard Forms ${escapeHtml(template.version)}</p><div class="template-fields">${template.fields.map((field) => `<label>${escapeHtml(field)}<input data-template-field="${escapeHtml(field)}" autocomplete="off"></label>`).join('')}</div><p class="auth-message" id="template-message" role="status"></p><div class="compose-actions"><button type="button" data-action="use-template">Insert into message</button><button type="button" class="secondary" data-action="back-templates">Back to templates</button></div>`;
    content.querySelector('[data-action="use-template"]').addEventListener('click', async () => {
      const values = {};
      content.querySelectorAll('[data-template-field]').forEach((input) => { values[input.dataset.templateField] = input.value; });
      const message = document.getElementById('template-message');
      try {
        const response = await fetch('/api/v1/templates/render', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ template_id: template.id, values }) });
        const body = await response.json().catch(() => ({}));
        if (response.status === 401) {
          window.clearTimeout(sessionTimer);
          workspace.hidden = true;
          authGate.hidden = false;
          resetMailboxState();
          authMessage(document.getElementById('login-form'), 'Your Webmail session has expired. Please sign in again.');
          return;
        }
        if (!response.ok) throw new Error(body.error || 'Template could not be rendered.');
        showCompose();
        form.querySelector('input[name="to"]').value = body.recipient || '';
        form.querySelector('input[name="subject"]').value = body.subject || '';
        form.querySelector('textarea[name="body"]').value = body.body || '';
      } catch (error) { message.textContent = error.message; }
    });
    content.querySelector('[data-action="back-templates"]').addEventListener('click', showTemplates);
  }

  async function showFolderManager() {
    folderView.hidden = true;
    composeView.hidden = true;
    signatureView.hidden = true;
    folderManager.hidden = false;
    templateView.hidden = true;
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

  function resetMailboxState() {
    currentCallsign = '';
    if (mailUser) mailUser.textContent = '';
    if (mailboxBadge) {
      mailboxBadge.className = 'status-badge status-unavailable';
      mailboxBadge.textContent = '! Mailbox: Sign in required';
    }
    if (syncStatus) {
      syncStatus.hidden = true;
      syncStatus.textContent = '';
    }
    setRefreshAvailability(false);
  }

  function setRefreshAvailability(transferActive) {
    const button = document.querySelector('[data-action="refresh"]');
    if (!button) return;
    // Refresh is always available. A manual click is the recovery control for
    // a stalled RF exchange and never requires logging out of the mailbox.
    button.disabled = false;
    button.setAttribute('aria-busy', transferActive ? 'true' : 'false');
    button.textContent = 'Refresh';
  }

  function renderSyncDebug(payload) {
    const sync = payload.sync || {};
    const services = payload.services || {};
    const devices = payload.devices || {};
    if (syncDebugSummary) syncDebugSummary.innerHTML = `<strong>${escapeHtml(sync.stage_label || sync.stage || 'No active session')}</strong><span>${escapeHtml(sync.message || 'No mailbox session message')}</span><span>Pat state: ${escapeHtml(sync.state || 'unknown')}</span><span>Dire Wolf: ${escapeHtml(services['Dire Wolf'] || 'unknown')}</span><span>AGW bridge: ${escapeHtml(services['AGW bridge'] || 'unknown')}</span><span>DigiRig: audio ${devices.audio ? 'present' : 'missing'}, serial ${devices.serial ? 'present' : 'missing'}, PTT ${devices.ptt ? 'present' : 'missing'}</span>`;
    const events = payload.events || [];
    if (syncDebugEvents) syncDebugEvents.innerHTML = events.length ? events.map((event) => `<div class="sync-debug-event"><time>${escapeHtml(event.at || '')}</time><strong>${escapeHtml(event.source || '')}</strong><span>${escapeHtml(event.message || '')}</span></div>`).join('') : 'No events collected yet.';
    if (syncDebugEvents) syncDebugEvents.scrollTop = syncDebugEvents.scrollHeight;
  }

  async function pollSyncDebug() {
    if (!syncDebugDialog || !syncDebugDialog.open) return;
    try {
      const response = await fetch('/api/v1/mail/sync/debug', { cache: 'no-store' });
      if (response.ok) renderSyncDebug(await response.json());
    } catch (_) { /* debug view is advisory */ }
    syncDebugTimer = window.setTimeout(pollSyncDebug, 1500);
  }

  async function openSyncDebug() {
    if (!syncDebugDialog) return;
    syncDebugDialog.showModal();
    await pollSyncDebug();
  }

  async function logout() {
    redirectingToLogin = true;
    logoutButton.disabled = true;
    try { await fetch('/api/v1/auth/logout', { method: 'POST' }); } finally {
      signature = '';
      resetMailboxState();
      document.title = 'N0JCG Winlink Email Server | Webmail';
      workspace.hidden = true;
      authGate.hidden = false;
      document.getElementById('login-form').reset();
      logoutButton.disabled = false;
      if (sessionTimer) window.clearTimeout(sessionTimer);
      window.clearTimeout(autoSyncTimer);
      redirectingToLogin = false;
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
    redirectingToLogin = false;
    window.clearInterval(loginRetryTimer);
    loginRetryTimer = null;
    loginRetryUntil = 0;
    const formElement = event.currentTarget;
    // A new credential attempt must start with a blank, hidden mailbox. This
    // prevents a previously authenticated view from remaining visible while
    // the replacement Winlink login is being verified.
    window.clearTimeout(syncTimer);
    window.clearTimeout(authProgressTimer);
    window.clearTimeout(queueTimer);
    window.clearTimeout(autoSyncTimer);
    window.clearTimeout(syncDebugTimer);
    signature = '';
    currentCallsign = '';
    resetMailboxState();
    workspace.hidden = true;
    authGate.hidden = false;
    const data = Object.fromEntries(new FormData(formElement));
    const callsign = String(data.email || '').split('@', 1)[0].trim().toUpperCase();
    const button = formElement.querySelector('button[type="submit"]');
    let lastAuthProgressMessage = '';
    button.disabled = true;
    authMessage(formElement, 'Validating through the Winlink client…');
    const pollAuthProgress = async () => {
      if (!callsign) return;
      try {
        const progressResponse = await nativeFetch(`/api/v1/auth/progress?callsign=${encodeURIComponent(callsign)}`, { cache: 'no-store' });
        if (progressResponse.ok) {
          const progress = await progressResponse.json();
          if (['CONNECTING', 'AUTHENTICATING'].includes(progress.state) || ['connecting', 'rms_connected', 'username_sent', 'password_sent', 'cms_connected', 'authenticating', 'mailbox_index', 'mailbox_records', 'mailbox_selection', 'downloading', 'uploading', 'complete', 'no_messages'].includes(progress.stage)) {
            lastAuthProgressMessage = progress.message || progress.stage_label || 'Contacting the RMS gateway…';
            authMessage(formElement, lastAuthProgressMessage);
          }
        }
      } catch (_) { /* login request remains authoritative */ }
      authProgressTimer = window.setTimeout(pollAuthProgress, 700);
    };
    pollAuthProgress();
    try {
      const response = await fetch('/api/v1/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
      const body = await response.json().catch(() => ({}));
      if (response.status === 429) {
        const retrySeconds = Math.max(0, Number(body.retry_after) || 0);
        const retryMinutes = Math.max(1, Math.ceil(retrySeconds / 60));
        throw new Error(`Too many login attempts. Try again in about ${retryMinutes} minute${retryMinutes === 1 ? '' : 's'}.`);
      }
      if (!response.ok) throw new Error(body.error || 'The RMS server did not complete authentication. Check the RMS target, frequency, radio mode, audio, and PTT path.');
      authGate.hidden = true;
      workspace.hidden = false;
      currentCallsign = body.callsign;
      updateRegistrationNotice(body.registration);
      showSignedInUser(body.callsign);
      scheduleSessionCheck(body.expires_at);
      try { await loadSignature(); } catch (_) { signature = ''; }
      mailboxBadge.className = 'status-badge status-unknown';
      mailboxBadge.textContent = `… Mailbox: Connecting - ${body.callsign}`;
      refreshFolderCounts();
      pollMailboxSync();
      scheduleAutomaticSync();
    } catch (error) {
      const detail = error.message === 'The RMS server did not complete authentication. Check the RMS target, frequency, radio mode, audio, and PTT path.' && lastAuthProgressMessage
        ? `${lastAuthProgressMessage} The RMS server did not return a final mailbox/authentication result.`
        : error.message;
      authMessage(formElement, `${detail} No mailbox data was opened. Check the operator diagnostics for the last RF/client event.`);
      if (/Wait 1 minute, then try the Winlink login again/i.test(detail)) {
        loginRetryUntil = Date.now() + 60000;
        const updateRetryMessage = () => {
          const remaining = Math.max(0, Math.ceil((loginRetryUntil - Date.now()) / 1000));
          if (remaining === 0) {
            window.clearInterval(loginRetryTimer);
            loginRetryTimer = null;
            button.disabled = false;
            authMessage(formElement, 'The RMS session-clear wait is complete. You may try the Winlink login again.');
            return;
          }
          button.disabled = true;
          authMessage(formElement, `The RMS is still clearing the previous packet session. Wait 1 minute before trying again (${remaining}s remaining). No mailbox data was opened.`);
        };
        updateRetryMessage();
        loginRetryTimer = window.setInterval(updateRetryMessage, 1000);
      }
    } finally {
      window.clearTimeout(authProgressTimer);
      button.disabled = loginRetryUntil > Date.now();
    }
  }

  async function restoreSession() {
    try {
      const response = await fetch('/api/v1/auth/session', { cache: 'no-store' });
      const body = await response.json();
      if (!response.ok || !body.authenticated) return;
      authGate.hidden = true;
      workspace.hidden = false;
      currentCallsign = body.callsign;
      updateRegistrationNotice(body.registration);
      showSignedInUser(body.callsign);
      scheduleSessionCheck(body.expires_at);
      try { await loadSignature(); } catch (_) { signature = ''; }
      mailboxBadge.className = 'status-badge status-unknown';
      mailboxBadge.textContent = `… Mailbox: Connecting - ${body.callsign}`;
      refreshFolderCounts();
      pollMailboxSync();
      scheduleAutomaticSync();
    } catch (_) {
      // The login form remains available when the session endpoint is offline.
    }
  }

  function scheduleSessionCheck(expiresAt) {
    if (sessionTimer) window.clearTimeout(sessionTimer);
    // A null expiry means the server issued a browser-session cookie. The
    // browser controls its lifetime; only explicit logout or browser close
    // should end the normal session.
    if (!Number(expiresAt)) return;
    const delay = Math.max(1000, (Number(expiresAt || 0) * 1000) - Date.now() + 1000);
    sessionTimer = window.setTimeout(async () => {
      try {
        const response = await fetch('/api/v1/auth/session', { cache: 'no-store' });
        const body = await response.json().catch(() => ({}));
        if (!response.ok || !body.authenticated) {
          workspace.hidden = true;
          authGate.hidden = false;
          resetMailboxState();
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
  document.querySelector('[data-action="templates"]').addEventListener('click', showTemplates);
  document.querySelector('[data-action="cancel-templates"]').addEventListener('click', () => showFolder('inbox'));
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
  document.querySelector('[data-action="refresh"]').addEventListener('click', async () => {
    const button = document.querySelector('[data-action="refresh"]');
    button.disabled = true;
    try {
      const response = await fetch('/api/v1/mail/sync', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ restart: true }) });
      const body = await response.json().catch(() => ({}));
      if (response.status === 401 && body.trial_relogin) {
        resetMailboxState();
        workspace.hidden = true;
        authGate.hidden = false;
        document.getElementById('login-form').reset();
        authMessage(document.getElementById('login-form'), body.error || 'Trial mode requires a new Winlink login.');
        return;
      }
      if (!response.ok) throw new Error(body.error || 'Mailbox synchronization could not be started.');
      syncStatus.hidden = false;
      syncStatus.textContent = 'Starting a new Packet RMS synchronization…';
      setRefreshAvailability(false);
      await showFolder('inbox');
      pollMailboxSync();
    } catch (error) {
      syncStatus.hidden = false;
      syncStatus.textContent = error.message;
      setRefreshAvailability(false);
    }
  });
  syncDebugButton?.addEventListener('click', openSyncDebug);
  document.getElementById('sync-debug-close')?.addEventListener('click', () => { window.clearTimeout(syncDebugTimer); syncDebugDialog?.close(); });
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
      const response = await fetch('/api/v1/mail/queue', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ recipient: data.to, subject: data.subject, body: data.body, attachment, draft_id: composeView.dataset.draftId || undefined }) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || 'Message could not be queued.');
      window.alert(body.sync_error ? `Message queued locally. Send could not start yet: ${body.sync_error}` : 'Message queued. Packet RMS synchronization has started.');
      composeView.removeAttribute('data-draft-id');
      showFolder('queue');
      await loadMessages('queue');
      await refreshNavCounts();
      pollQueueUntilSettled();
      pollMailboxSync();
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
