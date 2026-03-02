(function (global) {
  class OrbitControls { constructor(camera, dom){ this.camera=camera; this.domElement=dom; this.enableDamping=true; } update(){} }
  global.OrbitControls = OrbitControls;
})(window);
