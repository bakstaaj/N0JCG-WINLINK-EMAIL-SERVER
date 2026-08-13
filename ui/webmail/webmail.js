(() => {
  const folderTitle = document.getElementById('folder-title');
  const folderView = document.getElementById('folder-view');
  const composeView = document.getElementById('compose-view');
  const form = composeView;
  const authGate = document.getElementById('auth-gate');
  const workspace = document.getElementById('mail-workspace');
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
    document.querySelectorAll('[data-folder]').forEach((button) => button.classList.toggle('active', button.dataset.folder === folder));
  }

  function showCompose() {
    folderView.hidden = true;
    composeView.hidden = false;
    folderTitle.textContent = 'New message';
    composeView.querySelector('input[name="to"]').focus();
  }

  function authMessage(formElement, message) {
    formElement.querySelector('.auth-message').textContent = message;
  }

  function switchAuthTab(tab) {
    document.querySelectorAll('[data-auth-tab]').forEach((button) => button.classList.toggle('active', button.dataset.authTab === tab));
    document.getElementById('login-form').hidden = tab !== 'login';
    document.getElementById('register-form').hidden = tab !== 'register';
    document.getElementById('auth-title').textContent = tab === 'login' ? 'Sign in to your Winlink mailbox' : 'Register this Winlink mailbox';
  }

  async function submitAuth(event, endpoint) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const data = Object.fromEntries(new FormData(formElement));
    if (endpoint.endsWith('/register') && data.password !== data.password_confirm) {
      authMessage(formElement, 'Passwords do not match.');
      return;
    }
    delete data.password_confirm;
    delete data.consent;
    const button = formElement.querySelector('button[type="submit"]');
    button.disabled = true;
    authMessage(formElement, 'Validating through the Winlink client…');
    try {
      const response = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || 'Mailbox validation is unavailable.');
      authGate.hidden = true;
      workspace.hidden = false;
      document.querySelector('.status-badge').innerHTML = 'Mailbox: Connected';
      showFolder('inbox');
    } catch (error) {
      authMessage(formElement, error.message + ' No mailbox data was opened.');
    } finally {
      button.disabled = false;
    }
  }

  document.querySelectorAll('[data-auth-tab]').forEach((button) => button.addEventListener('click', () => switchAuthTab(button.dataset.authTab)));
  document.getElementById('login-form').addEventListener('submit', (event) => submitAuth(event, '/api/v1/auth/login'));
  document.getElementById('register-form').addEventListener('submit', (event) => submitAuth(event, '/api/v1/auth/register'));
  document.querySelectorAll('[data-folder]').forEach((button) => button.addEventListener('click', () => showFolder(button.dataset.folder)));
  document.querySelectorAll('[data-action="compose"]').forEach((button) => button.addEventListener('click', showCompose));
  document.querySelector('[data-action="cancel-compose"]').addEventListener('click', () => showFolder('inbox'));
  document.querySelector('[data-action="refresh"]').addEventListener('click', () => showFolder('inbox'));
  document.querySelector('[data-action="save-draft"]').addEventListener('click', () => window.alert('Draft storage will be enabled with the mailbox API.'));
  form.addEventListener('submit', (event) => { event.preventDefault(); window.alert('Message queueing is unavailable until Pat and the Packet RMS path are verified.'); });
})();
