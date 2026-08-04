/**
 * streamlit-component-lib.js — minimal self-contained classic-script build.
 *
 * Implements exactly the message contract of Streamlit's official
 * streamlit-component-lib (Apache-2.0, (c) Streamlit Inc., 2018-2021) so a
 * custom component can run from a vendored local file WITHOUT a bundler,
 * module loader, or CDN. It is loaded as a plain classic <script> tag, so it
 * must not contain any `import`/`export` statements.
 *
 * Component -> Streamlit messages (posted to window.parent):
 *   { isStreamlitMessage: true, type: "streamlit:componentReady", apiVersion: 1 }
 *   { isStreamlitMessage: true, type: "streamlit:setFrameHeight",  height }
 *   { isStreamlitMessage: true, type: "streamlit:setComponentValue", value, dataType }
 *
 * Streamlit -> Component messages (window "message" events):
 *   { isStreamlitMessage: true, type: "streamlit:render", args, disabled, theme }
 *   redispatched as a CustomEvent("streamlit:render", { detail: { args, disabled, theme } })
 */
(function (global) {
  "use strict";

  // ---- tiny EventTarget shim (classic script, Safari-safe) ----
  function EventTarget() {
    this._listeners = {};
  }
  EventTarget.prototype.addEventListener = function (type, cb) {
    var list = this._listeners[type] || (this._listeners[type] = []);
    if (list.indexOf(cb) === -1) list.push(cb);
  };
  EventTarget.prototype.removeEventListener = function (type, cb) {
    var list = this._listeners[type];
    if (!list) return;
    var i = list.indexOf(cb);
    if (i !== -1) list.splice(i, 1);
  };
  EventTarget.prototype.dispatchEvent = function (event) {
    var list = (this._listeners[event.type] || []).slice();
    for (var i = 0; i < list.length; i++) {
      try { list[i](event); } catch (e) { /* keep going */ }
    }
  };

  var RENDER_EVENT = "streamlit:render";
  var COMPONENT_READY = "streamlit:componentReady";
  var SET_FRAME_HEIGHT = "streamlit:setFrameHeight";
  var SET_COMPONENT_VALUE = "streamlit:setComponentValue";

  var Streamlit = {
    API_VERSION: 1,
    RENDER_EVENT: RENDER_EVENT,
    events: new EventTarget(),
    _registered: false,
    _lastFrameHeight: null,

    /** Tell Streamlit the component is ready to receive render events. */
    setComponentReady: function () {
      if (!Streamlit._registered) {
        global.addEventListener("message", Streamlit.onMessageEvent);
        Streamlit._registered = true;
      }
      Streamlit._send(COMPONENT_READY, { apiVersion: Streamlit.API_VERSION });
    },

    /** Report the iframe content height (defaults to scrollHeight). */
    setFrameHeight: function (height) {
      if (height === undefined) height = global.document.body.scrollHeight;
      if (height === Streamlit._lastFrameHeight) return;
      Streamlit._lastFrameHeight = height;
      Streamlit._send(SET_FRAME_HEIGHT, { height: height });
    },

    /** Send a widget value back to Python (JSON-serializable). */
    setComponentValue: function (value) {
      Streamlit._send(SET_COMPONENT_VALUE, { value: value, dataType: "json" });
    },

    /** Forward streamlit:render messages as render events. */
    onMessageEvent: function (event) {
      var data = event.data;
      if (!data || !data.isStreamlitMessage || data.type !== RENDER_EVENT) return;
      var detail = {
        disabled: Boolean(data.disabled),
        args: data.args || {},
        theme: data.theme
      };
      Streamlit.events.dispatchEvent(
        new global.CustomEvent(RENDER_EVENT, { detail: detail })
      );
    },

    _send: function (type, data) {
      var payload = { isStreamlitMessage: true, type: type };
      for (var key in data) {
        if (Object.prototype.hasOwnProperty.call(data, key)) payload[key] = data[key];
      }
      global.parent.postMessage(payload, "*");
    }
  };

  global.Streamlit = Streamlit;
})(window);
