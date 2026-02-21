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

  // Export
  C.renderNeighborsList = renderNeighborsList;
  C.openNeighborDetail = openNeighborDetail;
})();
