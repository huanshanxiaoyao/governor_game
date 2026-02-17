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
  var medNames = ["无", "简易医馆", "县医署", "完善医疗"];
  var medCosts = [0, 25, 50, 100];

  el("medical-slider").addEventListener("input", function () {
    var lv = parseInt(this.value);
    el("medical-display").textContent = lv + "级 — " + medNames[lv] + "（" + medCosts[lv] + "两/年）";
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
  el("btn-advance").addEventListener("click", function () {
    var g = Game.state.currentGame;
    if (!g || g.current_season > 12) return;

    var btn = el("btn-advance");
    btn.disabled = true;
    btn.textContent = "推进中...";

    api.advance(g.id)
      .then(function (report) {
        Game.state.lastReport = report;
        components.renderReport(report);
        screens.showTab("tab-report");

        if (report.game_over) {
          components.showToast("三年任期已满！", "info");
          // Fetch full summary
          return api.getSummary(g.id).then(function (summary) {
            Game.state.lastReport.fullSummary = summary;
            // Update button to go to summary
            btn.textContent = "查看任期总结";
            btn.disabled = false;
            btn.onclick = function () {
              components.renderSummary(summary);
              screens.show("screen-summary");
              // Reset handler
              btn.onclick = null;
            };
          });
        } else {
          // Refresh game state
          return api.getGame(g.id).then(function (data) {
            Game.setGame(data);
            btn.disabled = false;
            btn.textContent = "推进季度";
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
