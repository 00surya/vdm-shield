const $ = (selector) => document.querySelector(selector);
const sourceList = $('#source-list');
const incidentList = $('#incidents');
let busy = false;
let frameBusy = false;
let selectedKind = 'upload';
let currentSources = [];
let lastIncidentData = '';
let lastNormalData = '';
let activePage = 'analyze';
let metricsRange = '24h';
let metricsBusy = false;
let selectedHeatmapKey = null;
let errorTimer;
const frameSequences = new Map();
const displayedFrames = new Map();
function browserViewFps(cameraId) {
  const samples = displayedFrames.get(cameraId)?.times || [];
  const recent = samples.filter(at => performance.now() - at <= 5000);
  if (displayedFrames.has(cameraId)) displayedFrames.get(cameraId).times = recent;
  return recent.length > 1 ? (recent.length - 1) * 1000 / (recent.at(-1) - recent[0]) : 0;
}
function markDisplayed(cameraId, sequence) {
  const item = displayedFrames.get(cameraId) || {times: [], sequence: -1};
  if (sequence === item.sequence) return;
  item.sequence = sequence;
  item.times.push(performance.now());
  displayedFrames.set(cameraId, item);
}
const trainingLabels = ['normal', 'fight', 'possible_fall', 'person_down', 'person_down_after_fight', 'hands_up', 'robbery', 'chain_snatching', 'crowd_gathering', 'fainting', 'hit_and_run', 'accident', 'other_anomaly', 'gun_detected', 'knife_detected', 'grenade_detected', 'possible_explosion'];

function showMessage(message, tone = 'error') {
  const banner = $('#global-message');
  banner.textContent = message;
  banner.dataset.tone = tone;
  banner.hidden = false;
  clearTimeout(errorTimer);
  errorTimer = setTimeout(() => { banner.hidden = true; }, tone === 'success' ? 4000 : 8000);
}

function showError(message) { showMessage(message, 'error'); }
function showSuccess(message) { showMessage(message, 'success'); }

function showPage() {
  const requested = location.hash.slice(1);
  activePage = ['analyze', 'videos', 'sources', 'events', 'history', 'metrics', 'capacity', 'training', 'docs', 'sizing', 'logs'].includes(requested) ? requested : 'sources';
  document.querySelectorAll('.page').forEach(page => page.classList.toggle('active', page.dataset.page === activePage));
  document.querySelectorAll('[data-page-link]').forEach(link => {
    const active = link.dataset.pageLink === activePage;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  document.title = `${activePage[0].toUpperCase() + activePage.slice(1)} · VMD Shield`;
  window.scrollTo(0, 0);
  if (activePage === 'metrics') refreshMetrics();
  if (activePage === 'capacity') window.refreshCapacity?.();
  if (activePage === 'sources') refreshFrames();
  if (activePage === 'training') refreshTraining();
}

window.addEventListener('hashchange', showPage);
showPage();

function timeLabel(seconds) {
  if (!Number.isFinite(seconds)) return '0:00';
  const value = Math.max(0, Math.floor(seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { ...options, headers: { 'X-VMD-Client': 'dashboard', ...(options.headers || {}) } });
  } catch (error) {
    throw new Error('Cannot reach the analysis server. Restart it with start.command or .venv/bin/python run.py, then reload this page.');
  }
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    throw new Error(response.status === 404 || response.status === 405
      ? 'The running server does not support this request. Restart VMD Shield with start.command, then reload this page.'
      : `The server returned a page instead of API data (${response.status}). Restart VMD Shield and reload this page.`);
  }
  let data;
  try { data = await response.json(); }
  catch { throw new Error('The server sent invalid API data. Restart VMD Shield and reload this page.'); }
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

for (const tab of document.querySelectorAll('.tab')) {
  tab.addEventListener('click', () => {
    selectedKind = tab.dataset.tab;
    $('#kind').value = selectedKind;
    $('#upload-fields').hidden = selectedKind !== 'upload';
    $('#camera-fields').hidden = selectedKind !== 'camera';
    document.querySelectorAll('.tab').forEach(item => {
      item.classList.toggle('active', item === tab);
      item.setAttribute('aria-selected', String(item === tab));
    });
    $('#add-button').innerHTML = selectedKind === 'upload' ? 'Analyze video <span aria-hidden="true">→</span>' : 'Connect source <span aria-hidden="true">→</span>';
    $('#form-message').textContent = '';
  });
}

const videoInput = $('#video');
const dropzone = $('.dropzone');
videoInput.addEventListener('change', () => {
  $('#file-label').textContent = videoInput.files?.[0]?.name || 'Choose a video or drop it here';
});
for (const type of ['dragenter', 'dragover']) {
  dropzone.addEventListener(type, event => { event.preventDefault(); dropzone.classList.add('dragging'); });
}
for (const type of ['dragleave', 'drop']) {
  dropzone.addEventListener(type, event => { event.preventDefault(); dropzone.classList.remove('dragging'); });
}
dropzone.addEventListener('drop', event => {
  if (event.dataTransfer?.files?.length) {
    videoInput.files = event.dataTransfer.files;
    videoInput.dispatchEvent(new Event('change'));
  }
});

$('#source-form').addEventListener('submit', async event => {
  event.preventDefault();
  const sourceForm = event.currentTarget;
  const button = $('#add-button');
  button.disabled = true;
  $('#form-message').textContent = selectedKind === 'upload' ? 'Uploading video…' : 'Connecting stream…';
  try {
    const form = new FormData(sourceForm);
    form.set('kind', selectedKind);
    await api('/api/sources', { method: 'POST', body: form });
    $('#form-message').textContent = 'Source added. Waiting for processed frames…';
    sourceForm.reset();
    $('#video').value = '';
    $('#file-label').textContent = 'Choose a video or drop it here';
    await refresh();
    location.hash = '#sources';
  } catch (error) {
    $('#form-message').textContent = error.message;
  } finally { button.disabled = false; }
});

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function action(label, callback) {
  const button = node('button', '', label);
  button.type = 'button';
  button.addEventListener('click', callback);
  return button;
}

async function control(id, command) {
  try { await api(`/api/sources/${encodeURIComponent(id)}/${command}`, { method: 'POST' }); frameSequences.delete(id); displayedFrames.delete(id); await refresh(); }
  catch (error) { showError(error.message); }
}

async function setEcoMode(id, enabled, button) {
  if (button) {
    button.disabled = true;
    button.classList.add('saving');
    button.querySelector('.eco-switch-state').textContent = 'Saving…';
  }
  try {
    const changed = await api(`/api/sources/${encodeURIComponent(id)}/eco`, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled})});
    const savedSource = currentSources.find(item => item.camera_id === id);
    if (savedSource) Object.assign(savedSource, {eco_mode: changed.eco_mode, eco_state: changed.eco_state});
    renderSources(currentSources);
    showSuccess(`Eco mode turned ${changed.eco_mode ? 'on' : 'off'}${savedSource?.name ? ` for ${savedSource.name}` : ''}.`);
    try {
      const sourceData = await api('/api/sources');
      currentSources = sourceData.sources;
      renderSources(currentSources);
    } catch (_) {
      // The setting is already saved. Normal polling will refresh the details.
    }
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.classList.remove('saving');
      button.querySelector('.eco-switch-state').textContent = enabled ? 'Off' : 'On';
    }
    showError(error.message);
  }
}

