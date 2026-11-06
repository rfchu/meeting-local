(() => {
  const denied = () =>
    Promise.reject(
      new DOMException(
        "内嵌工作区不申请采集权限，请使用原生音轨或浏览器工作台。",
        "NotAllowedError",
      ),
    );
  if (navigator.mediaDevices) {
    for (const name of ["getUserMedia", "getDisplayMedia"]) {
      Object.defineProperty(navigator.mediaDevices, name, {
        value: denied,
        configurable: false,
        writable: false,
      });
    }
  }
  const unavailable = (_success, failure) => {
    if (typeof failure === "function")
      failure({ code: 1, message: "内嵌工作区不提供位置权限。" });
  };
  Object.defineProperty(navigator, "geolocation", {
    value: Object.freeze({
      getCurrentPosition: unavailable,
      watchPosition: (...args) => {
        unavailable(...args);
        return 0;
      },
      clearWatch: () => {},
    }),
    configurable: false,
  });
  Object.defineProperty(window, "__TINGJI_HOST__", {
    value: Object.freeze({ version: 1, embedded: true, web_capture: false }),
    writable: false,
    configurable: false,
  });
})();
