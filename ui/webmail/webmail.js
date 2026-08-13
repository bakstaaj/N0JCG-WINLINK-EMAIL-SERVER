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
  const emptyCopy = {
    inbox: ['No mailbox connection', 'Pat is installed but not connected to a Winlink mailbox yet. Configure the Packet RMS gateway and start the client service from the operator console before expecting messages here.'],
    sent: ['No sent messages', 'Sent message history will appear here after the Pat mailbox is connected.'],
    drafts: ['No drafts', 'Drafts are stored locally only until the mailbox API is connected.'],
    queue: ['Send queue is empty', 'Messages queued for Packet transmission will appear here with delivery state and retry evidence.']
  };

  function showFolder(folder) {
    const copy = emptyCopy[folder] || emptyCopy.inbox;
    folderTitle.textContent = folder.charAt(0).toUpperCase() + folder.slice(1);
    folderView.innerHTML = `<strong>${copy[0]}</strong><p>${copy[1]}</p>`;
    folderView.hidden = false;
    composeView.hidden = true;
    signatureView.hidden = true;
    document.querySelectorAll('[data-folder]').forEach((button) => button.classList.toggle('active', button.dataset.folder === folder));
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
      signature = '';
      document.querySelector('.status-badge').textContent = `Mailbox: Connected - ${body.callsign}`;
      showFolder('inbox');
    } catch (error) {
      authMessage(formElement, error.message + ' No mailbox data was opened.');
    } finally {
      button.disabled = false;
    }
  }

  document.getElementById('login-form').addEventListener('submit', submitAuth);
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
    const response = await fetch('/api/v1/account/signature', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ signature: nextSignature }) });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) { message.textContent = body.error || 'Signature could not be saved.'; return; }
    signature = body.signature || '';
    message.textContent = 'Signature saved.';
  });
})();
