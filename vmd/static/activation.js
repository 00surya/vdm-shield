const byId = id => document.getElementById(id);
async function status() {
  const response = await fetch('/api/licence');
  if (!response.ok) throw new Error('Cannot read licence status.');
  const state = await response.json();
  byId('state').textContent = !state.managed ? 'Development mode' : state.valid ? 'Licence active' : 'Processing is locked';
  byId('message').textContent = `${state.message} ${state.sync_message || ''}`;
  byId('details').textContent = state.managed ? `Device: ${state.device_id ?? 'Not activated'} · Source allowance: ${state.camera_limit} · Lease remaining: ${state.remaining_seconds}s` : 'Set VDM_CLOUD_URL when starting the app to enable managed activation.';
  byId('workspace').textContent = state.organisation_name ? `${state.organisation_name} / ${state.group_name || 'Organisation device'}${state.owner_email ? ' · ' + state.owner_email : ''}` : 'Choose your group on the website, then generate an activation code.';
  byId('activate').hidden = !state.managed;
}
async function action(name, data = {}) {
  const buttons = document.querySelectorAll('button');
  buttons.forEach(button => button.disabled = true);
  byId('result').textContent = 'Working…';
  try {
    const response = await fetch(`/api/licence/${name}`, {method:'POST', headers:{'Content-Type':'application/json','X-VMD-Client':'dashboard'},body:JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Request failed.');
    byId('result').textContent = result.message;
    if (name === 'activate') byId('code').value = '';
    await status();
  } catch (error) { byId('result').textContent = error.message; }
  finally { buttons.forEach(button => button.disabled = false); }
}
byId('activate').addEventListener('submit', event => {event.preventDefault(); action('activate', {code:byId('code').value});});
byId('check').addEventListener('click', () => action('check'));
byId('clear').addEventListener('click', () => {if(confirm('Remove local activation and stop processing? Saved evidence will remain.')) action('clear');});
status().catch(error => byId('result').textContent = error.message);
setInterval(() => status().catch(() => {}), 5000);
