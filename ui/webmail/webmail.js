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
  const mailboxBadge = document.querySelector('.status-badge');
  let sessionTimer;
  let syncTimer;
  let queueTimer;
  let autoSyncTimer;
  let currentCallsign = '';
  const emptyCopy = {
    inbox: ['No mailbox connection', 'The Winlink client is installed but not connected to a mailbox yet. Configure the Packet RMS gateway and start the client service from the operator console before expecting messages here.'],
    sent: ['No sent messages', 'Sent message history will appear here after the Winlink mailbox is connected.'],
    drafts: ['No drafts', 'Drafts are stored locally only until the mailbox API is connected.'],
    queue: ['Send queue is empty', 'Messages queued for Packet transmission will appear here with delivery state and retry evidence.']
  };

  async function pollMailboxSync() {
    if (!syncStatus || authGate.hidden === false) return;
    try {
      const response = await fetch('/api/v1/mail/sync', { cache: 'no-store' });
      if (!response.ok) return;
      const body = await response.json();
      const active = ['CONNECTING', 'AUTHENTICATING'].includes(body.state)
        || ['cms_connected', 'authenticating', 'downloading', 'uploading'].includes(body.stage)
        || (body.state === 'AUTHENTICATED' && !['complete', 'no_messages', 'failed'].includes(body.stage));
      if (body.state === 'ERROR') {
        window.clearTimeout(syncTimer);
        workspace.hidden = true;
        authGate.hidden = false;
        authMessage(document.getElementById('login-form'), body.message || 'Winlink authentication failed.');
        return;
      }
      if (mailboxBadge && currentCallsign) {
        mailboxBadge.className = `status-badge ${body.state === 'AUTHENTICATED' ? 'status-ready' : 'status-unknown'}`;
        const pendingLabel = body.stage === 'complete' ? 'downloaded' : 'downloading';
        const pending = Number.isInteger(body.pending_count) ? ` · ${body.pending_count} message${body.pending_count === 1 ? '' : 's'} ${pendingLabel}` : (body.outgoing_count ? ` · ${body.outgoing_count} message${body.outgoing_count === 1 ? '' : 's'} ${body.stage === 'complete' ? 'sent' : 'sending'}` : '');
        mailboxBadge.textContent = `${body.state === 'AUTHENTICATED' ? '✓ Mailbox: Connected' : '… Mailbox: Connecting'} - ${currentCallsign}${pending}`;
      }
      syncStatus.hidden = !active;
      syncStatus.textContent = active ? (body.message || 'Synchronizing the Winlink mailbox…') : (body.message || '');
      if (active) {
        window.clearTimeout(syncTimer);
        syncTimer = window.setTimeout(pollMailboxSync, 2500);
      }
      if (active && document.querySelector('[data-folder="inbox"].active')) loadMessages('inbox');
      if (!active && body.stage === 'complete' && document.querySelector('[data-folder="inbox"].active')) {
        loadMessages('inbox');
      }
      if (!active && body.stage === 'complete') {
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
        queueTimer = window.setTimeout(pollQueueUntilSettled, 2500);
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
        folderView.querySelectorAll('button[data-message-id]').forEach((button) => button.addEventListener('click', () => showMessage(folder, button.dataset.messageId)));
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
          queueTimer = window.setTimeout(pollQueueUntilSettled, 2500);
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
        folderView.innerHTML = '<strong>Mailbox synchronization in progress</strong><p>Your Winlink login is active. Messages will appear as the Packet RMS transfer completes.</p>';
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
      folderView.querySelectorAll('button[data-message-id]').forEach((button) => button.addEventListener('click', () => showMessage(folder, button.dataset.messageId)));
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
      window.clearTimeout(autoSyncTimer);
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
      currentCallsign = body.callsign;
      showSignedInUser(body.callsign);
      scheduleSessionCheck(body.expires_at);
      try { await loadSignature(); } catch (_) { signature = ''; }
      mailboxBadge.className = 'status-badge status-unknown';
      mailboxBadge.textContent = `… Mailbox: Connecting - ${body.callsign}`;
      refreshFolderCounts();
      pollMailboxSync();
      scheduleAutomaticSync();
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
      currentCallsign = body.callsign;
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
      const response = await fetch('/api/v1/mail/sync', { method: 'POST' });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || 'Mailbox synchronization could not be started.');
      syncStatus.hidden = false;
      syncStatus.textContent = 'Starting a new Packet RMS synchronization…';
      await showFolder('inbox');
      pollMailboxSync();
    } catch (error) {
      syncStatus.hidden = false;
      syncStatus.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
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