function ecoSwitch(item) {
  const control = node('div', 'eco-switch-control');
  const copy = node('div', 'eco-switch-copy');
  copy.append(node('strong', '', 'Eco mode'), node('small', '', item.eco_mode
    ? item.eco_state === 'quiet' ? 'Quiet scene · safety scans running' : 'Full analysis · watching for quiet scene'
    : 'Optional processing saver'));
  const button = node('button', `eco-switch${item.eco_mode ? ' is-on' : ''}`);
  button.type = 'button';
  button.setAttribute('role', 'switch');
  button.setAttribute('aria-checked', String(Boolean(item.eco_mode)));
  button.setAttribute('aria-label', `${item.eco_mode ? 'Turn off' : 'Turn on'} Eco mode for ${item.name || 'camera'}`);
  button.append(node('span', 'eco-switch-track'), node('span', 'eco-switch-state', item.eco_mode ? 'On' : 'Off'));
  button.addEventListener('click', () => setEcoMode(item.camera_id, !item.eco_mode, button));
  control.append(copy, button);
  return control;
}

function view(label, id, type) {
  const panel = node('div', 'view');
  const image = node('img');
  image.alt = label;
  image.id = `${type}-${id}`;
  image.hidden = true;
  const placeholder = node('div', 'placeholder', 'Waiting for frames');
  placeholder.id = `${type}-placeholder-${id}`;
  panel.append(image, placeholder, node('div', 'view-label', label));
  return panel;
}

