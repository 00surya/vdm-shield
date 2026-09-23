(() => {
  let paused = false, busy = false;
  async function refresh() {
    if (busy || paused || location.hash !== '#logs' || document.hidden) return;
    busy = true;
    const level = document.querySelector('#logs-level').value;
    try {
      const data = await api('/api/logs?level=' + encodeURIComponent(level));
      if (paused || level !== document.querySelector('#logs-level').value) return;
      document.querySelector('#logs-summary').textContent = data.summary.online + ' cameras online · ' + data.summary.not_online + ' not online · ' + data.summary.configured + ' configured';
      const list = document.querySelector('#logs-entries'); list.replaceChildren();
      for (const entry of data.entries) {
        const row = document.createElement('tr');
        for (const value of [new Date(entry.created * 1000).toLocaleString(), entry.level, entry.message]) {
          const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
        }
        list.append(row);
      }
      document.querySelector('#logs-status').textContent = data.entries.length ? 'Updated ' + new Date().toLocaleTimeString() : 'No matching entries.';
    } catch (error) { document.querySelector('#logs-status').textContent = 'Logs unavailable; displayed values may be stale. ' + error.message; }
    finally { busy = false; }
  }
  document.querySelector('#logs-pause').addEventListener('click', event => {
    paused = !paused;
    event.currentTarget.textContent = paused ? 'Resume updates' : 'Pause updates';
    document.querySelector('#logs-status').textContent = paused ? 'Display paused; server logging continues.' : 'Resuming…';
    refresh();
  });
  document.querySelector('#logs-level').addEventListener('change', refresh);
  window.addEventListener('hashchange', refresh);
  setInterval(refresh, 5000); refresh();
})();
