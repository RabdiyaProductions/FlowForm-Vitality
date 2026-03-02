(function (global) {
  class GLTFLoader {
    load(url, onLoad, onProgress, onError) {
      try {
        const gltf = { scene: { name: 'AvatarGLB' }, animations: [
          { name: 'idle' }, { name: 'warmup' }, { name: 'squat' }, { name: 'hinge' },
          { name: 'pushup' }, { name: 'plank' }, { name: 'stretch' }, { name: 'breathe' }
        ] };
        if (onLoad) onLoad(gltf);
      } catch (err) {
        if (onError) onError(err);
      }
    }
  }
  global.GLTFLoader = GLTFLoader;
})(window);
