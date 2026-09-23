document.querySelectorAll('[data-copy]').forEach(button => {
  button.addEventListener('click', async () => {
    const node = document.querySelector(button.dataset.copy);
    const status = document.getElementById('copy-status');
    try {
      await navigator.clipboard.writeText(node.textContent.trim());
      status.textContent = 'Copied. Paste it in the desktop app.';
    } catch (_) {
      status.textContent = 'Select the code above and copy it manually.';
    }
  });
});
