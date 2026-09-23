async function refreshLicenceBanner() {
  const banner = document.getElementById('licence-banner');
  try {
    const response = await fetch('/api/licence');
    if (!response.ok) throw new Error();
    const state = await response.json();
    banner.textContent = state.managed ? `${state.organisation_name || 'VDM'}${state.group_name ? ' / ' + state.group_name : ''} · ${state.valid ? 'Licence active' : 'Processing locked'} · ${state.message}` : state.message;
  } catch (_) { banner.textContent = 'Licence status unavailable. Open Activation & licence.'; }
}
refreshLicenceBanner();
setInterval(refreshLicenceBanner, 10000);
