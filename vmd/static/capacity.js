let fpsSaving = false;
const retentionForm = document.querySelector('#retention-form');
const retentionDays = document.querySelector('#retention-days');
const retentionStatus = document.querySelector('#retention-status');
let retentionLoaded = false;
let capacityBusy = false;
let deviceSnapshot = null;

function deviceText(tag, className, value) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = value;
  return element;
}

function ecoDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return '0 min';
  if (seconds < 60) return `${Math.round(seconds)} sec`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

async function loadRetention() {
  if (retentionLoaded) return;
  try {
    const setting = await api('/api/evidence-retention');
    if (![...retentionDays.options].some(option => Number(option.value) === setting.days)) {
      const option = new Option(`${setting.days} days`, setting.days);
      retentionDays.add(option);
    }
    retentionDays.value = setting.days;
    retentionDays.disabled = false;
    retentionForm.querySelector('button').disabled = false;
    retentionStatus.textContent = `New clips will be cleaned up after ${setting.days} ${setting.days === 1 ? 'day' : 'days'}.`;
    retentionLoaded = true;
  } catch (error) { retentionStatus.textContent = `Setting unavailable. Retrying… ${error.message}`; }
}

retentionForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (!retentionLoaded || !retentionForm.reportValidity()) return;
  const button = retentionForm.querySelector('button');
  button.disabled = true;
  retentionDays.disabled = true;
  retentionStatus.textContent = 'Saving…';
  try {
    const setting = await api('/api/evidence-retention', {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({days: Number(retentionDays.value)}),
    });
    retentionDays.value = setting.days;
    retentionStatus.textContent = `Saved. New clips will be cleaned up after ${setting.days} ${setting.days === 1 ? 'day' : 'days'}.`;
  } catch (error) { retentionStatus.textContent = `Could not save: ${error.message}`; }
  finally { button.disabled = false; retentionDays.disabled = false; }
});

// Static assets can update while an older Python server is still running.
// Show only fields that server actually provides; never infer camera health.
function renderOlderDevice(data) {
  clearDeviceSummary();
  document.querySelector('#capacity-updated').textContent = 'Server restart required';
  document.querySelector('#device-status').textContent = 'Device update pending';
  document.querySelector('#device-status').dataset.status = 'attention';
  document.querySelector('#device-status-detail').textContent =
    'The interface has been updated, but the server is still running an older version. Restart VMD, then refresh this page.';
  document.querySelector('#device-camera-count').textContent = 'Status pending restart';
  document.querySelector('#capacity-sources').replaceChildren();
  document.querySelector('#device-issues').replaceChildren();
  const free = data?.disk_free_gb;
  document.querySelector('#device-disk-free').textContent =
    typeof free === 'number' && Number.isFinite(free) ? `${free} GB free` : 'Unavailable';
  document.querySelector('#device-disk-detail').textContent = 'Full disk usage will be available after restart.';
  document.querySelector('#device-disk-meter').removeAttribute('value');
}

