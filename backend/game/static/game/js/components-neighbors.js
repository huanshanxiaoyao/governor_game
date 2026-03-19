/**
 * Neighbor county components — Game.components namespace (partial)
 * Depends on: components-core.js
 */
(function () {
  "use strict";

  var C = Game.components;
  var el = C.el, h = C.h, escapeHtml = C.escapeHtml;
  var calcMedicalCost = C.calcMedicalCost;
  var MEDICAL_NAMES = C.MEDICAL_NAMES;
  var ADMIN_COST_LABELS = C.ADMIN_COST_LABELS;

  var GOVERNOR_STYLE_COLORS = {
    minben: "#27ae60",
    zhengji: "#2e86c1",
    baoshou: "#8a7a5a",
    jinqu: "#c0392b",
    yuanhua: "#8e44ad",
  };

  var GOVERNOR_ARCHETYPE_COLORS = {
    VIRTUOUS: "#1a7a4a",
    MIDDLING: "#6b5d45",
    CORRUPT:  "#a93226",
  };

  function renderNeighborsList(neighbors) {
    var container = el("neighbors-list");
    container.innerHTML = "";

    if (!neighbors || neighbors.length === 0) {
      container.innerHTML = '<p class="hint">暂无邻县数据</p>';
      return;
    }

    var grid = h("div", "neighbors-grid");

    neighbors.forEach(function (n) {
      var cd = n.county_data || {};
      var totalPop = 0;
      (cd.villages || []).forEach(function (v) { totalPop += v.population; });

      var styleColor = GOVERNOR_STYLE_COLORS[n.governor_style] || "#8a7a5a";
      var archetypeColor = GOVERNOR_ARCHETYPE_COLORS[n.governor_archetype] || "#6b5d45";
      var archetypeLabel = escapeHtml(n.governor_archetype_display || "");
      var styleLabel = escapeHtml(n.governor_style_display || n.governor_style);

      // 农民粮食盈余（复用玩家侧逻辑）
      var ps = cd.peasant_surplus || {};
      var ncc = ps.consumer_confidence !== undefined ? ps.consumer_confidence : null;
      var grainHtml = "";
      if (ps.reserve !== undefined) {
        var g = Game.state.currentGame;
        var curSeason = g ? (g.current_season || 1) : 1;
        var curMoy = ((curSeason - 1) % 12) + 1;
        var rawDist = (9 - curMoy + 12) % 12;
        var harvestLabel = rawDist === 0 ? "今月秋收" : rawDist + "月";
        var ccIcon = ncc === null ? "" : (ncc >= 10 ? "↑" : (ncc <= 0 ? "↓" : "→"));
        var ccClass = ncc === null ? "" : (ncc >= 10 ? "delta-positive" : (ncc <= 0 ? "delta-negative" : ""));
        var ccText = ncc !== null
          ? '<span class="' + ccClass + '">' + ncc + '斤/人 ' + ccIcon + '</span>'
          : "—";
        grainHtml =
          '<div class="neighbor-grain">' +
            '<span>储粮 ' + Math.round(ps.reserve || 0).toLocaleString() + '斤</span>' +
            '<span>距秋收 ' + harvestLabel + '</span>' +
            '<span>月均余粮 ' + ccText + '</span>' +
            '<span>信心 ' + (ps.confidence_index !== undefined ? ps.confidence_index : '—') + '</span>' +
          '</div>';
      }

      // AI施政分析：取 last_reasoning 前两行拼合显示
      var reasoningHtml = '';
      if (n.last_reasoning) {
        var lines = n.last_reasoning.split('\n').filter(function (l) { return l.trim(); });
        var preview = lines.slice(0, 2).join(' ').substring(0, 140);
        if (n.last_reasoning.length > 140) preview += '…';
        reasoningHtml =
          '<div class="neighbor-analysis">' +
            '<span class="neighbor-analysis-label">【AI析】</span>' +
            escapeHtml(preview) +
          '</div>';
      }

      var card = h("div", "neighbor-card");
      card.dataset.neighborId = n.id;
      card.innerHTML =
        '<div class="neighbor-card-header">' +
          '<span class="neighbor-county-name">' + escapeHtml(n.county_name) + '</span>' +
          '<span class="neighbor-type-tag">' + escapeHtml(n.county_type_name || '') + '</span>' +
        '</div>' +
        '<div class="neighbor-governor">' +
          '<span class="neighbor-governor-name">' + escapeHtml(n.governor_name) + ' 知县</span>' +
          '<span class="neighbor-archetype-tag" style="background:' + archetypeColor + ';">' + archetypeLabel + '</span>' +
          '<span class="neighbor-style-tag" style="background:' + styleColor + ';">' + styleLabel + '</span>' +
        '</div>' +
        '<div class="neighbor-stats">' +
          '<span>人口 ' + totalPop + '</span>' +
          '<span>县库 ' + Math.round(cd.treasury || 0) + '两</span>' +
          '<span>民心 ' + Math.round(cd.morale || 0) + '</span>' +
          '<span>治安 ' + Math.round(cd.security || 0) + '</span>' +
          '<span>行政开支 ' + Math.round(cd.admin_cost || 0) + '两/年</span>' +
        '</div>' +
        grainHtml +
        reasoningHtml +
        '<div class="neighbor-card-actions">' +
          '<button class="neighbor-events-btn" data-neighbor-id="' + n.id + '">施政动态</button>' +
        '</div>';

      grid.appendChild(card);
    });

    container.appendChild(grid);
  }

  function openNeighborDetail(neighbor) {
    // 使用共享模块打开统一双标签详情卡片
    Game.countyDetail.open(neighbor, "neighbor");
  }

  function openNeighborTermReport(report) {
    var modal = el("neighbor-detail-modal");
    var body = el("neighbor-detail-body");
    if (!modal || !body) return;

    var headline = report.headline || {};
    var governor = report.governor || {};
    var scores = report.scores || {};
    var disaster = report.disaster_adjustment || {};

    el("neighbor-detail-title").textContent =
      (governor.county_name || "邻县") + " — " + (governor.governor_name || "知县") + " 任期述职";

    function fmtSigned(val, digits) {
      if (val === null || val === undefined || isNaN(val)) return "-";
      var n = Number(val);
      var sign = n > 0 ? "+" : "";
      return sign + n.toFixed(digits);
    }

    function fmtMaybe(val, digits) {
      if (val === null || val === undefined || isNaN(val)) return "-";
      return Number(val).toFixed(digits);
    }

    var html = "";
    html +=
      '<div class="nd-section">' +
        '<h4>综合评价</h4>' +
        '<div class="nd-governor-card">' +
          '<div style="margin-bottom:6px;"><strong>综合分：</strong>' + escapeHtml(fmtMaybe(headline.overall_score, 1)) + "</div>" +
          '<div style="margin-bottom:6px;"><strong>评级：</strong>' + escapeHtml(headline.grade || "-") +
            '　<strong>结论：</strong>' + escapeHtml(headline.outcome || "-") + "</div>" +
          '<div style="margin-bottom:6px;"><strong>排名：</strong>' +
            escapeHtml((scores.rank && scores.total_count) ? (scores.rank + "/" + scores.total_count) : "-") + "</div>" +
          '<div>' + escapeHtml(headline.narrative || "") + "</div>" +
        "</div>" +
      "</div>";

    var kpis = report.kpi_cards || [];
    if (kpis.length > 0) {
      html += '<div class="nd-section"><h4>核心指标</h4><table class="data-table" style="font-size:0.85em;">' +
        "<thead><tr><th>指标</th><th>基线</th><th>期末</th><th>变化</th></tr></thead><tbody>";
      kpis.forEach(function (k) {
        var unit = k.unit || "";
        var deltaText = "-";
        if (k.delta !== undefined && k.delta !== null) {
          deltaText = fmtSigned(k.delta, Math.abs(k.delta) < 10 ? 1 : 0) + unit;
        } else if (k.delta_pct !== undefined && k.delta_pct !== null) {
          deltaText = fmtSigned(k.delta_pct, 1) + "%";
        }
        html += "<tr>" +
          "<td>" + escapeHtml(k.label || "") + "</td>" +
          "<td>" + escapeHtml((k.initial === null || k.initial === undefined) ? "-" : String(k.initial) + unit) + "</td>" +
          "<td>" + escapeHtml((k.final === null || k.final === undefined) ? "-" : String(k.final) + unit) + "</td>" +
          "<td>" + escapeHtml(deltaText) + "</td>" +
          "</tr>";
      });
      html += "</tbody></table></div>";
    }

    html +=
      '<div class="nd-section">' +
        '<h4>灾害校正</h4>' +
        '<div class="nd-governor-card">' +
          '<div><strong>灾害次数：</strong>' + escapeHtml(String(disaster.disaster_count || 0)) + "</div>" +
          '<div><strong>暴露强度：</strong>' + escapeHtml(fmtMaybe(disaster.exposure, 3)) +
            "（邻县均值 " + escapeHtml(fmtMaybe(disaster.peer_avg_exposure, 3)) + "）</div>" +
          '<div><strong>暴露差值：</strong>' + escapeHtml(fmtSigned(disaster.exposure_gap, 3)) +
            "，<strong>消偏系数：</strong>x" + escapeHtml(fmtMaybe(disaster.disaster_multiplier, 3)) + "</div>" +
        "</div>" +
      "</div>";

    var years = report.yearly_reports || [];
    if (years.length > 0) {
      html += '<div class="nd-section"><h4>年度复盘</h4>';
      years.forEach(function (y) {
        var winter = y.winter_snapshot || {};
        var autumn = y.autumn || {};
        html +=
          '<div class="nd-governor-card" style="margin-bottom:8px;">' +
            '<div style="margin-bottom:4px;"><strong>第' + y.year + "年</strong></div>" +
            '<div style="font-size:0.9em;color:#5c4a2a;">县库' + (winter.treasury !== undefined ? winter.treasury : "-") +
            "两，民心" + (winter.morale !== undefined ? winter.morale : "-") +
            "，治安" + (winter.security !== undefined ? winter.security : "-") +
            "，秋收总税" + (autumn.total_tax !== undefined ? autumn.total_tax : "-") + "两</div>" +
            '<div style="margin-top:4px;font-size:0.85em;color:#6b5d45;">' + escapeHtml(y.summary_text || "") + "</div>" +
          "</div>";
      });
      html += "</div>";
    }

    var highlights = report.highlights || [];
    if (highlights.length > 0) {
      html += '<div class="nd-section"><h4>亮点</h4><ul class="summary2-list">';
      highlights.forEach(function (item) {
        html += "<li><strong>" + escapeHtml(item.title || "") + "：</strong>" + escapeHtml(item.detail || "") + "</li>";
      });
      html += "</ul></div>";
    }

    var risks = report.risks || [];
    if (risks.length > 0) {
      html += '<div class="nd-section"><h4>风险</h4><ul class="summary2-list">';
      risks.forEach(function (item) {
        html += "<li><strong>" + escapeHtml(item.title || "") + "：</strong>" + escapeHtml(item.detail || "") + "</li>";
      });
      html += "</ul></div>";
    }

    var recentEvents = report.recent_events || [];
    if (recentEvents.length > 0) {
      html += '<div class="nd-section"><h4>近期事件</h4>';
      recentEvents.forEach(function (evt) {
        html += '<div class="event-log-item">' +
          '<div class="event-log-header">' +
          '<span class="event-log-category">' + escapeHtml(evt.category || "") + "</span>" +
          '<span class="event-log-season">第' + escapeHtml(String(evt.season || "-")) + "月</span>" +
          "</div>" +
          '<div class="event-log-desc">' + escapeHtml(evt.description || "") + "</div>" +
          "</div>";
      });
      html += "</div>";
    }

    body.innerHTML = html;
    modal.classList.remove("hidden");
  }

  // ==================== 本府概览卡片 ====================

  function renderPrefectureCard(data) {
    var container = el("prefecture-overview-card");
    if (!container) return;

    if (!data) {
      container.innerHTML = '';
      return;
    }

    var qp = data.quota_progress || {};
    var dirs = data.pending_directives || [];
    var latestDir = dirs.length > 0 ? dirs[dirs.length - 1] : null;

    var dirHtml = '';
    if (latestDir) {
      dirHtml =
        '<div class="pref-directive">' +
          '<span class="pref-directive-type">【来文·' + escapeHtml(latestDir.directive_type || '') + '】</span> ' +
          escapeHtml((latestDir.text || '').substring(0, 50)) + (latestDir.text && latestDir.text.length > 50 ? '…' : '') +
        '</div>';
    }

    var inspectHtml = data.inspection_pending
      ? '<span class="pref-inspect-badge">巡查待进行</span>'
      : '';

    var quotaBar = '';
    var pct = qp.completion_pct || 0;
    var statusColor = pct >= (qp.expected_pct || 50) - 5 ? '#27ae60' : (pct < (qp.expected_pct || 50) - 20 ? '#c0392b' : '#c0702a');
    quotaBar =
      '<div class="pref-quota-bar-wrap">' +
        '<div class="pref-quota-bar" style="width:' + Math.min(100, pct) + '%;background:' + statusColor + ';"></div>' +
      '</div>' +
      '<div class="pref-quota-text">' +
        '年度农赋：' + escapeHtml(String(qp.agri_remitted || 0)) + ' / ' + escapeHtml(String(qp.agri_quota || 0)) + ' 两 (' +
        escapeHtml(String(pct)) + '%)&ensp;<span style="color:' + statusColor + ';">' + escapeHtml(qp.status || '') + '</span>' +
      '</div>';

    container.innerHTML =
      '<div class="prefecture-overview-card">' +
        '<div class="pref-card-header">' +
          '<span class="pref-name">' + escapeHtml(data.prefecture_name || '本府') + '</span>' +
          '<span class="pref-official">' + escapeHtml(data.prefect_name || '') + ' ' + escapeHtml(data.prefect_title || '知府') + '</span>' +
          '<span class="pref-affinity-tag" style="background:' + escapeHtml(data.affinity_color || '#6b5d45') + ';">' +
            escapeHtml(data.affinity_label || '尚可') +
          '</span>' +
          inspectHtml +
        '</div>' +
        quotaBar +
        dirHtml +
        '<button class="pref-gazette-btn" id="open-prefecture-gazette-btn">查看府志</button>' +
      '</div>';
  }

  function openPrefectureGazette(data) {
    var modal = el("prefecture-gazette-modal");
    var body = el("prefecture-gazette-body");
    var titleEl = el("prefecture-gazette-title");
    if (!modal || !body) return;

    if (titleEl) titleEl.textContent = (data.prefecture_name || '本府') + ' 府志';

    var CATEGORY_COLORS = {
      'PREFECT': '#2e86c1',
      'SETTLEMENT': '#6b5d45',
      'INVESTMENT': '#27ae60',
      'DISASTER': '#c0392b',
      'TAX': '#8e44ad',
      'NEGOTIATION': '#c0702a',
      'ANNEXATION': '#7b241c',
      'PROMISE': '#1a7a4a',
      'SYSTEM': '#888',
    };

    var entries = data.gazette_entries || [];
    var html = '';

    // 府情摘要
    var qp = data.quota_progress || {};
    html +=
      '<div class="nd-section">' +
        '<h4>府情</h4>' +
        '<div class="nd-governor-card">' +
          '<div><strong>知府：</strong>' + escapeHtml(data.prefect_name || '') + ' （' + escapeHtml(data.prefect_title || '') + '）</div>' +
          '<div><strong>上司态度：</strong><span style="color:' + escapeHtml(data.affinity_color || '#6b5d45') + ';">' + escapeHtml(data.affinity_label || '') + '</span></div>' +
          (data.bio ? '<div style="margin-top:4px;color:#5c4a2a;font-size:0.9em;">' + escapeHtml(data.bio) + '</div>' : '') +
          '<div style="margin-top:6px;"><strong>年度农赋：</strong>' + escapeHtml(String(qp.agri_remitted || 0)) + '/' + escapeHtml(String(qp.agri_quota || 0)) + ' 两 — ' + escapeHtml(qp.status || '') + '</div>' +
        '</div>' +
      '</div>';

    // 待处理指令
    var dirs = data.pending_directives || [];
    if (dirs.length > 0) {
      html += '<div class="nd-section"><h4>待处理知府来文</h4>';
      dirs.forEach(function (d) {
        html +=
          '<div class="nd-governor-card" style="margin-bottom:6px;">' +
            '<div><strong>【' + escapeHtml(d.directive_type || '') + '】</strong>&ensp;第' + escapeHtml(String(d.month || '-')) + '月</div>' +
            '<div style="margin-top:4px;">' + escapeHtml(d.text || '') + '</div>' +
          '</div>';
      });
      html += '</div>';
    }

    // 府志事件流
    html += '<div class="nd-section"><h4>府志·事件记录</h4>';
    if (entries.length === 0) {
      html += '<p class="hint">暂无记录</p>';
    } else {
      entries.forEach(function (e) {
        var color = CATEGORY_COLORS[e.category] || '#6b5d45';
        html +=
          '<div class="event-log-item">' +
            '<div class="event-log-header">' +
              '<span class="event-log-category" style="background:' + color + ';">' + escapeHtml(e.category_display || e.category) + '</span>' +
              '<span class="event-log-season">第' + escapeHtml(String(e.year || '-')) + '年 ' + escapeHtml(String(e.month || e.season || '-')) + '月</span>' +
            '</div>' +
            '<div class="event-log-desc">' + escapeHtml(e.description || '') + '</div>' +
          '</div>';
      });
    }
    html += '</div>';

    body.innerHTML = html;
    modal.classList.remove("hidden");
  }

  // ==================== 施政动态面板 ====================

  function showNeighborEvents(neighborId) {
    var g = Game.state.currentGame;
    if (!g) return;
    var gameId = g.id;

    // 找到对应邻县名，用于标题
    var neighborName = "邻县";
    var neighborGovernor = "";
    var allNeighbors = Game.state.neighbors || [];
    for (var i = 0; i < allNeighbors.length; i++) {
      if (String(allNeighbors[i].id) === String(neighborId)) {
        neighborName = allNeighbors[i].county_name || "邻县";
        neighborGovernor = allNeighbors[i].governor_name || "";
        break;
      }
    }

    // 复用现有的 neighbor-detail-modal
    var modal = el("neighbor-detail-modal");
    var body = el("neighbor-detail-body");
    var titleEl = el("neighbor-detail-title");
    if (!modal || !body) return;

    if (titleEl) titleEl.textContent = neighborName + (neighborGovernor ? " — " + neighborGovernor + " 施政动态" : " 施政动态");
    body.innerHTML = '<p class="hint">加载中…</p>';
    modal.classList.remove("hidden");

    Game.api.getNeighborEvents(gameId, neighborId, 20).then(function (data) {
      var events = Array.isArray(data) ? data : (data.events || data.results || []);
      if (!events.length) {
        body.innerHTML = '<p class="hint">暂无施政记录</p>';
        return;
      }

      var html = '<div class="nd-section"><h4>近期施政动态</h4>';
      events.forEach(function (evt) {
        var category = evt.category || evt.event_type || "决策";
        var desc = evt.description || evt.detail || "";
        var season = evt.season || evt.month || "";
        html +=
          '<div class="event-log-item">' +
            '<div class="event-log-header">' +
              '<span class="event-log-category">' + escapeHtml(category) + '</span>' +
              (season ? '<span class="event-log-season">第' + escapeHtml(String(season)) + '月</span>' : '') +
            '</div>' +
            '<div class="event-log-desc">' + escapeHtml(desc) + '</div>' +
          '</div>';
      });
      html += '</div>';
      body.innerHTML = html;
    }).catch(function () {
      body.innerHTML = '<p class="hint">加载失败，请稍后重试</p>';
    });
  }

  // 事件委托：点击「施政动态」按钮
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".neighbor-events-btn");
    if (btn) {
      showNeighborEvents(btn.dataset.neighborId);
    }
  });

  // Export
  C.renderNeighborsList = renderNeighborsList;
  C.openNeighborDetail = openNeighborDetail;
  C.openNeighborTermReport = openNeighborTermReport;
  C.renderPrefectureCard = renderPrefectureCard;
  C.openPrefectureGazette = openPrefectureGazette;
  C.showNeighborEvents = showNeighborEvents;
})();
