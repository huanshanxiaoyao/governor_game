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
  var countyTypeButtons = document.querySelectorAll(".btn-county-type");
  var prefectureTypeButtons = document.querySelectorAll(".btn-prefecture-type");
  var roleButtons = document.querySelectorAll(".btn-role");
  var startGameBtn = el("btn-start-game");
  var newGameHint = el("new-game-selection-hint");
  var selectedRole = "";
  var selectedBackground = "";
  var selectedCountyType = "";
  var selectedPrefectureType = "";

  function updateSelectionButtons(buttons, dataKey, selectedValue) {
    for (var i = 0; i < buttons.length; i++) {
      var btn = buttons[i];
      if (btn.dataset[dataKey] === selectedValue) {
        btn.classList.add("btn-choice-selected");
      } else {
        btn.classList.remove("btn-choice-selected");
      }
    }
  }

  function getSelectedButtonText(buttons, dataKey, selectedValue) {
    for (var i = 0; i < buttons.length; i++) {
      if (buttons[i].dataset[dataKey] === selectedValue) {
        var strong = buttons[i].querySelector("strong");
        return strong ? strong.textContent : selectedValue;
      }
    }
    return selectedValue;
  }

  function refreshNewGameState() {
    var ready = false;
    var hint = "请先选择角色与出身";
    if (selectedRole === "COUNTY") {
      ready = !!selectedBackground && !!selectedCountyType;
      if (selectedBackground && !selectedCountyType) hint = "请选择县域类型";
      else if (!selectedBackground) hint = "请选择出身背景";
      else {
        var bgText = getSelectedButtonText(bgButtons, "bg", selectedBackground);
        var countyText = getSelectedButtonText(countyTypeButtons, "countyType", selectedCountyType);
        hint = "已选：知县 / " + bgText + " / " + countyText;
      }
    } else if (selectedRole === "PREFECTURE") {
      ready = !!selectedBackground && !!selectedPrefectureType;
      if (selectedBackground && !selectedPrefectureType) hint = "请选择府域类型";
      else if (!selectedBackground) hint = "请选择出身背景";
      else {
        var bgText2 = getSelectedButtonText(bgButtons, "bg", selectedBackground);
        var prefText = getSelectedButtonText(prefectureTypeButtons, "prefectureType", selectedPrefectureType);
        hint = "已选：知府 / " + bgText2 + " / " + prefText;
      }
    }
    if (startGameBtn) startGameBtn.disabled = !ready;
    if (newGameHint) newGameHint.textContent = hint;
  }

  for (var ri = 0; ri < roleButtons.length; ri++) {
    roleButtons[ri].addEventListener("click", function () {
      selectedRole = this.dataset.role || "";
      updateSelectionButtons(roleButtons, "role", selectedRole);
      // Show/hide type sections
      var countySection = el("county-type-section");
      var prefectureSection = el("prefecture-type-section");
      if (countySection) countySection.classList.toggle("hidden", selectedRole !== "COUNTY");
      if (prefectureSection) prefectureSection.classList.toggle("hidden", selectedRole !== "PREFECTURE");
      // Reset sub-selections
      selectedCountyType = "";
      selectedPrefectureType = "";
      updateSelectionButtons(countyTypeButtons, "countyType", "");
      updateSelectionButtons(prefectureTypeButtons, "prefectureType", "");
      refreshNewGameState();
    });
  }

  for (var i = 0; i < bgButtons.length; i++) {
    bgButtons[i].addEventListener("click", function () {
      selectedBackground = this.dataset.bg || "";
      updateSelectionButtons(bgButtons, "bg", selectedBackground);
      refreshNewGameState();
    });
  }
  for (var j = 0; j < countyTypeButtons.length; j++) {
    countyTypeButtons[j].addEventListener("click", function () {
      selectedCountyType = this.dataset.countyType || "";
      updateSelectionButtons(countyTypeButtons, "countyType", selectedCountyType);
      refreshNewGameState();
    });
  }
  for (var pi = 0; pi < prefectureTypeButtons.length; pi++) {
    prefectureTypeButtons[pi].addEventListener("click", function () {
      selectedPrefectureType = this.dataset.prefectureType || "";
      updateSelectionButtons(prefectureTypeButtons, "prefectureType", selectedPrefectureType);
      refreshNewGameState();
    });
  }

  if (startGameBtn) {
    startGameBtn.addEventListener("click", function () {
      startGameBtn.disabled = true;
      if (selectedRole === "PREFECTURE") {
        api.createPrefecture(selectedBackground, selectedPrefectureType || null)
          .then(function (data) {
            components.showToast("新知府游戏已创建", "success");
            Game.state.prefectureGame = data;
            loadPrefectureScreen(data);
          })
          .catch(function (err) {
            components.showToast(err.message, "error");
          })
          .finally(function () {
            refreshNewGameState();
          });
      } else {
        if (!selectedBackground || !selectedCountyType) return;
        api.createGame(selectedBackground, selectedCountyType)
          .then(function (data) {
            components.showToast("新游戏已创建", "success");
            Game.setGame(data);
            api.precomputeNeighbors(data.id).catch(function () {});
            startPrecomputePolling(data.id);
            screens.show("screen-game");
            screens.showTab("tab-dashboard");
          })
          .catch(function (err) {
            components.showToast(err.message, "error");
          })
          .finally(function () {
            refreshNewGameState();
          });
      }
    });
  }
  refreshNewGameState();

  // ==================== Continue Game ====================
  el("game-list").addEventListener("click", function (e) {
    var btn = e.target.closest(".btn-continue");
    if (!btn) return;
    var gameId = btn.dataset.gameId;
    var playerRole = btn.dataset.playerRole || 'COUNTY_MAGISTRATE';

    if (playerRole === 'PREFECT') {
      api.getPrefectureOverview(gameId)
        .then(function (data) {
          Game.state.prefectureGame = data;
          loadPrefectureScreen(data);
        })
        .catch(function (err) {
          components.showToast(err.message, "error");
        });
      return;
    }

    api.getGame(gameId)
      .then(function (data) {
        var countyData = data.county_data || {};
        var termEndReason = countyData.term_end_reason;
        var isTerminal = termEndReason === "ANNUAL_REVIEW_DISMISSED" || termEndReason === "PROMOTED_TO_PREFECT";
        var termComplete = data.current_season > Game.MAX_MONTH && !isTerminal;
        if (data.current_season > Game.MAX_MONTH && isTerminal) {
          // Terminal game-over — show summary
          return loadSummaryPayload(data.id).then(function (payload) {
            renderSummaryPayload(payload);
          });
        }
        Game.setGame(data);
        screens.show("screen-game");
        screens.showTab("tab-dashboard");
        if (termComplete) {
          // Term complete but not terminal — show new-term modal immediately
          showTermCompleteFromSaved(data);
        } else {
          api.precomputeNeighbors(data.id).catch(function () {});
          startPrecomputePolling(data.id);
        }
      })
      .catch(function (err) {
        components.showToast(err.message, "error");
      });
  });

  // ==================== Prefecture Screen ====================

  function loadPrefectureScreen(data) {
    if (!data) return;
    Game.state.prefectureGame = data;
    refreshPrefectureTabVisibility(data);
    Game.prefecture.renderOverview(data);
    _updateJudicialBadge(data.pending_judicial_count || 0);
    showPrefTab("pref-tab-overview");
    screens.show("screen-prefecture");
    if (data.current_season <= 36) {
      api.precomputePrefecture(data.game_id).catch(function () {});
      startPrefecturePrecomputePolling(data.game_id);
    }
  }

  function showPrefTab(tabId) {
    var tabs = document.querySelectorAll("#screen-prefecture .tab-content");
    var btns = document.querySelectorAll("#pref-tab-nav .tab-btn");
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].classList.toggle("active", tabs[i].id === tabId);
      tabs[i].classList.toggle("hidden", tabs[i].id !== tabId);
    }
    for (var j = 0; j < btns.length; j++) {
      btns[j].classList.toggle("active", btns[j].dataset.tab === tabId);
    }
    // Load data for specific tabs
    if (tabId === "pref-tab-counties") {
      var pg = Game.state.prefectureGame;
      if (pg) Game.prefecture.renderCounties(pg.counties || []);
    }
    if (tabId === "pref-tab-infra") {
      var pg2 = Game.state.prefectureGame;
      if (pg2) {
        Promise.all([
          api.getPrefectureInvestStatus(pg2.game_id),
          api.getPrefectureTalent(pg2.game_id),
        ]).then(function (results) {
          Game.prefecture.renderInfraPanel(results[0], results[1]);
        }).catch(function (err) {
          components.showToast(err.message || "加载公共事业信息失败", "error");
        });
      }
    }
    if (tabId === "pref-tab-personnel") {
      var pgPersonnel = Game.state.prefectureGame;
      if (pgPersonnel) {
        api.getPrefecturePersonnel(pgPersonnel.game_id)
          .then(function (data) {
            Game.prefecture.renderPersonnelTab(data);
          })
          .catch(function (err) {
            components.showToast(err.message || "加载人事评议失败", "error");
          });
      }
    }
    if (tabId === "pref-tab-judicial") {
      var pg3 = Game.state.prefectureGame;
      if (pg3) {
        api.getPrefectureJudicialCases(pg3.game_id)
          .then(function (data) {
            Game.prefecture.renderJudicialTab(data);
          })
          .catch(function (err) {
            components.showToast(err.message || "加载司法卷宗失败", "error");
          });
      }
    }
  }

  function refreshPrefectureTabVisibility(data) {
    var btn = el("pref-personnel-tab-btn");
    if (!btn) return;
    var available = !!(data && data.personnel_available);
    btn.classList.toggle("hidden", !available);
    if (!available && btn.classList.contains("active")) {
      showPrefTab("pref-tab-overview");
    }
  }

  // Pref tab nav
  var prefTabBtns = document.querySelectorAll("#pref-tab-nav .tab-btn");
  for (var pt = 0; pt < prefTabBtns.length; pt++) {
    prefTabBtns[pt].addEventListener("click", function () {
      showPrefTab(this.dataset.tab);
    });
  }

  el("btn-pref-back").addEventListener("click", function () {
    stopPrefecturePrecomputePolling();
    loadGameList();
  });

  // ── 司法系统 ──
  var _currentJudicialCase = null;   // 当前在模态框中展示的卷宗

  function _openJudicialCaseModal(caseData) {
    _currentJudicialCase = caseData;
    var badge = el("judicial-badge");
    badge.textContent = caseData.difficulty;
    badge.className = "judicial-badge difficulty-" +
      (caseData.difficulty === "新手" ? "easy" : caseData.difficulty === "进阶" ? "medium" : "hard");
    el("judicial-modal-title").textContent = "【" + caseData.category + "】" + caseData.case_name;
    el("judicial-modal-body").innerHTML = Game.prefecture.renderJudicialCase(caseData);
    el("judicial-result-area").classList.add("hidden");
    el("judicial-result-area").innerHTML = "";
    el("judicial-modal-footer").classList.remove("hidden");
    el("judicial-modal-nav").classList.add("hidden");
    ["btn-judicial-approve", "btn-judicial-retry", "btn-judicial-retrial"].forEach(function (id) {
      el(id).disabled = false;
    });
    el("pref-judicial-modal").classList.remove("hidden");
  }

  function _closeJudicialModal() {
    el("pref-judicial-modal").classList.add("hidden");
    _currentJudicialCase = null;
  }

  function _handleJudicialDecision(action) {
    var c = _currentJudicialCase;
    var pg = Game.state.prefectureGame;
    if (!c || !pg) return;
    ["btn-judicial-approve", "btn-judicial-retry", "btn-judicial-retrial"].forEach(function (id) {
      el(id).disabled = true;
    });
    api.decidePrefectureCase(pg.game_id, c.case_id, action)
      .then(function (result) {
        el("judicial-modal-footer").classList.add("hidden");
        var resArea = el("judicial-result-area");
        resArea.innerHTML = Game.prefecture.renderJudicialResult(result);
        resArea.classList.remove("hidden");
        el("judicial-modal-nav").classList.remove("hidden");
        // 刷新司法标签内容
        return api.getPrefectureJudicialCases(pg.game_id);
      })
      .then(function (data) {
        Game.prefecture.renderJudicialTab(data);
        // 刷新标签徽章
        return api.getPrefectureOverview(pg.game_id);
      })
      .then(function (data) {
        Game.state.prefectureGame = data;
        refreshPrefectureTabVisibility(data);
        Game.prefecture.renderOverview(data);
        _updateJudicialBadge(data.pending_judicial_count || 0);
      })
      .catch(function (err) {
        components.showToast(err.message || "决策失败", "error");
        ["btn-judicial-approve", "btn-judicial-retry", "btn-judicial-retrial"].forEach(function (id) {
          el(id).disabled = false;
        });
      });
  }

  el("btn-judicial-approve").addEventListener("click", function () { _handleJudicialDecision("核准原判"); });
  el("btn-judicial-retry").addEventListener("click",   function () { _handleJudicialDecision("驳回重审"); });
  el("btn-judicial-retrial").addEventListener("click", function () { _handleJudicialDecision("提审改判"); });
  el("btn-judicial-modal-close").addEventListener("click", _closeJudicialModal);
  el("btn-judicial-close-done").addEventListener("click", _closeJudicialModal);
  el("pref-judicial-modal").addEventListener("click", function (e) {
    if (e.target === this) _closeJudicialModal();
  });

  // 司法标签徽章更新
  function _updateJudicialBadge(count) {
    var badge = el("judicial-tab-badge");
    if (!badge) return;
    if (count > 0) {
      badge.textContent = count;
      badge.classList.remove("hidden");
    } else {
      badge.classList.add("hidden");
    }
  }

  // 委托事件：司法标签中"查阅卷宗"按钮
  document.addEventListener("click", function (e) {
    if (e.target.classList.contains("btn-open-judicial-case")) {
      var idx = parseInt(e.target.dataset.idx, 10);
      var pg = Game.state.prefectureGame;
      if (!pg) return;
      api.getPrefectureJudicialCases(pg.game_id).then(function (data) {
        var c = data.pending_cases[idx];
        if (c) _openJudicialCaseModal(c);
      });
    }
  });

  // 推进月份（知府）
  el("btn-pref-advance").addEventListener("click", function () {
    var pg = Game.state.prefectureGame;
    if (!pg) return;
    var btn = el("btn-pref-advance");
    btn.disabled = true;
    btn.textContent = "推进中...";
    stopPrefecturePrecomputePolling();
    api.advancePrefectureMonth(pg.game_id)
      .then(function (result) {
        Game.prefecture.renderReport(result);
        el("pref-advance-modal-title").textContent = Game.seasonName(result.season) + " 府政月报";
        el("pref-advance-modal-close").textContent = "关闭";
        el("pref-advance-modal").classList.remove("hidden");
        prependPrefLogEntry(result);
        return api.getPrefectureOverview(pg.game_id);
      })
      .then(function (data) {
        Game.state.prefectureGame = data;
        refreshPrefectureTabVisibility(data);
        Game.prefecture.renderOverview(data);
        _updateJudicialBadge(data.pending_judicial_count || 0);
        if (data.current_season > 36) {
          components.showToast("三年任期已满！", "info");
        } else {
          api.precomputePrefecture(data.game_id).catch(function () {});
          startPrefecturePrecomputePolling(data.game_id);
        }
      })
      .catch(function (err) {
        components.showToast(err.message || "推进失败", "error");
      })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = "推进月份";
      });
  });

  // 府政月报弹窗关闭
  el("pref-advance-modal-close").addEventListener("click", function () {
    el("pref-advance-modal").classList.add("hidden");
  });
  el("pref-advance-modal").addEventListener("click", function (e) {
    if (e.target === this) el("pref-advance-modal").classList.add("hidden");
  });
  el("pref-advance-view-log").addEventListener("click", function () {
    el("pref-advance-modal").classList.add("hidden");
    showPrefTab("pref-tab-log");
  });

  // 归档府志条目
  function prependPrefLogEntry(result) {
    var logContent = el("pref-log-content");
    if (!logContent) return;
    // 移除占位提示
    var placeholder = logContent.querySelector(".hint");
    if (placeholder) placeholder.remove();
    var entry = Game.prefecture.buildPrefLogEntry(result);
    logContent.insertBefore(entry, logContent.firstChild);
  }

  // Quota button (open modal)
  el("btn-pref-quota").addEventListener("click", function () {
    var pg = Game.state.prefectureGame;
    if (!pg) return;
    Game.prefecture.openQuotaModal(pg);
  });

  // Quota inputs live total
  document.addEventListener("input", function (e) {
    if (e.target.classList.contains("pref-quota-input")) {
      Game.prefecture.updateQuotaRunningTotal();
    }
  });

  // Quota modal close
  el("pref-quota-modal-close").addEventListener("click", function () {
    el("pref-quota-modal").classList.add("hidden");
  });

  // Quota confirm
  el("btn-pref-quota-confirm").addEventListener("click", function () {
    var pg = Game.state.prefectureGame;
    if (!pg) return;
    var assignments = Game.prefecture.collectQuotaAssignments();
    api.setPrefectureQuota(pg.game_id, assignments)
      .then(function (result) {
        components.showToast(result.message || "定额已分配", "success");
        el("pref-quota-modal").classList.add("hidden");
        return api.getPrefectureOverview(pg.game_id);
      })
      .then(function (data) {
        Game.state.prefectureGame = data;
        refreshPrefectureTabVisibility(data);
        Game.prefecture.renderOverview(data);
        api.precomputePrefecture(data.game_id).catch(function () {});
        startPrefecturePrecomputePolling(data.game_id);
      })
      .catch(function (err) {
        components.showToast(err.message || "分配失败", "error");
      });
  });

  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".pref-todo-action");
    if (!btn) return;
    var targetTab = btn.dataset.targetTab;
    if (!targetTab) return;
    showPrefTab(targetTab);
  });

  // County table detail button click
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".btn-county-detail");
    if (!btn) return;
    var unitId = parseInt(btn.dataset.unitId);
    var pg = Game.state.prefectureGame;
    if (!pg || !unitId) return;
    api.getPrefectureCountyDetail(pg.game_id, unitId)
      .then(function (detail) {
        Game.prefecture.openCountyDetail(detail);
      })
      .catch(function (err) {
        components.showToast(err.message || "加载县详情失败", "error");
      });
  });

  // Unified county detail modal close
  el("cdc-close").addEventListener("click", function () {
    el("county-detail-modal").classList.add("hidden");
  });

  // Inspect buttons (tongpan / tuiguan)
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".btn-inspect-tongpan, .btn-inspect-tuiguan");
    if (!btn) return;
    var unitId = parseInt(btn.dataset.unitId);
    var pg = Game.state.prefectureGame;
    if (!pg || !unitId) return;
    var inspectType = btn.classList.contains("btn-inspect-tongpan") ? "tongpan" : "tuiguan";
    var oldText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "核查中...";
    api.inspectPrefectureCounty(pg.game_id, unitId, inspectType)
      .then(function (result) {
        Game.prefecture.showInspectResult(result);
      })
      .catch(function (err) {
        components.showToast(err.message || "核查失败", "error");
      })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = oldText;
      });
  });

  // Infra invest button
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".pref-infra-btn");
    if (!btn || btn.disabled) return;
    var project = btn.dataset.project;
    var level = parseInt(btn.dataset.level);
    var pg = Game.state.prefectureGame;
    if (!pg || !project || !level) return;
    btn.disabled = true;
    var oldText = btn.textContent;
    btn.textContent = "投资中...";
    api.investPrefecture(pg.game_id, project, level)
      .then(function (result) {
        components.showToast(result.message || "投资已启动", "success");
        return Promise.all([
          api.getPrefectureInvestStatus(pg.game_id),
          api.getPrefectureTalent(pg.game_id),
        ]);
      })
      .then(function (results) {
        Game.prefecture.renderInfraPanel(results[0], results[1]);
        return api.getPrefectureOverview(pg.game_id);
      })
      .then(function (data) {
        Game.state.prefectureGame = data;
        refreshPrefectureTabVisibility(data);
        Game.prefecture.renderOverview(data);
        api.precomputePrefecture(data.game_id).catch(function () {});
        startPrefecturePrecomputePolling(data.game_id);
      })
      .catch(function (err) {
        components.showToast(err.message || "投资失败", "error");
        btn.disabled = false;
        btn.textContent = oldText;
      });
  });

  // 公共事业 — 府学才池/府试折叠展开
  document.addEventListener("click", function (e) {
    var toggle = e.target.closest(".pref-school-toggle");
    if (!toggle) return;
    var targetId = toggle.dataset.target;
    var body = document.getElementById(targetId);
    if (!body) return;
    var isHidden = body.classList.toggle("hidden");
    var arrow = toggle.querySelector(".pref-school-arrow");
    if (arrow) arrow.style.transform = isHidden ? "" : "rotate(90deg)";
  });

  // Directive button (open directive modal from county detail)
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".btn-directive");
    if (!btn) return;
    var unitId = parseInt(btn.dataset.unitId);
    var countyName = btn.dataset.countyName || "该县";
    Game.prefecture.openDirectiveModal(unitId, countyName);
  });

  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".btn-pref-personnel-save");
    if (!btn) return;
    var pg = Game.state.prefectureGame;
    if (!pg) return;
    var unitId = parseInt(btn.dataset.unitId, 10);
    if (!unitId) return;
    var gradeEl = document.querySelector('.pref-review-grade[data-unit-id="' + unitId + '"]');
    var strengthsEl = document.querySelector('.pref-review-strengths[data-unit-id="' + unitId + '"]');
    var weaknessesEl = document.querySelector('.pref-review-weaknesses[data-unit-id="' + unitId + '"]');
    var focusEl = document.querySelector('.pref-review-focus[data-unit-id="' + unitId + '"]');
    if (!gradeEl || !strengthsEl || !weaknessesEl || !focusEl) return;

    btn.disabled = true;
    api.submitPrefecturePersonnelReview(pg.game_id, {
      unit_id: unitId,
      grade: gradeEl.value,
      strengths: strengthsEl.value.trim(),
      weaknesses: weaknessesEl.value.trim(),
      focus: focusEl.value.trim()
    })
      .then(function () {
        components.showToast("年度评议已保存", "success");
        return Promise.all([
          api.getPrefecturePersonnel(pg.game_id),
          api.getPrefectureOverview(pg.game_id)
        ]);
      })
      .then(function (results) {
        Game.prefecture.renderPersonnelTab(results[0]);
        Game.state.prefectureGame = results[1];
        refreshPrefectureTabVisibility(results[1]);
        Game.prefecture.renderOverview(results[1]);
      })
      .catch(function (err) {
        components.showToast(err.message || "保存评议失败", "error");
      })
      .finally(function () {
        btn.disabled = false;
      });
  });

  // Directive modal close / cancel
  el("btn-pref-directive-cancel").addEventListener("click", function () {
    el("pref-directive-modal").classList.add("hidden");
  });
  el("pref-directive-modal").addEventListener("click", function (e) {
    if (e.target === this) this.classList.add("hidden");
  });

  // Directive send
  el("btn-pref-directive-send").addEventListener("click", function () {
    var pg = Game.state.prefectureGame;
    if (!pg) return;
    var modal = el("pref-directive-modal");
    var unitId = parseInt(modal.dataset.unitId);
    var directive = el("pref-directive-input").value.trim();
    if (!directive) {
      components.showToast("请输入指令内容", "error");
      return;
    }
    var btn = this;
    btn.disabled = true;
    api.sendPrefectureDirective(pg.game_id, unitId, directive)
      .then(function (result) {
        components.showToast(result.response || "指令已下达", "success");
        el("pref-directive-modal").classList.add("hidden");
        return api.precomputePrefecture(pg.game_id).catch(function () {});
      })
      .then(function () {
        startPrefecturePrecomputePolling(pg.game_id);
      })
      .catch(function (err) {
        components.showToast(err.message || "下达失败", "error");
      })
      .finally(function () {
        btn.disabled = false;
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

  // ==================== Disaster Relief ====================
  el("btn-fill-relief-suggestion").addEventListener("click", function () {
    var g = Game.state.currentGame;
    if (!g) return;
    var advice = g.disaster_relief_advice || {};
    if (!advice.available || advice.suggested_claim === undefined) {
      components.showToast("当前无可用县丞建议", "info");
      return;
    }
    el("relief-claimed-loss").value = advice.suggested_claim;
    components.showToast("已填入县丞建议值", "info");
  });

  el("btn-submit-relief").addEventListener("click", function () {
    var g = Game.state.currentGame;
    if (!g) return;

    var claimedLoss = parseFloat(el("relief-claimed-loss").value);
    if (!(claimedLoss > 0)) {
      components.showToast("请填写有效的申报减免额度（两）", "error");
      return;
    }

    api.applyDisasterRelief(g.id, claimedLoss)
      .then(function (data) {
        components.showToast(data.message || "已提交灾害减免申请", "success");
        return api.getGame(g.id);
      })
      .then(function (data) {
        Game.setGame(data);
      })
      .catch(function (err) {
        components.showToast(err.message || "减免申请失败", "error");
      });
  });

  document.addEventListener("click", function (e) {
    if (e.target.id !== "btn-submit-annual-review") return;
    var g = Game.state.currentGame;
    if (!g) return;
    api.submitAnnualReview(g.id, {
      achievements: el("annual-review-achievements") ? el("annual-review-achievements").value.trim() : "",
      unfinished: el("annual-review-unfinished") ? el("annual-review-unfinished").value.trim() : "",
      faults: el("annual-review-faults") ? el("annual-review-faults").value.trim() : "",
      plan: el("annual-review-plan") ? el("annual-review-plan").value.trim() : ""
    })
      .then(function () {
        components.showToast("年度自陈已提交", "success");
        return api.getGame(g.id);
      })
      .then(function (data) {
        Game.setGame(data);
      })
      .catch(function (err) {
        components.showToast(err.message || "提交年度自陈失败", "error");
      });
  });

  function refreshCurrentGame(gameId) {
    return api.getGame(gameId).then(function (data) {
      Game.setGame(data);
      return data;
    });
  }

  // ==================== Emergency Grain Actions ====================
  el("btn-emergency-prefecture").addEventListener("click", function () {
    var g = Game.state.currentGame;
    if (!g) return;
    api.emergencyPrefectureRelief(g.id)
      .then(function (data) {
        components.showToast(data.message || "已提交拨粮请求", "success");
        return refreshCurrentGame(g.id);
      })
      .catch(function (err) {
        components.showToast(err.message || "拨粮请求失败", "error");
      });
  });

  el("btn-emergency-borrow").addEventListener("click", function () {
    var g = Game.state.currentGame;
    if (!g) return;
    var neighborId = parseInt(el("emergency-neighbor-select").value, 10);
    var amount = parseFloat(el("emergency-borrow-amount").value);
    if (!neighborId) {
      components.showToast("请先选择邻县", "error");
      return;
    }
    if (!(amount > 0)) {
      components.showToast("请填写有效借粮数量", "error");
      return;
    }
    api.emergencyBorrowNeighbor(g.id, neighborId, amount)
      .then(function (data) {
        components.showToast(data.message || "借粮成功", "success");
        return refreshCurrentGame(g.id);
      })
      .catch(function (err) {
        components.showToast(err.message || "借粮失败", "error");
      });
  });

  el("btn-emergency-gentry").addEventListener("click", function () {
    var g = Game.state.currentGame;
    if (!g) return;
    var amount = parseFloat(el("emergency-gentry-amount").value);
    if (!(amount > 0)) {
      components.showToast("请填写有效放粮协商数量", "error");
      return;
    }
    api.emergencyGentryRelief(g.id, amount)
      .then(function (data) {
        components.showToast(data.message || "已完成地主放粮协商", "success");
        return refreshCurrentGame(g.id);
      })
      .catch(function (err) {
        components.showToast(err.message || "协商失败", "error");
      });
  });

  el("btn-emergency-force").addEventListener("click", function () {
    var g = Game.state.currentGame;
    if (!g) return;
    var amount = parseFloat(el("emergency-force-amount").value);
    if (!(amount > 0)) {
      components.showToast("请填写有效强制征调数量", "error");
      return;
    }
    api.emergencyForceLevy(g.id, amount)
      .then(function (data) {
        var breakdown = data.levy_breakdown || [];
        if (breakdown.length > 0) {
          var lines = ["本次强征明细："];
          breakdown.forEach(function (item) {
            var gentryName = item.gentry_name || "地主";
            var villageName = item.village_name || "未知村";
            var taken = Number(item.taken || 0);
            var remaining = Number(item.remaining || 0);
            lines.push(
              gentryName + "（" + villageName + "）：强征" +
              taken.toFixed(1).replace(/\.0$/, "") + "斤，剩余" +
              remaining.toFixed(1).replace(/\.0$/, "") + "斤"
            );
          });
          window.alert(lines.join("\n"));
        }
        components.showToast(data.message || "已执行强制征调", "success");
        return refreshCurrentGame(g.id);
      })
      .catch(function (err) {
        components.showToast(err.message || "强制征调失败", "error");
      });
  });

  el("emergency-debug-toggle").addEventListener("change", function () {
    var g = Game.state.currentGame;
    if (!g) return;
    var enabled = !!this.checked;
    api.emergencySetDebugReveal(g.id, enabled)
      .then(function (data) {
        components.showToast(data.message || "设置已更新", "info");
        return refreshCurrentGame(g.id);
      })
      .catch(function (err) {
        components.showToast(err.message || "调试开关更新失败", "error");
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
    if (statusEl) statusEl.textContent = "";

    _precomputeTimer = setInterval(function () {
      api.getPrecomputeStatus(gameId)
        .then(function (data) {
          if (data.status === "done") {
            stopPrecomputePolling();
            if (statusEl) statusEl.textContent = "各邻县施政决策已就绪";
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

  var _prefPrecomputeTimer = null;
  function startPrefecturePrecomputePolling(gameId) {
    stopPrefecturePrecomputePolling();
    var statusEl = el("pref-precompute-status");
    if (statusEl) statusEl.textContent = "下辖州县施政预推演中...";

    _prefPrecomputeTimer = setInterval(function () {
      api.getPrefecturePrecomputeStatus(gameId)
        .then(function (data) {
          if (data.status === "done") {
            stopPrefecturePrecomputePolling();
            if (statusEl) statusEl.textContent = "下辖州县施政决策已就绪";
          } else if (data.status === "idle") {
            stopPrefecturePrecomputePolling();
            if (statusEl) statusEl.textContent = "";
          }
        })
        .catch(function () {
          stopPrefecturePrecomputePolling();
          if (statusEl) statusEl.textContent = "";
        });
    }, 3000);
  }

  function stopPrefecturePrecomputePolling() {
    if (_prefPrecomputeTimer) {
      clearInterval(_prefPrecomputeTimer);
      _prefPrecomputeTimer = null;
    }
  }

  // ==================== 暴动危机弹窗处理 ====================

  function _checkShowRiotModal() {
    var report = Game.state.lastReport;
    if (!report || !report.events) return;
    var riotEvt = null;
    for (var i = 0; i < report.events.length; i++) {
      var e = report.events[i];
      if (e.indexOf("【连锁暴动】") !== -1 || e.indexOf("【农民暴动】") !== -1) {
        riotEvt = e;
        break;
      }
    }
    if (!riotEvt) return;

    var isChain = riotEvt.indexOf("【连锁暴动】") !== -1;
    var body = riotEvt.replace(/^【[^】]+】/, "").trim();
    el("riot-crisis-title").textContent = isChain ? "连锁暴动" : "农民暴动";
    el("riot-crisis-body").textContent = body ||
      (isChain ? "周边县骚乱蔓延，本县民众冲击粮仓，治安归零。" : "连续两月余粮为负，农民强占地主粮仓，治安归零。");
    el("riot-crisis-modal").classList.remove("hidden");
  }

  el("btn-riot-crisis-close").addEventListener("click", function () {
    el("riot-crisis-modal").classList.add("hidden");
  });
  el("riot-crisis-modal").addEventListener("click", function (e) {
    if (e.target === this) this.classList.add("hidden");
  });

  // ==================== 贿赂弹窗处理 ====================

  var _pendingBribes = [];
  var _bribeAdvanceCallback = null;

  function showNextBribe() {
    if (_pendingBribes.length === 0) {
      if (_bribeAdvanceCallback) {
        var cb = _bribeAdvanceCallback;
        _bribeAdvanceCallback = null;
        cb();
      }
      return;
    }
    var bribe = _pendingBribes.shift();
    el("bribe-prompt").textContent = bribe.prompt;
    el("bribe-amount").textContent = "金额：" + bribe.amount + " 两";
    el("bribe-modal").classList.remove("hidden");

    el("btn-bribe-accept").onclick = function () {
      el("bribe-modal").classList.add("hidden");
      api.respondBribe(Game.state.currentGame.id, bribe.village_name, bribe.event_type, true)
        .then(function (res) {
          if (res.personal_wealth !== undefined && Game.state.currentGame && Game.state.currentGame.player) {
            Game.state.currentGame.player.personal_wealth = res.personal_wealth;
            Game.components.renderDashboard();
          }
          showNextBribe();
        })
        .catch(function () { showNextBribe(); });
    };
    el("btn-bribe-refuse").onclick = function () {
      el("bribe-modal").classList.add("hidden");
      api.respondBribe(Game.state.currentGame.id, bribe.village_name, bribe.event_type, false)
        .catch(function () {})
        .finally(function () { showNextBribe(); });
    };
  }

  function doAdvance(g, btn) {
    btn.disabled = true;
    btn.textContent = "推进中...";
    stopPrecomputePolling();

    api.advance(g.id)
      .then(function (report) {
        Game.state.lastReport = report;
        components.renderReport(report);
        el("advance-result-title").textContent = Game.seasonName(report.season) + " 施政月报";
        el("advance-result-modal").classList.remove("hidden");
        prependCountyLogEntry(report);

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
        } else if (report.term_complete) {
          return api.getGame(g.id).then(function (data) {
            Game.setGame(data);
            btn.disabled = false;
            btn.textContent = "推进月份";
            showTermCompleteModal(report.term_summary || {});
          });
        } else {
          return api.getGame(g.id).then(function (data) {
            Game.setGame(data);
            btn.disabled = false;
            btn.textContent = "推进月份";
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
  }

  el("btn-advance").addEventListener("click", function () {
    var g = Game.state.currentGame;
    if (!g) return;
    if (g.current_season > Game.MAX_MONTH) {
      // 任期届满但尚未续任 — 弹出续任弹窗
      showTermCompleteFromSaved(g);
      return;
    }

    var btn = el("btn-advance");
    btn.disabled = true;
    btn.textContent = "检查中...";
    stopPrecomputePolling();

    api.checkBribes(g.id)
      .then(function (res) {
        _pendingBribes = res.offers || [];
        if (_pendingBribes.length > 0) {
          btn.disabled = false;
          btn.textContent = "推进月份";
          _bribeAdvanceCallback = function () { doAdvance(g, btn); };
          showNextBribe();
        } else {
          doAdvance(g, btn);
        }
      })
      .catch(function () {
        // checkBribes failed — proceed without bribe check
        doAdvance(g, btn);
      });

  });

  // 月报弹窗关闭
  el("advance-result-close").addEventListener("click", function () {
    el("advance-result-modal").classList.add("hidden");
    _checkShowRiotModal();
  });
  el("advance-result-modal").addEventListener("click", function (e) {
    if (e.target === this) {
      this.classList.add("hidden");
      _checkShowRiotModal();
    }
  });
  el("advance-result-view-log").addEventListener("click", function () {
    el("advance-result-modal").classList.add("hidden");
    screens.showTab("tab-events");
  });

  // 归档县志月报条目
  function prependCountyLogEntry(report) {
    var logList = el("monthly-reports-log");
    if (!logList) return;
    var resultBody = el("advance-result-body");
    var bodyHtml = resultBody ? resultBody.innerHTML : "";
    var title = Game.seasonName(report.season) + " 月报";
    var eventCount = (report.events || []).length;
    var summary = eventCount ? "，" + eventCount + "件事件" : "，无特殊事件";
    var entry = document.createElement("div");
    entry.className = "monthly-log-entry";
    entry.innerHTML =
      '<div class="monthly-log-toggle">' +
        '<span class="monthly-log-title">' + components.escapeHtml(title) + summary + '</span>' +
        '<span class="monthly-log-arrow">&#9654;</span>' +
      '</div>' +
      '<div class="monthly-log-body">' + bodyHtml + '</div>';
    entry.querySelector(".monthly-log-toggle").addEventListener("click", function () {
      entry.classList.toggle("expanded");
    });
    logList.insertBefore(entry, logList.firstChild);
  }

  // ==================== Negotiation ====================

  function randomPick(items) {
    if (!items || items.length === 0) return "";
    return items[Math.floor(Math.random() * items.length)];
  }

  function buildDelegationMessage(session, speakerRole) {
    var type = (session && session.event_type) || "ANNEXATION";
    var context = (session && session.context_data) || {};
    var villageName = context.village_name || "";
    var targetAgentName = (session && session.agent_name) || "阁下";
    var placeName = villageName ? (villageName + "庄") : "贵庄";
    var roleSelf = speakerRole === "ADVISOR" ? "本师爷" : "本县丞";

    var opening = randomPick([
      roleSelf + "奉县尊之命前来会商",
      roleSelf + "受县衙所托，特来当面商议",
      "县衙已命" + roleSelf + "与你就此事直陈利害",
    ]);

    if (speakerRole === "ADVISOR") {
      if (type === "HIDDEN_LAND") {
        return opening + "。"
          + placeName + "地主" + targetAgentName
          + "，隐田之事请据实申报，县衙可依法酌情从宽；若再隐匿，必依章程清丈。";
      }
      if (type === "IRRIGATION") {
        return opening + "。"
          + placeName + "水利关乎长久收成，若贵户愿分担工费，县衙将据实记功并优先保障工程推进。";
      }
      return opening + "。"
        + placeName + "兼并一事已引发乡里不安，请贵户先行止步，县衙愿与你议定后续处置与田亩秩序。";
    }
    if (speakerRole === "DEPUTY") {
      if (type === "HIDDEN_LAND") {
        return opening + "。"
          + placeName + "地主" + targetAgentName
          + "，请按衙门文簿即刻补报隐田；若拒不申报，县衙将立案清丈并追责。";
      }
      if (type === "IRRIGATION") {
        return opening + "。"
          + "水利工程已列入公文流程，请贵户就分担银两给出明示，以便县衙尽速定工开建。";
      }
      return opening + "。"
        + placeName + "兼并争议需即刻止息，请贵户先停手，若执意扩并，县衙将依律处置并公开示谕。";
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
      showText = message;
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

  // Negotiation count link → switch to 人脉 tab and scroll to first negotiating agent
  document.addEventListener("click", function (e) {
    var link = e.target.closest("#nego-count-goto");
    if (!link) return;
    e.preventDefault();
    screens.showTab("tab-relationships");
    // After tab renders, scroll to first agent with active negotiation
    setTimeout(function () {
      var negotiations = Game.state.activeNegotiations || [];
      if (negotiations.length > 0) {
        var cardEl = document.getElementById("agent-card-" + negotiations[0].agent_name);
        if (cardEl) cardEl.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }, 100);
  });

  // Enter negotiation from agent card badge
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".btn-nego-enter");
    if (!btn) return;
    var sessionId = parseInt(btn.dataset.sessionId);
    var negotiations = Game.state.activeNegotiations || [];
    for (var i = 0; i < negotiations.length; i++) {
      if (negotiations[i].id === sessionId) {
        Game.state.activeNegotiation = negotiations[i];
        components.openNegotiationModal(negotiations[i]);
        syncNegotiationInputMode();
        return;
      }
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
    } else if (tabId === "tab-officialdom") {
      loadOfficialdom();
    }
  };

  function loadOfficialdom() {
    var g = Game.state.currentGame;
    if (!g) return;
    // 绑定子 tab 按钮（只绑定一次）
    var subtabBtns = document.querySelectorAll(".offdom-subtab-btn");
    if (subtabBtns.length && !subtabBtns[0]._offdomBound) {
      subtabBtns.forEach(function (btn) {
        btn._offdomBound = true;
        btn.addEventListener("click", function () {
          showOffdomSubtab(this.dataset.subtab);
        });
      });
    }
    // 默认加载全国行政体系
    if (Game.officialdom && !Game.officialdom.loaded) {
      Game.officialdom.load(g.id);
    }
  }

  // ── 升迁事件交互 ────────────────────────────────────────────────────────
  window.handlePromotionAdvisor = function () {
    var g = Game.state.currentGame;
    if (!g) return;
    Game.api.promotionAction(g.id, {sub_action: "reveal_advisor"}).then(function (data) {
      if (data.error) { components.showToast(data.error, "error"); return; }
      Game.officialdom.refreshCareer(g.id);
    }).catch(function (err) {
      components.showToast(err.message || "操作失败", "error");
    });
  };

  window.handlePromotionAction = function (actionType) {
    var g = Game.state.currentGame;
    if (!g) return;
    var label = {gift_governor:"送礼巡抚", gift_ministry:"送礼吏部", gift_both:"两路疏通", none:"不送礼"}[actionType] || actionType;
    if (!confirm("确认行动：" + label + "？")) return;
    Game.api.promotionAction(g.id, {action_type: actionType}).then(function (data) {
      if (data.error) { components.showToast(data.error, "error"); return; }
      if (data.gift_cost_paid > 0) {
        components.showToast("已花费" + data.gift_cost_paid + "两，当前家产" + (data.personal_wealth || 0) + "两", "success");
      }
      Game.officialdom.refreshCareer(g.id);
    }).catch(function (err) {
      components.showToast(err.message || "操作失败", "error");
    });
  };

  window.handleStartPrefectGame = function () {
    // 升迁成功后引导玩家开启知府新局
    var g = Game.state.currentGame;
    if (!g) return;
    if (confirm("即将开启知府新局，你将以升迁后的品级出任知府。确认就任？")) {
      // 跳转到创建知府游戏流程（复用现有 startPrefectureGame 入口）
      if (typeof startPrefectureGame === "function") {
        startPrefectureGame();
      } else {
        components.showToast("请返回主界面选择「开始新局 — 知府」", "info");
      }
    }
  };

  // ── 任期届满弹窗 ─────────────────────────────────────────────────────────
  function showTermCompleteModal(summary) {
    var modal = el("term-complete-modal");

    // 评级徽章
    var gradeBadge = el("term-complete-grade-badge");
    var gradeText = summary.final_grade || "";
    gradeBadge.textContent = gradeText || "—";
    gradeBadge.className = "grade-badge grade-" + ({"优":"A","良":"B","中":"C","差":"D"}[gradeText] || "D");

    // 正文
    var body = el("term-complete-body");
    var poolLabel = summary.pool_label || "";
    body.innerHTML = "第 <strong>" + (summary.term_index || 1) + "</strong> 任任期届满。" +
      (poolLabel ? "候选池状态：<strong>" + poolLabel + "</strong>。" : "");

    // 调任预览
    var transferEl = el("term-complete-transfer");
    var tp = summary.transfer_preview;
    if (tp) {
      transferEl.classList.remove("hidden");
      transferEl.innerHTML = "<div class='term-complete-transfer-title'>预计调任</div>" +
        "<span>" + tp.county_name + "（现任：" + tp.governor_name + "，人口约 " + tp.pop + "）</span>";
    } else {
      transferEl.classList.add("hidden");
    }

    // 统计
    var statsEl = el("term-complete-stats");
    var stats = [
      {label: "民心", val: summary.morale},
      {label: "治安", val: summary.security},
      {label: "商业", val: summary.commercial},
      {label: "文教", val: summary.education},
      {label: "县库", val: summary.treasury},
    ];
    statsEl.innerHTML = stats.map(function (s) {
      return "<div class='term-complete-stat'>" +
        "<div class='term-complete-stat-label'>" + s.label + "</div>" +
        "<div class='term-complete-stat-val'>" + (s.val != null ? s.val : "—") + "</div>" +
        "</div>";
    }).join("");

    // 口号
    el("term-complete-flavor").textContent = summary.flavor || "";

    modal.classList.remove("hidden");
  }

  function showTermCompleteFromSaved(data) {
    // 从已保存的游戏状态构建简版 summary 供弹窗显示
    var county = data.county_data || {};
    var track = county.career_track || {};
    var reviews = county.annual_reviews || [];
    var lastReview = reviews[reviews.length - 1] || {};
    var poolLevel = track.candidate_pool_level || 0;
    var poolLabels = {0:"未入候选池", 1:"初步候选池", 2:"二阶候选池", 3:"三阶候选池"};
    var flavorMap = {
      0: "考评欠佳，仍需历练，留任原职第{n}任。",
      1: "初露锋芒，仍须磨砺，留任原职第{n}任。",
      2: "政绩卓著，调任本府他县，续任第{n}任知县。",
      3: "已具知府候选资格，留任候缺，续任第{n}任。",
    };
    var termIndex = track.term_index || 1;
    var flavor = (flavorMap[poolLevel] || "任期届满，续任第{n}任。").replace("{n}", termIndex + 1);
    showTermCompleteModal({
      term_index: termIndex,
      final_grade: lastReview.final_grade || "",
      pool_level: poolLevel,
      pool_label: poolLabels[poolLevel] || "",
      rank: track.rank || "七品",
      flavor: flavor,
      transfer_preview: null,
      morale: county.morale != null ? Math.round(county.morale * 10) / 10 : null,
      security: county.security != null ? Math.round(county.security * 10) / 10 : null,
      commercial: county.commercial != null ? Math.round(county.commercial * 10) / 10 : null,
      education: county.education != null ? Math.round(county.education * 10) / 10 : null,
      treasury: county.treasury != null ? Math.round(county.treasury * 10) / 10 : null,
    });
  }

  el("btn-start-new-term").addEventListener("click", function () {
    var g = Game.state.currentGame;
    if (!g) return;
    var btn = this;
    btn.disabled = true;
    btn.textContent = "处理中...";
    api.startNewTerm(g.id)
      .then(function (res) {
        el("term-complete-modal").classList.add("hidden");
        if (res.error) {
          components.showToast(res.error, "error");
          btn.disabled = false;
          btn.textContent = "开始新任期";
          return;
        }
        Game.setGame(res.game);
        btn.disabled = false;
        btn.textContent = "开始新任期";
        var msg = "已开始第 " + res.term_index + " 任";
        if (res.transfer_info && res.transfer_info.new_county_name) {
          msg += "，调任 " + res.transfer_info.new_county_name;
        }
        components.showToast(msg, "success");
        screens.showTab("tab-dashboard");
        api.precomputeNeighbors(g.id).catch(function () {});
        startPrecomputePolling(g.id);
        // Refresh career panel if it was loaded
        if (Game.officialdom) Game.officialdom.careerLoaded = false;
      })
      .catch(function (err) {
        components.showToast(err.message || "续任失败", "error");
        btn.disabled = false;
        btn.textContent = "开始新任期";
      });
  });

  el("term-complete-modal").addEventListener("click", function (e) {
    // Do not close on backdrop click — player must press the button
  });

  function showOffdomSubtab(subtabId) {
    var panels = ["offdom-national", "offdom-career"];
    panels.forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.classList.toggle("hidden", id !== subtabId);
    });
    document.querySelectorAll(".offdom-subtab-btn").forEach(function (btn) {
      btn.classList.toggle("active", btn.dataset.subtab === subtabId);
    });
    if (subtabId === "offdom-career") {
      var g = Game.state.currentGame;
      if (g && Game.officialdom && !Game.officialdom.careerLoaded) {
        Game.officialdom.loadCareer(g.id);
      }
    }
  }

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
