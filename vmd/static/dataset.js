(() => {
  for (const label of trainingLabels) { const option = document.createElement('option'); option.value = label; option.textContent = label.replaceAll('_', ' '); $('#dataset-label').append(option); }
  async function load() {
    try {
      const items = await api('/api/training/uploads'); const list = $('#dataset-files'); list.replaceChildren();
      for (const item of items) {
        const row = node('div', 'normal-sample'); const link = node('a', '', item.name); link.href = `/api/training/uploads/${item.id}`;
        row.append(link, node('span', '', `${item.label.replaceAll('_', ' ')} · ${item.camera_id.replace('dataset:', '')}`), action('Delete', async () => {
          if (!confirm('Delete this training clip? Existing model weights remain unchanged until retraining.')) return;
          try { await api(`/api/training/uploads/${item.id}`, {method: 'DELETE'}); await load(); await refreshTraining(); } catch (error) { $('#dataset-message').textContent = error.message; }
        })); list.append(row);
      }
    } catch (error) { $('#dataset-message').textContent = error.message; }
  }
  $('#dataset-form').addEventListener('submit', async event => {
    event.preventDefault(); const form = event.currentTarget; const button = form.querySelector('button'); button.disabled = true;
    $('#dataset-message').textContent = 'Uploading reviewed clip…';
    try { await api('/api/training/uploads', {method: 'POST', body: new FormData(form)}); form.reset(); $('#dataset-message').textContent = 'Clip saved with your label. Train when the dataset is ready.'; await load(); await refreshTraining(); }
    catch (error) { $('#dataset-message').textContent = error.message; } finally { button.disabled = false; }
  });
  window.addEventListener('hashchange', () => { if (location.hash === '#training') load(); });
  if (location.hash === '#training') load();
})();
