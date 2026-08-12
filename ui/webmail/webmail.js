(() => {
  const folderTitle = document.getElementById('folder-title');
  const folderView = document.getElementById('folder-view');
  const composeView = document.getElementById('compose-view');
  const form = composeView;
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

  document.querySelectorAll('[data-folder]').forEach((button) => button.addEventListener('click', () => showFolder(button.dataset.folder)));
  document.querySelectorAll('[data-action="compose"]').forEach((button) => button.addEventListener('click', showCompose));
  document.querySelector('[data-action="cancel-compose"]').addEventListener('click', () => showFolder('inbox'));
  document.querySelector('[data-action="refresh"]').addEventListener('click', () => showFolder('inbox'));
  document.querySelector('[data-action="save-draft"]').addEventListener('click', () => window.alert('Draft storage will be enabled with the mailbox API.'));
  form.addEventListener('submit', (event) => { event.preventDefault(); window.alert('Message queueing is unavailable until Pat and the Packet RMS path are verified.'); });
})();
