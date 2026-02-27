/**
 * App entry point — event binding & init
 */
(function () {
  "use strict";

  var api = Game.api;
  var screens = Game.screens;
  var components = Game.components;

  // Helpers
  function el(id) { return document.getElementById(id); }

  function loadSummaryPayload(gameId) {
    return api.getSummaryV2(gameId)
      .then(function (summary) {
        return { version: "v2", data: summary };
      })
      .catch(function () {
        return api.getSummary(gameId).then(function (summary) {
          return { version: "v1", data: summary };
        });
      });
  }

  function renderSummaryPayload(payload) {
    if (payload && payload.version === "v2" && components.renderSummaryV2) {
      components.renderSummaryV2(payload.data);
    } else {
      components.renderSummary(payload.data);
    }
    screens.show("screen-summary");
  }

  // ==================== Login ====================
  el("login-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var username = el("login-username").value.trim();
    var password = el("login-password").value;
    var errEl = el("login-error");
    errEl.classList.add("hidden");

    api.login(username, password)
      .then(function (data) {
        Game.state.user = data.username;
        el("username-display").textContent = data.username;
        loadGameList();
      })
      .catch(function (err) {
        errEl.textContent = err.message || "登录失败";
        errEl.classList.remove("hidden");
      });
  });

  // ==================== Logout ====================
  el("btn-logout").addEventListener("click", function () {
    api.logout().then(function () {
      Game.state.user = null;
      Game.state.games = [];
      Game.state.currentGame = null;
      screens.show("screen-login");
      el("login-password").value = "";
    });
  });

  // ==================== New Game ====================
  var bgButtons = document.querySelectorAll(".btn-background");
  for (var i = 0; i < bgButtons.length; i++) {
    bgButtons[i].addEventListener("click", function () {
      var bg = this.dataset.bg;
      api.createGame(bg)
        .then(function (data) {
          components.showToast("新游戏已创建", "success");
          Game.setGame(data);
          screens.show("screen-game");
          screens.showTab("tab-dashboard");
        })
        .catch(function (err) {
          components.showToast(err.message, "error");
        });
    });
  }

  // ==================== Continue Game ====================
  el("game-list").addEventListener("click", function (e) {
    var btn = e.target.closest(".btn-continue");
    if (!btn) return;
    var gameId = btn.dataset.gameId;

    api.getGame(gameId)
      .then(function (data) {
        if (data.current_season > Game.MAX_MONTH) {
          // Game is over — show summary
          return loadSummaryPayload(data.id).then(function (payload) {
            renderSummaryPayload(payload);
          });
        }
        Game.setGame(data);
        screens.show("screen-game");
        screens.showTab("tab-dashboard");
      })
      .catch(function (err) {
        components.showToast(err.message, "error");
      });
  });

  // ==================== Back to list ====================
  el("btn-back-to-list").addEventListener("click", function () {
    loadGameList();
  });

  el("btn-back-from-summary").addEventListener("click", function () {
    loadGameList();
  });

  // ==================== Tabs ====================
  var tabBtns = document.querySelectorAll(".tab-btn");
  for (var t = 0; t < tabBtns.length; t++) {
    tabBtns[t].addEventListener("click", function () {
      screens.showTab(this.dataset.tab);
    });
  }

  // ==================== Tax Rate ====================
  el("tax-slider").addEventListener("input", function () {
    el("tax-display").textContent = this.value + "%";
  });

  el("btn-set-tax").addEventListener("click", function () {
    var g = Game.state.currentGame;
    if (!g) return;
    var rate = parseInt(el("tax-slider").value) / 100;

    api.setTaxRate(g.id, rate)
      .then(function (data) {
        components.showToast(data.message, "success");
        // Refresh game state
        return api.getGame(g.id);
      })
      .then(function (data) {
        Game.setGame(data);
      })
      .catch(function (err) {
        components.showToast(err.message, "error");
      });
  });

  // ==================== Commercial Tax Rate ====================
  el("commercial-tax-slider").addEventListener("input", function () {
    el("commercial-tax-display").textContent = this.value + "%";
  });

  el("btn-set-commercial-tax").addEventListener("click", function () {
    var g = Game.state.currentGame;
    if (!g) return;
    var rate = parseFloat(el("commercial-tax-slider").value) / 100;

    api.setCommercialTaxRate(g.id, rate)
      .then(function (data) {
        components.showToast(data.message, "success");
        return api.getGame(g.id);
      })
      .then(function (data) {
        Game.setGame(data);
      })
      .catch(function (err) {
        components.showToast(err.message, "error");
      });
  });

  // ==================== Investments ====================
  var pendingAction = null;

  el("invest-cards").addEventListener("click", function (e) {
    var card = e.target.closest(".invest-card");
    if (!card || card.classList.contains("disabled")) return;

    var action = card.dataset.action;
    if (!action) return;

    var needsVillage = card.dataset.needsVillage === "1";

    if (needsVillage) {
      // Show village selection modal
      pendingAction = action;
      var select = el("modal-village-select");
      select.innerHTML = "";
      var villages = Game.state.currentGame.county_data.villages;

      // Filter villages for fund_village_school (only those without school)
      var filtered = villages;
      if (action === "fund_village_school") {
        filtered = villages.filter(function (v) { return !v.has_school; });
      }

      filtered.forEach(function (v) {
        var opt = document.createElement("option");
        opt.value = v.name;
        opt.textContent = v.name + "（人口" + v.population + "）";
        select.appendChild(opt);
      });
      el("modal-title").textContent = getInvestName(action) + " — 选择目标村庄";
      el("village-modal").classList.remove("hidden");
    } else {
      doInvest(action, null);
    }
  });

  el("modal-confirm").addEventListener("click", function () {
    if (!pendingAction) return;
    var village = el("modal-village-select").value;
    el("village-modal").classList.add("hidden");

    // 过度开发预警 (doc 06a §2.5)
    if (pendingAction === "reclaim_land") {
      var investments = (Game.state.currentGame || {}).available_investments || [];
      var reclaimDef = null;
      for (var i = 0; i < investments.length; i++) {
        if (investments[i].action === "reclaim_land") { reclaimDef = investments[i]; break; }
      }
      var warnings = (reclaimDef && reclaimDef.village_warnings) || [];
      var warning = null;
      for (var j = 0; j < warnings.length; j++) {
        if (warnings[j].village === village) { warning = warnings[j]; break; }
      }
      if (warning) {
        // Show advisor warning modal
        el("advisor-warning-text").textContent =
          village + "土地开发率已达" + warning.utilization + "%，继续开垦恐增加水旱灾害风险。是否仍要继续？";
        el("advisor-warning-modal").classList.remove("hidden");
        // Store pending data for proceed/cancel
        el("advisor-warning-modal").dataset.action = pendingAction;
        el("advisor-warning-modal").dataset.village = village;
        pendingAction = null;
        return;
      }
    }

    doInvest(pendingAction, village);
    pendingAction = null;
  });

  el("modal-cancel").addEventListener("click", function () {
    pendingAction = null;
    el("village-modal").classList.add("hidden");
  });

  // Advisor warning modal handlers
  el("advisor-warning-proceed").addEventListener("click", function () {
    var modal = el("advisor-warning-modal");
    var action = modal.dataset.action;
    var village = modal.dataset.village;
    modal.classList.add("hidden");
    if (action && village) doInvest(action, village);
  });

  el("advisor-warning-cancel").addEventListener("click", function () {
    var modal = el("advisor-warning-modal");
    var village = modal.dataset.village;
    modal.classList.add("hidden");

    var g = Game.state.currentGame;
    if (g && village) {
      api.requestLandSurvey(g.id, village)
        .then(function (data) {
          components.showToast(data.message, "info");
        })
        .catch(function (err) {
          components.showToast(err.message || "勘查请求失败", "error");
        });
    }
  });

  function doInvest(action, targetVillage) {
    var g = Game.state.currentGame;
    if (!g) return;

    api.invest(g.id, action, targetVillage)
      .then(function (data) {
        components.showToast(data.message, "success");
        return api.getGame(g.id);
      })
      .then(function (data) {
        Game.setGame(data);
      })
      .catch(function (err) {
        components.showToast(err.message || "投资失败", "error");
      });
  }

  function getInvestName(action) {
    var defs = components.INVEST_DEFS;
    for (var i = 0; i < defs.length; i++) {
      if (defs[i].action === action) return defs[i].name;
    }
    return action;
  }

  // ==================== Advance Season ====================

  // 邻县预计算轮询
  var _precomputeTimer = null;
  function startPrecomputePolling(gameId) {
    stopPrecomputePolling();
    var statusEl = el("neighbor-precompute-status");
    if (statusEl) statusEl.innerHTML = "";
    var seen = {};

    _precomputeTimer = setInterval(function () {
      api.getPrecomputeStatus(gameId)
        .then(function (data) {
          if (!statusEl) return;
          // 显示每个新完成的邻县
          (data.completed || []).forEach(function (n) {
            if (!seen[n.neighbor_id]) {
              seen[n.neighbor_id] = true;
              var item = document.createElement("div");
              item.className = "precompute-item precompute-done";
              item.textContent = n.county_name + " " + n.governor_name + "知县已完成本轮施政";
              statusEl.appendChild(item);
            }
          });
          if (data.status === "done") {
            stopPrecomputePolling();
            var allDone = document.createElement("div");
            allDone.className = "precompute-item precompute-all-done";
            allDone.textContent = "各邻县施政决策已就绪";
            statusEl.appendChild(allDone);
          }
        })
        .catch(function () {
          stopPrecomputePolling();
        });
    }, 3000);
  }

  function stopPrecomputePolling() {
    if (_precomputeTimer) {
      clearInterval(_precomputeTimer);
      _precomputeTimer = null;
    }
  }

  el("btn-advance").addEventListener("click", function () {
    var g = Game.state.currentGame;
    if (!g || g.current_season > Game.MAX_MONTH) return;

    var btn = el("btn-advance");
    btn.disabled = true;
    btn.textContent = "推进中...";
    stopPrecomputePolling();

    api.advance(g.id)
      .then(function (report) {
        Game.state.lastReport = report;
        components.renderReport(report);
        screens.showTab("tab-report");

        if (report.game_over) {
          components.showToast("三年任期已满！", "info");
          return loadSummaryPayload(g.id).then(function (payload) {
            Game.state.lastReport.fullSummary = payload;
            btn.textContent = "查看任期总结";
            btn.disabled = false;
            btn.onclick = function () {
              renderSummaryPayload(payload);
              btn.onclick = null;
            };
          });
        } else {
          return api.getGame(g.id).then(function (data) {
            Game.setGame(data);
            btn.disabled = false;
            btn.textContent = "推进月份";
            // 触发后台预计算 + 开始轮询进度
            api.precomputeNeighbors(g.id).catch(function () {});
            startPrecomputePolling(g.id);
          });
        }
      })
      .catch(function (err) {
        components.showToast(err.message, "error");
        btn.disabled = false;
        btn.textContent = "推进月份";
      });
  });

  // ==================== Negotiation ====================

  function buildDelegationMessage(session, speakerRole) {
    var type = (session && session.event_type) || "ANNEXATION";
    if (speakerRole === "ADVISOR") {
      if (type === "HIDDEN_LAND") {
        return "请以县衙名义与其交涉，争取其主动申报隐田，必要时明确官府将依法清丈。";
      }
      if (type === "IRRIGATION") {
        return "请以长远利害说服其出资水利，尽量争取更高出资且不激化矛盾。";
      }
      return "请以县衙立场交涉，目标是停止兼并，兼顾稳定与后续治理。";
    }
    if (speakerRole === "DEPUTY") {
      if (type === "HIDDEN_LAND") {
        return "请按衙门章程交涉，要求其申报隐田，不从则启动清丈程序。";
      }
      if (type === "IRRIGATION") {
        return "请按公事流程与其协商出资水利，明确县衙推进工程的决心。";
      }
      return "请按县衙公事流程交涉，要求其停止兼并并说明后果。";
    }
    return "";
  }

  function syncNegotiationInputMode() {
    var speakerSelect = el("nego-speaker");
    var input = el("nego-input");
    var sendBtn = el("nego-send");
    if (!speakerSelect || !input || !sendBtn) return;

    var isPlayer = speakerSelect.value === "PLAYER";
    if (isPlayer) {
      input.classList.remove("hidden");
      input.placeholder = "输入你的话...";
      sendBtn.textContent = "发言";
    } else {
      input.classList.add("hidden");
      sendBtn.textContent = "委托谈判";
    }
  }

  // Open negotiation modal (from banner or report button)
  document.addEventListener("click", function (e) {
    var isBanner = e.target.id === "btn-open-negotiation" || e.target.closest("#btn-open-negotiation");
    var isReport = e.target.id === "btn-report-negotiate" || e.target.closest("#btn-report-negotiate");

    if (isBanner || isReport) {
      var session = Game.state.activeNegotiation;
      if (session) {
        components.openNegotiationModal(session);
        syncNegotiationInputMode();
      } else {
        // Fetch it first
        var g = Game.state.currentGame;
        if (!g) return;
        api.getActiveNegotiation(g.id).then(function (data) {
          if (data.active) {
            Game.state.activeNegotiation = data.session;
            components.openNegotiationModal(data.session);
            syncNegotiationInputMode();
          } else {
            components.showToast("当前没有进行中的谈判", "info");
          }
        });
      }
    }
  });

  // Close negotiation modal
  el("nego-close").addEventListener("click", function () {
    el("negotiation-modal").classList.add("hidden");
    // Refresh state in case negotiation resolved
    var g = Game.state.currentGame;
    if (g) {
      api.getGame(g.id).then(function (data) {
        Game.setGame(data);
      });
    }
  });

  // Send negotiation message
  function sendNegotiationMessage() {
    var g = Game.state.currentGame;
    var session = Game.state.activeNegotiation;
    if (!g || !session) return;

    var input = el("nego-input");
    var speakerSelect = el("nego-speaker");
    var speakerRole = speakerSelect ? speakerSelect.value : "PLAYER";
    var message = "";
    if (speakerRole === "PLAYER") {
      message = input.value.trim();
      if (!message) return;
    } else {
      message = buildDelegationMessage(session, speakerRole);
    }
    var speakerName = "";
    if (speakerRole === "ADVISOR") speakerName = "师爷";
    if (speakerRole === "DEPUTY") speakerName = "县丞";

    var sendBtn = el("nego-send");
    sendBtn.disabled = true;
    input.disabled = true;
    if (speakerSelect) speakerSelect.disabled = true;

    // Show player message immediately
    var showText = message;
    if (speakerRole !== "PLAYER") {
      showText = speakerRole === "ADVISOR" ? "请师爷代为交涉。" : "请县丞代为交涉。";
    }
    components.appendNegotiationMessage("player", showText, {
      speakerRole: speakerRole,
      speakerName: speakerName,
    });
    input.value = "";

    api.sendNegotiationChat(g.id, session.id, message, speakerRole)
      .then(function (result) {
        // Show agent response
        components.appendNegotiationMessage("agent", result.dialogue);

        // Update subtitle
        el("nego-subtitle").textContent =
          result.agent_name + " 第" + result.round + "/" + result.max_rounds + "轮";

        // Update session state
        if (Game.state.activeNegotiation) {
          Game.state.activeNegotiation.current_round = result.round;
        }

        // Check if resolved
        if (result.status === "resolved") {
          Game.state.activeNegotiation = null;
          components.showNegotiationResolved(result);
          components.showToast("谈判已结束", "info");
          // Refresh game state to update treasury and dashboard
          api.getGame(g.id).then(function (data) {
            Game.setGame(data);
          });
        } else if (result.handoff_to_player) {
          if (speakerSelect) speakerSelect.value = "PLAYER";
          syncNegotiationInputMode();
          components.showToast(result.handoff_message || "委托交涉未果，请你亲自出面", "info");
        }
      })
      .catch(function (err) {
        components.showToast(err.message || "发言失败", "error");
      })
      .finally(function () {
        sendBtn.disabled = false;
        input.disabled = false;
        if (speakerSelect) speakerSelect.disabled = false;
        syncNegotiationInputMode();
        if (!input.classList.contains("hidden")) input.focus();
      });
  }

  el("nego-send").addEventListener("click", sendNegotiationMessage);
  el("nego-input").addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      sendNegotiationMessage();
    }
  });
  if (el("nego-speaker")) {
    el("nego-speaker").addEventListener("change", syncNegotiationInputMode);
  }

  // Start irrigation negotiation (from village buttons)
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".btn-irrigation-nego");
    if (!btn) return;

    var g = Game.state.currentGame;
    if (!g) return;

    var villageName = btn.dataset.village;
    btn.disabled = true;
    btn.textContent = "发起中...";

    api.startIrrigationNegotiation(g.id, villageName)
      .then(function (session) {
        Game.state.activeNegotiation = session;
        components.renderNegotiationBanner();
        components.renderIrrigationNegotiateSection();
        components.openNegotiationModal(session);
        syncNegotiationInputMode();
        components.showToast("已与" + villageName + "地主开始协商", "success");
      })
      .catch(function (err) {
        components.showToast(err.message || "协商发起失败", "error");
        btn.disabled = false;
        btn.textContent = villageName + "地主";
      });
  });

  // ==================== Agent Profile ====================
  document.addEventListener("click", function (e) {
    var villageLink = e.target.closest(".village-name-link");
    if (!villageLink) return;
    var villageName = villageLink.dataset.villageName;
    if (!villageName || !components.openVillageDetail) return;
    components.openVillageDetail(villageName);
  });

  el("village-detail-close").addEventListener("click", function () {
    el("village-detail-modal").classList.add("hidden");
  });

  el("village-detail-modal").addEventListener("click", function (e) {
    if (e.target === this) this.classList.add("hidden");
  });

  document.addEventListener("click", function (e) {
    var link = e.target.closest(".agent-link");
    if (!link) return;
    var agentId = parseInt(link.dataset.agentId);
    var agents = Game.state.agents || [];
    for (var i = 0; i < agents.length; i++) {
      if (agents[i].id === agentId) {
        components.openAgentProfile(agents[i]);
        return;
      }
    }
  });

  el("agent-profile-close").addEventListener("click", function () {
    el("agent-profile-modal").classList.add("hidden");
  });

  // ==================== Event Logs (县志) ====================
  function loadEventLogs() {
    var g = Game.state.currentGame;
    if (!g) return;

    var category = el("events-category-filter").value;
    el("events-list").innerHTML = '<p class="hint">加载中...</p>';

    api.getEventLogs(g.id, category, null, 100)
      .then(function (logs) {
        components.renderEventLogs(logs);
      })
      .catch(function (err) {
        el("events-list").innerHTML = '<p class="hint">加载失败</p>';
      });
  }

  el("events-category-filter").addEventListener("change", loadEventLogs);
  el("btn-refresh-events").addEventListener("click", loadEventLogs);

  // Auto-load when tab is activated
  var origShowTab = Game.screens.showTab;
  Game.screens.showTab = function (tabId) {
    origShowTab(tabId);
    if (tabId === "tab-events") {
      loadEventLogs();
    } else if (tabId === "tab-relationships") {
      loadRelationships();
    } else if (tabId === "tab-staff") {
      loadStaffInfo();
    } else if (tabId === "tab-neighbors") {
      loadNeighbors();
    }
  };

  function loadRelationships() {
    var g = Game.state.currentGame;
    if (!g) return;

    var container = document.getElementById("relationships-list");
    container.innerHTML = '<p class="hint">加载中...</p>';

    api.getAgents(g.id)
      .then(function (agents) {
        components.renderRelationships(agents);
      })
      .catch(function () {
        container.innerHTML = '<p class="hint">加载失败</p>';
      });
  }

  // ==================== Staff (幕僚) ====================
  function loadStaffInfo() {
    var g = Game.state.currentGame;
    if (!g) return;

    var container = document.getElementById("staff-content");
    container.innerHTML = '<p class="hint">加载中...</p>';

    api.getStaff(g.id)
      .then(function (data) {
        components.renderStaffTab(data);
      })
      .catch(function () {
        container.innerHTML = '<p class="hint">加载失败</p>';
      });
  }

  // Staff chat button click (delegated)
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".btn-staff-chat");
    if (!btn || btn.disabled) return;
    var agentId = parseInt(btn.dataset.agentId);
    var agentName = btn.dataset.agentName;
    components.openStaffChat(agentId, agentName);
  });

  // Staff chat close
  el("staff-chat-close").addEventListener("click", function () {
    el("staff-chat-modal").classList.add("hidden");
    // Refresh staff info (question count may have changed)
    loadStaffInfo();
  });

  // Staff chat send
  function sendStaffChatMessage() {
    var g = Game.state.currentGame;
    var modal = el("staff-chat-modal");
    var agentId = parseInt(modal.dataset.agentId);
    if (!g || !agentId) return;

    var input = el("staff-chat-input");
    var message = input.value.trim();
    if (!message) return;

    var sendBtn = el("staff-chat-send");
    sendBtn.disabled = true;
    input.disabled = true;

    // Show player message immediately
    components.appendStaffChatMessage("player", message);
    input.value = "";

    api.chatWithAgent(g.id, agentId, message)
      .then(function (result) {
        components.appendStaffChatMessage("agent", result.dialogue);
      })
      .catch(function (err) {
        components.showToast(err.message || "对话失败", "error");
      })
      .finally(function () {
        sendBtn.disabled = false;
        input.disabled = false;
        input.focus();
      });
  }

  el("staff-chat-send").addEventListener("click", sendStaffChatMessage);
  el("staff-chat-input").addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      sendStaffChatMessage();
    }
  });

  // ==================== Neighbors (邻县) ====================
  function loadNeighbors() {
    var g = Game.state.currentGame;
    if (!g) return;

    var container = document.getElementById("neighbors-list");
    container.innerHTML = '<p class="hint">加载中...</p>';

    api.getNeighbors(g.id)
      .then(function (neighbors) {
        Game.state.neighbors = neighbors;
        components.renderNeighborsList(neighbors);
      })
      .catch(function () {
        container.innerHTML = '<p class="hint">加载失败</p>';
      });
  }

  // Neighbor card click → open detail modal
  document.addEventListener("click", function (e) {
    var card = e.target.closest(".neighbor-card");
    if (!card) return;
    var neighborId = parseInt(card.dataset.neighborId);
    var neighbors = Game.state.neighbors || [];
    for (var i = 0; i < neighbors.length; i++) {
      if (neighbors[i].id === neighborId) {
        components.openNeighborDetail(neighbors[i]);
        return;
      }
    }
  });

  // Summary v2: on-demand neighbor term report
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".summary2-neighbor-report-btn");
    if (!btn) return;

    var gameId = parseInt(btn.dataset.gameId, 10);
    var neighborId = parseInt(btn.dataset.neighborId, 10);
    if (!gameId || !neighborId) {
      components.showToast("缺少邻县报告参数", "error");
      return;
    }

    var oldText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "生成中...";

    api.getNeighborSummaryV2(gameId, neighborId)
      .then(function (report) {
        if (components.openNeighborTermReport) {
          components.openNeighborTermReport(report);
        }
      })
      .catch(function (err) {
        components.showToast(err.message || "生成邻县任期报告失败", "error");
      })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = oldText;
      });
  });

  // Close neighbor detail modal
  el("neighbor-detail-close").addEventListener("click", function () {
    el("neighbor-detail-modal").classList.add("hidden");
  });

  // ==================== Load Game List ====================
  function loadGameList() {
    api.listGames()
      .then(function (games) {
        Game.state.games = games;
        components.renderGameList(games);
        screens.show("screen-game-list");
      })
      .catch(function (err) {
        components.showToast(err.message, "error");
      });
  }

  // ==================== Auto-login on page load ====================
  function tryAutoLogin() {
    api.listGames()
      .then(function (games) {
        // Session is valid
        Game.state.games = games;
        components.renderGameList(games);
        screens.show("screen-game-list");
      })
      .catch(function () {
        // Not logged in
        screens.show("screen-login");
      });
  }

  tryAutoLogin();
})();
