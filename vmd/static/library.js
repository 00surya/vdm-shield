/* Saved file management, independent of live source cards. */
(() => {
  const find = selector => document.querySelector(selector);
  const element = (tag, text, className = '') => { const result = document.createElement(tag); result.textContent = text; result.className = className; return result; };
  let files = {videos: [], evidence: []};
  async function request(url, options = {}) {
    const response = await fetch(url, {...options, headers: {'X-VMD-Client': 'dashboard', ...options.headers}});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Request failed.');
    return data;
  }
  function button(text, run) {
    const result = element('button', text); result.type = 'button';
    result.addEventListener('click', async () => {
      result.disabled = true;
      try { await run(); } catch (error) { showError(error.message); } finally { result.disabled = false; }
    });
    return result;
  }
  function link(text, url) { const result = element('a', text); result.href = url; return result; }
  function inDates(file, prefix) {
    const date = new Date(file.created * 1000);
    const day = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
    const from = find(`#${prefix}-from`).value, to = find(`#${prefix}-to`).value;
    return (!from || day >= from) && (!to || day <= to);
  }
  function frameViewer(url, name) {
    const details = element('details'); details.append(element('summary', 'Load recorded frames'));
    const image = document.createElement('img'); image.alt = `Recorded frame: ${name}`; image.className = 'evidence-preview'; image.hidden = true;
    const status = element('p', 'Open to load recorded frames.');
    const controls = element('div', '', 'frame-controls'); controls.hidden = true;
    const input = document.createElement('input'); input.type = 'number'; input.min = 1; input.value = 1; input.step = 1; input.setAttribute('aria-label', 'Frame number (starts at 1)');
    const slider = document.createElement('input'); slider.type = 'range'; slider.min = 1; slider.value = 1; slider.setAttribute('aria-label', 'Recorded frame');
    const download = link('Download frame', '#'); let metadata = null; let index = 1; let loading = false;
    function show(value) {
      if (!metadata || !Number.isInteger(value) || value < 1 || value > metadata.frames) { status.textContent = 'Enter a frame number within the recording.'; return; }
      index = value; input.value = value; slider.value = value;
      image.hidden = false; image.src = `${url}/preview?frame=${value - 1}`;
      download.href = `${url}/preview?frame=${value - 1}&download=1`;
      status.textContent = `Frame ${value} of ${metadata.frames}${metadata.fps ? ` · ${((value - 1) / metadata.fps).toFixed(2)} seconds into recording` : ''}. Preview images are resized to at most 960 pixels wide.`;
    }
    image.addEventListener('error', () => { image.hidden = true; status.textContent = 'Frame unavailable. Choose another frame or download the recording.'; });
    controls.append(button('Previous', () => show(Math.max(1, index - 1))), input, button('Load frame', () => show(Number(input.value))), button('Next', () => show(Math.min(metadata.frames, index + 1))), download);
    slider.addEventListener('change', () => show(Number(slider.value)));
    details.addEventListener('toggle', async () => {
      if (!details.open || metadata || loading) return;
      loading = true; status.textContent = 'Loading recording…';
      try { metadata = await request(`${url}/preview?metadata=1`); input.max = metadata.frames; slider.max = metadata.frames; controls.hidden = false; slider.hidden = false; show(1); }
      catch (error) { status.textContent = error.message; } finally { loading = false; }
    });
    slider.hidden = true; details.append(image, controls, slider, status); return details;
  }
  window.recordedFrameViewer = frameViewer;
  function render() {
    const videos = files.videos.filter(file => inDates(file, 'video') && file.name.toLowerCase().includes(find('#video-search').value.toLowerCase()));
    const root = find('#video-library'); root.replaceChildren();
    find('#video-total').textContent = `${videos.length} of ${files.videos.length} saved videos`;
    if (!videos.length) root.append(element('p', files.videos.length ? 'No matching videos.' : 'No saved videos yet. Choose a file above and upload it.', 'empty'));
    for (const file of videos) {
      const card = element('article', '', 'panel file-card');
      card.append(element('h2', file.name), element('p', `${(file.bytes / 1048576).toFixed(1)} MB · Uploaded ${new Date(file.created * 1000).toLocaleString()}`));
      const actions = element('div', '', 'incident-actions');
      const url = `/api/library/videos/${encodeURIComponent(file.id)}`;
      actions.append(button('Analyze', async () => { await request(`${url}/analyze`, {method: 'POST'}); location.hash = 'sources'; await refresh(); }), link('Download video', url), button('Delete video', async () => {
        if (!confirm(`Delete the saved copy of “${file.name}”? Your original file and event evidence will be kept.`)) return;
        await request(url, {method: 'DELETE'}); await load();
      }));
      card.append(frameViewer(url, file.name), actions); root.append(card);
    }
  }
  async function load() {
    try { files = await request('/api/library'); render(); } catch (error) { showError(error.message); }
  }
  for (const prefix of ['video']) {
    for (const suffix of ['from', 'to']) find(`#${prefix}-${suffix}`).addEventListener('change', () => {
      const from = find(`#${prefix}-from`).value, to = find(`#${prefix}-to`).value;
      if (from && to && from > to) showError('From date must be on or before Through date.');
      render();
    });
    find(`#${prefix}-clear`).addEventListener('click', () => {
      for (const suffix of ['from', 'to', 'search']) find(`#${prefix}-${suffix}`).value = '';
      render();
    });
  }
  find('#video-search').addEventListener('input', render);
  find('#library-upload').addEventListener('submit', async event => {
    event.preventDefault(); const form = event.currentTarget; const submit = form.querySelector('button'); submit.disabled = true;
    find('#library-upload-status').textContent = 'Uploading… Keep this page open.';
    try {
      await request('/api/library/videos', {method: 'POST', body: new FormData(form)});
      form.reset(); find('#library-upload-status').textContent = 'Upload complete. Choose Analyze on your video below.'; await load();
    } catch (error) { find('#library-upload-status').textContent = error.message; } finally { submit.disabled = false; }
  });
  window.addEventListener('hashchange', () => { if (['#videos'].includes(location.hash)) load(); });
  if (['#videos'].includes(location.hash)) load();
})();
