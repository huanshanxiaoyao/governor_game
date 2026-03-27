/**
 * 幕僚室 — 对话施政 & 自创施政
 * Game.counsel namespace
 */
(function () {
  "use strict";

  var api = Game.api;
  var C = Game.components;
  var escapeHtml = C.escapeHtml;

  // ── 会话状态 ──────────────────────────────────────────────
  var _history = [];          // LLM 对话历史
  var _sending = false;
  var _currentGameId = null;
  var _notificationsShown = false;
  var _proactiveShown = false;
  var _proposedInSession = {};  // 本次会话已提报的构想名 → true（防重复）
  var _sidebarOpen = false;
  var _hasBadge = false;

  function el(id) { return document.getElementById(id); }

  function _keepLatestMessageVisible() {
    var box = el("counsel-messages");
    if (!box) return;
    window.requestAnimationFrame(function () {
      box.scrollTop = box.scrollHeight;
    });
  }

  // ── Badge ─────────────────────────────────────────────────

  function _setBadge(on) {
    _hasBadge = on;
    var sidebarBadge = el("counsel-sidebar-badge");
    var entryBadge   = el("counsel-entry-badge");
    if (sidebarBadge) sidebarBadge.classList.toggle("hidden", !on);
    if (entryBadge)   entryBadge.classList.toggle("hidden", !on);
  }

  // ── Sidebar ───────────────────────────────────────────────

  function _setSidebar(open) {
    _sidebarOpen = open;
    var sidebar = el("counsel-sidebar");
    var btn     = el("counsel-sidebar-toggle");
    if (sidebar) sidebar.classList.toggle("open", open);
    if (btn)     btn.classList.toggle("active", open);
    if (open) _setBadge(false);  // 打开即消红点
  }

  function toggleSidebar() {
    _setSidebar(!_sidebarOpen);
  }

  // ── 消息渲染 ──────────────────────────────────────────────

  var NPC_ICONS = { shiye: "📜", xiancheng: "⚖" };

  function _appendMessage(type, speakerKey, speakerName, text) {
    var box = el("counsel-messages");
    if (!box) return;
    var div = document.createElement("div");

    if (type === "system") {
      div.className = "counsel-msg counsel-msg-system";
      div.innerHTML = '<div class="counsel-system-text">' + escapeHtml(text) + '</div>';

    } else if (type === "user") {
      div.className = "counsel-msg counsel-msg-user";
      div.innerHTML =
        '<div class="counsel-msg-inner">' +
          '<div class="counsel-msg-bubble counsel-bubble-user">' + escapeHtml(text) + '</div>' +
          '<div class="counsel-msg-label">知县</div>' +
        '</div>';

    } else {
      var icon = NPC_ICONS[speakerKey] || "💬";
      div.className = "counsel-msg counsel-msg-npc";
      div.innerHTML =
        '<div class="counsel-msg-avatar">' + icon + '</div>' +
        '<div class="counsel-msg-inner">' +
          '<div class="counsel-msg-label">' + escapeHtml(speakerName || speakerKey) + '</div>' +
          '<div class="counsel-msg-bubble counsel-bubble-npc">' + escapeHtml(text) + '</div>' +
        '</div>';
    }

    box.appendChild(div);
    _keepLatestMessageVisible();
  }

  // ── 自动选村 ──────────────────────────────────────────────

  function _autoSelectVillage(action) {
    var g = Game.state.currentGame;
    var villages = ((g || {}).county_data || {}).villages || [];

    if (action === "fund_village_school") {
      var activeInvs = ((g || {}).county_data || {}).active_investments || [];
      var buildingSchoolVillages = activeInvs
        .filter(function (inv) { return inv.action === "fund_village_school"; })
        .map(function (inv) { return inv.target_village; });
      var eligible = villages.filter(function (v) {
        return !v.has_school && buildingSchoolVillages.indexOf(v.name) === -1;
      });
      if (!eligible.length) return null;
      eligible.sort(function (a, b) { return b.population - a.population; });
      return eligible[0].name;
    }

    if (action === "reclaim_land") {
      var investments = (g || {}).available_investments || [];
      var reclaimDef = null;
      for (var i = 0; i < investments.length; i++) {
        if (investments[i].action === "reclaim_land") { reclaimDef = investments[i]; break; }
      }
      var blocked  = (reclaimDef && reclaimDef.blocked_villages)  || [];
      var warnVils = ((reclaimDef && reclaimDef.village_warnings) || [])
        .map(function (w) { return w.village; });

      // 优先：非阻塞 且 不触发过度开垦警告
      var safe = villages.filter(function (v) {
        return blocked.indexOf(v.name) === -1 && warnVils.indexOf(v.name) === -1;
      });
      if (safe.length) {
        safe.sort(function (a, b) { return b.population - a.population; });
        return safe[0].name;
      }
      // 退而求其次：非阻塞（会触发警告，但不超限）
      var nonBlocked = villages.filter(function (v) { return blocked.indexOf(v.name) === -1; });
      if (nonBlocked.length) {
        nonBlocked.sort(function (a, b) { return b.population - a.population; });
        return nonBlocked[0].name;
      }
      return null; // 全部超限，不执行
    }

    return null;
  }

  // ── 执行施政卡片 ──────────────────────────────────────────

  function _executeCard(cardEl, grid, area, onCardsChanged) {
    var action  = cardEl.dataset.action;
    var name    = cardEl.dataset.name;
    var gameId  = parseInt(cardEl.dataset.gameId, 10);
    var needsVillage = cardEl.dataset.needsVillage === "1";
    var village = null;

    if (needsVillage) {
      village = _autoSelectVillage(action);
      if (!village) {
        _appendMessage("system", null, null,
          "❌ 「" + name + "」暂无符合条件的村庄，无法执行。");
        return;
      }
    }

    // 执行中：禁用卡片防重复点击
    cardEl.style.opacity = "0.5";
    cardEl.style.pointerEvents = "none";

    api.invest(gameId, action, village)
      .then(function (data) {
        var label = name + (village ? "（" + village + "）" : "");
        _appendMessage("system", null, null,
          "✅ 已下令「" + label + "」。" + (data.message || ""));
        // 将执行记录注入 LLM 历史，避免后续对话重复建议同一施政
        _history.push({ role: "user", content: "（已执行施政：" + label + "）" });
        cardEl.remove();
        if (onCardsChanged) onCardsChanged();
        // 刷新库银 / 在建项目等
        return api.getGame(gameId).then(function (updated) {
          Game.setGame(updated);
        });
      })
      .catch(function (err) {
        _appendMessage("system", null, null,
          "❌ 「" + name + "」执行失败：" + (err.message || "未知错误"));
        cardEl.style.opacity = "";
        cardEl.style.pointerEvents = "";
      });
  }

  // ── 建议施政卡片 ──────────────────────────────────────────

  function _renderActionCards(cards, gameId) {
    var area = el("counsel-action-cards");
    if (!area) return;
    if (!cards || !cards.length) {
      area.classList.add("hidden");
      area.innerHTML = "";
      return;
    }
    area.classList.remove("hidden");
    var head = document.createElement("div");
    head.className = "counsel-cards-head";

    var label = document.createElement("div");
    label.className = "counsel-cards-label";

    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "counsel-cards-toggle";

    head.appendChild(label);
    head.appendChild(toggle);

    var grid = document.createElement("div");
    grid.className = "counsel-cards-grid";

    function _syncCardsState() {
      var count = grid.querySelectorAll(".counsel-action-card").length;
      label.textContent = "建议施政（" + count + "）";
      if (!count) {
        area.classList.add("hidden");
        return;
      }
      area.classList.remove("hidden");
    }

    function _setCollapsed(collapsed) {
      area.classList.toggle("collapsed", collapsed);
      toggle.textContent = collapsed ? "展开" : "收起";
    }

    cards.forEach(function (card) {
      var disabled = !!card.disabled_reason || card.cost === null || card.cost === undefined;
      var costText = (card.cost !== null && card.cost !== undefined) ? card.cost + " 两" : "—";
      var cardEl = document.createElement("div");
      cardEl.className = "counsel-action-card" + (disabled ? " disabled" : "");

      var inner = '<div class="counsel-card-body">' +
        '<div class="counsel-card-name">' + escapeHtml(card.name || card.action) + '</div>' +
        '<div class="counsel-card-cost">' + escapeHtml(costText) + '</div>' +
        (card.rationale
          ? '<div class="counsel-card-rationale">' + escapeHtml(card.rationale) + '</div>' : '') +
        (card.disabled_reason
          ? '<div class="counsel-card-disabled">' + escapeHtml(card.disabled_reason) + '</div>' : '') +
        '</div>';
      inner += '<button class="counsel-card-dismiss" title="忽略此建议" tabindex="-1">×</button>';
      cardEl.innerHTML = inner;

      if (!disabled) {
        cardEl.dataset.action      = card.action;
        cardEl.dataset.name        = card.name || card.action;
        cardEl.dataset.gameId      = gameId;
        cardEl.dataset.needsVillage = card.requires_village ? "1" : "0";
      }

      // × → 静默忽略
      cardEl.querySelector(".counsel-card-dismiss").addEventListener("click", function (e) {
        e.stopPropagation();
        cardEl.remove();
        _syncCardsState();
      });

      // 点击主体 → 就地执行
      if (!disabled) {
        cardEl.addEventListener("click", function (e) {
          if (e.target.classList.contains("counsel-card-dismiss")) return;
          _executeCard(cardEl, grid, area, _syncCardsState);
        });
      }

      grid.appendChild(cardEl);
    });

    area.innerHTML = "";
    area.appendChild(head);
    area.appendChild(grid);
    _syncCardsState();
    _setCollapsed(true);
    toggle.addEventListener("click", function () {
      _setCollapsed(!area.classList.contains("collapsed"));
    });
    _keepLatestMessageVisible();
  }

  // ── 新构想申报卡片 ────────────────────────────────────────

  function _renderProposeCards(items, gameId) {
    var area = el("counsel-propose-cards");
    if (!area) return;

    // 过滤掉本次会话中已提报过的构想
    var newItems = (items || []).filter(function (item) {
      return item.name && !_proposedInSession[item.name];
    });

    if (!newItems.length) {
      return;  // 没有新构想，不改变现有区域状态
    }

    area.classList.remove("hidden");

    var head = document.createElement("div");
    head.className = "counsel-propose-cards-head";
    var label = document.createElement("span");
    label.className = "counsel-propose-cards-label";
    label.textContent = "新构想申报（" + newItems.length + "）";
    head.appendChild(label);

    newItems.forEach(function (item) {
      var name      = item.name || "";
      var rationale = item.rationale || "";

      var cardEl = document.createElement("div");
      cardEl.className = "counsel-propose-card";
      cardEl.innerHTML =
        '<div class="counsel-propose-card-body">' +
          '<div class="counsel-propose-card-name">' + escapeHtml(name) + '</div>' +
          (rationale ? '<div class="counsel-propose-card-rationale">' + escapeHtml(rationale) + '</div>' : '') +
          '<div class="counsel-propose-card-hint">此举非常规，需报请省布政司核准方可执行。</div>' +
          '<div class="counsel-propose-card-actions">' +
            '<button class="counsel-propose-card-submit">申报省布政司</button>' +
            '<button class="counsel-propose-card-dismiss">暂不考虑</button>' +
          '</div>' +
        '</div>';

      var submitBtn  = cardEl.querySelector(".counsel-propose-card-submit");
      var dismissBtn = cardEl.querySelector(".counsel-propose-card-dismiss");

      dismissBtn.addEventListener("click", function () {
        cardEl.remove();
        _syncProposeArea();
      });

      submitBtn.addEventListener("click", function () {
        submitBtn.disabled = true;
        submitBtn.textContent = "提交中…";

        api.counselPropose(gameId, name, rationale)
          .then(function (res) {
            _proposedInSession[name] = true;
            _appendMessage("system", null, null,
              "已向省布政司递交申请「" + name + "」，请等候下月批复。");
            _history.push({ role: "user", content: "（已申报新施政构想：" + name + "）" });
            cardEl.remove();
            _syncProposeArea();
            _loadPolicies(gameId);
          })
          .catch(function (err) {
            submitBtn.disabled = false;
            submitBtn.textContent = "申报省布政司";
            _appendMessage("system", null, null,
              "❌ 申报失败：" + ((err && err.message) || "未知错误"));
          });
      });

      area.appendChild(cardEl);
    });

    // head 插到第一张卡片之前
    area.insertBefore(head, area.firstChild);
    _keepLatestMessageVisible();
  }

  function _syncProposeArea() {
    var area = el("counsel-propose-cards");
    if (!area) return;
    var remaining = area.querySelectorAll(".counsel-propose-card").length;
    if (!remaining) {
      area.innerHTML = "";
      area.classList.add("hidden");
    }
  }

  // ── 侧边栏：申报记录 ──────────────────────────────────────

  function _renderPolicyList(policies, gameId) {
    var area = el("counsel-policy-list");
    if (!area) return;

    if (!policies || !policies.length) {
      area.innerHTML = '<p class="hint" style="font-size:0.82em;padding:4px 0;">尚无申报记录。</p>';
      return;
    }

    var pending  = policies.filter(function (p) { return p.status === "PENDING"; });
    var approved = policies.filter(function (p) {
      return p.status === "APPROVED" || p.status === "PROMOTED";
    });
    var rejected = policies.filter(function (p) { return p.status === "REJECTED"; });

    function groupHtml(label, items, cls) {
      if (!items.length) return "";
      var s = '<div class="counsel-policy-group">' +
        '<div class="counsel-policy-group-hd">' + label +
        ' <span class="counsel-policy-count">' + items.length + '</span></div>';
      items.forEach(function (p) {
        s += '<div class="counsel-policy-item ' + cls + '" data-policy-id="' + p.id + '">' +
          '<div class="counsel-policy-item-name">' + escapeHtml(p.policy_name) + '</div>';
        if (cls === "status-approved" && p.cost) {
          s += '<div class="counsel-policy-item-meta">' + p.cost + ' 两 · ' + (p.delay_months || 0) + ' 月</div>';
        }
        if (p.rejection_reason) {
          s += '<div class="counsel-policy-item-reason">' + escapeHtml(p.rejection_reason) + '</div>';
        }
        if (cls === "status-approved" && !p.is_executed) {
          s += '<button class="counsel-policy-discard-btn" data-policy-id="' + p.id + '">丢弃</button>';
        }
        s += '</div>';
      });
      return s + '</div>';
    }

    area.innerHTML =
      groupHtml("⏳ 待审核", pending,  "status-pending") +
      groupHtml("✅ 已批准", approved, "status-approved") +
      groupHtml("❌ 已拒绝", rejected, "status-rejected");

    // 绑定丢弃按钮事件
    area.querySelectorAll(".counsel-policy-discard-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var policyId = parseInt(btn.dataset.policyId, 10);
        var itemEl   = area.querySelector('.counsel-policy-item[data-policy-id="' + policyId + '"]');
        var name     = itemEl ? itemEl.querySelector(".counsel-policy-item-name").textContent : "";
        if (!confirm("确认丢弃「" + name + "」？丢弃后不可恢复。")) return;
        btn.disabled = true;
        btn.textContent = "丢弃中…";
        api.counselDiscardPolicy(gameId, policyId)
          .then(function () {
            if (itemEl) itemEl.remove();
            _appendMessage("system", null, null, "已丢弃「" + name + "」。");
          })
          .catch(function (err) {
            btn.disabled = false;
            btn.textContent = "丢弃";
            _appendMessage("system", null, null, "丢弃失败：" + (err.message || "未知错误"));
          });
      });
    });
  }

  function _loadPolicies(gameId) {
    api.counselPolicies(gameId)
      .then(function (res) { _renderPolicyList(res.policies || [], gameId); })
      .catch(function () {});
  }

  // ── 发送消息 ──────────────────────────────────────────────

  function sendMessage(gameId) {
    if (_sending) return;
    var input = el("counsel-input");
    if (!input) return;
    var text = (input.value || "").trim();
    if (!text) return;

    _appendMessage("user", null, null, text);
    _history.push({ role: "user", content: text });
    input.value = "";

    _sending = true;
    var btn = el("counsel-send");
    if (btn) { btn.disabled = true; btn.textContent = "问询中…"; }

    api.counselMessage(gameId, text, _history.slice(0, -1))
      .then(function (res) {
        var speaker = res.speaker     || "shiye";
        var name    = res.speaker_name || speaker;
        var reply   = res.reply        || "（幕僚沉默）";
        _appendMessage("npc", speaker, name, reply);
        _history.push({ role: "assistant", content: reply });
        _renderActionCards(res.suggested_actions || [], gameId);
        _renderProposeCards(res.proposed_policies || [], gameId);
      })
      .catch(function (err) {
        _appendMessage("system", null, null, "问询失败：" + (err.message || "未知错误"));
      })
      .finally(function () {
        _sending = false;
        if (btn) { btn.disabled = false; btn.textContent = "问询"; }
      });
  }

  // ── 提交申报 ──────────────────────────────────────────────

  function submitPropose(gameId) {
    var nameEl      = el("counsel-propose-name");
    var rationaleEl = el("counsel-propose-rationale");
    var btn         = el("counsel-propose-submit");
    var name        = (nameEl      ? nameEl.value      : "").trim();
    var rationale   = (rationaleEl ? rationaleEl.value : "").trim();

    if (!name) { C.showToast("请填写施政构想名称", "error"); return; }
    if (btn) { btn.disabled = true; btn.textContent = "提交中…"; }

    api.counselPropose(gameId, name, rationale)
      .then(function (res) {
        C.showToast(res.message || "已提交申请", "success");
        if (nameEl)      nameEl.value = "";
        if (rationaleEl) rationaleEl.value = "";
        _appendMessage("system", null, null,
          "已向省布政司递交申请「" + name + "」，请等候下月批复。");
        _loadPolicies(gameId);
      })
      .catch(function (err) {
        C.showToast(err.message || "提交失败", "error");
      })
      .finally(function () {
        if (btn) { btn.disabled = false; btn.textContent = "递交申请"; }
      });
  }

  // ── 打开面板 ──────────────────────────────────────────────

  function open(gameId) {
    // 换局时重置会话
    if (_currentGameId !== gameId) {
      _currentGameId    = gameId;
      _history          = [];
      _proposedInSession = {};
      _notificationsShown = false;
      _proactiveShown   = false;
      var box = el("counsel-messages");
      if (box) box.innerHTML = "";
      var cards = el("counsel-action-cards");
      if (cards) { cards.innerHTML = ""; cards.classList.add("hidden"); }
      var pCards = el("counsel-propose-cards");
      if (pCards) { pCards.innerHTML = ""; pCards.classList.add("hidden"); }
    }

    el("counsel-modal").classList.remove("hidden");

    // 加载申报记录
    _loadPolicies(gameId);

    // 滚到底
    _keepLatestMessageVisible();

    // ── 批复通知（结算后首次打开注入系统消息）──
    if (!_notificationsShown) {
      api.counselPendingNotifications(gameId)
        .then(function (res) {
          var notifs = res.notifications || [];
          if (notifs.length) {
            _notificationsShown = true;
            _setBadge(false);
            notifs.forEach(function (n) {
              var icon = n.approved ? "✅" : "❌";
              var msg  = icon + " 布政司批复「" + n.policy_name + "」：" +
                (n.approved ? "已批准" : "未获批准") +
                (n.rationale ? "。" + n.rationale : "。");
              _appendMessage("system", null, null, msg);
            });
            // 重新加载申报记录（状态已更新）
            _loadPolicies(gameId);
          }
        })
        .catch(function () {});
    }

    // ── 主动提醒（会话内只触发一次）──
    if (!_proactiveShown) {
      api.counselProactive(gameId)
        .then(function (res) {
          if (res.trigger) {
            _proactiveShown = true;
            var t = res.trigger;
            _appendMessage("npc", t.speaker, t.speaker_name, t.message || "");
            _history.push({ role: "assistant", content: t.message || "" });
            _renderActionCards(t.suggested_actions || [], gameId);
          }
        })
        .catch(function () {});
    }
  }

  // ── 外部调用：标记有新批复（结算后由 app.js 触发）─────────

  function markNewNotifications() {
    _notificationsShown = false;  // 下次打开重新拉取
    _setBadge(true);
  }

  // ── 导出 ──────────────────────────────────────────────────

  window.Game = window.Game || {};
  window.Game.counsel = {
    open:                 open,
    sendMessage:          sendMessage,
    submitPropose:        submitPropose,
    toggleSidebar:        toggleSidebar,
    markNewNotifications: markNewNotifications,
  };

})();
