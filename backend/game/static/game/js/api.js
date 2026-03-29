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
      return res.text().then(function (text) {
        var data = {};
        if (text) {
          try {
            data = JSON.parse(text);
          } catch (_parseErr) {
            var nonJsonError = new Error(res.ok ? "服务器返回了非 JSON 响应" : "服务器内部错误");
            nonJsonError.status = res.status;
            nonJsonError.raw = text;
            throw nonJsonError;
          }
        }
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
    getGameKnowledge: function () {
      return request("GET", "/api/game-knowledge/");
    },
    listGames: function () {
      return request("GET", "/api/games/");
    },
    createGame: function (countyType) {
      return request("POST", "/api/games/", {
        county_type: countyType,
      });
    },
    getGame: function (id) {
      return request("GET", "/api/games/" + id + "/");
    },
    dismissTutorial: function (id) {
      return request("POST", "/api/games/" + id + "/dismiss-tutorial/");
    },
    submitFeedback: function (id, content) {
      return request("POST", "/api/games/" + id + "/feedback/", { content: content });
    },
    getCountyJudicial: function (id) {
      return request("GET", "/api/games/" + id + "/judicial/");
    },
    decideCountyJudicial: function (id, caseId, action, verdictCode) {
      var body = { case_id: caseId, action: action };
      if (verdictCode) body.verdict_code = verdictCode;
      return request("POST", "/api/games/" + id + "/judicial/decide/", body);
    },
    getCountyJudicialDebug: function (id) {
      return request("GET", "/api/games/" + id + "/judicial/debug/");
    },
    submitAnnualReview: function (id, payload) {
      return request("POST", "/api/games/" + id + "/annual-review/", payload);
    },
    invest: function (id, action, targetVillage) {
      var body = { action: action };
      if (targetVillage) body.target_village = targetVillage;
      return request("POST", "/api/games/" + id + "/invest/", body);
    },
    requestLandSurvey: function (id, villageName) {
      return request("POST", "/api/games/" + id + "/land-survey/", { village_name: villageName });
    },
    checkBribes: function (id) {
      return request("GET", "/api/games/" + id + "/check-bribes/");
    },
    respondBribe: function (id, villageName, eventType, accept) {
      return request("POST", "/api/games/" + id + "/respond-bribe/", {
        village_name: villageName,
        event_type: eventType,
        accept: accept,
      });
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
    adjustRemitRatio: function (id, remitRatio) {
      return request("POST", "/api/games/" + id + "/remit-ratio/", { remit_ratio: remitRatio });
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
    emergencyBuyGrain: function (id, amountJin) {
      return request("POST", "/api/games/" + id + "/emergency/buy-grain/", { amount: amountJin });
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
    // Prefecture overview (知县视角)
    getPrefectureOverviewForCounty: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/prefecture-overview/");
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
    getCareer: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/career/");
    },
    promotionAction: function (gameId, payload) {
      return request("POST", "/api/games/" + gameId + "/promotion-action/", payload);
    },
    startNewTerm: function (gameId, choice) {
      return request("POST", "/api/games/" + gameId + "/new-term/", { choice: choice || "transfer" });
    },
    getCountyRumors: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/rumors/");
    },
    getAnnualReviewDraft: function (gameId) {
      return request("POST", "/api/games/" + gameId + "/annual-review/draft/", {});
    },
    // Prefecture (知府)
    createPrefecture: function (prefectureType) {
      var body = {};
      if (prefectureType) body.prefecture_type = prefectureType;
      return request("POST", "/api/prefecture/create/", body);
    },
    getPrefectureOverview: function (gameId) {
      return request("GET", "/api/prefecture/" + gameId + "/");
    },
    precomputePrefecture: function (gameId) {
      return request("POST", "/api/prefecture/" + gameId + "/precompute/", {});
    },
    getPrefecturePrecomputeStatus: function (gameId) {
      return request("GET", "/api/prefecture/" + gameId + "/precompute/");
    },
    advancePrefectureMonth: function (gameId) {
      return request("POST", "/api/prefecture/" + gameId + "/advance/", {});
    },
    getPrefectureCountyDetail: function (gameId, unitId) {
      return request("GET", "/api/prefecture/" + gameId + "/counties/" + unitId + "/");
    },
    getPrefecturePersonnel: function (gameId) {
      return request("GET", "/api/prefecture/" + gameId + "/personnel/");
    },
    submitPrefecturePersonnelReview: function (gameId, payload) {
      return request("POST", "/api/prefecture/" + gameId + "/personnel/", payload);
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
    // Letters (书信系统)
    getLetterInbox: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/letters/");
    },
    getLetterSent: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/letters/sent/");
    },
    getLetterPending: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/letters/pending/");
    },
    getLetterBlockingCheck: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/letters/blocking-check/");
    },
    getLetterSummary: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/letters/summary/");
    },
    getLetterDetail: function (gameId, letterId) {
      return request("GET", "/api/games/" + gameId + "/letters/" + letterId + "/");
    },
    replyLetter: function (gameId, letterId, payload) {
      return request("POST", "/api/games/" + gameId + "/letters/" + letterId + "/reply/", payload);
    },
    archiveLetter: function (gameId, letterId) {
      return request("POST", "/api/games/" + gameId + "/letters/" + letterId + "/archive/", {});
    },
    composeLetter: function (gameId, payload) {
      return request("POST", "/api/games/" + gameId + "/letters/", payload);
    },
    // Counsel (幕僚群聊 + 自创施政)
    counselMessage: function (gameId, message, history) {
      return request("POST", "/api/games/" + gameId + "/counsel/message/", {
        message: message,
        history: history || [],
      });
    },
    counselPropose: function (gameId, policyName, rationale) {
      return request("POST", "/api/games/" + gameId + "/counsel/propose/", {
        policy_name: policyName,
        rationale: rationale || "",
      });
    },
    counselPolicies: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/counsel/policies/");
    },
    counselDiscardPolicy: function (gameId, policyId) {
      return request("POST", "/api/games/" + gameId + "/counsel/policies/" + policyId + "/discard/");
    },
    counselPendingNotifications: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/counsel/pending-notifications/");
    },
    counselProactive: function (gameId) {
      return request("GET", "/api/games/" + gameId + "/counsel/proactive/");
    },
    // Imperial tour levy
    imperialTourDecide: function (gameId, paymentRatio, apportionmentMethod) {
      return request("POST", "/api/games/" + gameId + "/imperial-tour/decide/", {
        payment_ratio: paymentRatio,
        apportionment_method: apportionmentMethod,
      });
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
