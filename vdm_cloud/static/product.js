/* Local product illustrations. No camera, account or inference API is called here. */
(() => {
  'use strict';
  const root = document.documentElement;
  const media = window.matchMedia('(prefers-reduced-motion: reduce)');
  const state = { motion: !media.matches, progress: 0, step: 0, exploded: false, playing: false };
  const scenes = [], stories = [];
  const motionButton = document.querySelector('.motion-toggle');
  let userMotionPreference = false, suspended = false, frame = 0, previous = 0, rendererModule;
  root.classList.add('enhanced');
  const text = (selector, value) => {
    const node = document.querySelector(selector);
    if (node.textContent !== value) node.textContent = value;
  };
  const select = (selector, value, key) => document.querySelectorAll(selector).forEach(button => {
    const active = button.dataset[key] === String(value);
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });

  // A single clock drives visible stories. Time never advances in the background.
  function running(story) {
    return state.motion && story.visible && !story.paused && !document.hidden && !suspended;
  }
  function refreshScenes() {
    const host = document.querySelector('[data-scene="architecture"]');
    host.classList.toggle('is-assembled', !state.exploded);
    host.querySelectorAll('.fallback-layer').forEach((layer, index) => layer.classList.toggle('active', 4 - index === state.step));
    scenes.forEach(scene => scene.update());
  }
  function syncStory(story) {
    const active = running(story);
    story.element.classList.toggle('story-running', active);
    story.element.classList.toggle('offscreen-motion', !story.visible);
    const label = !state.motion ? 'Enable motion' : story.paused ? story.resumeLabel : story.pauseLabel;
    story.button.setAttribute('aria-label', label);
    story.button.querySelector('span').textContent = active || (!story.paused && state.motion) ? 'Ⅱ' : '▶';
    const caption = story.button.querySelector('.story-toggle-label, .tour-label, .check-play-label');
    if (caption) caption.textContent = label;
    story.live?.setAttribute('aria-live', active ? 'off' : 'polite');
    story.onRunning?.(active);
  }
  function reconcile() {
    stories.forEach(syncStory);
    cancelAnimationFrame(frame);
    frame = 0;
    previous = 0;
    if (stories.some(running)) frame = requestAnimationFrame(tick);
  }
  function tick(now) {
    frame = 0;
    if (!stories.some(running)) { previous = 0; return; }
    if (previous && now - previous < 32) { frame = requestAnimationFrame(tick); return; }
    const delta = previous ? Math.min((now - previous) / 1000, .1) : 0;
    previous = now;
    stories.forEach(story => {
      if (!running(story)) return;
      story.elapsed = (story.elapsed + delta) % story.duration;
      story.render(story.elapsed, true);
    });
    frame = requestAnimationFrame(tick);
  }
  const visibility = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
    entries.forEach(entry => {
      const story = stories.find(item => item.observe === entry.target);
      if (story) story.visible = entry.isIntersecting && entry.intersectionRatio >= .15;
    });
    reconcile();
  }, { threshold: [0, .15] }) : null;
  function createStory(options) {
    const story = { elapsed: 0, paused: false, visible: false, ...options };
    story.observe ||= story.element;
    stories.push(story);
    story.button.addEventListener('click', () => {
      if (!state.motion) { userMotionPreference = true; state.motion = true; story.paused = false; story.onToggle?.(true); applyMotion(); }
      else { story.paused = !story.paused; story.onToggle?.(!story.paused); reconcile(); }
    });
    story.render(0, false);
    syncStory(story);
    visibility?.observe(story.observe);
    return story;
  }
  function seekStory(story, seconds) {
    story.elapsed = seconds;
    story.render(seconds, false);
    reconcile();
  }
  function applyMotion() {
    root.classList.toggle('motion-paused', !state.motion);
    motionButton.setAttribute('aria-pressed', String(!state.motion));
    motionButton.setAttribute('aria-label', state.motion ? 'Pause all animations' : 'Enable animations');
    text('.motion-icon', state.motion ? 'Ⅱ' : '▶');
    text('.motion-label', state.motion ? 'Pause motion' : 'Motion paused');
    reconcile();
    refreshScenes();
  }
  motionButton.addEventListener('click', () => {
    userMotionPreference = true;
    state.motion = !state.motion;
    applyMotion();
  });
  media.addEventListener('change', () => {
    if (!userMotionPreference) { state.motion = !media.matches; applyMotion(); }
  });

  const menu = document.querySelector('#mobile-nav');
  const menuButton = document.querySelector('.menu-toggle');
  function closeMenu() {
    menu.hidden = true;
    menuButton.setAttribute('aria-expanded', 'false');
    menuButton.setAttribute('aria-label', 'Open navigation');
  }
  menuButton.addEventListener('click', () => {
    const open = menu.hidden;
    menu.hidden = !open;
    menuButton.setAttribute('aria-expanded', String(open));
    menuButton.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  });
  menu.addEventListener('click', event => { if (event.target.closest('a')) closeMenu(); });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !menu.hidden) { closeMenu(); menuButton.focus(); }
  });
  window.matchMedia('(min-width: 701px)').addEventListener('change', event => { if (event.matches) closeMenu(); });

  const demo = document.querySelector('.hero-product');
  const demoPhases = [
    ['quiet', 'A quieter moment.', 'Eco mode', 'Less activity. A lighter workload.', 'Periodic checks continue while the scene is quiet.', '⌁'],
    ['active', 'A little more attention.', 'Activity', 'Movement brings the scene into focus.', 'Normal checks resume. Movement alone is not an incident.', '◎'],
    ['review', 'The context is yours.', 'Review', 'An event worth a closer look.', 'When an alert is raised, review the available footage locally.', '↗']
  ];
  const hero = createStory({
    element: demo, duration: 21, button: document.querySelector('#demo-play'),
    pauseLabel: 'Pause the camera story', resumeLabel: 'Resume the camera story',
    onRunning: active => demo.classList.toggle('demo-playing', active),
    render(time) {
      const index = Math.floor(time / 7);
      const [phase, heading, badge, title, copy, icon] = demoPhases[index];
      if (demo.dataset.demoPhase !== phase || time === 0) {
        demo.dataset.demoPhase = phase;
        text('#demo-heading', heading); text('#demo-badge', badge);
        text('#demo-event-title', title); text('#demo-event-copy', copy);
        text('.demo-event-icon', icon);
        select('[data-demo-select]', phase, 'demoSelect');
      }
      demo.style.setProperty('--stage-progress', String((time % 7) / 7));
      demo.style.setProperty('--review-progress', String(Math.max(0, (time - 14) / 7)));
      if (index === 1) {
        const walk = Math.min(1, (time - 7) / 5);
        demo.style.setProperty('--person-offset', `${(-90 + 155 * walk * walk * (3 - 2 * walk)).toFixed(2)}px`);
      }
    }
  });
  document.querySelectorAll('[data-demo-select]').forEach((button, index) => button.addEventListener('click', () => seekStory(hero, index * 7)));

  const check = document.querySelector('.confirmation-demo');
  const checkBreak = document.querySelector('#check-break');
  const checkBars = [...document.querySelectorAll('.check-track i')];
  let resetHold = false;
  const confirmation = createStory({
    element: check, observe: check.querySelector('.confirmation-player'), duration: 12,
    button: document.querySelector('#check-play'), live: check.querySelector('.check-status'),
    pauseLabel: 'Pause example', resumeLabel: 'Resume example',
    render(time) {
      const seconds = Math.min(5, Math.max(0, time - 1));
      const phase = time < 1 ? 'ready' : time < 6 ? 'checking' : 'complete';
      text('#check-clock', `${seconds.toFixed(1)} / 5.0 s`);
      text('#check-seconds', seconds.toFixed(1));
      check.style.setProperty('--check-progress', String(seconds * 20));
      checkBars.forEach((bar, index) => { bar.style.transform = `scaleX(${Math.max(0, Math.min(1, seconds - index))})`; });
      checkBreak.disabled = phase !== 'checking';
      if (resetHold && phase === 'ready') return;
      resetHold = false;
      check.dataset.checkState = phase;
      if (phase === 'ready') {
        text('#check-title', 'Time to look closer.');
        text('#check-description', 'Follow a check with uninterrupted supporting evidence.');
      } else if (phase === 'checking') {
        text('#check-title', 'Checking the interaction…');
        text('#check-description', 'The same pair. Supporting evidence over time. No alert yet.');
      } else {
        text('#check-title', 'Possible fight. Ready for review.');
        text('#check-description', 'In this example, evidence held for five seconds. A person reviews the footage next.');
      }
    }
  });
  checkBreak.addEventListener('click', () => {
    confirmation.paused = true;
    resetHold = true;
    seekStory(confirmation, 0);
    check.dataset.checkState = 'reset';
    text('#check-title', 'Evidence changed. Check restarted.');
    text('#check-description', 'No alert from this example. Supporting evidence would need to build again from the beginning.');
  });

  const learningVisual = document.querySelector('.learning-visual');
  const learningSteps = [
    ['It starts with your experience.', 'Your experience is the starting point.', 'Confirm useful alerts, correct false alarms and add examples from your own site. Your team gives the footage its meaning.', 'Reviewed by your team', 'Useful experience. Kept on your device.'],
    ['Make it familiar with your space.', 'Teach it from your own footage.', 'When you’re ready, start a learning update in the app. Use examples of everyday activity and events you want VDM to notice. Your footage stays on your device.', 'Learning from your examples', 'Started by you. Built around your site.'],
    ['Confidence comes from checking.', 'Check before moving forward.', 'VDM checks an update before it is used. If it does not pass, the current version stays in place. Keep reviewing examples and try again when you’re ready.', 'An update worth checking', 'Only a passing update can be used.'],
    ['Keep building on your experience.', 'Put what it learns to work.', 'A checked update can suggest more moments to review, alongside the protection already included. Review new footage and choose when to start another learning update.', 'Your experience, put to work', 'Alongside the protection already included.']
  ];
  const learning = createStory({
    element: document.querySelector('.learning-layout'), observe: learningVisual,
    duration: 32, button: document.querySelector('#learning-play'), live: document.querySelector('.learning-detail'),
    pauseLabel: 'Pause learning story', resumeLabel: 'Resume learning story',
    render(time) {
      const step = Math.floor(time / 8);
      if (learningVisual.dataset.learningPhase !== String(step) || time === 0) {
        learningVisual.dataset.learningPhase = String(step);
        select('[data-learning-step]', step, 'learningStep');
        document.querySelectorAll('[data-learning-node]').forEach(node => node.classList.toggle('active', Number(node.dataset.learningNode) === step));
        const [stage, title, copy, badge, note] = learningSteps[step];
        text('#learning-stage', stage); text('#learning-title', title); text('#learning-description', copy);
        text('#learning-card-heading', badge); text('#learning-card-note', note);
      }
      learningVisual.style.setProperty('--learning-progress', String(time / 32));
      learningVisual.style.setProperty('--stage-progress', String((time % 8) / 8));
    }
  });
  document.querySelectorAll('[data-learning-step]').forEach(button => button.addEventListener('click', () => seekStory(learning, Number(button.dataset.learningStep) * 8)));

  const ecoElement = document.querySelector('.eco-demo');
  const eco = createStory({
    element: ecoElement, duration: 16, button: document.querySelector('#eco-play'), live: document.querySelector('.eco-state-detail'),
    pauseLabel: 'Pause Eco story', resumeLabel: 'Resume Eco story',
    render(time) {
      const active = time >= 8, phase = active ? 'active' : 'quiet';
      if (ecoElement.dataset.ecoState !== phase || time === 0) {
        ecoElement.dataset.ecoState = phase;
        select('[data-eco-select]', phase, 'ecoSelect');
        text('#eco-kicker', active ? 'Back to the moment.' : 'A lighter rhythm.');
        text('#eco-title', active ? 'Movement sets the pace.' : 'Quiet, but still checking.');
        text('#eco-description', active ? 'Movement restores normal checks. VDM stays active while checking a possible interaction, even if the scene briefly settles.' : 'VDM eases its workload while keeping periodic checks running in the background.');
      }
      ecoElement.style.setProperty('--stage-progress', String((time % 8) / 8));
    }
  });
  document.querySelectorAll('[data-eco-select]').forEach(button => button.addEventListener('click', () => seekStory(eco, button.dataset.ecoSelect === 'active' ? 8 : 0)));

  const chapters = [
    ['Start with your own cameras.', 'Add a compatible network camera, plug in a webcam or open a saved video. Set everything up in the desktop app.', 'Connect your cameras'],
    ['Give the moment a closer look.', 'VDM checks sustained evidence before a possible-fight alert. Movement, closeness and relative depth contribute. Eco mode eases the workload when a live scene is quiet.', 'Notice what matters'],
    ['Keep the story around the moment.', 'Open the available footage from before and after an event. Related alerts, original video and highlighted clips stay together on your computer.', 'Keep the context'],
    ['You make the call.', 'Review the footage. Confirm an event, dismiss a false alarm or correct its label. Your team stays in charge of what happens next.', 'Make the call'],
    ['Build on your own experience.', 'Use correctly reviewed footage to start a learning update in the app. Updates are checked before use, and work alongside the protection already included.', 'Learn from your site']
  ];
  const progress = document.querySelector('#tour-progress');
  const explode = document.querySelector('#explode-toggle');
  let manualAssembly = false;
  function updateExplodeButton() {
    explode.setAttribute('aria-pressed', String(state.exploded));
    explode.replaceChildren(document.createTextNode(state.exploded ? 'Bring together ' : 'Separate layers '));
    const symbol = document.createElement('span');
    symbol.setAttribute('aria-hidden', 'true');
    symbol.textContent = state.exploded ? '−' : '+';
    explode.append(symbol);
  }
  const tour = createStory({
    element: document.querySelector('.architecture-player'), observe: document.querySelector('.architecture-stage'), duration: 25,
    button: document.querySelector('#tour-play'), live: document.querySelector('.chapter-detail'),
    pauseLabel: 'Pause the tour', resumeLabel: 'Resume the tour',
    onToggle: resumed => { if (resumed) manualAssembly = false; },
    onRunning(active) { if (state.playing !== active) { state.playing = active; refreshScenes(); } },
    render(time) {
      state.progress = time / 25;
      progress.value = String(state.progress * 100);
      progress.style.setProperty('--progress', `${state.progress * 100}%`);
      text('#tour-time', `00:${String(Math.floor(time)).padStart(2, '0')}`);
      const step = Math.min(4, Math.floor(time / 5));
      const exploded = manualAssembly ? state.exploded : time >= 1.4 && time < 23.4;
      const changed = state.step !== step || state.exploded !== exploded || time === 0;
      state.exploded = exploded;
      if (changed) {
        state.step = step;
        select('.chapter', step, 'step');
        document.querySelectorAll('[data-layer]').forEach(node => node.classList.toggle('active', Number(node.dataset.layer) === step));
        text('#chapter-title', chapters[step][0]); text('#chapter-description', chapters[step][1]);
        text('#chapter-location', 'On your device'); text('#step-count', `${step + 1} of 5`);
        updateExplodeButton();
        refreshScenes();
      }
      progress.setAttribute('aria-valuetext', `${chapters[step][2]}, ${Math.floor(time)} of 25 seconds`);
    }
  });
  progress.addEventListener('input', () => {
    tour.paused = true;
    manualAssembly = false;
    seekStory(tour, Math.min(25, Number(progress.value) / 100 * 25));
  });
  document.querySelectorAll('.chapter').forEach(button => button.addEventListener('click', () => {
    manualAssembly = false;
    seekStory(tour, Number(button.dataset.step) * 5 + 1.5);
  }));
  explode.addEventListener('click', () => {
    tour.paused = true; manualAssembly = true; state.exploded = !state.exploded;
    updateExplodeButton(); reconcile(); refreshScenes();
  });

  // Keep the static page fully usable if visibility observation is unavailable.
  if (!visibility) {
    let visibilityFrame = 0;
    const checkVisibility = () => {
      visibilityFrame = 0;
      stories.forEach(story => {
        const rect = story.observe.getBoundingClientRect();
        const shown = Math.min(innerHeight, rect.bottom) - Math.max(0, rect.top);
        story.visible = shown >= Math.min(rect.height * .15, innerHeight * .15);
      });
      reconcile();
    };
    const scheduleVisibility = () => { if (!visibilityFrame) visibilityFrame = requestAnimationFrame(checkVisibility); };
    addEventListener('scroll', scheduleVisibility, { passive: true });
    addEventListener('resize', scheduleVisibility, { passive: true });
    checkVisibility();
  }
  if ('IntersectionObserver' in window) {
    const reveal = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) { entry.target.classList.add('visible'); reveal.unobserve(entry.target); }
    }), { threshold: .06, rootMargin: '0px 0px -22px 0px' });
    document.querySelectorAll('.reveal').forEach(element => reveal.observe(element));
    root.classList.add('js-motion');
  }
  async function loadScene(host) {
    if (host.dataset.loading) return;
    host.dataset.loading = 'true';
    try {
      rendererModule ||= import('./product-scene.js?v=20260923-motion2');
      const { createProductScene } = await rendererModule;
      const scene = createProductScene(host, state);
      scenes.push(scene); scene.update();
    } catch (error) {
      host.classList.add('scene-unavailable');
      host.querySelector('.render-mode').textContent = 'Illustrated overview';
      console.warn('The 3D view is unavailable. The illustrated overview remains available.', error);
    }
  }
  if ('IntersectionObserver' in window) {
    const preload = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) { loadScene(entry.target); preload.unobserve(entry.target); }
    }), { rootMargin: '250px' });
    document.querySelectorAll('.scene-host').forEach(host => preload.observe(host));
  } else document.querySelectorAll('.scene-host').forEach(loadScene);
  document.addEventListener('visibilitychange', () => {
    root.classList.toggle('page-hidden', document.hidden);
    reconcile(); refreshScenes();
  });
  window.addEventListener('pagehide', () => {
    suspended = true; reconcile(); scenes.forEach(scene => scene.suspend());
  });
  window.addEventListener('pageshow', () => { suspended = false; reconcile(); refreshScenes(); });
  applyMotion();
})();
