/* Unified event evidence review, with server-side time filtering and pagination. */
(() => {
  const drafts = new Map();
  const messages = new Map();
  const saving = new Set();
  const friendly = value => value.replaceAll('_', ' ');
  const states = {event: {page: 1}, history: {page: 1}};
  function render(items, prefix) {
    const incidentList = $(prefix === 'event' ? '#incidents' : '#history-list');
    const existing = new Map([...incidentList.children].map(row => [row.dataset.id, row]));
    const priorState = new Map([...incidentList.querySelectorAll('.incident')].map(row => [row.dataset.id, {open: row.querySelector('.incident-details')?.open, label: row.querySelector('.label-select')?.value}]));
    // Move unchanged cards in place so polling does not interrupt video playback.
    const keep = new Set(items.map(item => item.id));
    for (const row of [...incidentList.children]) if (!keep.has(row.dataset.id)) row.remove();
    if (!items.length) { incidentList.replaceChildren(node('p', 'empty', 'No events match this time range and filters.')); return; }
    let cursor = incidentList.firstChild;
    function place(row) {
      if (row === cursor) cursor = cursor.nextSibling;
      else incidentList.insertBefore(row, cursor);
    }
  for (const item of items) {
    const signature = JSON.stringify(item);
    const cached = existing.get(item.id);
    if (cached?.dataset.signature === signature) { place(cached); continue; }
    const row = node('article', 'incident');
    row.dataset.signature = signature;
    row.dataset.id = item.id;
    const info = node('div');
    const displayLabel = item.train_label || (item.review === 'false_positive' ? 'normal' : item.event_type);
    info.append(node('h3', '', `${friendly(displayLabel)} · ${item.camera_name}`));
    if (displayLabel !== item.event_type) info.append(node('p', 'original-detection', `Operator label: ${friendly(displayLabel)} · Originally detected: ${friendly(item.event_type)}`));
    info.append(node('p', '', `${new Date(item.created * 1000).toLocaleString()} · ${item.signals?.object_detection ? "Object confidence" : item.signals?.model_generated ? "Clip alert score" : "Rule score"} ${Number(item.score).toFixed(2)}`));
    if (item.signals?.frame_identity?.frame_id != null) {
      const identity = item.signals.frame_identity;
      if (Number.isFinite(identity.stream_seconds)) info.append(node('p', 'event-time', 'Event seen at ' + identity.stream_seconds.toFixed(3) + ' seconds ' + (identity.time_basis === 'media' ? 'in the video' : 'after camera connection')));
      if (item.signals.detected_at) info.append(node('p', '', 'Detection completed at ' + new Date(item.signals.detected_at * 1000).toLocaleString()));
      info.append(node('p', '', `Observed frame ${identity.frame_id} · Session ${identity.session_id} · ${identity.captured_at ? new Date(identity.captured_at * 1000).toLocaleTimeString() : 'Capture time unavailable'}`));
      if (Number.isFinite(item.signals.capture_to_detection_ms)) info.append(node('p', '', `Detection delay: ${item.signals.capture_to_detection_ms.toFixed(0)} ms. Evidence is aligned to the observed frame, not alert arrival.`));
    }
    if (item.reasons?.length) info.append(node('p', 'event-reason', `Why flagged: ${item.reasons[0]}`));
    if (item.source_start != null) {
      const span = item.source_end > item.source_start ? `${timeLabel(item.source_start)}–${timeLabel(item.source_end)}` : timeLabel(item.source_start);
      info.append(node('p', '', `Signal position ${span} in source · ${item.event_count || 1} signal${item.event_count === 1 ? '' : 's'} grouped`));
    }
    const top = node('div', 'incident-top');
    if (item.signals?.priority === 'high') info.append(node('span', 'priority-pill', 'High priority · object detection'));
    if (item.signals?.priority === 'low') info.append(node('span', 'priority-pill low', 'Lower priority · unusual motion'));
    top.append(info, node('span', `review-badge ${item.review}`, item.review.replaceAll('_', ' ')));
    const reviewPanel = node('section', 'review-panel');
    reviewPanel.setAttribute('aria-label', 'Review and label this event');
    const savedLabel = item.train_label || (item.review === 'false_positive' ? 'normal' : item.event_type);
    const reviewed = item.review !== 'unreviewed';
    const heading = node('div', 'review-panel-heading');
    heading.append(node('strong', '', reviewed ? 'Review saved' : 'Review this event'),
      node('span', 'saved-label', `${reviewed ? 'Saved label' : 'Suggested label'}: ${friendly(savedLabel)}`));
    const feedback = node('p', 'review-feedback', messages.get(item.id) || '');
    feedback.setAttribute('role', 'status'); feedback.setAttribute('aria-live', 'polite');
    const buttons = node('div', 'review-decisions');
    async function save(kind, value) {
      if (saving.has(prefix)) return;
      saving.add(prefix); states[prefix].requestId = (states[prefix].requestId || 0) + 1;
      reviewPanel.setAttribute('aria-busy', 'true');
      reviewPanel.querySelectorAll('button,select').forEach(control => { control.disabled = true; });
      feedback.classList.remove('error'); feedback.textContent = 'Saving…';
      try {
        const payload = kind === 'label' ? {label: value} : {decision: value};
        await api(`/api/incidents/${encodeURIComponent(item.id)}/${kind}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
        drafts.delete(item.id);
        const message = kind === 'label' ? `Saved label: ${friendly(value)}. Review ${value === 'normal' ? 'marked false positive' : 'confirmed'}.`
          : value === 'unreviewed' ? 'Review undone. Operator label cleared; this event needs review again.'
          : value === 'false_positive' ? 'Saved as false positive. Training label: normal.'
          : `Review confirmed. Training label: ${friendly(savedLabel === 'normal' ? item.event_type : savedLabel)}.`;
        messages.set(item.id, message); feedback.textContent = message;
        $(`#${prefix}-feedback`).textContent = `${friendly(item.event_type)} · ${item.camera_name}: ${message}`;
        // Invalidate the old card even if the server values did not change.
        row.dataset.signature = '';
      } catch (error) {
        feedback.classList.add('error'); feedback.textContent = `Not saved: ${error.message}. Please try again.`;
        messages.delete(item.id);
      } finally {
        saving.delete(prefix); reviewPanel.removeAttribute('aria-busy');
        reviewPanel.querySelectorAll('button,select').forEach(control => { control.disabled = false; });
      }
      await load(prefix, true);
    }
    for (const [label, decision] of [['Confirm event', 'confirmed'], ['False alarm · normal', 'false_positive']]) {
      const control = action(label, () => save('review', decision));
      control.className = `review-choice ${item.review === decision ? 'selected' : ''}`;
      control.setAttribute('aria-pressed', String(item.review === decision));
      control.disabled = item.review === decision;
      buttons.append(control);
    }
    if (reviewed) { const undo = action('Undo review', () => save('review', 'unreviewed')); undo.className = 'undo-review'; buttons.append(undo); }
    const details = node('details', 'incident-details');
    details.open = priorState.get(item.id)?.open || false;
    details.append(node('summary', '', 'Detection details & downloads'));
    if (item.reasons?.length) details.append(node('p', '', item.reasons.join(' · ')));
    const links = node('div', 'incident-links');
    if (item.clip) { const link = node('a', '', 'Download clip'); link.href = `/api/library/evidence/${encodeURIComponent(item.id)}?kind=annotated`; links.append(link); }
    if (item.raw_clip) { const link = node('a', '', 'Raw clip'); link.href = `/api/library/evidence/${encodeURIComponent(item.id)}?kind=raw`; links.append(link); }
    details.append(links);
    details.append(node('p', '', item.raw_start != null ? `Training clip: ${timeLabel(item.raw_start)}–${timeLabel(item.raw_end)} in source, without analysis overlays.` : 'Older raw training clips may be shorter and lower resolution than the review video.'));
    const labelControls = node('div', 'label-controls');
    const selector = node('select', 'label-select');
    selector.setAttribute('aria-label', `Training label for ${item.camera_name} ${item.event_type}`);
    for (const label of trainingLabels) {
      const option = node('option', '', label.replaceAll('_', ' '));
      option.value = label;
      selector.append(option);
    }
    selector.value = drafts.get(item.id) || savedLabel;
    selector.id = `label-${prefix}-${item.id}`;
    const labelTitle = node('label', 'review-label-title', 'Correct the event type'); labelTitle.htmlFor = selector.id;
    const saveButton = action('Save label', () => save('label', selector.value)); saveButton.className = 'primary';
    saveButton.disabled = reviewed && selector.value === savedLabel;
    selector.addEventListener('change', () => {
      drafts.set(item.id, selector.value);
      saveButton.disabled = reviewed && selector.value === savedLabel;
      feedback.classList.remove('error');
      feedback.textContent = selector.value === savedLabel ? (reviewed ? 'This label is already saved.' : 'Suggested label. Save to confirm your review.') : 'Unsaved change — select Save label to apply.';
    });
    labelControls.append(selector, saveButton);
    reviewPanel.append(heading, node('p', 'review-help', 'Confirm the detected event, mark it as normal, or choose a corrected label. Saving a label also saves your review.'), buttons, labelTitle, labelControls, feedback);
    row.append(top);
    if (item.clip || item.raw_clip) {
      const evidence = node('div', 'event-evidence');
      const video = document.createElement('video'); video.controls = true; video.preload = 'none'; video.playsInline = true;
      video.setAttribute('aria-label', `Evidence for ${item.event_type} at ${new Date(item.created * 1000).toLocaleString()}`);
      video.poster = `/api/library/evidence/${encodeURIComponent(item.id)}/preview`;
      const savedVideo = cached?.querySelector('video');
      const version = `${item.event_count}:${item.source_end}:${item.evidence_end}:${item.raw_end}:${item.clip}:${item.raw_clip}`;
      video.dataset.version = version;
      video.src = `/api/library/evidence/${encodeURIComponent(item.id)}/play?v=${encodeURIComponent(version)}`;
      const clipRange = item.evidence_start != null ? `Saved clip: ${timeLabel(item.evidence_start)}–${timeLabel(item.evidence_end)} in source. ` : 'Older clip: exact source range was not recorded. ';
      const message = node('p', 'hint', clipRange + 'Overlays are detector proposals; the operator label is shown above.');
      video.addEventListener('error', () => { message.textContent = 'Playback unavailable. Use the frame viewer or download the clip below.'; });
      evidence.append(savedVideo?.dataset.version === version ? savedVideo : video, message);
      row.append(evidence);
      details.append(window.recordedFrameViewer(`/api/library/evidence/${encodeURIComponent(item.id)}`, item.event_type));
      details.append(action('Delete evidence', async () => {
        if (!confirm('Delete both saved evidence clips? The event record stays, but the clips will be unavailable for review and future training.')) return;
        try { await api(`/api/library/evidence/${encodeURIComponent(item.id)}`, {method: 'DELETE'}); await load(prefix, true); }
        catch (error) { showError(error.message); }
      }));
    } else row.append(node('p', 'section-note', 'Evidence is unavailable or still being saved.'));
    row.append(reviewPanel, details);
    if (cached) { if (cached === cursor) cursor = cached.nextSibling; cached.remove(); }
    place(row);
  }
  }
  async function load(prefix, force = false) {
    if (saving.has(prefix)) return;
    const state = states[prefix];
    const params = new URLSearchParams({scope: prefix === 'event' ? 'recent' : 'history', page: state.page});
    for (const name of ['search', 'review', 'priority']) params.set(name, $(`#${prefix}-${name}`).value);
    for (const name of ['from', 'to']) {
      const value = $(`#${prefix}-${name}`).value;
      if (value) params.set(name, new Date(value).getTime() / 1000);
    }
    if (params.has('from') && params.has('to') && Number(params.get('from')) > Number(params.get('to'))) {
      $(`#${prefix}-summary`).textContent = 'From time must be before Through time.'; return;
    }
    const requestId = state.requestId = (state.requestId || 0) + 1;
    try {
      const data = await api(`/api/event-records?${params}`);
      if (requestId !== state.requestId) return;
      if (state.page > data.pages) { state.page = data.pages; return load(prefix); }
      $(`#${prefix}-summary`).textContent = `${data.total} matching events · ${data.items.filter(item => item.review === 'unreviewed').length} need review on this page`;
      $(`#${prefix}-page`).textContent = `Page ${data.page} of ${data.pages}`;
      $(`#${prefix}-previous`).disabled = state.page <= 1;
      $(`#${prefix}-next`).disabled = state.page >= data.pages;
      const signature = JSON.stringify(data.items);
      if (force || signature !== state.signature) { render(data.items, prefix); state.signature = signature; }
    } catch (error) { $(`#${prefix}-summary`).textContent = error.message; }
  }
  for (const prefix of ['event', 'history']) {
    let timer;
    for (const name of ['search', 'review', 'priority', 'from', 'to']) $(`#${prefix}-${name}`).addEventListener('input', () => {
      states[prefix].requestId = (states[prefix].requestId || 0) + 1;
      clearTimeout(timer); timer = setTimeout(() => { states[prefix].page = 1; load(prefix); }, 250);
    });
    $(`#${prefix}-clear`).addEventListener('click', () => {
      for (const name of ['search', 'from', 'to']) $(`#${prefix}-${name}`).value = '';
      for (const name of ['review', 'priority']) $(`#${prefix}-${name}`).value = 'all';
      states[prefix].page = 1; load(prefix);
    });
    for (const [name, delta] of [['previous', -1], ['next', 1]]) $(`#${prefix}-${name}`).addEventListener('click', () => { states[prefix].page += delta; load(prefix); });
  }
  function active() {
    if (location.hash === '#evidence') { location.hash = 'events'; return; }
    if (['#events', '#history'].includes(location.hash)) load(location.hash === '#events' ? 'event' : 'history');
    document.querySelectorAll('.page:not(.active) video').forEach(video => video.pause());
  }
  window.addEventListener('hashchange', active);
  setInterval(active, 3000);
  active();
})();
