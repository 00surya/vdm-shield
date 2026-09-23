(() => {
  // Keep documentation controls independent of the live-monitoring script.
  const $ = selector => document.querySelector(selector);
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const action = (title, callback) => {
    const button = node('button', '', title);
    button.type = 'button';
    button.addEventListener('click', callback);
    return button;
  };
  const cache = new Map(); let content = null; let selected = 'en'; let requestId = 0;
  try { const saved = localStorage.getItem('vmd-docs-language'); if (['en', 'hi', 'ta', 'ur', 'kn', 'te', 'bn', 'mr', 'gu', 'ml'].includes(saved)) selected = saved; } catch (_) {}
  $('#docs-language').value = selected;
  function render() {
    if (!content) return;
    const engineering = content.audience === 'engineering';
    $('#docs').lang = engineering ? 'en' : selected;
    $('#docs').dir = !engineering && selected === 'ur' ? 'rtl' : 'ltr';
    $('#docs-topics').setAttribute('aria-label', content.title);
    for (const [id, key] of [['title','title'],['subtitle','subtitle'],['note','note'],['language-label','language'],['search-label','search']]) $(`#docs-${id}`).textContent = content[key];
    $('#docs-search').placeholder = content.search;
    const query = $('#docs-search').value.trim().toLocaleLowerCase();
    const sections = content.sections.filter(section => section.join(' ').toLocaleLowerCase().includes(query));
    $('#docs-topics').replaceChildren(); $('#docs-content').replaceChildren();
    $('#docs-status').textContent = sections.length ? '' : content.empty;
    for (const [id, title, text] of sections) {
      const card = node('article', 'docs-card'); card.id = `doc-topic-${id}`; card.tabIndex = -1;
      card.append(node('h2', '', title)); for (const paragraph of text.split('\n')) card.append(node('p', '', paragraph));
      $('#docs-content').append(card);
      const button = action(title, () => { card.scrollIntoView({behavior:'smooth', block:'start'}); card.focus({preventScroll:true}); });
      $('#docs-topics').append(button);
    }
  }
  async function load() {
    const id = ++requestId; const language = $('#docs-language').value;
    const engineering = $('#docs-audience').value === 'engineering';
    const key = engineering ? 'engineering' : language;
    $('#docs-language').disabled = false;
    $('#docs-language-help').textContent = engineering
      ? 'Engineering is available in English. Choose a language to open the translated Operator guide.'
      : 'Language changes apply to the Operator guide. Application controls remain in English.';
    $('#docs-status').textContent = 'Loading guide…';
    $('#docs-content').setAttribute('aria-busy', 'true');
    try {
      if (!cache.has(key)) { const response = await fetch(`/static/docs/${key}.json`); if (!response.ok) throw Error('Could not load documentation.'); cache.set(key, await response.json()); }
      if (id !== requestId) return;
      selected = language; content = cache.get(key); $('#docs-search').value = ''; render();
      try { localStorage.setItem('vmd-docs-language', selected); } catch (_) {}
    } catch(error) {
      if (id === requestId) {
        $('#docs-status').textContent = error.message;
        $('#docs-content').replaceChildren(); $('#docs-topics').replaceChildren();
        content = null;
      }
    } finally { if (id === requestId) $('#docs-content').setAttribute('aria-busy', 'false'); }
  }
  $('#docs-audience').addEventListener('change', load);
  $('#docs-language').addEventListener('change', () => {
    $('#docs-audience').value = 'operator';
    load();
  }); $('#docs-search').addEventListener('input', render);
  window.addEventListener('hashchange', () => { if (location.hash === '#docs' && !content) load(); });
  if (location.hash === '#docs') load();
})();
