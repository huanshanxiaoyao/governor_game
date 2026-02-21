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
        if (data.current_season > 12) {
          // Game is over — show summary
          return api.getSummary(data.id).then(function (summary) {
            components.renderSummary(summary);
            screens.show("screen-summary");
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

  // ==================== Medical Level ====================
  el("medical-slider").addEventListener("input", function () {
    var lv = parseInt(this.value);
    var g = Game.state.currentGame;
    var cost = 0;
    if (g && g.county_data) {
      cost = components.calcMedicalCost(lv, g.county_data);
    }
    el("medical-display").textContent = lv + "级 — " + components.MEDICAL_NAMES[lv] + "（" + cost + "两/年）";
  });

  el("btn-set-medical").addEventListener("click", function () {
    var g = Game.state.currentGame;
    if (!g) return;
    var level = parseInt(el("medical-slider").value);

    api.setMedicalLevel(g.id, level)
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
    doInvest(pendingAction, village);
    pendingAction = null;
  });

  el("modal-cancel").addEventListener("click", function () {
    pendingAction = null;
    el("village-modal").classList.add("hidden");
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
    if (!g || g.current_season > 12) return;

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
          return api.getSummary(g.id).then(function (summary) {
            Game.state.lastReport.fullSummary = summary;
            btn.textContent = "查看任期总结";
            btn.disabled = false;
            btn.onclick = function () {
              components.renderSummary(summary);
              screens.show("screen-summary");
              btn.onclick = null;
            };
          });
        } else {
          return api.getGame(g.id).then(function (data) {
            Game.setGame(data);
            btn.disabled = false;
            btn.textContent = "推进季度";
            // 触发后台预计算 + 开始轮询进度
            api.precomputeNeighbors(g.id).catch(function () {});
            startPrecomputePolling(g.id);
          });
        }
      })
      .catch(function (err) {
        components.showToast(err.message, "error");
        btn.disabled = false;
        btn.textContent = "推进季度";
      });
  });

  // ==================== Negotiation ====================

  // Open negotiation modal (from banner or report button)
  document.addEventListener("click", function (e) {
    var isBanner = e.target.id === "btn-open-negotiation" || e.target.closest("#btn-open-negotiation");
    var isReport = e.target.id === "btn-report-negotiate" || e.target.closest("#btn-report-negotiate");

    if (isBanner || isReport) {
      var session = Game.state.activeNegotiation;
      if (session) {
        components.openNegotiationModal(session);
      } else {
        // Fetch it first
        var g = Game.state.currentGame;
        if (!g) return;
        api.getActiveNegotiation(g.id).then(function (data) {
          if (data.active) {
            Game.state.activeNegotiation = data.session;
            components.openNegotiationModal(data.session);
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
    var message = input.value.trim();
    if (!message) return;

    var sendBtn = el("nego-send");
    sendBtn.disabled = true;
    input.disabled = true;

    // Show player message immediately
    components.appendNegotiationMessage("player", message);
    input.value = "";

    api.sendNegotiationChat(g.id, session.id, message)
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
        }
      })
      .catch(function (err) {
        components.showToast(err.message || "发言失败", "error");
      })
      .finally(function () {
        sendBtn.disabled = false;
        input.disabled = false;
        input.focus();
      });
  }

  el("nego-send").addEventListener("click", sendNegotiationMessage);
  el("nego-input").addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      sendNegotiationMessage();
    }
  });

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
