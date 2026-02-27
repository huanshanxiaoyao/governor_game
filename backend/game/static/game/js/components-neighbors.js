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

  var GOVERNOR_STYLE_COLORS = {
    minben: "#27ae60",
    zhengji: "#2e86c1",
    baoshou: "#8a7a5a",
    jinqu: "#c0392b",
    yuanhua: "#8e44ad",
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

      var card = h("div", "neighbor-card");
      card.dataset.neighborId = n.id;
      card.innerHTML =
        '<div class="neighbor-card-header">' +
          '<span class="neighbor-county-name">' + escapeHtml(n.county_name) + '</span>' +
          '<span class="neighbor-type-tag">' + escapeHtml(n.county_type_name || '') + '</span>' +
        '</div>' +
        '<div class="neighbor-governor">' +
          '<span class="neighbor-governor-name">' + escapeHtml(n.governor_name) + ' 知县</span>' +
          '<span class="neighbor-style-tag" style="background:' + styleColor + ';">' +
            escapeHtml(n.governor_style_display || n.governor_style) + '</span>' +
        '</div>' +
        '<div class="neighbor-stats">' +
          '<span>人口 ' + totalPop + '</span>' +
          '<span>县库 ' + Math.round(cd.treasury || 0) + '两</span>' +
          '<span>民心 ' + Math.round(cd.morale || 0) + '</span>' +
          '<span>治安 ' + Math.round(cd.security || 0) + '</span>' +
        '</div>' +
        (n.last_reasoning ?
          '<div class="neighbor-analysis">' +
            escapeHtml(n.last_reasoning.split('\n')[0].substring(0, 80)) +
          '</div>' : '');

      grid.appendChild(card);
    });

    container.appendChild(grid);
  }

  function openNeighborDetail(neighbor) {
    var modal = el("neighbor-detail-modal");
    var body = el("neighbor-detail-body");
    var cd = neighbor.county_data || {};

    el("neighbor-detail-title").textContent = neighbor.county_name + " — " + neighbor.governor_name + " 知县";

    var styleColor = GOVERNOR_STYLE_COLORS[neighbor.governor_style] || "#8a7a5a";
    var totalPop = 0;
    (cd.villages || []).forEach(function (v) { totalPop += v.population; });

    var html = '';

    // 知县人设
    html +=
      '<div class="nd-section">' +
        '<h4>知县人设</h4>' +
        '<div class="nd-governor-card">' +
          '<span class="neighbor-style-tag" style="background:' + styleColor + ';">' +
            escapeHtml(neighbor.governor_style_display || neighbor.governor_style) + '</span>' +
          '<p>' + escapeHtml(neighbor.governor_bio || '') + '</p>' +
        '</div>' +
      '</div>';

    // 指标网格
    var ml = cd.medical_level || 0;
    var neighborMedCost = calcMedicalCost(ml, cd);
    html +=
      '<div class="nd-section">' +
        '<h4>县情指标</h4>' +
        '<div class="nd-stats-grid">' +
          '<div class="nd-stat"><span class="nd-stat-label">人口</span><span class="nd-stat-value">' + totalPop + '</span></div>' +
          '<div class="nd-stat"><span class="nd-stat-label">县库</span><span class="nd-stat-value">' + Math.round(cd.treasury || 0) + '两</span></div>' +
          '<div class="nd-stat"><span class="nd-stat-label">民心</span><span class="nd-stat-value">' + Math.round(cd.morale || 0) + '</span></div>' +
          '<div class="nd-stat"><span class="nd-stat-label">治安</span><span class="nd-stat-value">' + Math.round(cd.security || 0) + '</span></div>' +
          '<div class="nd-stat"><span class="nd-stat-label">商业</span><span class="nd-stat-value">' + Math.round(cd.commercial || 0) + '</span></div>' +
          '<div class="nd-stat"><span class="nd-stat-label">文教</span><span class="nd-stat-value">' + Math.round(cd.education || 0) + '</span></div>' +
          '<div class="nd-stat"><span class="nd-stat-label">税率</span><span class="nd-stat-value">' + Math.round((cd.tax_rate || 0.12) * 100) + '%</span></div>' +
          '<div class="nd-stat"><span class="nd-stat-label">水利</span><span class="nd-stat-value">' + (cd.irrigation_level || 0) + '/2</span></div>' +
          '<div class="nd-stat"><span class="nd-stat-label">衙役</span><span class="nd-stat-value">' + (cd.bailiff_level || 0) + '/3</span></div>' +
          '<div class="nd-stat"><span class="nd-stat-label">义仓</span><span class="nd-stat-value">' + (cd.has_granary ? '有' : '无') + '</span></div>' +
          '<div class="nd-stat"><span class="nd-stat-label">医疗</span><span class="nd-stat-value">' + MEDICAL_NAMES[ml] + (neighborMedCost > 0 ? '(' + neighborMedCost + '两/年)' : '') + '</span></div>' +
        '</div>' +
      '</div>';

    // 村庄表
    if (cd.villages && cd.villages.length > 0) {
      html += '<div class="nd-section"><h4>村庄</h4>';
      html += '<table class="data-table" style="font-size:0.85em;">';
      html += '<thead><tr><th>村名</th><th>人口</th><th>耕地</th><th>地主占比</th><th>民心</th><th>村塾</th></tr></thead><tbody>';
      cd.villages.forEach(function (v) {
        html += '<tr>' +
          '<td>' + escapeHtml(v.name) + '</td>' +
          '<td>' + v.population + '</td>' +
          '<td>' + v.farmland + '</td>' +
          '<td>' + (v.gentry_land_pct * 100).toFixed(0) + '%</td>' +
          '<td>' + Math.round(v.morale || 0) + '</td>' +
          '<td>' + (v.has_school ? '有' : '无') + '</td></tr>';
      });
      html += '</tbody></table></div>';
    }

    // 集市
    if (cd.markets && cd.markets.length > 0) {
      html += '<div class="nd-section"><h4>集市</h4>';
      cd.markets.forEach(function (m) {
        html += '<div class="nd-market">' + escapeHtml(m.name) +
          ' — 商户' + m.merchants + '，贸易指数' + m.trade_index + '</div>';
      });
      html += '</div>';
    }

    // AI分析
    if (neighbor.last_reasoning) {
      html +=
        '<div class="nd-section">' +
          '<h4>知县近期分析</h4>' +
          '<div class="nd-reasoning">' + escapeHtml(neighbor.last_reasoning) + '</div>' +
        '</div>';
    }

    // 事件日志区域 (lazy load)
    html += '<div class="nd-section"><h4>近期事件</h4><div id="nd-events-list"><p class="hint">加载中...</p></div></div>';

    body.innerHTML = html;
    modal.classList.remove("hidden");

    // Load events
    var g = Game.state.currentGame;
    if (g) {
      Game.api.getNeighborEvents(g.id, neighbor.id, 50)
        .then(function (events) {
          var evtContainer = document.getElementById("nd-events-list");
          if (!evtContainer) return;
          evtContainer.innerHTML = "";
          if (!events || events.length === 0) {
            evtContainer.innerHTML = '<p class="hint">暂无事件</p>';
            return;
          }
          events.forEach(function (evt) {
            var catColor = {
              AI_DECISION: "#8e44ad",
              SETTLEMENT: "#27ae60",
              DISASTER: "#c0392b",
              INVESTMENT: "#2e86c1",
              TAX: "#d4a017",
            }[evt.category] || "#8a7a5a";
            var seasonText = Game.seasonName(evt.season);
            var item = h("div", "event-log-item",
              '<div class="event-log-header">' +
                '<span class="event-log-category" style="background:' + catColor + ';">' + evt.category + '</span>' +
                '<span class="event-log-season">' + seasonText + '</span>' +
              '</div>' +
              '<div class="event-log-desc">' + escapeHtml(evt.description || evt.event_type) + '</div>');
            evtContainer.appendChild(item);
          });
        })
        .catch(function () {
          var evtContainer = document.getElementById("nd-events-list");
          if (evtContainer) evtContainer.innerHTML = '<p class="hint">加载失败</p>';
        });
    }
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

  // Export
  C.renderNeighborsList = renderNeighborsList;
  C.openNeighborDetail = openNeighborDetail;
  C.openNeighborTermReport = openNeighborTermReport;
})();