function renderDevice(data) {
  if (fpsSaving || document.activeElement?.closest('.camera-fps-form')) return;
  const device = data?.device;
  if (!device) {
    renderOlderDevice(data);
    return;
  }
  const evidence = device.evidence;
  document.querySelector('#device-evidence-status').textContent = !evidence ? 'Clip status needs a restart'
    : evidence.last_error || !evidence.writer_alive ? 'Some clips may not be saving' : 'Alert clips are saving normally';
  document.querySelector('#device-evidence-detail').textContent = evidence
    ? evidence.last_error
      ? `Please contact support. ${evidence.last_error}`
      : `${evidence.last_saved_at ? 'Last alert clip saved ' + new Date(evidence.last_saved_at * 1000).toLocaleString() + '.' : 'No new alert clip has been created since this restart.'}${evidence.queued_jobs ? ` ${evidence.queued_jobs} ${evidence.queued_jobs === 1 ? 'clip is' : 'clips are'} waiting to be saved.` : ''}${evidence.dropped_records ? ` ${evidence.dropped_records} ${evidence.dropped_records === 1 ? 'clip could' : 'clips could'} not be saved.` : ''}`
    : 'Restart VMD to see whether alert clips are saving.';
  const product = document.querySelector('#device-product');
  product.replaceChildren();
  const productLabels = {name: 'Device name', version: 'Software version', support: 'Support contact',
    update_status: 'Software updates', camera_limit: 'Camera capacity', access: 'Sign-in protection'};
  for (const [label, value] of Object.entries(device.product || {status: 'Restart server for device information'})) {
    const row = document.createElement('div');
    row.append(deviceText('dt', '', productLabels[label] || label.replaceAll('_', ' ')), deviceText('dd', '', value));
    product.append(row);
  }
  const states = {
    idle: ['Ready to add a camera', 'Connect a camera when you are ready to start monitoring.'],
    starting: ['Getting cameras ready', 'The system is checking the video and detection tools.'],
    attention: ['Something needs your attention', 'Follow the message below to restore normal monitoring.'],
    ready: ['Everything is working', 'Cameras are connected, detection is running, and clips can be saved.'],
  };
  const [title, detail] = states[device.status] || ['Status unavailable', 'Refresh to try again.'];
  const banner = document.querySelector('.device-banner');
  banner.dataset.status = device.status;
  document.querySelector('.device-banner-icon').textContent = device.status === 'attention' ? '!' : device.status === 'starting' ? '…' : device.status === 'idle' ? '+' : '✓';
  document.querySelector('#device-status').textContent = title;
  document.querySelector('#device-status').dataset.status = device.status;
  document.querySelector('#device-status-detail').textContent = detail;
  const issues = document.querySelector('#device-issues');
  issues.replaceChildren();
  if (device.issues.length) {
    const panel = deviceText('div', 'device-alert-panel', '');
    panel.append(deviceText('strong', '', 'Action needed'));
    const list = deviceText('ul', 'device-alerts', '');
    for (const issue of device.issues) list.append(deviceText('li', '', issue));
    panel.append(list); issues.append(panel);
  } else {
    const okay = deviceText('div', 'device-all-clear', '');
    okay.append(deviceText('strong', '', 'No action needed'), deviceText('span', '', 'The latest system check found no problems.'));
    issues.append(okay);
  }
  document.querySelector('#device-camera-count').textContent = device.configured_cameras
    ? `${device.online_cameras} of ${device.configured_cameras} connected` : 'No cameras added';
  const sources = document.querySelector('#capacity-sources');
  sources.replaceChildren();
  if (!device.cameras.length) {
    const row = document.createElement('tr');
    const cell = deviceText('td', '', 'No cameras configured. Set up a camera to start monitoring.');
    cell.colSpan = 4; row.append(cell); sources.append(row);
  }
  for (const camera of device.cameras) {
    const row = document.createElement('tr');
    const active = camera.status === 'running' && !camera.stale;
    const state = camera.stale || camera.status === 'error' ? 'Offline'
      : camera.status === 'starting' ? 'Starting' : active ? 'Online' : 'Stopped';
    const name = deviceText('td', '', '');
    name.append(deviceText('strong', '', camera.name || 'Camera'));
    if (active && camera.frame_age_seconds != null) {
      name.append(deviceText('small', '', `Last frame ${camera.frame_age_seconds}s ago`));
    }
    const ecoSetting = deviceText('div', 'camera-eco-setting', '');
    ecoSetting.append(deviceText('span', '', 'Eco mode'));
    const ecoToggle = deviceText('button', `eco-switch${camera.eco_mode ? ' is-on' : ''}`, '');
    ecoToggle.type = 'button';
    ecoToggle.setAttribute('role', 'switch');
    ecoToggle.setAttribute('aria-checked', String(Boolean(camera.eco_mode)));
    ecoToggle.setAttribute('aria-label', `${camera.eco_mode ? 'Turn off' : 'Turn on'} Eco mode for ${camera.name || 'camera'}`);
    ecoToggle.append(deviceText('span', 'eco-switch-track', ''), deviceText('span', 'eco-switch-state', camera.eco_mode ? 'On' : 'Off'));
    ecoToggle.addEventListener('click', async () => {
      ecoToggle.disabled = true;
      ecoToggle.classList.add('saving');
      ecoToggle.querySelector('.eco-switch-state').textContent = 'Saving…';
      const status = document.querySelector('#eco-save-status');
      status.textContent = `Saving Eco mode for ${camera.name || 'camera'}…`;
      try {
        const changed = await api(`/api/sources/${encodeURIComponent(camera.camera_id)}/eco`, {
          method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled: !camera.eco_mode}),
        });
        status.textContent = `Saved. Eco mode is ${changed.eco_mode ? 'on' : 'off'} for ${camera.name || 'camera'}.`;
        camera.eco_mode = changed.eco_mode;
        camera.eco_state = changed.eco_state;
        ecoToggle.classList.toggle('is-on', changed.eco_mode);
        ecoToggle.classList.remove('saving');
        ecoToggle.disabled = false;
        ecoToggle.setAttribute('aria-checked', String(changed.eco_mode));
        ecoToggle.setAttribute('aria-label', `${changed.eco_mode ? 'Turn off' : 'Turn on'} Eco mode for ${camera.name || 'camera'}`);
        ecoToggle.querySelector('.eco-switch-state').textContent = changed.eco_mode ? 'On' : 'Off';
        try { renderDevice(await api('/api/capacity')); }
        catch (_) { status.textContent += ' Live totals will refresh automatically.'; }
      } catch (error) {
        status.textContent = `Could not change Eco mode: ${error.message}`;
        ecoToggle.disabled = false;
        ecoToggle.classList.remove('saving');
        ecoToggle.querySelector('.eco-switch-state').textContent = camera.eco_mode ? 'On' : 'Off';
      }
    });
    ecoSetting.append(ecoToggle);
    name.append(ecoSetting);
    const connection = deviceText('td', '', '');
    connection.append(deviceText('span', `device-badge ${active ? 'good' : 'warn'}`, state));
    const rate = deviceText('td', '', '');
    const speedDetails = deviceText('details', 'camera-speed-details', '');
    speedDetails.append(deviceText('summary', '', 'Speed details'));
    if (active && camera.measured) {
      const target = camera.eco_mode && camera.eco_state === 'quiet' ? 1 : Math.min(camera.input_fps || camera.target_fps, camera.target_fps);
      rate.append(deviceText('span', `device-badge ${camera.keeping_up ? 'good' : 'warn'}`, camera.keeping_up ? 'Working well' : 'Needs attention'));
      rate.append(deviceText('small', '', camera.eco_mode && camera.eco_state === 'quiet'
        ? 'Quiet scene · reduced processing' : camera.keeping_up ? 'Analysis is keeping up' : 'Analysis is slower than requested'));
      speedDetails.append(deviceText('strong', '', `Analysis: ${camera.analyzed_fps.toFixed(1)} FPS`));
      speedDetails.append(deviceText('small', '', camera.eco_mode && camera.eco_state === 'quiet'
        ? `Eco quiet scan: ${target.toFixed(1)} FPS · Active target: ${camera.target_fps.toFixed(1)} FPS`
        : `Input: ${camera.input_fps.toFixed(1)} FPS · Target: ${camera.target_fps.toFixed(1)} FPS`));
      if (camera.input_fps > 0 && camera.input_fps < camera.target_fps) speedDetails.append(deviceText('small', '', 'Incoming video is below the requested target.'));
    } else rate.append(deviceText('span', `device-badge ${active ? '' : 'warn'}`, active ? 'Checking…' : 'Not running'));
    const fpsForm = deviceText('form', 'camera-fps-form', '');
    const fpsLabel = deviceText('label', '', 'Target FPS');
    const fpsInput = document.createElement('input');
    fpsInput.type = 'number'; fpsInput.min = '1'; fpsInput.max = '60'; fpsInput.step = '1';
    fpsInput.required = true; fpsInput.value = camera.target_fps;
    fpsInput.setAttribute('aria-label', `Target analysis FPS for ${camera.name || 'Camera'}`);
    fpsLabel.append(fpsInput);
    const save = deviceText('button', '', 'Save FPS'); save.type = 'submit';
    fpsForm.append(fpsLabel, save);
    fpsForm.addEventListener('submit', async event => {
      event.preventDefault();
      if (!fpsForm.reportValidity()) return;
      fpsSaving = true; save.disabled = true;
      const status = document.querySelector('#fps-save-status');
      status.textContent = 'Saving analysis target…';
      try {
        const setting = await api(`/api/sources/${encodeURIComponent(camera.camera_id)}/fps`, {
          method: 'PUT', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({target_fps: Number(fpsInput.value)}),
        });
        status.textContent = `Saved ${setting.target_fps} FPS for ${camera.name || 'Camera'}. Allow a few seconds for measurements to settle.`;
        document.activeElement?.blur();
      } catch (error) { status.textContent = error.message; }
      finally { fpsSaving = false; save.disabled = false; }
    });
    speedDetails.append(fpsForm);
    rate.append(speedDetails);
    const checks = deviceText('td', '', '');
    const friendlyWorker = value => value === 'ready' ? 'Ready' : value === 'error' ? 'Needs attention' : 'Starting';
    checks.append(deviceText('small', '', `Depth: ${friendlyWorker(camera.depth_status)}`),
      deviceText('small', '', `Objects: ${friendlyWorker(camera.object_status)}`));
    if (camera.eco_mode) checks.append(deviceText('span', 'device-badge eco', camera.eco_state === 'quiet' ? 'Eco · quiet' : 'Eco · active'));
    if (camera.workers_stale && active) checks.append(deviceText('span', 'device-badge warn', 'Delayed'));
    row.append(name, connection, rate, checks);
    sources.append(row);
  }
  const storage = device.storage;
  document.querySelector('#device-disk-free').textContent = `${storage.free_gb} GB free`;
  document.querySelector('#device-disk-meter').value = storage.used_pct;
  document.querySelector('#device-disk-detail').textContent = `${storage.used_gb} of ${storage.total_gb} GB used`;
  deviceSnapshot = {exported_at: new Date().toISOString(), device, hardware: data.hardware};
  document.querySelector('#device-export').disabled = false;
  document.querySelector('#device-online-kpi').textContent = device.configured_cameras ? `${device.online_cameras} / ${device.configured_cameras}` : 'None yet';
  document.querySelector('#device-online-note').textContent = device.configured_cameras ? 'Connected cameras' : 'Add your first camera';
  const allDetectionsHealthy = device.configured_cameras > 0 && device.healthy_cameras === device.configured_cameras;
  document.querySelector('#device-health-kpi').textContent = !device.configured_cameras ? 'Waiting'
    : allDetectionsHealthy ? 'Working' : device.status === 'starting' ? 'Starting' : 'Check';
  document.querySelector('#device-storage-kpi').textContent = `${storage.free_gb} GB`;
  document.querySelector('#device-eco-kpi').textContent = device.eco_cameras ? `${device.eco_cameras} on` : 'Off';
  document.querySelector('#device-eco-note').textContent = device.eco_cameras
    ? device.eco_comparison?.ready
      ? `${device.eco_quiet_cameras} quiet · ${device.eco_comparison.pose_scans_saved_estimate.toLocaleString()} pose scans avoided`
      : `${device.eco_quiet_cameras} quiet · measuring savings`
    : 'Available for live cameras';
  const comparison = device.eco_comparison || {};
  const normalBar = document.querySelector('#eco-normal-bar');
  const actualBar = document.querySelector('#eco-actual-bar');
  const comparisonList = document.querySelector('#eco-camera-comparisons');
  comparisonList.replaceChildren();
  if (comparison.ready && comparison.normal_pose_scans_estimate > 0) {
    const normal = comparison.normal_pose_scans_estimate;
    const actual = comparison.actual_pose_scans;
    normalBar.max = normal; normalBar.value = normal;
    actualBar.max = normal; actualBar.value = Math.min(actual, normal);
    document.querySelector('#eco-normal-label').textContent = `${normal.toLocaleString()} estimated pose scans`;
    document.querySelector('#eco-actual-label').textContent = `${actual.toLocaleString()} actual pose scans`;
    document.querySelector('#eco-saved-scans').textContent = comparison.pose_scans_saved_estimate.toLocaleString();
    document.querySelector('#eco-reduction').textContent = `${comparison.pose_reduction_pct}%`;
    document.querySelector('#eco-quiet-time').textContent = ecoDuration(device.eco_quiet_seconds);
    document.querySelector('#eco-comparison-status').textContent = device.eco_quiet_seconds > 0
      ? `Measured from ${comparison.camera_count} ${comparison.camera_count === 1 ? 'camera' : 'cameras'} in this server session.`
      : 'Eco mode is on, but motion has kept full analysis active. Savings begin after 10 continuous quiet seconds.';
  } else {
    normalBar.max = 1; normalBar.value = 0; actualBar.max = 1; actualBar.value = 0;
    document.querySelector('#eco-normal-label').textContent = 'Waiting for full-analysis measurement';
    document.querySelector('#eco-actual-label').textContent = device.eco_cameras ? 'Eco mode is collecting data' : 'Eco mode is off';
    document.querySelector('#eco-saved-scans').textContent = '—';
    document.querySelector('#eco-reduction').textContent = '—';
    document.querySelector('#eco-quiet-time').textContent = ecoDuration(device.eco_quiet_seconds);
    document.querySelector('#eco-comparison-status').textContent = device.eco_cameras
      ? 'Keep the camera running through active and quiet periods to build a comparison.'
      : 'Turn on Eco mode for a live camera to begin measuring.';
  }
  for (const camera of device.cameras.filter(item => item.eco_mode || item.eco_comparison_ready)) {
    const item = deviceText('div', 'eco-camera-comparison', '');
    item.append(deviceText('strong', '', camera.name || 'Camera'),
      deviceText('span', '', camera.eco_comparison_ready
        ? `${camera.eco_pose_reduction_pct}% estimated reduction · ${camera.eco_pose_scans_saved_estimate.toLocaleString()} pose scans avoided`
        : `${camera.eco_state === 'quiet' ? 'Quiet mode' : 'Full analysis'} · collecting comparison data`));
    comparisonList.append(item);
  }
  const seconds = device.service_uptime_seconds;
  document.querySelector('#device-uptime').textContent = seconds == null ? 'Unavailable' :
    seconds < 60 ? '< 1 min' : seconds < 3600 ? `${Math.floor(seconds / 60)} min` :
    `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
  document.querySelector('#device-storage-health').textContent = storage.low ? 'Space is running low' : 'Enough space is available';
  document.querySelector('#device-storage-health').dataset.low = String(storage.low);
  const hardware = data.hardware || {};
  const specs = document.querySelector('#device-hardware');
  specs.replaceChildren();
  for (const [label, value] of [
    ['System', hardware.model || hardware.os || 'Unavailable'],
    ['Processor', hardware.chip || hardware.architecture || 'Unavailable'],
    ['Memory', hardware.memory_gb == null ? 'Unavailable' : `${hardware.memory_gb} GB`],
    ['Processing mode', hardware.inference_device || 'Unavailable'],
    ['Low storage threshold', 'Less than 10% free or 5 GB free'],
    ['Tested camera capacity', 'Not certified; current performance is checked live'],
  ]) {
    const item = document.createElement('div');
    item.append(deviceText('dt', '', label), deviceText('dd', '', value)); specs.append(item);
  }
  document.querySelector('#capacity-updated').textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

window.refreshCapacity = async function () {
  if (capacityBusy) return;
  capacityBusy = true;
  document.querySelector('#device-refresh').disabled = true;
  try {
    await loadRetention();
    renderDevice(await api('/api/capacity'));
  } catch (error) {
    clearDeviceSummary();
    document.querySelector('#capacity-updated').textContent = 'Connection lost · retrying';
    document.querySelector('#device-status').textContent = 'Device status unavailable';
    document.querySelector('#device-status').dataset.status = 'attention';
    document.querySelector('#device-status-detail').textContent = error.message;
    document.querySelector('#device-camera-count').textContent = 'Unknown';
    document.querySelector('#capacity-sources').replaceChildren();
    document.querySelector('#device-issues').replaceChildren();
    document.querySelector('#device-disk-free').textContent = 'Unavailable';
    document.querySelector('#device-disk-detail').textContent = 'Waiting for device connection.';
    document.querySelector('#device-disk-meter').removeAttribute('value');
  } finally { capacityBusy = false; document.querySelector('#device-refresh').disabled = false; }
};

function clearDeviceSummary() {
  deviceSnapshot = null;
  document.querySelector('#device-evidence-status').textContent = 'Status unavailable';
  document.querySelector('#device-evidence-detail').textContent = '';
  document.querySelector('#device-product').replaceChildren();
  document.querySelector('#device-export').disabled = true;
  for (const id of ['device-online-kpi', 'device-health-kpi', 'device-storage-kpi', 'device-eco-kpi', 'device-uptime']) {
    document.getElementById(id).textContent = '—';
  }
  document.querySelector('#device-online-note').textContent = 'Waiting for the system';
  document.querySelector('#device-eco-note').textContent = 'Waiting for the system';
  document.querySelector('#eco-comparison-status').textContent = 'Waiting for the system';
  document.querySelector('#eco-normal-label').textContent = 'Unavailable';
  document.querySelector('#eco-actual-label').textContent = 'Unavailable';
  document.querySelector('#eco-saved-scans').textContent = '—';
  document.querySelector('#eco-reduction').textContent = '—';
  document.querySelector('#eco-quiet-time').textContent = '—';
  document.querySelector('#eco-camera-comparisons').replaceChildren();
  document.querySelector('#device-storage-health').textContent = 'Current storage status unavailable';
  document.querySelector('#device-hardware').replaceChildren();
}
document.querySelector('#device-refresh').addEventListener('click', () => window.refreshCapacity());
document.querySelector('#device-export').addEventListener('click', () => {
  if (!deviceSnapshot) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(deviceSnapshot, null, 2)], {type: 'application/json'}));
  const link = document.createElement('a');
  link.href = url; link.download = 'vmd-device-diagnostics.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

const deviceFullscreen = document.querySelector('#device-fullscreen');
deviceFullscreen.hidden = !document.fullscreenEnabled;
deviceFullscreen.addEventListener('click', async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch (_) { document.querySelector('#device-status-detail').textContent = 'Full screen is unavailable in this browser.'; }
});
document.addEventListener('fullscreenchange', () => {
  deviceFullscreen.textContent = document.fullscreenElement ? 'Exit full screen' : 'Open full screen';
});
if (location.hash === '#capacity') window.refreshCapacity();
setInterval(() => {
  if (location.hash === '#capacity' && !document.hidden) window.refreshCapacity();
}, 5000);

document.querySelector('#sizing-form')?.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button');
  const result = document.querySelector('#sizing-result');
  button.disabled = true;
  result.textContent = 'Checking validated configurations…';
  try {
    const plan = await api('/api/deployment-plan', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    result.replaceChildren(deviceText('h2', '', plan.matches.length ? 'Validated configurations' : 'Benchmark required'));
    if (!plan.matches.length) result.append(deviceText('p', '', 'No tested device matches these requirements. Add a full-pipeline concurrent-camera benchmark before quoting hardware.'));
    for (const match of plan.matches) {
      const card = deviceText('article', 'capacity-result-card', '');
      card.append(deviceText('h3', '', match.name),
        deviceText('p', '', `${match.devices} device(s) · up to ${match.cameras_per_device} cameras each · measured minimum ${match.measured_minimum_fps} FPS per camera`),
        deviceText('p', '', match.specifications),
        deviceText('p', '', `Benchmark: ${match.benchmark_date} · ${match.report}`));
      result.append(card);
    }
    result.append(deviceText('p', 'capacity-note', plan.note));
  } catch (error) { result.textContent = error.message; }
  finally { button.disabled = false; }
});