function renderSources(sources) {
  const existing = new Map([...sourceList.querySelectorAll('.source-card')].map(card => [card.dataset.id, card]));
  sourceList.replaceChildren();
  $('#source-count').textContent = `${sources.length} / 4`;
  if (!sources.length) { sourceList.append(node('p', 'empty', 'Add a video or camera to start analysis.')); return; }
  for (const item of sources) {
    const card = existing.get(item.camera_id) || node('article', 'source-card');
    card.dataset.id = item.camera_id;
    const priorPose = card.querySelector('.view img[id^="pose-"]')?.src;
    const priorDepth = card.querySelector('.view img[id^="depth-"]')?.src;
    const priorThreat = card.querySelector('.view img[id^="threat-"]')?.src;
    const detailsOpen = card.querySelector('.source-details')?.open || false;
    card.replaceChildren();
    const head = node('div', 'source-head');
    const title = node('div');
    title.append(node('h3', '', item.name), node('p', '', item.source_kind === 'upload' ? 'Uploaded video' : 'Live camera / stream'));
    const headActions = node('div', 'source-head-actions');
    headActions.append(node('span', `badge ${item.status}`, item.status));
    if (item.source_kind === 'camera') headActions.append(ecoSwitch(item));
    head.append(title, headActions);
    const views = node('div', 'views');
    const sampledAt = (sourceTime, age) => sourceTime == null ? '' : item.source_kind === 'upload'
      ? ` · sampled at ${timeLabel(sourceTime)}` : ` · ${Math.round(age || 0)}s old`;
    views.append(view(`Pose + motion${item.source_kind === 'upload' && item.source_time != null ? ` · at ${timeLabel(item.source_time)}` : ''}`, item.camera_id, 'pose'),
      view(`Relative depth${sampledAt(item.depth_meta?.source_time, item.depth_meta?.age_seconds)}`, item.camera_id, 'depth'),
      view(`Objects + threats${sampledAt(item.threat_source_time, item.threat_age_seconds)}`, item.camera_id, 'threat'));
    const metrics = node('div', 'metrics');
    metrics.append(node('span', '', `Input ${Number(item.capture_fps || 0).toFixed(1)} FPS`),
      node('span', '', `Analyzed ${Number(item.processed_fps || 0).toFixed(1)} FPS`),
      node('span', '', `Browser view ${activePage === 'sources' ? browserViewFps(item.camera_id).toFixed(1) : '—'} FPS`),
      node('span', '', `People ${item.people || 0}`), node('span', '', `Rule score ${Number(item.score || 0).toFixed(2)} / 1`), node('span', '', `Depth ${item.depth_meta?.status || 'waiting'}`));
    if (item.alert) metrics.append(node('span', 'alert', item.alert.label));
    for (const pending of item.signals?.pending_pairs || []) {
      if (pending.state === 'possible_fight') continue;
      const depthNote = pending.depth_status === 'compatible' ? '' : ` · depth ${pending.depth_status}`;
      metrics.append(node('span', '', `Checking interaction · tracks ${pending.tracks.join(' & ')} · ${Number(pending.seconds).toFixed(1)} / ${pending.required_seconds}s${depthNote}`));
    }
    metrics.append(node('span', '', `Object detector: ${item.threat_status || 'loading'}${item.threat_stale && item.threat_status === 'ready' ? ' · waiting for fresh sample' : ''}`));
    for (const object of item.threat_objects || []) {
      metrics.append(node('span', 'alert', `${object.label} · ${Math.round(object.confidence * 100)}% confidence`));
    }
    metrics.append(node('span', '', `Scene objects (YOLO26s): ${item.object_status || 'waiting'}`));
    if (item.eco_mode) metrics.append(node('span', 'eco-status', `Eco ${item.eco_state === 'quiet' ? '· quiet scene' : '· full analysis'} · ${item.eco_skipped_frames || 0} scans avoided`));
    const counts = new Map();
    for (const object of item.scene_objects || []) counts.set(object.label, (counts.get(object.label) || 0) + 1);
    for (const [label, count] of counts) metrics.append(node('span', '', `${label} × ${count} · context`));
    const zPanel = node('section', 'depth-z');
    zPanel.setAttribute('aria-label', 'Relative Z axis');
    const zHeading = node('div', 'depth-z-heading');
    zHeading.append(node('strong', '', 'Relative Z axis'),
      node('span', '', item.depth_available && item.depth_meta?.sequence != null ? `Frame ${item.depth_meta.sequence}${sampledAt(item.depth_meta.source_time, item.depth_meta.age_seconds)}` : 'Waiting for depth'));
    zPanel.append(zHeading, node('p', 'depth-z-note', '0 = nearer · 1 = farther. Compare people in this sample only; values are not metres.'));
    if (item.depth_available && item.depth_people?.length) {
      for (const person of item.depth_people) {
        const row = node('div', 'depth-z-row');
        row.append(node('span', '', `Track ${person.track_id}`));
        if (Number.isFinite(person.relative_z)) {
          const axis = node('meter', 'depth-z-meter');
          axis.min = 0; axis.max = 1; axis.value = person.relative_z;
          axis.setAttribute('aria-label', `Track ${person.track_id} relative Z, nearer at 0 and farther at 1`);
          row.append(axis, node('span', 'depth-z-value', `Z ${person.relative_z.toFixed(2)}`));
        } else {
          row.append(node('span', 'depth-z-uncertain', 'Z uncertain'));
        }
        zPanel.append(row);
      }
    } else {
      const message = item.depth_meta?.status === 'simulated' ? 'Synthetic demo — no camera distance measured'
        : !item.depth_available ? (item.depth_meta?.stale ? 'Waiting for a fresh depth sample' : 'Relative Z is not available yet')
        : item.depth_z_status === 'no_pose' ? 'No matching pose for this depth frame' : 'No tracked person in this depth sample';
      zPanel.append(node('p', 'depth-z-note', message));
    }
    const details = node('details', 'source-details');
    details.open = detailsOpen;
    details.append(node('summary', '', 'Analysis details'));
    const detailedMetrics = node('div', 'detail-metrics');
    detailedMetrics.append(node('span', '', `Motion ${item.signals?.local_flow ?? '—'}`),
      node('span', '', `Frames captured ${item.captured_frames || 0}`),
      node('span', '', `Frames analyzed ${item.processed_frames || 0}`),
      node('span', '', `Frames skipped ${item.skipped_frames || 0}`),
      node('span', '', `Analysis target ${item.target_fps || 0} FPS`),
      node('span', '', `Per-frame work ${Math.round(item.latency_ms || 0)} ms`));
    if (item.source_fps) detailedMetrics.append(node('span', '', `Source reports ${Number(item.source_fps).toFixed(1)} FPS`));
    if (item.capture_to_processed_ms != null) detailedMetrics.append(node('span', '', `Camera to analysis ${item.capture_to_processed_ms} ms`));
    details.append(node('p', 'source-message', 'Input is decoded frames per second; analyzed is completed pose/motion frames per second; browser view counts frames loaded on this tab. The object and depth views are sampled separately.'));
    details.append(detailedMetrics);
    if (item.depth_error) details.append(node('p', 'source-message', item.depth_error));
    if (item.depth_meta?.model) details.append(node('p', 'source-message', `${item.depth_meta.model} · ${item.depth_meta.backend || ''} · target ${item.depth_meta.target_fps} sample/s`));
    if (item.object_error) details.append(node('p', 'source-message', item.object_error));
    details.append(node('p', 'source-message', 'Blue boxes: 80-class scene detector, context only. Red boxes: specialist weapon detector. General detections never raise weapon alarms.'));
    if (item.threat_error) details.append(node('p', 'source-message', item.threat_error));
    details.append(node('p', 'source-message', 'Object confidence is a model score, not a probability of danger. Small or hidden objects may be missed.'));
    const depthPairs = node('div', 'depth-pairs');
    if (item.depth_pairs?.length) {
      for (const pair of item.depth_pairs.slice(0, 3)) {
        let label = `Tracks ${pair.tracks.join(' & ')}: z order uncertain${pair.relative_gap == null ? '' : ` · relative gap ${pair.relative_gap}`}`;
        if (pair.status === 'separated') label = `Tracks ${pair.tracks.join(' & ')}: track ${pair.nearer_track} appears nearer · relative gap ${pair.relative_gap}`;
        depthPairs.append(node('p', '', label));
      }
    } else {
      depthPairs.append(node('p', '', 'No close pair in the latest depth sample'));
    }
    const timeline = node('div', 'timeline');
    if (item.source_kind === 'upload') {
      const duration = item.duration_seconds;
      timeline.append(node('div', 'timeline-label', duration ? `${timeLabel(item.source_time || 0)} / ${timeLabel(duration)} · ${Math.round((item.progress || 0) * 100)}% processed` : 'Preparing video timeline…'));
      const progress = node('progress');
      progress.max = 1;
      progress.value = item.progress || 0;
      timeline.append(progress);
    } else {
      timeline.append(node('div', 'timeline-label', item.status === 'running' ? `Live feed · frame ${item.sequence || 0} · last update ${item.frame_age_seconds ?? 0}s ago` : 'Waiting for stream'));
    }
    const controls = node('div', 'actions');
    if (['running', 'starting', 'stopping'].includes(item.status)) controls.append(action('Stop', () => control(item.camera_id, 'stop')));
    else controls.append(action('Restart', () => control(item.camera_id, 'restart')));
    controls.append(action('Remove source', () => control(item.camera_id, 'remove')));
    details.append(depthPairs);
    card.append(head, views, timeline, metrics, zPanel, details, node('p', 'source-message', item.message || ''), controls);
    sourceList.append(card);
    if (priorPose) { const img = card.querySelector(`#pose-${item.camera_id}`); img.src = priorPose; img.hidden = false; img.nextElementSibling.hidden = true; }
    if (priorDepth && item.depth_available) { const img = card.querySelector(`#depth-${item.camera_id}`); img.src = priorDepth; img.hidden = false; img.nextElementSibling.hidden = true; }
    if (priorThreat && !item.threat_stale && item.threat_status === 'ready') { const img = card.querySelector(`#threat-${item.camera_id}`); img.src = priorThreat; img.hidden = false; img.nextElementSibling.hidden = true; }
  }
}

