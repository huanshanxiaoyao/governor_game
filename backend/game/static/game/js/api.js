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
    requestLandSurvey: function (id, villageName) {
      return request("POST", "/api/games/" + id + "/land-survey/", { village_name: villageName });
    },
    advance: function (id) {
      return request("POST", "/api/games/" + id + "/advance/", {});
    },
    precomputeNeighbors: function (id) {
      return request("POST", "/api/games/" + id + "/neighbors/precompute/", {});
    },
    getPrecomputeStatus: function (id) {
      return request("GET", "/api/games/" + id + "/neighbors/precompute/");
    },
    setTaxRate: function (id, rate) {
      return request("POST", "/api/games/" + id + "/tax-rate/", { tax_rate: rate });
    },
    setCommercialTaxRate: function (id, rate) {
      return request("POST", "/api/games/" + id + "/commercial-tax-rate/", { commercial_tax_rate: rate });
    },
    getSummary: function (id) {
      return request("GET", "/api/games/" + id + "/summary/");
    },
    getSummaryV2: function (id) {
      return request("GET", "/api/games/" + id + "/summary-v2/");
    },
    // Negotiation
    getActiveNegotiation: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/negotiations/active/");
    },
    sendNegotiationChat: function (gameId, sessionId, message, speakerRole) {
      var body = { message: message };
      if (speakerRole) body.speaker_role = speakerRole;
      return request("POST", "/api/games/" + gameId + "/negotiations/" + sessionId + "/chat/", body);
    },
    getNegotiationHistory: function (gameId, sessionId) {
      return request("GET", "/api/games/" + gameId + "/negotiations/" + sessionId + "/chat/");
    },
    startIrrigationNegotiation: function (gameId, villageName) {
      return request("POST", "/api/games/" + gameId + "/negotiations/start-irrigation/", { village_name: villageName });
    },
    // Staff
    getStaff: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/staff/");
    },
    // Agents
    getAgents: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/agents/");
    },
    chatWithAgent: function (gameId, agentId, message) {
      return request("POST", "/api/games/" + gameId + "/agents/" + agentId + "/chat/", { message: message });
    },
    getAgentChatHistory: function (gameId, agentId) {
      return request("GET", "/api/games/" + gameId + "/agents/" + agentId + "/chat/");
    },
    // Promises
    getPromises: function (gameId, promiseStatus) {
      var qs = promiseStatus ? "?status=" + encodeURIComponent(promiseStatus) : "";
      return request("GET", "/api/games/" + gameId + "/promises/" + qs);
    },
    // Neighbors
    getNeighbors: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/neighbors/");
    },
    getNeighborDetail: function (gameId, neighborId) {
      return request("GET", "/api/games/" + gameId + "/neighbors/" + neighborId + "/");
    },
    getNeighborEvents: function (gameId, neighborId, limit) {
      var qs = limit ? "?limit=" + limit : "";
      return request("GET", "/api/games/" + gameId + "/neighbors/" + neighborId + "/events/" + qs);
    },
    getNeighborSummaryV2: function (gameId, neighborId) {
      return request("GET", "/api/games/" + gameId + "/neighbors/" + neighborId + "/summary-v2/");
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
