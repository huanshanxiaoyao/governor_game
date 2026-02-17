/**
 * API client — Game.api namespace
 */
(function () {
  "use strict";

  function getCSRF() {
    var match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : "";
  }

  function request(method, path, body) {
    var opts = {
      method: method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
    };
    if (method !== "GET") {
      opts.headers["X-CSRFToken"] = getCSRF();
    }
    if (body !== undefined) {
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok) {
          var err = new Error(data.error || data.message || "请求失败");
          err.data = data;
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  window.Game = window.Game || {};
  window.Game.api = {
    login: function (username, password) {
      return request("POST", "/api/login/", { username: username, password: password });
    },
    logout: function () {
      return request("POST", "/api/logout/", {});
    },
    listGames: function () {
      return request("GET", "/api/games/");
    },
    createGame: function (background) {
      return request("POST", "/api/games/", { background: background });
    },
    getGame: function (id) {
      return request("GET", "/api/games/" + id + "/");
    },
    invest: function (id, action, targetVillage) {
      var body = { action: action };
      if (targetVillage) body.target_village = targetVillage;
      return request("POST", "/api/games/" + id + "/invest/", body);
    },
    advance: function (id) {
      return request("POST", "/api/games/" + id + "/advance/", {});
    },
    setTaxRate: function (id, rate) {
      return request("POST", "/api/games/" + id + "/tax-rate/", { tax_rate: rate });
    },
    setMedicalLevel: function (id, level) {
      return request("POST", "/api/games/" + id + "/medical-level/", { medical_level: level });
    },
    getSummary: function (id) {
      return request("GET", "/api/games/" + id + "/summary/");
    },
    // Negotiation
    getActiveNegotiation: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/negotiations/active/");
    },
    sendNegotiationChat: function (gameId, sessionId, message) {
      return request("POST", "/api/games/" + gameId + "/negotiations/" + sessionId + "/chat/", { message: message });
    },
    getNegotiationHistory: function (gameId, sessionId) {
      return request("GET", "/api/games/" + gameId + "/negotiations/" + sessionId + "/chat/");
    },
    startIrrigationNegotiation: function (gameId, villageName) {
      return request("POST", "/api/games/" + gameId + "/negotiations/start-irrigation/", { village_name: villageName });
    },
    // Agents
    getAgents: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/agents/");
    },
    // Promises
    getPromises: function (gameId, promiseStatus) {
      var qs = promiseStatus ? "?status=" + encodeURIComponent(promiseStatus) : "";
      return request("GET", "/api/games/" + gameId + "/promises/" + qs);
    },
    // Event logs
    getEventLogs: function (gameId, category, season, limit) {
      var params = [];
      if (category) params.push("category=" + encodeURIComponent(category));
      if (season) params.push("season=" + encodeURIComponent(season));
      if (limit) params.push("limit=" + encodeURIComponent(limit));
      var qs = params.length > 0 ? "?" + params.join("&") : "";
      return request("GET", "/api/games/" + gameId + "/events/" + qs);
    },
  };
})();