function renderIncidents(items) {
  const urgent = items.filter(item => item.created >= Date.now() / 1000 - 43200 && item.signals?.priority === 'high' && item.review === 'unreviewed');
  $('#threat-notice').hidden = urgent.length === 0;
  $('#threat-notice-text').textContent = `${urgent.length} recent object alerts need review`;
}

function renderTrainingAudit(audit) {
  $('#audit-clips').textContent = audit.candidate_clips.toLocaleString();
  $('#audit-reviewed').textContent = audit.reviewed_clips.toLocaleString();
  $('#audit-provisional').textContent = audit.provisional_clips.toLocaleString();
  $('#audit-sources').textContent = audit.source_count.toLocaleString();
  $('#audit-missing').textContent = audit.missing_raw.toLocaleString();
  $('#audit-origin').textContent = `Label origin: ${audit.origin.heuristic_proposed || 0} heuristic proposals · ${audit.origin.auto_normal || 0} automatically sampled normal clips · ${audit.origin.operator_reviewed || 0} operator-reviewed clips. ${audit.rejected_normal} normal sample(s) rejected.`;
  const readiness = $('#training-readiness');
  const normalCount = audit.labels.find(item => item.label === 'normal')?.count || 0;
  const eventCount = Math.max(0, ...audit.labels.filter(item => item.label !== 'normal').map(item => item.count));
  readiness.textContent = audit.training_ready ? 'Minimum clip counts met · review labels before training' : `Next: collect ${Math.max(0, 3 - normalCount)} more normal clips and ${Math.max(0, 2 - eventCount)} more clips of one event type.`;
  readiness.classList.toggle('ready', audit.training_ready);

  const rows = $('#audit-labels');
  rows.replaceChildren();
  if (!audit.labels.length) {
    const row = node('tr');
    const cell = node('td', '', 'No usable raw clips yet. Analyze footage to collect training examples.');
    cell.colSpan = 5;
    row.append(cell);
    rows.append(row);
  }
  for (const item of audit.labels) {
    const row = node('tr');
    const countCell = node('td', 'label-count');
    const bar = node('progress', 'label-progress');
    bar.max = Math.max(1, audit.candidate_clips);
    bar.value = item.count;
    countCell.append(node('strong', '', String(item.count)), bar);
    row.append(node('td', 'audit-label', item.label.replaceAll('_', ' ')), countCell,
               node('td', '', String(item.reviewed)), node('td', '', String(item.provisional)),
               node('td', '', String(item.sources)));
    rows.append(row);
  }
  const biggest = audit.labels[0]?.label.replaceAll('_', ' ') || 'none';
  $('#audit-balance').textContent = audit.candidate_clips
    ? `Largest label: ${biggest} (${Math.round(audit.dominant_label_share * 100)}% of clips). Largest source: ${Math.round(audit.dominant_source_share * 100)}% of clips. ${audit.excluded_model_generated} unreviewed model-generated clip(s) excluded to prevent self-training.`
    : 'No candidate clips yet. Unreviewed model-generated alerts are excluded from training.';
  const findings = $('#audit-findings');
  findings.replaceChildren();
  for (const finding of audit.findings) {
    const row = node('div', `audit-finding ${finding.level}`);
    row.append(node('strong', '', finding.title), node('p', '', finding.detail));
    findings.append(row);
  }
}

