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
    createGame: function (background, countyType) {
      return request("POST", "/api/games/", {
        background: background,
        county_type: countyType,
      });
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
    applyDisasterRelief: function (id, claimedLoss) {
      return request("POST", "/api/games/" + id + "/disaster-relief/", { claimed_loss: claimedLoss });
    },
    emergencyPrefectureRelief: function (id) {
      return request("POST", "/api/games/" + id + "/emergency/prefecture-relief/", {});
    },
    emergencyBorrowNeighbor: function (id, neighborId, amount) {
      return request("POST", "/api/games/" + id + "/emergency/borrow-neighbor/", {
        neighbor_id: neighborId,
        amount: amount,
      });
    },
    emergencyGentryRelief: function (id, amount) {
      return request("POST", "/api/games/" + id + "/emergency/gentry-relief/", { amount: amount });
    },
    emergencyForceLevy: function (id, amount) {
      return request("POST", "/api/games/" + id + "/emergency/force-levy/", { amount: amount });
    },
    emergencySetDebugReveal: function (id, enabled) {
      return request("POST", "/api/games/" + id + "/emergency/debug-toggle/", { enabled: enabled });
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
    getActiveNegotiations: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/negotiations/active-list/");
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
    // Officialdom
    getOfficialdom: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/officialdom/");
    },
    // Prefecture (知府)
    createPrefecture: function (background, prefectureType) {
      var body = { background: background };
      if (prefectureType) body.prefecture_type = prefectureType;
      return request("POST", "/api/prefecture/create/", body);
    },
    getPrefectureOverview: function (gameId) {
      return request("GET", "/api/prefecture/" + gameId + "/");
    },
    advancePrefectureMonth: function (gameId) {
      return request("POST", "/api/prefecture/" + gameId + "/advance/", {});
    },
    getPrefectureCountyDetail: function (gameId, unitId) {
      return request("GET", "/api/prefecture/" + gameId + "/counties/" + unitId + "/");
    },
    setPrefectureQuota: function (gameId, assignments) {
      return request("POST", "/api/prefecture/" + gameId + "/quota/", { assignments: assignments });
    },
    sendPrefectureDirective: function (gameId, unitId, directive) {
      return request("POST", "/api/prefecture/" + gameId + "/directive/", { unit_id: unitId, directive: directive });
    },
    inspectPrefectureCounty: function (gameId, unitId, inspectType) {
      return request("POST", "/api/prefecture/" + gameId + "/inspect/", { unit_id: unitId, inspect_type: inspectType });
    },
    getPrefectureInvestStatus: function (gameId) {
      return request("GET", "/api/prefecture/" + gameId + "/invest/");
    },
    investPrefecture: function (gameId, project, level) {
      return request("POST", "/api/prefecture/" + gameId + "/invest/", { project: project, level: level });
    },
    getPrefectureTalent: function (gameId) {
      return request("GET", "/api/prefecture/" + gameId + "/talent/");
    },
    getPrefectureJudicialCases: function (gameId) {
      return request("GET", "/api/prefecture/" + gameId + "/judicial/");
    },
    decidePrefectureCase: function (gameId, caseId, action) {
      return request("POST", "/api/prefecture/" + gameId + "/judicial/decide/", { case_id: caseId, action: action });
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
