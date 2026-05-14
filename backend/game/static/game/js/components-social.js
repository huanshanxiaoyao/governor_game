/**
 * Social components (negotiation, events, relationships, agents, staff)
 * Depends on: components-core.js
 */
(function () {
  "use strict";

  var C = Game.components;
  var el = C.el, h = C.h, escapeHtml = C.escapeHtml;

  // ==================== Negotiation ====================

  var EVENT_TYPE_NAMES = {
    ANNEXATION: "地主兼并",
    IRRIGATION: "兴建水利",
    HIDDEN_LAND: "隐匿土地",
    VILLAGE_REQ_SCHOOL: "村民请愿·建村塾",
    VILLAGE_REQ_TAX: "村民请愿·减税",
    LANDLORD_DEMAND_FACILITY: "地主要求·升级设施",
  };

  function formatMsgTime(isoString) {
    try {
      var d = new Date(isoString);
      var hh = String(d.getHours()).padStart(2, '0');
      var mm = String(d.getMinutes()).padStart(2, '0');
      var ss = String(d.getSeconds()).padStart(2, '0');
      return hh + ':' + mm + ':' + ss;
    } catch (e) {
      return '';
    }
  }

  function getNegotiationPlayerLabel(speakerRole, speakerName) {
    if (speakerRole === "ADVISOR") return (speakerName || "师爷") + "（代）";
    if (speakerRole === "DEPUTY") return (speakerName || "县丞") + "（代）";
    return "县令";
  }

  function checkActiveNegotiation() {
    var g = Game.state.currentGame;
    if (!g) return;

    Game.api.getActiveNegotiation(g.id)
      .then(function (data) {
        Game.state.activeNegotiation = data.active ? data.session : null;
        renderNegotiationBanner();
        renderIrrigationNegotiateSection();
      })
      .catch(function () {
        Game.state.activeNegotiation = null;
        renderNegotiationBanner();
        renderIrrigationNegotiateSection();
      });
  }

  function renderNegotiationBanner() {
    var banner = el("negotiation-banner");
    var session = Game.state.activeNegotiation;

    if (!session) {
      banner.classList.add("hidden");
      banner.innerHTML = "";
      // Also remove stale "前往谈判" button in report tab
      var reportBtn = document.getElementById("btn-report-negotiate");
      if (reportBtn) {
        var parent = reportBtn.closest(".report-event-urgent");
        if (parent) {
          parent.classList.remove("report-event-urgent");
          parent.style.fontWeight = "normal";
        }
        reportBtn.remove();
      }
      return;
    }

    var typeName = EVENT_TYPE_NAMES[session.event_type] || session.event_type;
    banner.classList.remove("hidden");
    banner.innerHTML =
      '<div class="nego-banner-text">' +
        '<strong>' + typeName + '谈判进行中</strong> — ' +
        session.agent_name + '（' + session.agent_role_title + '）' +
        ' 第' + session.current_round + '/' + session.max_rounds + '轮' +
      '</div>' +
      '<button class="btn btn-primary btn-small" id="btn-open-negotiation">进入谈判</button>';
  }

  function renderIrrigationNegotiateSection() {
    var section = el("irrigation-negotiate-section");
    var g = Game.state.currentGame;
    if (!g) { section.classList.add("hidden"); return; }

    // Find active irrigation investment
    var irrigationInv = null;
    (g.county_data.active_investments || []).forEach(function (inv) {
      if (inv.action === "build_irrigation") irrigationInv = inv;
    });

    if (!irrigationInv || Game.state.activeNegotiation) {
      section.classList.add("hidden");
      return;
    }

    // Filter out villages already negotiated
    var negotiated = irrigationInv.negotiated_villages || [];
    var remaining = g.county_data.villages.filter(function (v) {
      return negotiated.indexOf(v.name) === -1;
    });

    if (remaining.length === 0) {
      section.classList.add("hidden");
      return;
    }

    section.classList.remove("hidden");
    var html =
      '<h4 class="section-title">水利协商</h4>' +
      '<p style="font-size:0.9em;margin-bottom:10px;color:#5c4a2a;">' +
        '水利工程建设中，可与各村地主协商，请其出资分担费用：</p>' +
      '<div class="irrigation-village-btns">';

    remaining.forEach(function (v) {
      html += '<button class="btn btn-small btn-irrigation-nego" data-village="' +
        v.name + '">' + v.name + '地主</button>';
    });

    html += '</div>';
    section.innerHTML = html;
  }

  function openNegotiationModal(session) {
    var g = Game.state.currentGame;
    if (!g) return;

    var typeName = EVENT_TYPE_NAMES[session.event_type] || session.event_type;
    el("nego-title").textContent = typeName + "谈判";
    el("nego-subtitle").textContent =
      session.agent_name + "（" + session.agent_role_title + "） " +
      "第" + session.current_round + "/" + session.max_rounds + "轮";

    var msgContainer = el("nego-messages");
    msgContainer.innerHTML = '<p class="hint">加载中...</p>';

    var resolvedDiv = el("nego-resolved");
    resolvedDiv.classList.add("hidden");
    resolvedDiv.innerHTML = "";

    var inputArea = el("nego-input-area");

    // 承诺提示（仅请愿/要求类谈判显示）
    var REQUEST_TYPES = ['VILLAGE_REQ_SCHOOL', 'VILLAGE_REQ_TAX', 'LANDLORD_DEMAND_FACILITY'];
    var hintEl = el("nego-promise-hint");
    if (hintEl) {
      if (REQUEST_TYPES.indexOf(session.event_type) !== -1) {
        hintEl.classList.remove("hidden");
      } else {
        hintEl.classList.add("hidden");
      }
    }

    if (session.status === "resolved") {
      inputArea.classList.add("hidden");
      resolvedDiv.classList.remove("hidden");
      resolvedDiv.innerHTML = '<div class="nego-outcome">谈判已结束</div>';
    } else {
      inputArea.classList.remove("hidden");
    }

    el("negotiation-modal").classList.remove("hidden");
    el("nego-input").value = "";
    if (el("nego-speaker")) el("nego-speaker").value = "PLAYER";
    el("nego-input").focus();

    // Load history
    Game.api.getNegotiationHistory(g.id, session.id)
      .then(function (data) {
        renderNegotiationMessages(data.messages || []);
        if (data.session) {
          Game.state.activeNegotiation = data.session;
          var subtitle = data.session.agent_name + "（" + data.session.agent_role_title + "） " +
            "第" + data.session.current_round + "/" + data.session.max_rounds + "轮";
          if (data.session.status === 'resolved') {
            subtitle += " [已结束]";
          }
          el("nego-subtitle").textContent = subtitle;

          // For resolved sessions: update the resolved panel with outcome + summary
          if (data.session.status === 'resolved') {
            var outcome = data.session.outcome || {};
            var summary = outcome.summary;
            var summaryHtml = summary ? _renderSummaryHtml(summary) : '';
            resolvedDiv.innerHTML =
              '<div class="nego-outcome">谈判已结束</div>' + summaryHtml;
          }
        }
      })
      .catch(function () {
        msgContainer.innerHTML = '<p class="hint">加载失败</p>';
      });
  }

  function _renderSummaryHtml(summary) {
    if (!summary) return '';
    var html = '<div class="nego-summary">';
    if (summary.conclusion) {
      html += '<div class="ns-conclusion">' + escapeHtml(summary.conclusion) + '</div>';
    }
    if (summary.player_promises && summary.player_promises.length) {
      html += '<div class="ns-section"><span class="ns-label">县令承诺：</span>';
      summary.player_promises.forEach(function (p) {
        html += '<span class="ns-item">' + escapeHtml(p) + '</span>';
      });
      html += '</div>';
    }
    if (summary.npc_concessions && summary.npc_concessions.length) {
      html += '<div class="ns-section"><span class="ns-label">对方承诺：</span>';
      summary.npc_concessions.forEach(function (p) {
        html += '<span class="ns-item ns-item-npc">' + escapeHtml(p) + '</span>';
      });
      html += '</div>';
    }
    if (summary.key_moment) {
      html += '<div class="ns-key-moment">' + escapeHtml(summary.key_moment) + '</div>';
    }
    html += '</div>';
    return html;
  }

  function renderNegotiationMessages(messages) {
    var container = el("nego-messages");
    container.innerHTML = "";

    // Only show hint if there are no non-advisor messages yet
    var hasConversation = messages.some(function (m) { return m.role !== 'advisor'; });
    if (messages.length === 0 || !hasConversation) {
      container.innerHTML = '<p class="hint" style="text-align:center;padding:20px;">开始与对方交涉吧</p>';
      if (messages.length === 0) return;
    }

    messages.forEach(function (msg) {
      var cls, label;
      if (msg.role === 'advisor') {
        cls = 'nego-msg nego-msg-advisor';
        label = (msg.advisor_name || '师爷') + ' 提示';
      } else if (msg.role === 'player') {
        cls = 'nego-msg nego-msg-player';
        label = getNegotiationPlayerLabel(msg.speaker_role, msg.speaker_name);
      } else {
        cls = 'nego-msg nego-msg-agent';
        label = '';
      }
      var tsHtml = msg.created_at ? '<span class="nego-msg-ts">' + formatMsgTime(msg.created_at) + '</span>' : '';
      var div = h('div', cls,
        (label ? '<span class="nego-msg-label">' + label + '</span>' : '') +
        '<div class="nego-msg-content">' + escapeHtml(msg.content) + '</div>' +
        tsHtml);
      container.appendChild(div);
    });

    container.scrollTop = container.scrollHeight;
  }

  function appendNegotiationMessage(role, content, opts) {
    var container = el("nego-messages");
    // Remove the hint if present
    var hint = container.querySelector(".hint");
    if (hint) hint.remove();

    opts = opts || {};

    var cls = role === "player" ? "nego-msg nego-msg-player" : "nego-msg nego-msg-agent";
    var label = role === "player"
      ? getNegotiationPlayerLabel(opts.speakerRole, opts.speakerName)
      : "";
    var now = new Date();
    var tsHtml = '<span class="nego-msg-ts">' + formatMsgTime(now.toISOString()) + '</span>';
    var div = h("div", cls,
      (label ? '<span class="nego-msg-label">' + label + '</span>' : '') +
      '<div class="nego-msg-content">' + escapeHtml(content) + '</div>' +
      tsHtml);
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
  }

  function showNegotiationResolved(result) {
    var inputArea = el("nego-input-area");
    inputArea.classList.add("hidden");

    var resolvedDiv = el("nego-resolved");
    resolvedDiv.classList.remove("hidden");

    var decision = result.final_decision || "unknown";
    var decisionText = {
      "stop_annexation": "地主同意停止兼并",
      "proceed_annexation": "地主执意继续兼并",
      "accept": "请求已被接受",
      "refuse": "请求被拒绝",
      "declare_all": "地主主动申报全部隐田",
      "auto_close": "谈判超时自动关闭",
    }[decision] || decision;

    // Event-type specific overrides
    if (result.event_type === "HIDDEN_LAND" && decision === "refuse") {
      decisionText = "地主拒绝申报，官府强制清丈";
    }
    if (result.event_type === "IRRIGATION" && decision === "accept") {
      decisionText = "地主同意出资";
    }
    if (result.event_type === "IRRIGATION" && decision === "refuse") {
      decisionText = "地主拒绝出资";
    }

    var extraHtml = "";
    if (result.event_type === "IRRIGATION" && decision === "accept" && result.contribution_offer) {
      extraHtml =
        '<br><span style="color:#6b8f4a;font-weight:bold;">出资: ' +
        result.contribution_offer + ' 两</span>';
      if (result.treasury !== undefined) {
        extraHtml += '<br>县库余额: ' + result.treasury + ' 两';
      }
    }

    var summaryHtml = result.summary ? _renderSummaryHtml(result.summary) : '';

    resolvedDiv.innerHTML =
      '<div class="nego-outcome">' +
        '<strong>谈判结束</strong><br>' +
        decisionText + extraHtml +
      '</div>' +
      summaryHtml;

    el("nego-subtitle").textContent += " [已结束]";
  }

  // ==================== Event Logs ====================

  var CATEGORY_LABELS = {
    SYSTEM: "系统",
    INVESTMENT: "投资",
    TAX: "税率",
    NEGOTIATION: "谈判",
    JUDICIAL: "司法",
    LETTER: "书信",
    BRIBERY: "行贿",
    PROFILE: "声望",
    DISASTER: "灾害",
    SETTLEMENT: "结算",
    ANNEXATION: "兼并",
    HIDDEN_LAND: "隐田",
    PROMISE: "承诺",
  };

  var CATEGORY_COLORS = {
    SYSTEM: "#8a7a5a",
    INVESTMENT: "#2e86c1",
    TAX: "#8e44ad",
    NEGOTIATION: "#d4a017",
    JUDICIAL: "#2c5aa0",
    LETTER: "#7f8c8d",
    BRIBERY: "#a93226",
    PROFILE: "#6c5ce7",
    DISASTER: "#c0392b",
    SETTLEMENT: "#27ae60",
    ANNEXATION: "#e67e22",
    HIDDEN_LAND: "#8b4513",
    PROMISE: "#8e44ad",
  };

  function getSettlementPayload(logData) {
    if (!logData) return null;
    if (logData.settlement_report) return logData.settlement_report;
    if (!logData.events && !logData.monthly_snapshot && !logData.autumn && !logData.winter_snapshot) {
      return null;
    }
    var payload = {};
    if (logData.events) payload.events = logData.events;
    if (logData.monthly_snapshot) payload.monthly_snapshot = logData.monthly_snapshot;
    if (logData.population_update) payload.population_update = logData.population_update;
    if (logData.autumn) payload.autumn = logData.autumn;
    if (logData.winter_snapshot) payload.winter_snapshot = logData.winter_snapshot;
    return payload;
  }

  function renderEventLogs(logs) {
    var container = el("events-list");
    container.innerHTML = "";

    if (!logs || logs.length === 0) {
      container.innerHTML = '<p class="hint">暂无事件记录</p>';
      return;
    }

    logs.forEach(function (log) {
      var catLabel = CATEGORY_LABELS[log.category] || log.category;
      var catColor = CATEGORY_COLORS[log.category] || "#8a7a5a";
      var seasonText = Game.seasonName(log.season);
      var extraHtml = "";

      if (log.category === "SETTLEMENT") {
        var settlementPayload = getSettlementPayload(log.data);
        if (settlementPayload) {
          extraHtml =
            '<details class="event-log-settlement-details">' +
              "<summary>查看当月月报</summary>" +
              '<pre class="event-log-settlement-json">' +
                escapeHtml(JSON.stringify(settlementPayload, null, 2)) +
              "</pre>" +
            "</details>";
        }
      }

      var item = h("div", "event-log-item");
      item.innerHTML =
        '<div class="event-log-header">' +
          '<span class="event-log-category" style="background:' + catColor + ';">' + catLabel + '</span>' +
          '<span class="event-log-season">' + seasonText + '</span>' +
        '</div>' +
        '<div class="event-log-desc">' + escapeHtml(log.description || log.event_type) + '</div>' +
        extraHtml;
      container.appendChild(item);
    });
  }

  // ==================== Relationships / 社交 ====================

  var LOCAL_ROLES = {
    ADVISOR: true, DEPUTY: true,
    LIUFANG: true, CONSTABLE: true, BAILIFF_CEREMONY: true, BAILIFF_LABOR: true,
    GENTRY: true, VILLAGER: true, CLAN_YOUTH: true,
  };

  // 汇报链（知县视角；知府玩家时 PREFECT 不在链内）
  var CHAIN_ROLES = {
    PREFECT: true,                    // 知府
    PROVINCIAL_GOVERNOR: true,        // 巡抚
    PROVINCIAL_COMMISSIONER: true,    // 布政使 / 按察使
  };
  // 中央官员
  var CENTRAL_ROLES = {
    CABINET_CHIEF: true, CABINET_MEMBER: true,
    MINISTER: true, VICE_MINISTER: true,
    CHIEF_CENSOR: true, VICE_CENSOR: true, CENSOR: true,
  };
  // 其他地方官员
  var OTHER_LOCAL_ROLES = { PREFECT_PEER: true };

  // 所有外部角色（用于可见性过滤）
  var ALL_REMOTE_ROLES = Object.assign(
    {}, { EMPEROR: true }, CHAIN_ROLES, CENTRAL_ROLES, OTHER_LOCAL_ROLES
  );

  function _buildRelCard(a, negoByAgent, isLocal, promisesByAgentId) {
    var aff = a.affinity;
    var affClass = aff < 30 ? "affinity-low" : (aff < 60 ? "affinity-mid" : "affinity-high");
    var barColor = aff < 30 ? "#c0392b" : (aff < 60 ? "#d4a017" : "#27ae60");
    var barPct   = Math.max(0, Math.min(100, ((aff + 99) / 198) * 100));
    var roleInfo = a.role_title + (a.village_name ? "（" + a.village_name + "）" : "");

    // 社会身份标签
    var si = a.social_identity || {};
    var identityParts = [];
    if (a.age) identityParts.push(a.age + '岁');
    if (si.native_place && si.native_place !== '__local__') identityParts.push(si.native_place + '人');
    if (si.clan_id && si.clan_id !== '__local__') identityParts.push(si.clan_id);
    var identityHtml = '';
    if (identityParts.length) {
      identityHtml = '<div class="agent-identity-line">' + escapeHtml(identityParts.join(' · ')) + '</div>';
    }
    // 同乡标签（去掉"年岁相仿"，只保留同乡）
    var hometownHtml = '';
    var _hrel = (a.hometown_relation || '').replace('年岁相仿', '').replace('、', '').trim();
    if (_hrel) {
      hometownHtml = '<span class="hometown-badge">' + escapeHtml(_hrel) + '</span>';
    }

    var actionBtn;
    if (a.role === 'CLAN_YOUTH') {
      var nominated = (a.attributes && a.attributes.exam_eligible);
      var canNominate = !(a.attributes && a.attributes.can_nominate === false);
      var disabled = nominated || !canNominate;
      var disabledTitle = nominated
        ? '举荐已确认，本年度不可更改'
        : (!canNominate ? '仅可举荐本年度新推举的宗族后生' : '');
      actionBtn = '<button class="btn btn-small btn-clan-nominate rel-action-btn"' +
        ' data-agent-id="' + a.id + '"' + (disabled ? ' disabled title="' + escapeHtml(disabledTitle) + '"' : '') + '>' +
        (nominated ? '已举荐' : (canNominate ? '举荐应试' : '往年后生')) + '</button>';
    } else if (isLocal) {
      actionBtn = '<button class="btn btn-small btn-staff-chat rel-action-btn"' +
        ' data-agent-id="' + a.id + '" data-agent-name="' + escapeHtml(a.name) + '">交谈</button>';
    } else {
      actionBtn = '<button class="btn btn-small btn-agent-letter rel-action-btn"' +
        ' data-agent-id="' + a.id + '">书信</button>';
    }

    var html =
      '<div class="relationship-card-header">' +
        '<span class="relationship-name">' + escapeHtml(a.name) + '</span>' +
        '<span class="relationship-role">' + escapeHtml(roleInfo) + '</span>' +
        hometownHtml +
      '</div>' +
      identityHtml +
      '<div class="rel-card-middle">' +
        '<div>' +
          '<div class="affinity-value ' + affClass + '">好感度: ' + aff + '</div>' +
          '<div class="affinity-bar">' +
            '<div class="affinity-bar-fill" style="width:' + barPct.toFixed(1) + '%;background:' + barColor + ';"></div>' +
          '</div>' +
        '</div>' +
        actionBtn +
      '</div>';

    var agentNego = negoByAgent[a.name];
    if (agentNego) {
      var typeName = EVENT_TYPE_NAMES[agentNego.event_type] || agentNego.event_type;
      html +=
        '<div class="nego-agent-badge">' +
          '<span class="nego-badge-text">' + typeName + '谈判进行中</span>' +
          '<button class="btn btn-primary btn-small btn-nego-enter" data-session-id="' + agentNego.id + '">进入谈判</button>' +
        '</div>';
    }

    if (a.memory && a.memory.length > 0) {
      html += '<div class="memory-list">';
      a.memory.forEach(function (m) {
        html += '<div class="memory-list-item">' + escapeHtml(m) + '</div>';
      });
      html += '</div>';
    }

    html += _buildPromiseBadgeHtml(a.id, promisesByAgentId);

    var card = document.createElement("div");
    card.className = "relationship-card";
    card.id = "agent-card-" + a.id;
    card.innerHTML = html;

    // 点击卡片主体打开人物档案（不触发按钮点击）
    card.addEventListener("click", function (e) {
      if (e.target.tagName === "BUTTON" || e.target.closest("button")) return;
      openAgentProfile(a);
    });

    return card;
  }

  // ── 宗族概览（从 county_data.clans 读取） ────────────────────────────────

  var _CLAN_AFFINITY_LABELS = [
    [70, '和睦', '#27ae60'],
    [40, '平稳', '#2980b9'],
    [20, '冷淡', '#d4a017'],
    [10, '对抗', '#e67e22'],
    [0,  '敌对', '#c0392b'],
  ];

  function _clanAffinityLabel(aff) {
    for (var i = 0; i < _CLAN_AFFINITY_LABELS.length; i++) {
      if (aff >= _CLAN_AFFINITY_LABELS[i][0]) {
        return { text: _CLAN_AFFINITY_LABELS[i][1], color: _CLAN_AFFINITY_LABELS[i][2] };
      }
    }
    return { text: '敌对', color: '#c0392b' };
  }

  function _renderClanOverview(container, agents) {
    var g = Game.state.currentGame;
    var clans = ((g || {}).county_data || {}).clans || {};
    var playerSi = ((g || {}).county_data || {}).player_social_identity || {};

    if (!Object.keys(clans).length) return;

    // 按 total_influence 降序，只展示本县有落脚点的宗族（local_members 非空）
    var clanIds = Object.keys(clans).filter(function (id) {
      return (clans[id].local_members || []).length > 0;
    }).sort(function (a, b) {
      return (clans[b].total_influence || 0) - (clans[a].total_influence || 0);
    });

    var hd = document.createElement('div');
    hd.className = 'social-section-hd';
    hd.textContent = '本县宗族（' + clanIds.length + ' 支）';
    container.appendChild(hd);

    // 玩家身份信息行
    if (playerSi.native_place || playerSi.clan_id) {
      var playerInfoDiv = document.createElement('div');
      playerInfoDiv.className = 'player-identity-line';
      var parts = [];
      if (playerSi.native_place) parts.push('你的籍贯：' + playerSi.native_place);
      if (playerSi.clan_id)      parts.push('宗族：' + playerSi.clan_id);
      if (playerSi.age)          parts.push('年龄：' + playerSi.age + '岁');
      playerInfoDiv.textContent = parts.join('　');
      container.appendChild(playerInfoDiv);
    }

    // agent id → agent 查找表
    var agentById = {};
    agents.forEach(function (a) { agentById[a.id] = a; });

    var grid = document.createElement('div');
    grid.className = 'clan-overview-grid';

    clanIds.forEach(function (clanId) {
      var clan = clans[clanId];
      var aff = clan.clan_affinity || 50;
      var label = _clanAffinityLabel(aff);
      var barPct = Math.max(0, Math.min(100, aff));
      var totalInfluence = clan.total_influence || 0;
      var influenceBarPct = Math.max(0, Math.min(100, Math.round(totalInfluence / 4)));
      var streak = clan.low_affinity_streak || 0;

      // 本县地主（GENTRY）列表
      var localGentry = (clan.local_members || [])
        .map(function (id) { return agentById[id]; })
        .filter(function (a) { return a && a.role === 'GENTRY'; });
      var gentryDesc = localGentry.map(function (a) {
        var vName = (a.attributes || {}).village_name || '';
        return escapeHtml(a.name) + (vName ? '（' + escapeHtml(vName) + '）' : '');
      }).join('、');

      // 在外同族官员
      var officialLines = (clan.official_members || [])
        .map(function (id) { return agentById[id]; })
        .filter(Boolean)
        .map(function (a) {
          return '<span class="clan-official-tag">' +
            escapeHtml(a.name) + ' — ' + escapeHtml(a.role_title || a.role) +
          '</span>';
        })
        .join(' ');

      // 同府他县分支
      var branches = clan.other_county_branches || 0;
      var branchText = branches > 0
        ? '约 ' + branches + ' 县有族人'
        : '暂无已知分支';

      var warningHtml = '';
      if (aff < 30) {
        var warnText = aff < 10
          ? '⚠ 宗族已公然对抗，秋粮征收严重折损'
          : '⚠ 宗族心存不满，征收效率下降';
        if (streak >= 2) warnText += '，已连续' + streak + '月对立';
        warningHtml = '<div class="clan-warning">' + warnText + '</div>';
      }

      // 所在村庄
      var villages = (clan.local_villages || []).map(escapeHtml).join('、') || '—';

      var card = document.createElement('div');
      card.className = 'clan-card' + (aff < 30 ? ' clan-card-tension' : '');
      card.innerHTML =
        '<div class="clan-card-header">' +
          '<span class="clan-name">' + escapeHtml(clanId) + '</span>' +
          '<span class="clan-label" style="color:' + label.color + '">' + label.text + '</span>' +
        '</div>' +
        '<div class="clan-affinity-row">' +
          '<span>宗族态度</span>' +
          '<div class="affinity-bar clan-bar">' +
            '<div class="affinity-bar-fill" style="width:' + barPct + '%;background:' + label.color + '"></div>' +
          '</div>' +
          '<span>' + aff + '</span>' +
        '</div>' +
        '<div class="clan-detail-row"><span class="clan-detail-label">所在村庄</span>' + villages + '</div>' +
        (gentryDesc ? '<div class="clan-detail-row"><span class="clan-detail-label">本族地主</span>' + gentryDesc + '</div>' : '') +
        '<div class="clan-detail-row"><span class="clan-detail-label">在外同族</span>' +
          (officialLines || '<span class="clan-none-hint">无</span>') +
        '</div>' +
        '<div class="clan-detail-row"><span class="clan-detail-label">同府他县</span>' + escapeHtml(branchText) + '</div>' +
        '<div class="clan-influence-row">' +
          '<span class="clan-detail-label">整体影响力</span>' +
          '<div class="affinity-bar clan-bar">' +
            '<div class="affinity-bar-fill clan-influence-fill" style="width:' + influenceBarPct + '%"></div>' +
          '</div>' +
          '<span>' + totalInfluence + '</span>' +
        '</div>' +
        warningHtml;

      grid.appendChild(card);
    });

    container.appendChild(grid);
  }

  function renderRelationships(agents, promisesByAgentId) {
    var container = el("relationships-list");
    container.innerHTML = "";

    if (!agents || agents.length === 0) {
      container.innerHTML = '<p class="hint">暂无社交数据</p>';
      return;
    }

    var g = Game.state.currentGame;
    var playerRole = (g || {}).player_role || "";
    var adminLoc   = ((g || {}).county_data || {}).admin_location || {};
    var playerProvince   = adminLoc.province   || "";
    var playerPrefecture = adminLoc.prefecture || "";

    // 汇报链：只保留玩家所在省/府的直接上级官员
    var localAgents   = agents.filter(function (a) { return !!LOCAL_ROLES[a.role]; });
    var emperorAgents = agents.filter(function (a) { return a.role === "EMPEROR"; });
    var centralAgents = agents.filter(function (a) { return !!CENTRAL_ROLES[a.role]; });
    var chainAgents   = agents.filter(function (a) {
      if (!CHAIN_ROLES[a.role]) return false;
      if (playerRole === "PREFECT" && a.role === "PREFECT") return false;
      if (a.role === "PREFECT") {
        return !playerPrefecture || a.prefecture === playerPrefecture;
      }
      return !playerProvince || a.province === playerProvince;
    });

    var negoByAgent = {};
    (Game.state.activeNegotiations || []).forEach(function (s) {
      negoByAgent[s.agent_name] = s;
    });

    // ── 本县人物 panel ────────────────────────────────────────────
    var localPanel = document.createElement("div");
    localPanel.id = "social-local-panel";

    function renderSubgroup(panelEl, title, list, isLocal) {
      if (!list.length) return;
      var subHd = document.createElement("div");
      subHd.className = "social-sub-hd";
      subHd.dataset.sg = title;
      subHd.textContent = title;
      panelEl.appendChild(subHd);
      var grid = document.createElement("div");
      grid.className = "social-section-grid";
      list.forEach(function (a) { grid.appendChild(_buildRelCard(a, negoByAgent, isLocal, promisesByAgentId)); });
      panelEl.appendChild(grid);
    }

    // 顺序：幕僚 → 乡绅 → 村民代表 → 宗族后生（六房/衙役三班暂不展示）
    var localSubgroups = [
      { title: "幕僚",    roles: ["ADVISOR", "DEPUTY"] },
      { title: "乡绅",    roles: ["GENTRY"] },
      { title: "村民代表", roles: ["VILLAGER"] },
      { title: "宗族后生", roles: ["CLAN_YOUTH"] },
    ];
    localSubgroups.forEach(function (sg) {
      var sub = localAgents.filter(function (a) { return sg.roles.indexOf(a.role) !== -1; });
      renderSubgroup(localPanel, sg.title, sub, true);
    });
    container.appendChild(localPanel);

    // ── 上位 panel ────────────────────────────────────────────────
    var upperPanel = document.createElement("div");
    upperPanel.id = "social-upper-panel";
    upperPanel.className = "hidden";

    function renderUpperSection(panelEl, title, list) {
      if (!list.length) return;
      var hd = document.createElement("div");
      hd.className = "social-section-hd";
      hd.textContent = title;
      panelEl.appendChild(hd);
      var grid = document.createElement("div");
      grid.className = "social-section-grid";
      list.forEach(function (a) { grid.appendChild(_buildRelCard(a, negoByAgent, false, promisesByAgentId)); });
      panelEl.appendChild(grid);
    }

    renderUpperSection(upperPanel, "皇帝", emperorAgents);
    renderUpperSection(upperPanel, "汇报链", chainAgents);
    renderUpperSection(upperPanel, "中央官员", centralAgents);
    container.appendChild(upperPanel);

    // 绑定「举荐」按钮（举荐一经确认不可撤销）
    container.querySelectorAll(".btn-clan-nominate").forEach(function (btn) {
      // 已举荐或非本年候选，直接禁用按钮
      if (btn.textContent === '已举荐' || btn.textContent === '往年后生') {
        btn.disabled = true;
        return;
      }
      btn.addEventListener("click", function () {
        var agentId = parseInt(btn.dataset.agentId, 10);
        var g = Game.state.currentGame;
        if (!g) return;
        var agentName = btn.closest('.relationship-card') && btn.closest('.relationship-card').querySelector('.rel-name')
          ? btn.closest('.relationship-card').querySelector('.rel-name').textContent.trim()
          : '该后生';
        // 确认弹窗
        if (!confirm('确认举荐 ' + agentName + ' 参加府试？\n举荐一经确认，本年度不可更改。')) return;
        btn.disabled = true;
        Game.api.nominateClanYouth(g.id, agentId)
          .then(function (res) {
            // 举荐成功后永久禁用按钮并更新文字
            btn.textContent = '已举荐';
            btn.disabled = true;
            btn.title = '举荐已确认，本年度不可更改';
            Game.components.showToast(res.message || '已举荐应试', 'success');
            // 同步内存中的 clan_youth_pending
            if (g.county_data && res.clan_youth_pending !== undefined) {
              g.county_data.clan_youth_pending = res.clan_youth_pending;
            }
          })
          .catch(function (err) {
            btn.disabled = false;
            Game.components.showToast(err.message || '操作失败', 'error');
          });
      });
    });

    // 绑定「书信」按钮
    container.querySelectorAll(".btn-agent-letter").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var agentId = parseInt(btn.dataset.agentId, 10);
        var agent = agents.find(function (a) { return a.id === agentId; });
        if (!agent) return;
        var g = Game.state.currentGame;
        if (!g) return;
        Game.letter.openCompose(g.id, [agent]);
      });
    });
  }

  // ==================== Agent Profile Modal ====================

  var PERSONALITY_LABELS = {
    openness: "开放性",
    conscientiousness: "尽责性",
    agreeableness: "宜人性",
  };
  var IDEOLOGY_LABELS = {
    reform_vs_tradition: "革新↔传统",
    people_vs_authority: "民本↔权威",
    pragmatic_vs_idealist: "务实↔理想",
  };

  function openAgentProfile(agent) {
    var modal = el("agent-profile-modal");
    if (!modal) return;

    var body = el("agent-profile-body");
    body.innerHTML = "";

    // Header
    var roleInfo = agent.role_title;
    if (agent.village_name) roleInfo += "（" + agent.village_name + "）";

    var headerHtml =
      '<div class="profile-section">' +
        '<h3>' + agent.name + '</h3>' +
        '<div class="profile-role">' + roleInfo + '</div>' +
        '<div class="profile-bio">' + escapeHtml(agent.bio || "") + '</div>' +
      '</div>';
    body.innerHTML += headerHtml;

    // Stats（宗族后生暂不展示基础属性，防止玩家单纯按数值择优）
    if (agent.role !== 'CLAN_YOUTH') {
      var statsHtml =
        '<div class="profile-section">' +
          '<h4>基础属性</h4>' +
          '<div class="profile-grid">' +
            '<div class="profile-stat"><span class="profile-stat-label">智力</span><span class="profile-stat-value">' + agent.intelligence + '</span></div>' +
            '<div class="profile-stat"><span class="profile-stat-label">魅力</span><span class="profile-stat-value">' + agent.charisma + '</span></div>' +
            '<div class="profile-stat"><span class="profile-stat-label">忠诚</span><span class="profile-stat-value">' + agent.loyalty + '</span></div>' +
            '<div class="profile-stat"><span class="profile-stat-label">好感度</span><span class="profile-stat-value">' + agent.affinity + '</span></div>' +
          '</div>' +
        '</div>';
      body.innerHTML += statsHtml;
    } else {
      body.innerHTML +=
        '<div class="profile-section">' +
          '<div class="profile-stat"><span class="profile-stat-label">好感度</span><span class="profile-stat-value">' + agent.affinity + '</span></div>' +
          '<p class="hint" style="margin-top:6px">此子潜质尚待观察，举荐后方知分晓。</p>' +
        '</div>';
    }

    // Personality
    var p = agent.personality || {};
    var pHtml = '<div class="profile-section"><h4>性格特征</h4><div class="profile-grid">';
    for (var pk in PERSONALITY_LABELS) {
      if (p[pk] !== undefined) {
        pHtml += '<div class="profile-stat"><span class="profile-stat-label">' + PERSONALITY_LABELS[pk] + '</span><span class="profile-stat-value">' + (p[pk] * 100).toFixed(0) + '%</span></div>';
      }
    }
    pHtml += '</div></div>';
    body.innerHTML += pHtml;

    // Ideology
    var ideo = agent.ideology || {};
    var iHtml = '<div class="profile-section"><h4>意识形态</h4><div class="profile-grid">';
    for (var ik in IDEOLOGY_LABELS) {
      if (ideo[ik] !== undefined) {
        iHtml += '<div class="profile-stat"><span class="profile-stat-label">' + IDEOLOGY_LABELS[ik] + '</span><span class="profile-stat-value">' + (ideo[ik] * 100).toFixed(0) + '%</span></div>';
      }
    }
    iHtml += '</div></div>';
    body.innerHTML += iHtml;

    // Reputation
    var rep = agent.reputation || {};
    var rHtml = '<div class="profile-section"><h4>社会声望</h4><div class="profile-grid">';
    if (rep.scholarly !== undefined) rHtml += '<div class="profile-stat"><span class="profile-stat-label">学识</span><span class="profile-stat-value">' + rep.scholarly + '</span></div>';
    if (rep.political !== undefined) rHtml += '<div class="profile-stat"><span class="profile-stat-label">官场</span><span class="profile-stat-value">' + rep.political + '</span></div>';
    if (rep.popular !== undefined) rHtml += '<div class="profile-stat"><span class="profile-stat-label">民间</span><span class="profile-stat-value">' + rep.popular + '</span></div>';
    rHtml += '</div></div>';
    body.innerHTML += rHtml;

    // Goals
    var goals = agent.goals || [];
    if (goals.length > 0) {
      var gHtml = '<div class="profile-section"><h4>目标</h4><ul class="profile-goals">';
      goals.forEach(function (g) { gHtml += '<li>' + escapeHtml(g) + '</li>'; });
      gHtml += '</ul></div>';
      body.innerHTML += gHtml;
    }

    // Backstory
    if (agent.backstory) {
      body.innerHTML += '<div class="profile-section"><h4>背景故事</h4><p class="profile-backstory">' + escapeHtml(agent.backstory) + '</p></div>';
    }

    // Memory
    var mem = agent.all_memory || [];
    if (mem.length > 0) {
      var mHtml = '<div class="profile-section"><h4>记忆 (' + mem.length + ')</h4><div class="profile-memory">';
      mem.forEach(function (m) { mHtml += '<div class="profile-memory-item">' + escapeHtml(m) + '</div>'; });
      mHtml += '</div></div>';
      body.innerHTML += mHtml;
    }

    // Relationships
    var rels = agent.relationships || [];
    if (rels.length > 0) {
      var relsHtml = '<div class="profile-section"><h4>社交关系</h4><div class="profile-rels">';
      rels.forEach(function (r) {
        relsHtml +=
          '<div class="profile-rel-item">' +
            '<span class="profile-rel-name">' + escapeHtml(r.name) + '</span>' +
            '<span class="profile-rel-role">（' + escapeHtml(r.role_title) + '）</span>' +
            '<span class="profile-rel-desc">' + escapeHtml(r.desc) + '</span>' +
          '</div>';
      });
      relsHtml += '</div></div>';
      body.innerHTML += relsHtml;
    }

    // 交谈按钮（本地可对话角色）
    var localChatRoles = { ADVISOR: true, DEPUTY: true, GENTRY: true, VILLAGER: true,
                           LIUFANG: true, CONSTABLE: true, BAILIFF_CEREMONY: true, BAILIFF_LABOR: true };
    if (localChatRoles[agent.role]) {
      var chatDiv = document.createElement("div");
      chatDiv.className = "profile-section profile-chat-footer";
      var chatBtn = document.createElement("button");
      chatBtn.className = "btn btn-primary";
      chatBtn.textContent = "与" + agent.name + "交谈";
      chatBtn.addEventListener("click", function () {
        modal.classList.add("hidden");
        openStaffChat(agent.id, agent.name);
      });
      chatDiv.appendChild(chatBtn);
      body.appendChild(chatDiv);
    }

    modal.classList.remove("hidden");
  }

  // ==================== Staff (幕僚) Tab ====================

  function renderStaffTab(data) {
    var container = el("staff-content");
    container.innerHTML = "";

    // 师爷 section
    if (data.advisor) {
      var a = data.advisor;
      var remaining = a.questions_limit - a.questions_used;
      var btnDisabled = remaining <= 0 ? ' disabled' : '';
      var btnText = remaining <= 0 ? '本月已用尽' : '问策';

      var section = h("div", "staff-section");
      section.innerHTML =
        '<h3>师爷</h3>' +
        '<div class="staff-card">' +
          '<div class="staff-card-header">' +
            '<span class="staff-name">' + escapeHtml(a.name) + '</span>' +
            '<span class="staff-role">' + escapeHtml(a.role_title) + ' · 等级 ' + a.level + '</span>' +
          '</div>' +
          '<div class="staff-bio">' + escapeHtml(a.bio) + '</div>' +
          '<div class="staff-meta">' +
            '<span class="staff-question-badge">剩余问策: ' + remaining + '/' + a.questions_limit + '</span>' +
            '<span class="affinity-value">好感: ' + a.affinity + '</span>' +
          '</div>' +
          '<button class="btn btn-primary btn-small btn-staff-chat" data-agent-id="' + a.agent_id + '" data-agent-name="' + escapeHtml(a.name) + '"' + btnDisabled + '>' + btnText + '</button>' +
        '</div>';
      container.appendChild(section);
    }

    // 县丞 section
    if (data.deputy) {
      var d = data.deputy;
      var section2 = h("div", "staff-section");
      section2.innerHTML =
        '<h3>县丞</h3>' +
        '<div class="staff-card">' +
          '<div class="staff-card-header">' +
            '<span class="staff-name">' + escapeHtml(d.name) + '</span>' +
            '<span class="staff-role">' + escapeHtml(d.role_title) + '</span>' +
          '</div>' +
          '<div class="staff-bio">' + escapeHtml(d.bio) + '</div>' +
          '<div class="staff-meta">' +
            '<span class="affinity-value">好感: ' + d.affinity + '</span>' +
          '</div>' +
          '<button class="btn btn-primary btn-small btn-staff-chat" data-agent-id="' + d.agent_id + '" data-agent-name="' + escapeHtml(d.name) + '">交谈</button>' +
        '</div>';
      container.appendChild(section2);
    }

    // 衙役 section
    if (data.bailiffs) {
      var b = data.bailiffs;
      var section3 = h("div", "staff-section");
      section3.innerHTML =
        '<h3>衙役</h3>' +
        '<div class="staff-card">' +
          '<div class="staff-card-header">' +
            '<span class="staff-name">衙役班</span>' +
            '<span class="staff-role">等级 ' + b.level + ' / ' + b.max_level + '</span>' +
          '</div>' +
          '<div class="staff-bio">现有衙役 <strong>' + b.count + '</strong> 名' +
            '（基础' + b.base_count + '人' + (b.level > 0 ? '，增设' + (b.level * 4) + '人' : '') + '）。' +
            '负责治安巡逻、缉捕盗贼、维持秩序。可通过"施政—增设衙役"扩编。</div>' +
        '</div>';
      container.appendChild(section3);
    }

    // 班房 section
    if (data.liufang && data.liufang.length > 0) {
      var section4 = h("div", "staff-section");
      var liufangHtml = '<h3>六房</h3><div class="liufang-grid">';
      data.liufang.forEach(function (lf) {
        liufangHtml +=
          '<div class="liufang-card">' +
            '<div class="liufang-name">' + escapeHtml(lf.name) + '</div>' +
            '<div class="liufang-desc">' + escapeHtml(lf.desc) + '</div>' +
          '</div>';
      });
      liufangHtml += '</div>';
      section4.innerHTML = liufangHtml;
      container.appendChild(section4);
    }

    // 衙署开支明细 section
    var g = Game.state.currentGame;
    var c = g && g.county_data;
    if (c && c.admin_cost_detail) {
      var LABELS = Game.components.ADMIN_COST_LABELS;
      var keys = ["official_salary", "deputy_salary", "advisor_fee", "clerks_cost",
                  "bailiff_cost", "school_cost", "office_cost"];
      var section5 = h("div", "staff-section");
      var costHtml = '<h3>衙署开支明细</h3><div class="admin-cost-detail" style="display:block">';
      keys.forEach(function (k) {
        var val = c.admin_cost_detail[k];
        if (val !== undefined) {
          costHtml +=
            '<div class="admin-detail-row">' +
              '<span class="admin-detail-label">' + (LABELS[k] || k) + '</span>' +
              '<span class="admin-detail-value">' + val + '两/年</span>' +
            '</div>';
        }
      });
      costHtml +=
        '<div class="admin-detail-row admin-detail-total">' +
          '<span class="admin-detail-label"><strong>合计</strong></span>' +
          '<span class="admin-detail-value"><strong>' + c.admin_cost + '两/年</strong></span>' +
        '</div></div>';
      section5.innerHTML = costHtml;
      container.appendChild(section5);
    }
  }

  function openStaffChat(agentId, agentName) {
    var modal = el("staff-chat-modal");
    el("staff-chat-title").textContent = agentName;
    el("staff-chat-subtitle").textContent = "";
    el("staff-chat-messages").innerHTML = '<p class="hint">加载中...</p>';
    el("staff-chat-input").value = "";
    var hintEl = el("staff-chat-hint");
    if (hintEl) hintEl.innerHTML = "";
    modal.classList.remove("hidden");

    // Load chat history
    var g = Game.state.currentGame;
    if (!g) return;

    // Load chat snapshot (topics + recent_focus); silent on failure
    if (hintEl) {
      Game.api.getChatSnapshot(g.id, agentId).then(function (snap) {
        var html = "";
        if (snap.topics_of_concern && snap.topics_of_concern.length) {
          html += '<div class="chat-topics">关心：' +
            escapeHtml(snap.topics_of_concern.slice(0, 2).join(" / ")) + '</div>';
        }
        if (snap.recent_focus) {
          var warn = snap.has_unresolved_promise ? "⚠ " : "";
          html += '<div class="chat-focus">📜 ' + warn +
            escapeHtml(snap.recent_focus) + '</div>';
        }
        hintEl.innerHTML = html;
      }).catch(function () { /* silent */ });
    }

    Game.api.getAgentChatHistory(g.id, agentId).then(function (data) {
      var container = el("staff-chat-messages");
      container.innerHTML = "";
      if (data.messages && data.messages.length > 0) {
        data.messages.forEach(function (msg) {
          appendStaffChatMessage(msg.role === "player" ? "player" : "agent", msg.content);
        });
      } else {
        container.innerHTML = '<p class="hint">尚无对话记录，请发言开始交谈</p>';
      }
    }).catch(function () {
      el("staff-chat-messages").innerHTML = '<p class="hint">加载失败</p>';
    });

    // Store active agent id for sending
    modal.dataset.agentId = agentId;
  }

  function appendStaffChatMessage(role, content) {
    var container = el("staff-chat-messages");
    // Clear hint if present
    var hint = container.querySelector(".hint");
    if (hint) hint.remove();

    var cls = role === "player" ? "nego-msg-player" : "nego-msg-agent";
    var label = role === "player" ? "你" : el("staff-chat-title").textContent;
    var div = h("div", "nego-msg " + cls,
      '<span class="nego-msg-label">' + escapeHtml(label) + '</span>' +
      '<span class="nego-msg-text">' + escapeHtml(content) + '</span>'
    );
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
  }

  // ── 承诺渲染 ──────────────────────────────────────────────────────────────

  var PROMISE_STATUS_CONF = {
    PENDING:   { label: '待履行', color: '#c0a030', cls: 'promise-status-pending' },
    FULFILLED: { label: '已履行', color: '#27ae60', cls: 'promise-status-fulfilled' },
    BROKEN:    { label: '已违约', color: '#c0392b', cls: 'promise-status-broken' },
  };

  function _promiseDeadlineText(p, currentSeason) {
    var diff = p.deadline_season - (currentSeason || 0);
    if (p.status !== 'PENDING') return '';
    if (diff < 0)  return '（已逾期）';
    if (diff === 0) return '（本月到期）';
    return '（还剩 ' + diff + ' 月）';
  }

  function renderPromiseList(promises, filterStatus, focusAgentId) {
    var container = document.getElementById('promise-list-container');
    if (!container) return;

    var g = Game.state.currentGame;
    var currentSeason = g ? g.current_season : 0;

    var list = promises || [];
    if (filterStatus) list = list.filter(function (p) { return p.status === filterStatus; });
    if (focusAgentId) list = list.filter(function (p) { return p.agent_id === focusAgentId; });

    if (!list.length) {
      container.innerHTML = '<p class="hint">' + (focusAgentId ? '该人物暂无承诺记录' : '暂无承诺记录') + '</p>';
      return;
    }

    // 排序：待履行在前，按截止月升序；其余按时间倒序
    var pending = list.filter(function (p) { return p.status === 'PENDING'; })
      .sort(function (a, b) { return a.deadline_season - b.deadline_season; });
    var others = list.filter(function (p) { return p.status !== 'PENDING'; })
      .sort(function (a, b) { return b.season_made - a.season_made; });
    var sorted = pending.concat(others);

    var html = '';
    // 若有 focusAgentId，显示人物名标题
    if (focusAgentId && list.length > 0) {
      html += '<div class="promise-agent-focus-hd">与 ' + escapeHtml(list[0].agent_name) + ' 的承诺</div>';
    }

    sorted.forEach(function (p) {
      var conf = PROMISE_STATUS_CONF[p.status] || { label: p.status, color: '#888', cls: '' };
      var deadlineText = _promiseDeadlineText(p, currentSeason);
      var overdue = p.status === 'PENDING' && p.deadline_season < currentSeason;
      html +=
        '<div class="promise-card' + (overdue ? ' promise-card-overdue' : '') + '" data-promise-id="' + p.id + '">' +
          '<div class="promise-card-bar" style="background:' + conf.color + ';"></div>' +
          '<div class="promise-card-body">' +
            '<div class="promise-card-top">' +
              '<span class="promise-type-tag">' + escapeHtml(p.promise_type_display) + '</span>' +
              '<span class="promise-status-tag ' + conf.cls + '">' + conf.label + '</span>' +
            '</div>' +
            '<div class="promise-card-desc">' + escapeHtml(p.description) + '</div>' +
            '<div class="promise-card-meta">' +
              '<span class="promise-agent-name">对象：' + escapeHtml(p.agent_name) + '</span>' +
              '<span class="promise-deadline">截止第 ' + p.deadline_season + ' 月' + escapeHtml(deadlineText) + '</span>' +
            '</div>' +
          '</div>' +
        '</div>';
    });
    container.innerHTML = html;

    // 点击查看详情
    container.querySelectorAll('.promise-card').forEach(function (card) {
      card.addEventListener('click', function () {
        var pid = parseInt(card.dataset.promiseId, 10);
        var p = (Game.state.promises || []).find(function (x) { return x.id === pid; });
        if (p) openPromiseDetail(p);
      });
    });
  }

  function openPromiseDetail(p) {
    var modal = document.getElementById('promise-detail-modal');
    var title = document.getElementById('promise-detail-title');
    var body  = document.getElementById('promise-detail-body');
    if (!modal || !title || !body) return;

    var conf = PROMISE_STATUS_CONF[p.status] || { label: p.status, color: '#888' };
    title.textContent = p.promise_type_display + '——' + p.agent_name;

    var g = Game.state.currentGame;
    var currentSeason = g ? g.current_season : 0;
    var deadlineText = _promiseDeadlineText(p, currentSeason);
    var overdue = p.status === 'PENDING' && p.deadline_season < currentSeason;

    body.innerHTML =
      '<div class="promise-detail-status" style="color:' + conf.color + ';">' + conf.label + (overdue ? '（已逾期）' : '') + '</div>' +
      '<div class="promise-detail-row"><span class="pdl">承诺内容</span><span>' + escapeHtml(p.description) + '</span></div>' +
      '<div class="promise-detail-row"><span class="pdl">承诺对象</span><span>' + escapeHtml(p.agent_name) + '</span></div>' +
      '<div class="promise-detail-row"><span class="pdl">承诺时间</span><span>第 ' + p.season_made + ' 月</span></div>' +
      '<div class="promise-detail-row"><span class="pdl">截止时间</span><span>第 ' + p.deadline_season + ' 月' + escapeHtml(deadlineText) + '</span></div>';

    modal.classList.remove('hidden');
  }

  // 人物卡片上的承诺 badge（pending 数量）
  function _buildPromiseBadgeHtml(agentId, promisesByAgentId) {
    var list = (promisesByAgentId || {})[agentId] || [];
    var pending = list.filter(function (p) { return p.status === 'PENDING'; });
    if (!pending.length) return '';
    return (
      '<div class="promise-agent-badge" data-agent-id="' + agentId + '">' +
        '<span class="promise-agent-badge-icon">⚑</span>' +
        '<span>' + pending.length + ' 项待履行承诺</span>' +
        '<button class="btn-promise-view btn btn-small">查看</button>' +
      '</div>'
    );
  }

  // Export
  C.checkActiveNegotiation = checkActiveNegotiation;
  C.renderNegotiationBanner = renderNegotiationBanner;
  C.renderIrrigationNegotiateSection = renderIrrigationNegotiateSection;
  C.openNegotiationModal = openNegotiationModal;
  C.renderNegotiationMessages = renderNegotiationMessages;
  C.appendNegotiationMessage = appendNegotiationMessage;
  C.showNegotiationResolved = showNegotiationResolved;
  C.renderEventLogs = renderEventLogs;
  C.renderRelationships = renderRelationships;
  C.renderPromiseList = renderPromiseList;
  C.openPromiseDetail = openPromiseDetail;
  C.renderClanOverview = _renderClanOverview;
  C.openAgentProfile = openAgentProfile;
  C.renderStaffTab = renderStaffTab;
  C.openStaffChat = openStaffChat;
  C.appendStaffChatMessage = appendStaffChatMessage;

  // ── 师爷提示对话框 ──────────────────────────────────────────────────────────
  // showAdvisorDialog(text, buttons)
  // buttons: [{label, action}]

  function showAdvisorDialog(text, buttons) {
    var modal = el('advisor-dialog-modal');
    var textEl = el('advisor-dialog-text');
    var btnsEl = el('advisor-dialog-btns');
    if (!modal) return;

    textEl.textContent = text;
    btnsEl.innerHTML = '';
    buttons.forEach(function (b) {
      var btn = document.createElement('button');
      btn.className = 'btn btn-small';
      btn.textContent = b.label;
      btn.addEventListener('click', function () {
        modal.classList.add('hidden');
        if (b.action) b.action();
      });
      btnsEl.appendChild(btn);
    });

    modal.classList.remove('hidden');
    // 点击背景关闭（取第一个按钮动作 = 默认"继续"）
    modal.onclick = function (e) {
      if (e.target === modal) {
        modal.classList.add('hidden');
      }
    };
  }

  C.showAdvisorDialog = showAdvisorDialog;
})();