function renderEvaluation(evaluation, trainedAt) {
  const measured = evaluation?.state === 'measured';
  const labelRows = $('#evaluation-labels');
  labelRows.replaceChildren();
  for (const item of (measured ? evaluation.per_label || [] : [])) {
    const row = node('tr');
    for (const value of [item.label.replaceAll('_', ' '), item.clips, item.correct, item.clips - item.correct]) row.append(node('td', '', String(value)));
    labelRows.append(row);
  }
  if (!labelRows.children.length) {
    const row = node('tr'); const cell = node('td', '', 'No per-label results yet. Train with enough reviewed clips to measure them.');
    cell.colSpan = 4; row.append(cell); labelRows.append(row);
  }
  const asPercent = value => value == null ? '—' : `${Math.round(value * 100)}%`;
  $('#eval-precision').textContent = measured ? asPercent(evaluation.event_precision) : '—';
  $('#eval-recall').textContent = measured ? asPercent(evaluation.event_recall) : '—';
  $('#eval-accuracy').textContent = measured ? asPercent(evaluation.binary_accuracy) : '—';
  $('#eval-type-accuracy').textContent = measured ? asPercent(evaluation.type_accuracy) : '—';
  if (measured) {
    $('#evaluation-status').textContent = `Held-out validation on ${evaluation.clips} operator-reviewed clips from ${evaluation.sources} held-out sources.`;
    const c = evaluation.confusion;
    $('#evaluation-detail').textContent = `Last trained ${new Date(trainedAt * 1000).toLocaleString()}. Held-out results: ${c.true_event} events caught, ${c.missed_event} events missed, ${c.false_alarm} false alarms, ${c.true_normal} normal clips correctly left unflagged. Coverage: ${evaluation.clips} of ${evaluation.eligible_clips ?? evaluation.clips} reviewed clips; ${evaluation.skipped_clips ?? 0} not in this test set (may be used for training). Retrain after label changes to update these values.`;
  } else {
    $('#evaluation-status').textContent = 'Not measured yet';
    $('#evaluation-detail').textContent = evaluation?.reason || 'Collect operator-reviewed clips from several sources, then train to measure these values.';
  }
}

