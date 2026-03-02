(function () {
  const canvas = document.getElementById('avatarCanvas');
  const statusEl = document.getElementById('avatarStatus');
  const poseSel = document.getElementById('poseSelect');
  const fallback = document.getElementById('avatarFallback');
  if (!canvas || !window.THREE) return;

  function webglAvailable() {
    try {
      const c = document.createElement('canvas');
      return !!(window.WebGLRenderingContext && (c.getContext('webgl') || c.getContext('experimental-webgl')));
    } catch (_) { return false; }
  }

  if (!webglAvailable()) {
    fallback.style.display = 'block';
    statusEl.textContent = 'WebGL unavailable; using 2D fallback.';
    return;
  }

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0d1628);
  const camera = new THREE.PerspectiveCamera(50, 1.5, 0.1, 100);
  camera.position.set(0, 1.6, 2.2);
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  const controls = new OrbitControls(camera, renderer.domElement);
  const clock = new THREE.Clock();

  scene.add(new THREE.HemisphereLight(0xffffff, 0x223344, 1.2));
  const dir = new THREE.DirectionalLight(0xffffff, 0.8);
  dir.position.set(2, 4, 2);
  scene.add(dir);

  let mixer = null;
  let actions = {};
  let currentAction = null;

  function playPose(name) {
    const next = actions[name];
    if (!next) {
      statusEl.textContent = `Pose '${name}' not found; showing default.`;
      return;
    }
    if (currentAction && currentAction !== next && currentAction.crossFadeTo) {
      currentAction.crossFadeTo(next, 0.25, false);
    } else if (currentAction && currentAction.stop) {
      currentAction.stop();
    }
    currentAction = next.reset().fadeIn(0.15).play();
    statusEl.textContent = `Pose: ${name}`;
  }

  new GLTFLoader().load('/assets/avatars/avatar.glb', (gltf) => {
    scene.add(gltf.scene);
    mixer = new THREE.AnimationMixer(gltf.scene);
    (gltf.animations || []).forEach((clip) => {
      actions[clip.name.toLowerCase()] = mixer.clipAction(clip);
    });
    const initial = (poseSel.value || 'idle').toLowerCase();
    playPose(initial);
  }, null, () => {
    fallback.style.display = 'block';
    statusEl.textContent = '3D model failed to load; using 2D fallback.';
  });

  poseSel.addEventListener('change', () => playPose(poseSel.value.toLowerCase()));

  function resize() {
    const w = canvas.clientWidth || 640;
    const h = canvas.clientHeight || 420;
    renderer.setSize(w, h, false);
  }
  resize();
  window.addEventListener('resize', resize);

  (function animate() {
    requestAnimationFrame(animate);
    controls.update();
    if (mixer) mixer.update(clock.getDelta());
    renderer.render(scene, camera);
  })();
})();
