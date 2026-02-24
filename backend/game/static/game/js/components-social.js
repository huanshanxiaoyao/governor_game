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
  };

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

    if (session.status === "resolved") {
      inputArea.classList.add("hidden");
      resolvedDiv.classList.remove("hidden");
      resolvedDiv.innerHTML = '<div class="nego-outcome">谈判已结束</div>';
    } else {
      inputArea.classList.remove("hidden");
    }

    el("negotiation-modal").classList.remove("hidden");
    el("nego-input").value = "";
    el("nego-input").focus();

    // Load history
    Game.api.getNegotiationHistory(g.id, session.id)
      .then(function (data) {
        renderNegotiationMessages(data.messages || []);
        if (data.session) {
          Game.state.activeNegotiation = data.session;
          el("nego-subtitle").textContent =
            data.session.agent_name + "（" + data.session.agent_role_title + "） " +
            "第" + data.session.current_round + "/" + data.session.max_rounds + "轮";
        }
      })
      .catch(function () {
        msgContainer.innerHTML = '<p class="hint">加载失败</p>';
      });
  }

  function renderNegotiationMessages(messages) {
    var container = el("nego-messages");
    container.innerHTML = "";

    if (messages.length === 0) {
      container.innerHTML = '<p class="hint" style="text-align:center;padding:20px;">开始与对方交涉吧</p>';
      return;
    }

    messages.forEach(function (msg) {
      var cls = msg.role === "player" ? "nego-msg nego-msg-player" : "nego-msg nego-msg-agent";
      var label = msg.role === "player" ? "县令" : "";
      var div = h("div", cls,
        (label ? '<span class="nego-msg-label">' + label + '</span>' : '') +
        '<div class="nego-msg-content">' + escapeHtml(msg.content) + '</div>');
      container.appendChild(div);
    });

    container.scrollTop = container.scrollHeight;
  }

  function appendNegotiationMessage(role, content) {
    var container = el("nego-messages");
    // Remove the hint if present
    var hint = container.querySelector(".hint");
    if (hint) hint.remove();

    var cls = role === "player" ? "nego-msg nego-msg-player" : "nego-msg nego-msg-agent";
    var label = role === "player" ? "县令" : "";
    var div = h("div", cls,
      (label ? '<span class="nego-msg-label">' + label + '</span>' : '') +
      '<div class="nego-msg-content">' + escapeHtml(content) + '</div>');
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
      "accept": "地主同意出资",
      "refuse": "地主拒绝出资",
    }[decision] || decision;

    var extraHtml = "";
    if (result.event_type === "IRRIGATION" && decision === "accept" && result.contribution_offer) {
      extraHtml =
        '<br><span style="color:#6b8f4a;font-weight:bold;">出资: ' +
        result.contribution_offer + ' 两</span>';
      if (result.treasury !== undefined) {
        extraHtml += '<br>县库余额: ' + result.treasury + ' 两';
      }
    }

    resolvedDiv.innerHTML =
      '<div class="nego-outcome">' +
        '<strong>谈判结束</strong><br>' +
        decisionText + extraHtml +
      '</div>';

    el("nego-subtitle").textContent += " [已结束]";
  }

  // ==================== Event Logs ====================

  var CATEGORY_LABELS = {
    SYSTEM: "系统",
    INVESTMENT: "投资",
    TAX: "税率",
    NEGOTIATION: "谈判",
    DISASTER: "灾害",
    SETTLEMENT: "结算",
    ANNEXATION: "兼并",
    PROMISE: "承诺",
  };

  var CATEGORY_COLORS = {
    SYSTEM: "#8a7a5a",
    INVESTMENT: "#2e86c1",
    TAX: "#8e44ad",
    NEGOTIATION: "#d4a017",
    DISASTER: "#c0392b",
    SETTLEMENT: "#27ae60",
    ANNEXATION: "#e67e22",
    PROMISE: "#8e44ad",
  };

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

      var item = h("div", "event-log-item");
      item.innerHTML =
        '<div class="event-log-header">' +
          '<span class="event-log-category" style="background:' + catColor + ';">' + catLabel + '</span>' +
          '<span class="event-log-season">' + seasonText + '</span>' +
        '</div>' +
        '<div class="event-log-desc">' + escapeHtml(log.description || log.event_type) + '</div>';
      container.appendChild(item);
    });
  }

  // ==================== Relationships ====================

  function renderRelationships(agents) {
    var container = el("relationships-list");
    container.innerHTML = "";

    if (!agents || agents.length === 0) {
      container.innerHTML = '<p class="hint">暂无人脉数据</p>';
      return;
    }

    agents.forEach(function (a) {
      var card = h("div", "relationship-card");

      // Affinity coloring
      var aff = a.affinity;
      var affClass = aff < 30 ? "affinity-low" : (aff < 60 ? "affinity-mid" : "affinity-high");
      var barColor = aff < 30 ? "#c0392b" : (aff < 60 ? "#d4a017" : "#27ae60");

      // Affinity bar: map -99..99 to 0..100%
      var barPct = Math.max(0, Math.min(100, ((aff + 99) / 198) * 100));

      var roleInfo = a.role_title;
      if (a.village_name) roleInfo += "（" + a.village_name + "）";

      var html =
        '<div class="relationship-card-header">' +
          '<span class="relationship-name">' + a.name + '</span>' +
          '<span class="relationship-role">' + roleInfo + '</span>' +
        '</div>' +
        '<div class="affinity-value ' + affClass + '">好感度: ' + aff + '</div>' +
        '<div class="affinity-bar">' +
          '<div class="affinity-bar-fill" style="width:' + barPct.toFixed(1) + '%;background:' + barColor + ';"></div>' +
        '</div>';

      // Recent memories
      if (a.memory && a.memory.length > 0) {
        html += '<div class="memory-list">';
        a.memory.forEach(function (m) {
          html += '<div class="memory-list-item">' + escapeHtml(m) + '</div>';
        });
        html += '</div>';
      }

      card.innerHTML = html;
      container.appendChild(card);
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

    // Stats
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
    modal.classList.remove("hidden");

    // Load chat history
    var g = Game.state.currentGame;
    if (!g) return;

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
  C.openAgentProfile = openAgentProfile;
  C.renderStaffTab = renderStaffTab;
  C.openStaffChat = openStaffChat;
  C.appendStaffChatMessage = appendStaffChatMessage;
})();