async function refreshTraining() {
  try {
    const data = await api('/api/training');
    $('#training-status').textContent = data.model.message;
    $('#temporal-active').textContent = data.model.active ? `Active: ResNet18 + GRU · trained ${new Date(data.model.active_trained_at * 1000).toLocaleString()}` : 'No temporal model active. Pose, depth and object analysis continue.';
    $('#temporal-params').textContent = '16 RGB frames · frozen ResNet18 · GRU 64 · AdamW 0.001 · batch 8 · 30 epochs · ' + (data.model.trainable_parameters ? `${data.model.trainable_parameters.toLocaleString()} trainable parameters` : 'head size depends on learned classes');
    $('#training-progress').hidden = data.model.state !== 'training';
    $('#training-progress').value = data.model.epoch || 0;
    if (data.model.inference_error) $('#temporal-active').textContent += ` · ${data.model.inference_error}`;
    $('#train-button').disabled = data.model.state === 'training' || !data.audit.training_ready;
    renderTrainingAudit(data.audit);
    renderEvaluation(data.model.evaluation, data.model.trained_at);
    const list = $('#normal-samples');
    const normalData = JSON.stringify(data.normal_samples);
    if (normalData === lastNormalData) return;
    lastNormalData = normalData;
    list.replaceChildren();
    if (data.normal_samples.length) list.append(node('h3', '', 'Automatically sampled normal clips'));
    for (const sample of data.normal_samples) {
      const row = node('div', 'normal-sample');
      row.append(node('span', '', `${new Date(sample.created * 1000).toLocaleString()} · ${sample.review}`));
      const link = node('a', '', 'Download raw clip');
      link.href = `/api/training/normal/${encodeURIComponent(sample.id)}/clip`;
      row.append(link);
      for (const [label, decision] of [['Accept', 'accepted'], ['Reject', 'rejected']]) {
        row.append(action(label, async () => {
          try {
            await api(`/api/training/normal/${encodeURIComponent(sample.id)}/review`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({decision})});
            await refreshTraining();
          } catch (error) { showError(error.message); }
        }));
      }
      list.append(row);
    }
  } catch (error) { $('#training-status').textContent = error.message; }
}

$('#train-button').addEventListener('click', async () => {
  try { await api('/api/training/train', {method: 'POST'}); await refreshTraining(); }
  catch (error) { $('#training-status').textContent = error.message; }
});

for (const button of document.querySelectorAll('.range')) {
  button.addEventListener('click', () => {
    metricsRange = button.dataset.range;
    document.querySelectorAll('.range').forEach(item => {
      const active = item === button;
      item.classList.toggle('active', active);
      item.setAttribute('aria-pressed', String(active));
    });
    refreshMetrics();
  });
}

function drawColumns(target, rows, key, count, bucketSeconds, label) {
  const values = new Map(rows.map(row => [Number(row.bucket), Number(row[key] || 0)]));
  const localShift = -new Date().getTimezoneOffset() * 60;
  const currentBucket = Math.floor((Date.now() / 1000 + localShift) / bucketSeconds) * bucketSeconds - localShift;
  const bins = Array.from({length: count}, (_, index) => currentBucket - (count - 1 - index) * bucketSeconds);
  const highest = Math.max(1, ...bins.map(bucket => values.get(bucket) || 0));
  target.replaceChildren();
  target.setAttribute('aria-label', `${label}: ${bins.map(bucket => `${new Date(bucket * 1000).toLocaleString()}: ${values.get(bucket) || 0}`).join('; ')}`);
  if (!rows.length || bins.every(bucket => !values.get(bucket))) {
    target.append(node('p', 'chart-empty', `No ${label.toLowerCase()} recorded in this period.`));
    return;
  }
  for (const [index, bucket] of bins.entries()) {
    const value = values.get(bucket) || 0;
    const column = node('div', 'chart-column');
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 100 100');
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('aria-hidden', 'true');
    if (value) {
      const bar = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      const height = Math.max(3, value / highest * 100);
      bar.setAttribute('x', '20');
      bar.setAttribute('y', String(100 - height));
      bar.setAttribute('width', '60');
      bar.setAttribute('height', String(height));
      bar.setAttribute('rx', '2');
      bar.setAttribute('class', 'chart-bar-shape');
      svg.append(bar);
    }
    const date = new Date(bucket * 1000);
    const tick = node('span', 'chart-tick', count === 7
      ? date.toLocaleDateString(undefined, {weekday: 'short'})
      : (index % 6 === 0 || index === count - 1 ? date.toLocaleTimeString(undefined, {hour: 'numeric'}) : ''));
    column.title = key === 'peak' && !values.has(bucket)
      ? `${date.toLocaleString()}: no saved frame samples`
      : `${date.toLocaleString()}: ${value} ${label.toLowerCase()}`;
    if (key === 'peak' && !values.has(bucket)) column.classList.add('no-sample');
    column.append(svg, tick);
    target.append(column);
  }
}

function localDayKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function drawHeatmap(eventRows, sampleRows) {
  const target = $('#signal-heatmap');
  target.replaceChildren();
  const counts = new Map();
  const samples = new Map();
  for (const row of eventRows) {
    const date = new Date(Number(row.bucket) * 1000);
    const key = `${localDayKey(date)}-${date.getHours()}`;
    counts.set(key, (counts.get(key) || 0) + Number(row.count));
  }
  for (const row of sampleRows) {
    const date = new Date(Number(row.bucket) * 1000);
    const key = `${localDayKey(date)}-${date.getHours()}`;
    samples.set(key, (samples.get(key) || 0) + Number(row.samples));
  }
  const hours = node('div', 'heatmap-row heatmap-hours');
  hours.append(node('span', 'heatmap-day', 'Local time'));
  for (let hour = 0; hour < 24; hour++) {
    hours.append(node('span', '', hour % 4 === 0 ? String(hour).padStart(2, '0') : ''));
  }
  target.append(hours);
  const today = new Date();
  let selected = null;
  let selectedDescription = null;
  for (let dayIndex = 0; dayIndex < 7; dayIndex++) {
    const day = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6 + dayIndex);
    const row = node('div', 'heatmap-row');
    row.append(node('span', 'heatmap-day', day.toLocaleDateString(undefined, {weekday: 'short', day: 'numeric'})));
    for (let hour = 0; hour < 24; hour++) {
      const key = `${localDayKey(day)}-${hour}`;
      const count = counts.get(key) || 0;
      const sampleCount = samples.get(key) || 0;
      const cell = node('button', 'heat-cell');
      cell.type = 'button';
      const level = count >= 4 ? 'many' : count >= 2 ? 'two' : count === 1 ? 'one' : sampleCount ? 'zero' : 'unknown';
      cell.classList.add(`heat-${level}`);
      const label = `${day.toLocaleDateString(undefined, {weekday: 'long', month: 'short', day: 'numeric'})}, ${String(hour).padStart(2, '0')}:00–${String(hour).padStart(2, '0')}:59: ${count} detection signal${count === 1 ? '' : 's'}, ${sampleCount} saved frame sample${sampleCount === 1 ? '' : 's'}`;
      cell.title = label;
      cell.setAttribute('aria-label', label);
      if (key === selectedHeatmapKey) {
        cell.classList.add('selected');
        selected = cell;
        selectedDescription = label;
      }
      const describe = () => { $('#heatmap-selection').textContent = label; };
      cell.addEventListener('mouseenter', describe);
      cell.addEventListener('focus', describe);
      cell.addEventListener('click', () => {
        selected?.classList.remove('selected');
        cell.classList.add('selected');
        selected = cell;
        selectedHeatmapKey = key;
        describe();
      });
      row.append(cell);
    }
    target.append(row);
  }
  $('#heatmap-selection').textContent = selectedDescription || 'Select a square to see its exact signal and sample counts.';
}

function drawTypes(types) {
  const target = $('#type-chart');
  target.replaceChildren();
  if (!types.length) {
    target.append(node('p', 'chart-empty', 'No detected event types in this period.'));
    return;
  }
  const highest = Math.max(...types.map(item => item.count));
  for (const item of types) {
    const row = node('div', 'type-row');
    const track = node('progress', 'type-track');
    track.max = highest;
    track.value = item.count;
    row.append(node('span', '', item.event_type.replaceAll('_', ' ')), track, node('strong', '', String(item.count)));
    target.append(row);
  }
}

async function refreshMetrics() {
  if (metricsBusy) return;
  metricsBusy = true;
  const requestedRange = metricsRange;
  try {
    const data = await api(`/api/analytics?range=${requestedRange}&tz_offset=${new Date().getTimezoneOffset()}`);
    if (requestedRange !== metricsRange) return;
    const pipelineBody = $('#pipeline-metrics');
    pipelineBody.replaceChildren();
    const format = (value, unit) => Number.isFinite(value) ? `${value.toFixed(1)} ${unit}` : '—';
    for (const source of data.pipeline || []) {
      const stages = [['Camera input', {completed_fps: source.capture_fps}],
        ['Pose + motion', source.pipeline?.pose_motion],
        ['Depth', source.pipeline?.depth],
        ['Objects + weapons', source.pipeline?.objects_weapons]];
      for (const [stage, measurement] of stages) {
        const row = node('tr', '');
        for (const value of [`${source.name || 'Source'} · ${stage}${source.status !== 'running' ? ' (' + source.status + ')' : ''}`,
          format(measurement?.completed_fps, 'fps'), format(measurement?.inference_ms, 'ms'),
          format(measurement?.capture_to_result_ms, 'ms'), format(measurement?.result_age_seconds, 's')]) row.append(node('td', '', value));
        pipelineBody.append(row);
      }
    }
    if (!data.pipeline?.length) { const row = node('tr', ''); const cell = node('td', '', 'Connect a source to measure the pipeline.'); cell.colSpan = 5; row.append(cell); pipelineBody.append(row); }
    $('#pipeline-evidence').textContent = data.evidence ? `Evidence: ${data.evidence.queued_jobs} queued jobs · ${data.evidence.dropped_records} dropped records · Writer ${data.evidence.writer_alive ? 'running' : 'stopped'}` : 'Evidence telemetry unavailable.';
    const total = data.events.reduce((sum, item) => sum + item.count, 0);
    $('#metric-events').textContent = total.toLocaleString();
    $('#metric-unreviewed').textContent = (data.reviews.unreviewed || 0).toLocaleString();
    $('#metric-confirmed').textContent = (data.reviews.confirmed || 0).toLocaleString();
    $('#metric-dismissed').textContent = (data.reviews.false_positive || 0).toLocaleString();
    $('#metric-peak').textContent = data.people.length ? Math.max(...data.people.map(item => item.peak)).toLocaleString() : '—';
    const count = requestedRange === '24h' ? 24 : 7;
    drawColumns($('#event-chart'), data.events, 'count', count, data.bucket_seconds, 'Detected events');
    drawColumns($('#people-chart'), data.people, 'peak', count, data.bucket_seconds, 'Peak people');
    $('#event-chart-note').textContent = `${total.toLocaleString()} detection signal${total === 1 ? '' : 's'} in this range. Bars show ${requestedRange === '24h' ? 'hours' : 'days'}.`;
    $('#people-chart-note').textContent = `${data.people.reduce((sum, item) => sum + item.samples, 0).toLocaleString()} saved frame samples. Blank intervals have no saved samples.`;
    drawHeatmap(data.heatmap_events, data.heatmap_samples);
    drawTypes(data.types);
    $('#metrics-status').textContent = `Updated ${new Date().toLocaleTimeString()} · Times shown in your local timezone`;
  } catch (error) {
    $('#metrics-status').textContent = error.message;
  } finally {
    metricsBusy = false;
    if (requestedRange !== metricsRange && activePage === 'metrics') refreshMetrics();
  }
}

