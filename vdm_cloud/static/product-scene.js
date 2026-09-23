/* The floating cards illustrate the desktop experience. All textures are local,
 * generated artwork; no camera footage or account data is loaded into this tour. */
import * as THREE from './vendor/three.module.min.js';

const NAMES = ['Connect your cameras', 'Notice what matters', 'Keep the context', 'Make the call', 'Learn from your site'];
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

function roundedShape(width, depth, radius) {
  const x = -width / 2, y = -depth / 2;
  const shape = new THREE.Shape();
  shape.moveTo(x + radius, y);
  shape.lineTo(x + width - radius, y);
  shape.quadraticCurveTo(x + width, y, x + width, y + radius);
  shape.lineTo(x + width, y + depth - radius);
  shape.quadraticCurveTo(x + width, y + depth, x + width - radius, y + depth);
  shape.lineTo(x + radius, y + depth);
  shape.quadraticCurveTo(x, y + depth, x, y + depth - radius);
  shape.lineTo(x, y + radius);
  shape.quadraticCurveTo(x, y, x + radius, y);
  return shape;
}
function slabGeometry(width, depth, height, radius = 0.12) {
  const geometry = new THREE.ExtrudeGeometry(roundedShape(width, depth, radius), {
    depth: height, bevelEnabled: true, bevelSegments: 2, steps: 1, bevelSize: 0.028, bevelThickness: 0.028, curveSegments: 6
  });
  geometry.rotateX(-Math.PI / 2);
  return geometry;
}
function canvasTexture(index) {
  const canvas = document.createElement('canvas');
  canvas.width = 1024;
  canvas.height = 640;
  const ctx = canvas.getContext('2d');
  const box = (x, y, w, h, fill, radius = 16) => {
    ctx.fillStyle = fill;
    ctx.beginPath(); ctx.roundRect(x, y, w, h, radius); ctx.fill();
  };
  const text = (value, x, y, size = 24, color = '#1d1d1f', weight = 500) => {
    ctx.fillStyle = color;
    ctx.font = `${weight} ${size}px -apple-system, BlinkMacSystemFont, Arial, sans-serif`;
    ctx.fillText(value, x, y);
  };
  const line = (x, y, width, color = '#d5d5dc') => box(x, y, width, 9, color, 4);
  const check = (x, y, size = 20) => {
    ctx.strokeStyle = '#555561'; ctx.lineWidth = 5; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.beginPath(); ctx.moveTo(x, y + size / 2); ctx.lineTo(x + size / 3, y + size); ctx.lineTo(x + size, y); ctx.stroke();
  };
  const room = (x, y, w, h) => {
    ctx.save();
    ctx.beginPath(); ctx.roundRect(x, y, w, h, 14); ctx.clip();
    const gradient = ctx.createLinearGradient(x, y, x + w, y + h);
    gradient.addColorStop(0, '#c4c1bc'); gradient.addColorStop(1, '#e5e2dd');
    ctx.fillStyle = gradient; ctx.fillRect(x, y, w, h);
    ctx.fillStyle = '#b4b2af';
    ctx.beginPath(); ctx.moveTo(x, y + h); ctx.lineTo(x + w * .33, y + h * .52); ctx.lineTo(x + w * .7, y + h * .52); ctx.lineTo(x + w, y + h); ctx.fill();
    box(x + w * .33, y + h * .15, w * .37, h * .53, '#8e9297', 0);
    box(x + w * .36, y + h * .18, w * .31, h * .47, '#747a82', 0);
    ctx.fillStyle = '#46484e'; ctx.beginPath(); ctx.arc(x + w * .56, y + h * .46, h * .035, 0, Math.PI * 2); ctx.fill();
    box(x + w * .54, y + h * .5, w * .045, h * .17, '#46484e', 5);
    ctx.strokeStyle = '#46484e'; ctx.lineWidth = Math.max(3, w * .012); ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(x + w * .562, y + h * .66); ctx.lineTo(x + w * .535, y + h * .79); ctx.moveTo(x + w * .562, y + h * .66); ctx.lineTo(x + w * .59, y + h * .79); ctx.stroke();
    ctx.restore();
  };
  ctx.fillStyle = '#fafafa'; ctx.fillRect(0, 0, 1024, 640);
  ctx.fillStyle = '#eeeeF1'; ctx.fillRect(0, 0, 1024, 65);
  for (let i = 0; i < 3; i++) { ctx.fillStyle = '#c6c6cc'; ctx.beginPath(); ctx.arc(35 + i * 22, 33, 6, 0, Math.PI * 2); ctx.fill(); }
  text('VDM Shield', 445, 41, 20, '#6e6e73');
  text(NAMES[index], 52, 139, 41, '#1d1d1f', 600);
  text(['Your space, in one place.', 'A moment worth a closer look.', 'The story around the moment.', 'Your experience. Your decision.', 'Built on your experience.'][index], 54, 178, 22, '#86868b', 400);
  if (index === 0) {
    room(52, 218, 574, 302);
    room(646, 218, 324, 176);
    box(646, 413, 324, 107, '#efeff3');
    text('On your device', 675, 477, 25, '#5d5d68');
    text('Entrance', 55, 565, 23, '#6e6e73');
  } else if (index === 1) {
    room(52, 218, 590, 330);
    ctx.strokeStyle = '#f4f4fc'; ctx.lineWidth = 3;
    ctx.strokeRect(52 + 590 * .51, 218 + 330 * .39, 590 * .11, 330 * .44);
    box(663, 218, 307, 330, '#efeff3');
    text('Needs review', 687, 273, 25);
    line(690, 307, 201); line(690, 331, 158);
    box(687, 465, 212, 45, '#dedee6', 22);
    text('Open the moment', 708, 495, 19, '#575762');
  } else if (index === 2) {
    for (let i = 0; i < 3; i++) {
      room(52 + 312 * i, 233, 294, 220);
      text(['Before', 'The moment', 'After'][i], 56 + 312 * i, 493, 24, '#6e6e73');
    }
    line(54, 547, 918, '#d4d4dc'); box(426, 539, 175, 25, '#8f8f9e', 8);
  } else if (index === 3) {
    room(52, 218, 510, 330);
    ['Watch the footage', 'Record your decision', 'Keep the context'].forEach((label, i) => {
      box(590, 228 + 106 * i, 380, 84, '#efeff3');
      check(611, 253 + 106 * i);
      text(label, 658, 279 + 106 * i, 23, '#5b5b65');
    });
  } else {
    box(52, 220, 918, 340, '#efeff3');
    ['Review', 'Teach', 'Check', 'Improve'].forEach((label, i) => {
      const x = 167 + i * 230;
      if (i < 3) { ctx.strokeStyle = '#d2d2dc'; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(x + 47, 342); ctx.lineTo(x + 183, 342); ctx.stroke(); }
      ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(x, 342, 45, 0, Math.PI * 2); ctx.fill();
      check(x - 13, 327, 28);
      ctx.textAlign = 'center'; text(label, x, 427, 25, '#50505b'); ctx.textAlign = 'left';
    });
    ctx.textAlign = 'center'; text('Your footage stays with you.', 512, 515, 23, '#8a8a93', 400); ctx.textAlign = 'left';
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  return texture;
}

function createStack() {
  const group = new THREE.Group();
  const layers = [];
  const plateGeometry = slabGeometry(4.55, 2.88, 0.075, 0.16);
  const coverGeometry = new THREE.PlaneGeometry(4.43, 2.76);
  for (let i = 0; i < 5; i++) {
    const layer = new THREE.Group();
    const material = new THREE.MeshStandardMaterial({ color: 0xb5b5bf, metalness: 0.38, roughness: 0.4, emissive: 0xdedee8, emissiveIntensity: 0.025 });
    layer.add(new THREE.Mesh(plateGeometry, material));
    // Unlit artwork preserves the familiar paper tones under the scene lighting.
    const coverMaterial = new THREE.MeshBasicMaterial({ map: canvasTexture(i), toneMapped: false });
    const cover = new THREE.Mesh(coverGeometry, coverMaterial);
    cover.rotation.x = -Math.PI / 2;
    cover.position.y = 0.108;
    layer.add(cover);
    const perimeter = new THREE.LineSegments(new THREE.EdgesGeometry(plateGeometry, 25), new THREE.LineBasicMaterial({ color: 0xe6e6f0, transparent: true, opacity: 0.25 }));
    layer.add(perimeter);
    group.add(layer);
    layers.push({ group: layer, material, perimeter });
  }
  const base = new THREE.Mesh(slabGeometry(4.7, 3.03, 0.12, 0.2), new THREE.MeshStandardMaterial({ color: 0x38383e, roughness: 0.36, metalness: 0.45 }));
  group.add(base);
  return { group, layers, base };
}

export function createProductScene(host, state) {
  const canvas = host.querySelector('canvas');
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.setClearColor(0x101012, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.1;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 80);
  const stack = createStack();
  scene.add(stack.group);
  const ambient = new THREE.HemisphereLight(0xf2f2ff, 0x23232a, 2); scene.add(ambient);
  const key = new THREE.DirectionalLight(0xffffff, 2.7); key.position.set(-4, 8, 5); scene.add(key);
  const rim = new THREE.DirectionalLight(0xbcbcd4, 2); rim.position.set(5, 3, -4); scene.add(rim);

  // A soft contact shadow anchors the floating software stack without a shadow map.
  const shadowCanvas = document.createElement('canvas'); shadowCanvas.width = shadowCanvas.height = 128;
  const context = shadowCanvas.getContext('2d');
  const gradient = context.createRadialGradient(64, 64, 4, 64, 64, 63);
  gradient.addColorStop(0, 'rgba(0,0,0,.65)'); gradient.addColorStop(1, 'rgba(0,0,0,0)');
  context.fillStyle = gradient; context.fillRect(0, 0, 128, 128);
  const shadow = new THREE.Mesh(new THREE.PlaneGeometry(8, 6), new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(shadowCanvas), transparent: true, depthWrite: false }));
  shadow.rotation.x = -Math.PI / 2; scene.add(shadow);

  let visible = true, lost = false, lastRender = 0, elapsed = 0, width = 1, height = 1;
  let spread = 1.04;
  let rotation = -0.31;
  let selected = state.step;
  let suspended = false;
  const labels = [...host.querySelectorAll('[data-layer]')];
  const projected = new THREE.Vector3();
  const target = new THREE.Vector3();
  const pointer = { x: 0, y: 0 };

  function resize() {
    const rect = host.getBoundingClientRect(); width = Math.max(1, rect.width); height = Math.max(1, rect.height);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    const distance = 13.2;
    // Fit both the card width and the complete exploded stack in narrow viewports.
    const portrait = Math.max(1, 0.98 / camera.aspect);
    camera.position.set(distance * 0.57 * portrait, distance * 0.56 * portrait, distance * 0.86 * portrait);
    camera.lookAt(-0.15, 0, 0);
    camera.updateProjectionMatrix();
    draw(performance.now(), true);
  }
  function draw(now, immediate = false) {
    if (lost || suspended || document.hidden || !visible) return;
    const delta = lastRender ? Math.min((now - lastRender) / 1000, 0.08) : 1 / 30;
    // 30 fps is enough for a slow diagram and keeps background GPU work modest.
    if (!immediate && now - lastRender < 32) return;
    lastRender = now;
    if (state.motion && state.playing) elapsed += delta;
    const targetSpread = state.exploded ? 1.09 : 0.175;
    const ease = !state.motion || immediate ? 1 : 1 - Math.exp(-delta * 5);
    spread += (targetSpread - spread) * ease;
    selected += (state.step - selected) * ease;
    const targetRotation = -0.38 + (state.motion ? Math.sin(elapsed * 0.13) * 0.12 + pointer.x * 0.08 : 0);
    rotation += (targetRotation - rotation) * ease;
    stack.group.rotation.y = rotation;
    for (let i = 0; i < 5; i++) {
      const item = stack.layers[i];
      const active = i === state.step;
      item.group.position.y = (i - 2) * spread;
      const emphasis = state.exploded ? Math.max(0, 1 - Math.abs(i - selected)) : 0;
      item.group.position.x = emphasis * 0.52;
      item.group.position.z = emphasis * 0.26;
      item.group.rotation.y = emphasis * -0.035;
      item.material.emissiveIntensity = active ? 0.15 : 0.025;
      item.perimeter.material.opacity = active ? 0.9 : 0.18;
    }
    stack.base.position.y = -2 * spread - 0.24;
    shadow.position.y = -2 * spread - 0.27;
    scene.updateMatrixWorld(true);
    for (let i = 0; i < labels.length; i++) {
      const label = labels[i];
      target.set(2.05, 0.14, 1.32);
      stack.layers[i].group.localToWorld(target);
      projected.copy(target).project(camera);
      const x = clamp((projected.x * 0.5 + 0.5) * width + 12, 12, width - 113);
      const y = clamp((-projected.y * 0.5 + 0.5) * height - 11, 45, height - 54);
      label.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
      label.hidden = !state.exploded;
    }
    renderer.render(scene, camera);
    host.classList.add('scene-ready');
  }
  function update() {
    suspended = false;
    if (lost) return;
    renderer.setAnimationLoop(null);
    lastRender = 0;
    if (visible && !document.hidden) {
      draw(performance.now(), !state.motion || !state.playing);
      if (state.motion && state.playing) renderer.setAnimationLoop(draw);
    }
  }
  const resizeObserver = new ResizeObserver(resize); resizeObserver.observe(host);
  let visibilityObserver;
  if ('IntersectionObserver' in window) {
    visibilityObserver = new IntersectionObserver(entries => {
      visible = entries[0].isIntersecting; update();
    }, { rootMargin: '60px' });
    visibilityObserver.observe(host);
  }
  host.addEventListener('pointermove', event => {
    if (event.pointerType !== 'mouse' || !state.motion) return;
    const bounds = host.getBoundingClientRect(); pointer.x = ((event.clientX - bounds.left) / bounds.width - 0.5) * 2;
  }, { passive: true });
  host.addEventListener('pointerleave', () => { pointer.x = 0; });
  canvas.addEventListener('webglcontextlost', event => {
    event.preventDefault(); lost = true; renderer.setAnimationLoop(null); host.classList.remove('scene-ready');
    const mode = host.querySelector('.render-mode'); if (mode) mode.textContent = 'Illustrated overview';
  });
  canvas.addEventListener('webglcontextrestored', () => {
    lost = false; resize(); update();
    const mode = host.querySelector('.render-mode'); if (mode) mode.textContent = 'An illustrated tour';
  });
  resize();
  update();
  return {
    update,
    suspend() { suspended = true; renderer.setAnimationLoop(null); },
    dispose() {
      renderer.setAnimationLoop(null); resizeObserver.disconnect(); visibilityObserver?.disconnect();
      const geometries = new Set(), materials = new Set(), textures = new Set();
      scene.traverse(object => {
        if (object.geometry) geometries.add(object.geometry);
        if (object.material) (Array.isArray(object.material) ? object.material : [object.material]).forEach(material => {
          materials.add(material); if (material.map) textures.add(material.map);
        });
      });
      geometries.forEach(geometry => geometry.dispose()); textures.forEach(texture => texture.dispose()); materials.forEach(material => material.dispose()); renderer.dispose();
    }
  };
}