async function refresh() {
  if (busy) return;
  busy = true;
  try {
    const [sourceData, incidents] = await Promise.all([api('/api/sources'), api('/api/incidents')]);
    currentSources = sourceData.sources;
    renderSources(currentSources);
    const incidentData = JSON.stringify(incidents);
    if (incidentData !== lastIncidentData) {
      renderIncidents(incidents);
      lastIncidentData = incidentData;
    }
    if (currentSources.some(item => item.sequence > 0) && $('#form-message').textContent.startsWith('Source added.')) {
      $('#form-message').textContent = '';
    }
    if (activePage === 'sources') await refreshFrames();
  } catch (error) { showError(error.message); }
  finally { busy = false; }
}

async function refreshFrames() {
  if (activePage !== 'sources' || frameBusy || !currentSources.length) return;
  frameBusy = true;
  try {
    await Promise.all(currentSources.map(async item => {
      if (!['running', 'starting', 'finished'].includes(item.status)) return;
      const seen = frameSequences.get(item.camera_id) || {pose: -1, depth: -1, threat: -1};
      if (item.status === 'finished' && seen.pose === item.sequence &&
          (item.depth_meta?.sequence == null || seen.depth === item.depth_meta.sequence) &&
          (item.threat_sequence == null || seen.threat === item.threat_sequence)) return;
      const query = new URLSearchParams({pose_after: seen.pose, depth_after: seen.depth, threat_after: seen.threat});
      const frame = await api(`/api/sources/${encodeURIComponent(item.camera_id)}/frames?${query}`);
      for (const type of ['pose', 'depth', 'threat']) {
        const image = document.getElementById(`${type}-${item.camera_id}`);
        if (!image) continue;
        if (frame[type]) {
          if (type === 'pose') image.onload = () => requestAnimationFrame(() => {
            if (image.isConnected && activePage === 'sources') markDisplayed(item.camera_id, frame.sequence);
          });
          image.src = `data:image/jpeg;base64,${frame[type]}`;
          image.hidden = false;
          image.nextElementSibling.hidden = true;
          seen[type] = type === 'pose' ? frame.sequence : type === 'depth' ? frame.depth_meta.sequence : frame.threat_sequence;
        } else if (type === 'threat' && (frame.threat_stale || frame.threat_status !== 'ready')) {
          image.removeAttribute('src');
          image.hidden = true;
          image.nextElementSibling.hidden = false;
          image.nextElementSibling.textContent = 'Object detector: ' + (frame.threat_status === 'ready' ? 'awaiting fresh sample' : frame.threat_status);
        } else if (type === 'depth' && (frame.depth_meta?.status === 'error' || frame.depth_meta?.status === 'no_sample' || frame.depth_meta?.stale)) {
          image.removeAttribute('src');
          image.hidden = true;
          image.nextElementSibling.hidden = false;
          image.nextElementSibling.textContent = frame.depth_meta?.status === 'no_sample' ? 'No depth sample' : 'Depth unavailable';
        }
      }
      frameSequences.set(item.camera_id, seen);
    }));
  } catch (error) { showError(error.message); }
  finally { frameBusy = false; }
}

refresh();
setInterval(refresh, 1000);
async function pollFrames() {
  await refreshFrames();
  setTimeout(pollFrames, activePage !== 'sources' ? 250 : currentSources.length > 2 ? 100 : currentSources.length === 2 ? 75 : 50);
}
pollFrames();
setInterval(() => { if (activePage === 'training') refreshTraining(); }, 3000);
setInterval(() => { if (activePage === 'metrics') refreshMetrics(); }, 15000);
